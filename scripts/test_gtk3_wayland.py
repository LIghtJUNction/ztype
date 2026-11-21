#!/usr/bin/env python3
"""Check GTK3 input using real Mousepad files on a private headless Sway."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from test_wayland import Keyboard, build_keyboard


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
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def nodes(tree):
    yield tree
    for child in tree["nodes"] + tree["floating_nodes"]:
        yield from nodes(child)


def exercise(binary, root, stack):
    # Never inherit a display, session bus, app profile or input module.
    env = {k: os.environ[k] for k in ("PATH", "LD_LIBRARY_PATH") if k in os.environ}
    env.update(HOME=str(root), XDG_RUNTIME_DIR=str(root), LANG="C.UTF-8",
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={root}/unused-bus", GSETTINGS_BACKEND="memory",
               NO_AT_BRIDGE="1", WLR_BACKENDS="headless", WLR_RENDERER="pixman",
               WLR_HEADLESS_OUTPUTS="1", WLR_LIBINPUT_NO_DEVICES="1")
    def run(*args):
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=5, check=True).stdout

    def launch(label, argv, **extra):
        log = stack.enter_context((root / f"{label}.log").open("w"))
        proc = subprocess.Popen(argv, env=dict(env, **extra), stdout=log, stderr=log,
                                stdin=subprocess.DEVNULL)
        stack.callback(stop, proc)
        return proc

    if not json.loads(run(binary, "features")).get("wayland_popup"):
        raise RuntimeError("this regression requires a ztype build with the wayland-popup feature")
    sway_conf = root / "sway.conf"
    sway_conf.write_text("xwayland disable\noutput * resolution 960x640\nseat seat0 fallback true\n")
    sway = launch("sway", ["sway", "-c", sway_conf])
    def private_display():
        if sway.poll() is not None:
            raise RuntimeError((root / "sway.log").read_text())
        return next((p.name for p in root.glob("wayland-*") if p.is_socket()), None)
    env["WAYLAND_DISPLAY"] = wait_for(private_display, "Sway did not open its private socket")
    ipc = root / f"sway-ipc.{os.getuid()}.{sway.pid}.sock"
    wait_for(ipc.is_socket, "missing IPC socket for owned Sway PID")
    env["SWAYSOCK"] = str(ipc)
    keyboard = Keyboard(build_keyboard(root), root / "keyboard.xkb", env)
    stack.callback(keyboard.stop)

    settings, dictionary, state = (root / name for name in ("settings", "dict.tsv", "popup.json"))
    dictionary.write_text("nihao\t你好\t5000\nta\t他\t5000\nta\t她\t4999\n", encoding="utf-8")
    settings.write_text("initial_mode=chinese\n")
    notify = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
    notify.bind(str(root / "ready"))
    notify.settimeout(6)
    def start(label):
        ime = launch(label, [binary, "wayland", "--config", settings, "--dict", dictionary],
                     NOTIFY_SOCKET=str(root / "ready"), ZTYPE_POPUP_STATE_PATH=str(state))
        if notify.recv(128) != b"READY=1" or ime.poll() is not None:
            raise RuntimeError(f"Wayland frontend did not become ready: {(root / (label + '.log')).read_text()}")
        return ime

    ime = start("chinese")
    def window(app):
        tree = json.loads(run("swaymsg", "-rt", "get_tree"))
        return next((n for n in nodes(tree) if n.get("pid") == app.pid and n.get("shell") == "xdg_shell"), None)

    def editor(label, launcher=True):
        path = root / f"{label}.txt"
        path.touch()
        argv = ["mousepad", "--disable-server", path]
        if launcher:
            argv = [binary, "gtk3-wayland", *argv]
        app = launch(label, argv, GDK_BACKEND="wayland", GTK_IM_MODULE="gtk-im-context-simple")
        wait_for(lambda: window(app), f"Mousepad did not map a native window for {label}")
        return app, path

    def focus(client):
        app, _ = client
        node = window(app)
        run("swaymsg", f'[con_id={node["id"]}] focus')
        wait_for(lambda: (window(app) or {}).get("focused"), "editor failed to gain focus")

    def key(client, *args):
        app, _ = client
        if sway.poll() is not None or ime.poll() is not None or not (window(app) or {}).get("focused"):
            raise RuntimeError("private frontend or focused editor unavailable")
        keyboard.send(*args)

    def visible(expected):
        def observe():
            if state.exists():
                value = json.loads(state.read_text())
                presentation = "presented" if expected else "hidden"
                if value["requested_visible"] == expected and value["presentation_result"] == presentation:
                    return value
        return wait_for(observe, f"expected popup visibility {expected}")

    def save(client, expected):
        key(client, "shortcut", "ctrl", "s")
        raw = wait_for(lambda: client[1].read_bytes(), "Mousepad did not save its file")
        if raw != expected.encode():
            raise AssertionError(f"{client[1].name}: expected {expected.encode().hex()}, received {raw.hex()}")
        return raw.hex()

    result = {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "sway": run("sway", "--version").strip(),
              "mousepad": run("mousepad", "--version").splitlines()[0]}
    control = editor("control", False)
    focus(control)
    key(control, "text", "nihao")
    key(control, "key", "space")
    key(control, "key", "Return")
    result["simple_module_hex"] = save(control, "nihao \n")
    stop(control[0])

    first, second = editor("first"), editor("second")
    focus(first)
    key(first, "text", "nihao")
    composing = visible(True)
    focus(second)
    cleared = visible(False)
    if cleared["generation"] <= composing["generation"]:
        raise AssertionError("old input context survived the focus switch")
    key(second, "text", "ta")
    visible(True)
    key(second, "text", "2")
    visible(False)
    key(second, "key", "Return")
    result["selected_in_new_editor_hex"] = save(second, "她\n")
    focus(first)
    key(first, "key", "Return")
    result["old_editor_hex"] = save(first, "\n")
    result["focus_generations"] = [composing["generation"], cleared["generation"]]
    stop(first[0])
    stop(second[0])

    stop(ime)
    settings.write_text("initial_mode=english\n")
    if "initial_mode=english" not in run(binary, "config", "--config", settings).splitlines():
        raise AssertionError("initial mode differs from saved configuration")
    ime = start("english")
    last = editor("after-restart")
    focus(last)
    key(last, "text", "nihao")
    key(last, "key", "space")
    key(last, "key", "Return")
    key(last, "shortcut", "ctrl", "space")
    key(last, "text", "nihao")
    visible(True)
    key(last, "key", "space")
    key(last, "key", "Return")
    result["restarted_english_then_chinese_hex"] = save(last, "nihao \n你好\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    missing = [p for p in ("sway", "swaymsg", "mousepad", "wayland-scanner", "pkg-config", "cc")
               if not shutil.which(p)]
    if missing:
        parser.exit(77, f"Unsupported fixture environment. Missing {', '.join(missing)}\n")
    with tempfile.TemporaryDirectory(prefix="ztype-gtk3-") as directory, ExitStack() as stack:
        result = exercise(args.binary.resolve(strict=True), Path(directory), stack)
    print(json.dumps(result, ensure_ascii=False, indent=2))
