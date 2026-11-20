#!/usr/bin/env python3
"""Prove native Unicode delivery on a private Xvfb display, never the host display.

Requires Xvfb, xauth, xdpyinfo, D-Bus tools, at-spi2-core, a C compiler,
pkg-config and GTK 3 development files. This script installs nothing or changes
host input settings. Optional xdotool exercises the native candidate panel too.
"""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import select
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

FIXTURE = r'''
#include <gtk/gtk.h>
#include <gdk/gdkx.h>
#include <glib-unix.h>
#include <signal.h>
#include <stdio.h>
static const char *result_path;
static GtkWidget *input;
static gboolean readonly(gpointer unused) {
    (void)unused; gtk_editable_set_editable(GTK_EDITABLE(input), FALSE);
    char *path = g_strconcat(result_path, ".mode", NULL);
    g_file_set_contents(path,"readonly",-1,NULL);g_free(path);return G_SOURCE_CONTINUE;
}
static gboolean writable(gpointer unused) {
    (void)unused; gtk_editable_set_editable(GTK_EDITABLE(input), TRUE);
    char *path = g_strconcat(result_path, ".mode", NULL);
    g_file_set_contents(path,"editable",-1,NULL);g_free(path);return G_SOURCE_CONTINUE;
}
static void changed(GtkEditable *editable, gpointer data) {
    (void)data;
    const char *text = gtk_entry_get_text(GTK_ENTRY(editable));
    g_file_set_contents(result_path, text, -1, NULL);
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    result_path = argv[1];
    gtk_init(&argc, &argv);
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), "ztype isolated Unicode receiver");
    gtk_window_set_default_size(GTK_WINDOW(window), 1000, 120);
    GtkWidget *entry = gtk_entry_new();
    input = entry;
    gtk_widget_get_accessible(entry);
    g_unix_signal_add(SIGUSR1, readonly, NULL);
    g_unix_signal_add(SIGUSR2, writable, NULL);
    gtk_container_add(GTK_CONTAINER(window), entry);
    g_signal_connect(entry, "changed", G_CALLBACK(changed), NULL);
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);
    gtk_widget_show_all(window);
    gtk_widget_grab_focus(entry);
    gtk_window_present(GTK_WINDOW(window));
    gdk_window_focus(gtk_widget_get_window(window), GDK_CURRENT_TIME);
    g_file_set_contents(result_path, "", 0, NULL);
    printf("%lu\n", gdk_x11_window_get_xid(gtk_widget_get_window(entry)));
    fflush(stdout);
    gtk_main();
    return 0;
}
'''

def line_with_timeout(stream, timeout=10):
    if not select.select([stream], [], [], timeout)[0]:
        raise RuntimeError("native fixture startup timed out")
    line = stream.readline().strip()
    if not line:
        raise RuntimeError("native fixture exited before startup")
    return line

def await_text(path: Path, expected: str):
    deadline = time.monotonic() + 5
    actual = ""
    while time.monotonic() < deadline:
        try:
            actual = path.read_text()
        except (FileNotFoundError, UnicodeError):
            pass
        if actual == expected:
            return
        time.sleep(0.02)
    raise AssertionError(f"native input mismatch: expected {expected!r}, received {actual!r}")

