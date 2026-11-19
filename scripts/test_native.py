#!/usr/bin/env python3
"""Prove native Unicode delivery on a private Xvfb display, never the host display.

Requires already-installed Xvfb, a C compiler, pkg-config and GTK 3 development
files. This script installs nothing and restores no global state because it
changes none. Optional xdotool exercises the native candidate panel too.
"""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import tempfile
import time

FIXTURE = r'''
#include <gtk/gtk.h>
#include <gdk/gdkx.h>
#include <stdio.h>
static const char *result_path;
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".build/native-proof"))
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    plugin = args.plugin.resolve(strict=True) if args.plugin else None
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"display_isolation": "private Xvfb -displayfd; no host display or input settings", "checks": []}
    with tempfile.TemporaryDirectory(prefix="ztype-native-") as scratch:
        scratch = Path(scratch)
        source, receiver = scratch / "receiver.c", scratch / "receiver"
        source.write_text(FIXTURE)
        flags = subprocess.check_output(["pkg-config", "--cflags", "--libs", "gtk+-3.0"], text=True)
        subprocess.run(["cc", str(source), "-o", str(receiver), *shlex.split(flags)], check=True)
        read_fd, write_fd = os.pipe()
        server = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                                  pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.close(write_fd)
        children = []
        try:
            with os.fdopen(read_fd) as display_pipe:
                display = ":" + line_with_timeout(display_pipe)
            environment = dict(os.environ, DISPLAY=display, ZUSE_BACKEND="x11", GTK_IM_MODULE="simple", XMODIFIERS="@im=none")
            environment.pop("WAYLAND_DISPLAY", None)
            environment.pop("XAUTHORITY", None)
            result = scratch / "received.txt"
            client = subprocess.Popen([str(receiver), str(result)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(client)
            window_id = line_with_timeout(client.stdout)
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
            report["utf8_bytes"] = len(report["received"].encode())
            report["status"] = "passed"
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait()
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()
