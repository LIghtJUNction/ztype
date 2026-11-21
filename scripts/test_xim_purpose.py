#!/usr/bin/env python3
"""Check XIM focus consent with two synthetic GTK3 fields on a private Xwayland."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socket
import sys
import subprocess
import tempfile
import time


def wait_for(probe, message):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.04)
    raise RuntimeError(message)


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def exercise(binary, root, stack):
    # Only inherit tool/library lookup; all session and application state is private.
    env = {key: os.environ[key] for key in ("PATH", "LD_LIBRARY_PATH") if key in os.environ}
    env.update(HOME=str(root), XDG_RUNTIME_DIR=str(root), LANG="C.UTF-8",
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={root}/unused-bus", GSETTINGS_BACKEND="memory",
               NO_AT_BRIDGE="1", WLR_BACKENDS="headless", WLR_RENDERER="pixman",
               WLR_HEADLESS_OUTPUTS="1", WLR_LIBINPUT_NO_DEVICES="1", XWAYLAND_NO_GLAMOR="1")
    def run(*args):
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=5, check=True).stdout.strip()

    def launch(name, args, **kwargs):
        log = stack.enter_context((root / f"{name}.log").open("w"))
        proc = subprocess.Popen(args, stdin=kwargs.pop("stdin", subprocess.DEVNULL), stdout=kwargs.pop("stdout", log), stderr=log,
                                env=kwargs.pop("env", env), start_new_session=True, **kwargs)
        stack.callback(stop, proc)
        return proc

    features = json.loads(run(binary, "features"))
    if features.get("xim_field_purpose") != "unknown":
        raise RuntimeError("expected an X11 build reporting unknown field purpose")
    config = root / "sway.conf"
    config.write_text("xwayland disable\noutput * resolution 960x640\nseat seat0 fallback true\n")
    compositor = launch("sway", ["sway", "-c", config])
    def wayland_socket():
        if compositor.poll() is not None:
            raise RuntimeError((root / "sway.log").read_text())
        return next((p for p in root.glob("wayland-*") if p.is_socket()), None)
    env["WAYLAND_DISPLAY"] = wait_for(wayland_socket, "private Sway socket unavailable").name

    # Require the newly launched Xwayland process to acknowledge this exact display.
    # Its cookie and parent Wayland socket are both unique to this temporary directory.
    number = 2000 + secrets.randbelow(20000)
    env.update(DISPLAY=f":{number}", XAUTHORITY=str(root / "Xauthority"))
    Path(env["XAUTHORITY"]).touch(mode=0o600)
    cookie = secrets.token_hex(16)
    subprocess.run(["xauth", "-q"], input=f"add :{number} . {cookie}\n", env=env,
                   text=True, check=True, timeout=5)
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as ready:
        with os.fdopen(write_fd, "wb"):
            display = launch("Xwayland", ["Xwayland", env["DISPLAY"], "-displayfd", str(write_fd),
                             "-auth", env["XAUTHORITY"], "-nolisten", "tcp", "-noreset", "-shm"],
                             pass_fds=(write_fd,))
        if not select.select([ready], [], [], 8)[0] or ready.readline().strip() != str(number).encode():
            raise RuntimeError(f"private Xwayland startup failed: {(root / 'Xwayland.log').read_text()}")

    env.update(XMODIFIERS="@im=ztype-fixture", ZTYPE_XIM_NAME="ztype-fixture",
               GTK_IM_MODULE="xim", GDK_BACKEND="x11")
    dictionary = root / "fixture.tsv"
    dictionary.write_text("ta\t他\t5000\nta\t她\t4999\n", encoding="utf-8")
    history = root / "history"
    history.mkdir()
    settings = root / "config"
    state = root / "popup.json"
    notify = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
    notify.bind(str(root / "ready"))
    notify.settimeout(6)
    report = {"elf_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    for manual in [False, True]:
        mode = "manual" if manual else "passthrough"
        # Omit the policy in the first run to exercise existing configurations.
        settings.write_text("initial_mode=chinese\nlearning=true\n"
                            f"user_dictionary={history / 'words.tsv'}\n"
                            + ("xim_unknown_purpose=manual\n" if manual else ""))
        if f"xim_unknown_purpose={mode}" not in run(binary, "config", "--config", settings).splitlines():
            raise AssertionError("effective XIM policy differs from configuration")
        ime = launch(mode, [binary, "x11", "--config", settings, "--dict", dictionary],
                     env=dict(env, NOTIFY_SOCKET=str(root / "ready"), ZTYPE_POPUP_STATE_PATH=str(state)))
        if notify.recv(128) != b"READY=1":
            raise RuntimeError("XIM failed to register")
        wait_for(lambda: all(marker in (root / f"{mode}.log").read_text() for marker in
                             ("field purpose=unknown", f"policy={mode}", "learning=off")),
                 "missing XIM purpose diagnostic")
        app = launch(mode + "-gtk", [sys.executable, __file__, "--receiver"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                     env=dict(env, ZTYPE_XIM_PURPOSE_FIXTURE=str(root)))
        stack.callback(app.stdin.close)
        stack.callback(app.stdout.close)

        def answer():
            if not select.select([app.stdout], [], [], 5)[0]:
                raise RuntimeError("synthetic GTK client did not respond")
            return json.loads(app.stdout.readline())

        def command(name):
            app.stdin.write(name + "\n")
            app.stdin.flush()
            return answer()

        def key(*args):
            if (display.poll() is not None or ime.poll() is not None or app.poll() is not None
                    or run("xdotool", "getwindowfocus", "getwindowpid") != str(app.pid)):
                raise RuntimeError("private XIM or focused fixture is unavailable")
            run("xdotool", *args)

        def mapped_popup():
            result = subprocess.run(["xdotool", "search", "--onlyvisible", "--name",
                                     "^zinput-x11 candidate popup$"], env=env,
                                    capture_output=True, text=True, timeout=3)
            if result.returncode not in (0, 1):
                raise RuntimeError("could not inspect the private candidate window")
            return bool(result.stdout.strip())

        window = answer()["window"]
        run("xdotool", "windowfocus", "--sync", str(window))
        command("plain")
        if manual:
            key("key", "ctrl+space")
            key("type", "ta")
            wait_for(mapped_popup, "plain field did not show candidates after opt-in")
        command("masked")
        wait_for(lambda: not mapped_popup(), "candidate window survived the field change")
        # Selection arrives after changing fields. It must be an ordinary digit here.
        key("key", "2")
        key("type", "nihao")
        wait_for(lambda: command("status")["masked_literal"], "masked field lost literal text")
        if mapped_popup():
            raise AssertionError("masked field mapped a candidate window")
        key("key", "space")
        wait_for(lambda: command("status")["masked_complete"], "masked field converted the fixture")
        command("plain")
        key("type", "ta")
        wait_for(lambda: command("status")["plain_literal"], "old focus consent was reused")
        key("key", "ctrl+a", "BackSpace")
        if manual:
            key("key", "ctrl+space")
            key("type", "ta")
            wait_for(mapped_popup, "fresh opt-in did not compose")
            key("key", "2")
            wait_for(lambda: command("status")["selected_once"], "fresh selection did not commit once")
        else:
            key("key", "ctrl+space")
            key("type", "ta")
            wait_for(lambda: command("status")["plain_literal"], "default policy allowed opt-in")
        if mapped_popup() or not command("status")["masked_complete"]:
            raise AssertionError("candidate visibility or destination mismatch")
        stop(app)
        stop(ime)
        if state.exists():
            raise AssertionError("unknown-purpose XIM wrote popup diagnostics")
        if list(history.iterdir()):
            raise AssertionError("XIM created a personal dictionary or lock")
        report[mode] = {"masked_literal": True, "no_masked_candidate": True,
                        "history_directory_empty": True, "no_popup_diagnostics": True,
                        "fresh_selection_tested": manual}
    return report


def receiver():
    # The transport gets no field annotations from this test. Only GTK knows
    # which widget is masked. Report predicates, never field contents.
    directory = os.environ.get("ZTYPE_XIM_PURPOSE_FIXTURE")
    if not directory or os.environ.get("XAUTHORITY") != str(Path(directory) / "Xauthority"):
        raise RuntimeError("receiver requires the private Xwayland fixture")
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11, GLib, Gtk

    win = Gtk.Window()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    plain, masked = Gtk.Entry(), Gtk.Entry()
    masked.set_visibility(False)
    masked.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    for field in (plain, masked):
        box.pack_start(field, False, False, 0)
    win.add(box)
    win.show_all()
    plain.grab_focus()
    print(json.dumps({"window": GdkX11.X11Window.get_xid(win.get_window())}), flush=True)

    def control(*_):
        name = sys.stdin.readline().strip()
        if not name:
            Gtk.main_quit()
            return False
        if name in ("plain", "masked"):
            (plain if name == "plain" else masked).grab_focus()
            win.get_display().sync()
        print(json.dumps({"both_empty": not plain.get_text() and not masked.get_text(),
                          "masked_literal": masked.get_text() == "2nihao",
                          "masked_complete": masked.get_text() == "2nihao ",
                          "plain_literal": plain.get_text() == "ta",
                          "selected_once": plain.get_text() == "她"}), flush=True)
        return True
    GLib.io_add_watch(sys.stdin, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, control)
    Gtk.main()


if __name__ == "__main__":
    if sys.argv[1:] == ["--receiver"]:
        receiver()
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--binary", type=Path, required=True)
        args = parser.parse_args()
        missing = [p for p in ("sway", "Xwayland", "xdotool", "xauth") if not shutil.which(p)]
        if missing:
            parser.exit(77, f"Missing fixture tools: {', '.join(missing)}\n")
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("GdkX11", "3.0")
        except (ImportError, ValueError) as error:
            parser.exit(77, f"Missing GTK3 fixture runtime: {error}\n")
        with tempfile.TemporaryDirectory(prefix="ztype-xim-") as directory, ExitStack() as stack:
            result = exercise(args.binary.resolve(strict=True), Path(directory), stack)
        print(json.dumps(result, indent=2))