def reserve_display():
    # Atomic directory creation is the per-user display lease. It remains held
    # until Xvfb exits; unlike unlinking a flock file, it has no open-inode race.
    for _ in range(32):
        number = str(10_000 + secrets.randbelow(20_000))
        lease = Path("/tmp") / f"project-z-xvfb-{os.getuid()}-{number}.lock"
        try:
            lease.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return number, lease
    raise RuntimeError("could not reserve a private high-number display")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".build/native-proof"))
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    plugin = args.plugin.resolve(strict=True) if args.plugin else None
    if not args.inside:
        command = ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "--inside", "--binary", str(binary), "--output", str(args.output.resolve())]
        if plugin:
            command += ["--plugin", str(plugin)]
        return subprocess.call(command)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"display_isolation": "private high-number Xvfb with its own authorization, session and accessibility bus; no host display or input settings", "checks": []}
    with tempfile.TemporaryDirectory(prefix="ztype-native-") as scratch:
        scratch = Path(scratch)
        source, receiver = scratch / "receiver.c", scratch / "receiver"
        source.write_text(FIXTURE)
        flags = subprocess.check_output(["pkg-config", "--cflags", "--libs", "gtk+-3.0"], text=True)
        subprocess.run(["cc", str(source), "-o", str(receiver), *shlex.split(flags)], check=True)
        requested, display_lease = reserve_display()
        read_fd = write_fd = None
        server = None
        children = []
        try:
            read_fd, write_fd = os.pipe()
            authority = scratch / "Xauthority"
            authority.touch(mode=0o600)
            authorization = subprocess.run(["xauth", "-f", str(authority), "add", ":" + requested, "MIT-MAGIC-COOKIE-1", secrets.token_hex(16)], capture_output=True, timeout=5)
            if authorization.returncode:
                raise RuntimeError("failed to authorize the private display")
            server = subprocess.Popen(["Xvfb", ":" + requested, "-displayfd", str(write_fd), "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-nopn", "-auth", str(authority)],
                                      pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            os.close(write_fd)
            write_fd = None
            with os.fdopen(read_fd) as display_pipe:
                read_fd = None
                display = ":" + line_with_timeout(display_pipe)
            if display != ":" + requested or server.poll() is not None:
                raise RuntimeError("private display allocation was not confirmed")
            bus = subprocess.Popen(["dbus-daemon", "--config-file=/usr/share/defaults/at-spi2/accessibility.conf", "--nofork", "--print-address=1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(bus)
            address = line_with_timeout(bus.stdout)
            if not address.startswith("unix:"):
                raise RuntimeError("private accessibility bus did not start")
            environment = dict(os.environ, DISPLAY=display, XAUTHORITY=str(authority), ZUSE_BACKEND="x11", GDK_BACKEND="x11", GTK_IM_MODULE="simple", XMODIFIERS="@im=none", NO_AT_BRIDGE="0", AT_SPI_BUS_ADDRESS=address, GIO_USE_VFS="local")
            environment.pop("WAYLAND_DISPLAY", None)
            subprocess.run(["xdpyinfo", "-display", display], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=5)
            report["display"] = {"number": display, "high_number_lease": True, "private_authorization": True, "ready_matches_requested": True}
            registry_path = next((str(p) for p in [Path("/usr/lib/at-spi2-registryd"), Path("/usr/libexec/at-spi2-registryd")] if p.is_file()), None)
            if not registry_path:
                raise RuntimeError("at-spi2-registryd is required for the Unicode test")
            registry = subprocess.Popen([registry_path], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            children.append(registry)
            deadline = time.monotonic() + 5
            while True:
                owner = subprocess.run(["gdbus", "call", "--address", address, "--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.NameHasOwner", "org.a11y.atspi.Registry"], capture_output=True, text=True, timeout=2)
                if "true" in owner.stdout:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("private accessibility registry did not become ready")
                time.sleep(0.02)
            result = scratch / "received.txt"
            client = subprocess.Popen([str(receiver), str(result)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(client)
            window_id = line_with_timeout(client.stdout)
            deadline = time.monotonic() + 5
            while True:
                registered = subprocess.run(["gdbus", "call", "--address", address, "--dest", "org.a11y.atspi.Registry", "--object-path", "/org/a11y/atspi/accessible/root", "--method", "org.a11y.atspi.Accessible.GetChildren"], capture_output=True, text=True, timeout=2)
                if "/org/a11y/atspi/accessible/root" in registered.stdout:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("receiver did not register its editable accessibility object")
                time.sleep(0.02)
            cases = [
                (["--engine", "literal", "--text", "Z "], "Z "),
                (["--text", "nihao"], "你好"),
                (["--engine", "kana", "--text", "konnichiha"], "こんにちは"),
                (["--engine", "literal", "--text", " مرحبا नमस्ते 🙂"], " مرحبا नमस्ते 🙂"),
            ]
            if plugin:
                cases.append((["--plugin", str(plugin), "--text", "sekai"], "せかい"))
            expected = ""
            for options, text in cases:
                completed = subprocess.run([str(binary), "commit", "--window", window_id, *options], env=environment,
                                           capture_output=True, text=True, timeout=15, check=True)
                expected += text
                await_text(result, expected)
                report["checks"].append({"command": options, "expected_append": text, "stdout": completed.stdout.strip(), "exact_utf8": True})
            report["received"] = result.read_text()
            report["status"] = "native-commit-passed; panel pending"
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            if shutil.which("xdotool"):
                def open_panel(options):
                    panel = subprocess.Popen([str(binary), "panel", "--window", window_id, *options],
                                             env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    children.append(panel)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        search = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "^ztype compose$"],
                                                env=environment, capture_output=True, text=True)
                        if search.returncode == 0:
                            panel_id = search.stdout.splitlines()[-1]
                            subprocess.run(["xdotool", "windowraise", panel_id], env=environment, check=True)
                            subprocess.run(["xdotool", "windowfocus", panel_id], env=environment, check=True)
                            return panel, panel_id
                        if panel.poll() is not None:
                            raise RuntimeError("ztype panel exited: " + panel.stderr.read().decode())
                        time.sleep(0.05)
                    raise RuntimeError("ztype panel did not open on the isolated display")

                def capture_panel(panel_id, name):
                    if not shutil.which("import"):
                        return
                    screenshot = args.output.resolve() / name
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        subprocess.run(["import", "-display", display, "-window", panel_id, str(screenshot)],
                                       env=environment, check=True, timeout=10)
                        if screenshot.stat().st_size > 2048:
                            return
                        time.sleep(0.1)
                    raise RuntimeError("candidate panel did not produce a visible frame")

                def keys(panel_id, *values):
                    # Exercise ordinary focused input, not background XSendEvent.
                    subprocess.run(["xdotool", "windowfocus", panel_id], env=environment, check=True)
                    subprocess.run(["xdotool", "key", "--clearmodifiers", *values],
                                   env=environment, check=True, timeout=10)

                def close_panel(panel):
                    panel.terminate()
                    panel.wait(timeout=3)

                panel, panel_id = open_panel(["--text", "nihao"])
                properties = subprocess.check_output(["xprop", "-id", panel_id, "WM_NORMAL_HINTS", "WM_CLASS"], env=dict(environment, LC_ALL="C"), text=True, timeout=10)
                (args.output / "window-properties.txt").write_text(properties)
                minimum = re.search(r"minimum size:\s*(\d+) by (\d+)", properties)
                assert minimum and tuple(map(int, minimum.groups())) == (520, 112), properties
                assert '"ztype", "Ztype"' in properties, properties
                report["window"] = {"minimum_size": [520, 112], "class": ["ztype", "Ztype"], "properties": "window-properties.txt"}
                capture_panel(panel_id, "panel.png")
                subprocess.run(["xdotool", "mousemove", "--window", panel_id, "497", "21", "click", "1"], env=environment, check=True, timeout=10)
                time.sleep(0.1)
                capture_panel(panel_id, "panel-help.png")
                keys(panel_id, "Escape", "Return")
                expected += "你好"
                await_text(result, expected)
                report["checks"].append({"command": "native panel Enter", "expected_append": "你好", "exact_utf8": True})
                close_panel(panel)
                # The panel commits in its key-press callback while Return can
                # still be physically down. Literal ASCII must remain literal
                # without releasing/replaying the operator's held key.
                panel, panel_id = open_panel(["--engine", "literal", "--text", "ASCII Ztype"])
                subprocess.run(["xdotool", "windowfocus", panel_id, "keydown", "Return"], env=environment, check=True, timeout=10)
                try:
                    expected += "ASCII Ztype"
                    await_text(result, expected)
                    report["checks"].append({"command": "native panel ASCII Enter while Return remains down", "expected_append": "ASCII Ztype", "exact_utf8": True})
                except Exception:
                    capture_panel(panel_id, "panel-held-enter-failure.png")
                    raise
                finally:
                    subprocess.run(["xdotool", "keyup", "Return"], env=environment, check=True, timeout=10)
                close_panel(panel)

                # All nine candidates remain reachable despite the compact row.
                dictionary = scratch / "pages.tsv"
                words = ["候选" + number for number in "一二三四五六七八九"]
                dictionary.write_text("".join(f"page\t{word}\t{9-index}\n" for index, word in enumerate(words)))
                panel, panel_id = open_panel(["--engine", "table", "--dict", str(dictionary), "--text", "page"])
                capture_panel(panel_id, "panel-pages-before.png")
                subprocess.run(["xdotool", "mousemove", "--window", panel_id, "496", "61", "click", "1"],
                               env=environment, check=True, timeout=10)
                time.sleep(0.15)
                capture_panel(panel_id, "panel-paging.png")
                keys(panel_id, "Return")
                expected += words[5]
                await_text(result, expected)
                report["checks"].append({"command": "native panel next page and Enter", "expected_append": words[5], "exact_utf8": True})
                close_panel(panel)

                # Invalid edited text and an empty composition must never send
                # the valid candidate that preceded them.
                panel, panel_id = open_panel(["--engine", "unicode", "--text", "U+4F60"])
                keys(panel_id, "ctrl+a")
                subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "1", "U+597D"],
                               env=environment, check=True, timeout=10)
                keys(panel_id, "ctrl+c")
                time.sleep(0.1)
                keys(window_id, "ctrl+v")
                expected += "好"
                await_text(result, expected)
                report["checks"].append({"command": "Ctrl+A replaces draft and Ctrl+C copies through native clipboard", "expected_append": "好", "exact_utf8": True})
                keys(panel_id, "ctrl+a")
                subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "1", "U+D800"],
                               env=environment, check=True, timeout=10)
                keys(panel_id, "Return")
                time.sleep(0.2)
                capture_panel(panel_id, "panel-invalid.png")
                assert result.read_text() == expected, "invalid edited input committed a stale candidate"
                keys(panel_id, "Escape", "Return")
                time.sleep(0.2)
                assert result.read_text() == expected, "empty composition committed a stale candidate"
                report["checks"].append({"command": "invalid edit and Escape prevent stale panel commit", "expected_append": "", "exact_utf8": True})
                close_panel(panel)
            else:
                report["panel"] = "not exercised: xdotool unavailable"
            report["received"] = result.read_text()
            os.kill(client.pid, signal.SIGUSR1)
            await_text(Path(str(result) + ".mode"), "readonly")
            rejected = subprocess.run([str(binary), "commit", "--window", window_id, "--engine", "literal", "--text", "ASCII prefix🙂"], env=environment, capture_output=True, text=True, timeout=15)
            assert rejected.returncode != 0 and "submitted" not in rejected.stdout, rejected
            assert result.read_text() == expected, "unsupported target received part of a rejected string"
            report["checks"].append({"command": "readonly target rejects the complete mixed string", "expected_append": "", "rejected": True, "stderr": rejected.stderr.strip()})
            report["utf8_bytes"] = len(report["received"].encode())
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["error"] = str(error)
            if "result" in locals() and result.exists():
                report["received"] = result.read_text()
            raise
        finally:
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            if server is not None:
                if server.poll() is None:
                    server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait()
            for descriptor in (read_fd, write_fd):
                if descriptor is not None:
                    os.close(descriptor)
            display_lease.rmdir()
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    raise SystemExit(main())

