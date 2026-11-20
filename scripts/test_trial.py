#!/usr/bin/env python3
"""Exercise the installed interactive trial through actual Wayland keyboard input."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import test_wayland as native


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def service_state():
    result = subprocess.run(
        ["systemctl", "--user", "show", "fcitx5.service", "ztype-wayland.service",
         "-p", "ActiveState", "-p", "MainPID"], text=True, capture_output=True, check=True,
    )
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".build/interactive-trial-proof"))
    args = parser.parse_args()
    binary = args.binary.expanduser().resolve(strict=True)
    helper = binary.parent.parent / "libexec/ztype/try_wayland.py"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    session_path, state_path = output / "session.json", output / "receiver.json"
    for path in (session_path, state_path):
        if path.exists():
            raise RuntimeError(f"Use a fresh evidence directory: {path}")
    report = {"schema": "ztype.installed-trial-acceptance.v1", "checks": [], "passed": False}
    before = service_state()
    process = keyboard = None
    try:
        with (output / "trial.log").open("w") as log, tempfile.TemporaryDirectory(
                prefix="ztype-trial-keyboard-") as temporary:
            process = subprocess.Popen([
                sys.executable, str(helper), "--binary", str(binary),
                "--state", str(state_path), "--session-file", str(session_path),
            ], stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 40
            session = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Trial exited before readiness: " + (output / "trial.log").read_text()[-2000:])
                session = read_json(session_path)
                if session and session.get("status") == "ready":
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("Installed trial did not become ready")
            nested = dict(os.environ)
            nested.update(
                WAYLAND_DISPLAY=session["wayland_display"],
                HYPRLAND_INSTANCE_SIGNATURE=session["compositor_instance"],
            )
            nested.pop("DISPLAY", None)
            if session["wayland_display"] == os.environ["WAYLAND_DISPLAY"]:
                raise RuntimeError("Trial did not allocate a separate Wayland connection")
            devices = json.loads(native.command(
                ["hyprctl", "-j", "devices"], environment=nested,
            ).stdout)
            device_names = {
                device["name"] for kind in ("keyboards", "mice")
                for device in devices.get(kind, [])
            }
            if "wl_keyboard" not in device_names:
                raise RuntimeError("Expected the nested Wayland keyboard before input isolation")
            isolated_devices = []
            for name in ("wl_keyboard", "wl_pointer"):
                if name in device_names:
                    native.command([
                        "hyprctl", "eval",
                        "hl.device({name=" + json.dumps(name) + ", enabled=false})",
                    ], environment=nested)
                    isolated_devices.append(name)
            report["isolation"] = {
                "parent_wayland_input_disabled": isolated_devices,
                "independent_virtual_keyboard": True,
                "host_input_method_modified": False,
            }
            executable = native.build_keyboard(Path(temporary))
            keyboard = native.Keyboard(executable, output / "keyboard.xkb", nested)

            def wait(predicate, description):
                deadline = time.monotonic() + 6
                latest = None
                while time.monotonic() < deadline:
                    latest = read_json(state_path)
                    if latest and predicate(latest):
                        return latest
                    if process.poll() is not None:
                        raise RuntimeError("Trial exited while waiting for " + description)
                    time.sleep(0.02)
                raise AssertionError(description + ": " + json.dumps(latest, ensure_ascii=False))

            def send(*arguments):
                for before_send in (True, False):
                    if not before_send:
                        keyboard.send(*arguments)
                    focused = json.loads(native.command(
                        ["hyprctl", "-j", "activewindow"], environment=nested,
                    ).stdout)
                    if focused.get("pid") != session["receiver_pid"]:
                        raise RuntimeError("The private trial lost focus; stopped sending keys")

            def passed(name, state=None):
                record = {"name": name, "passed": True}
                if state:
                    record.update(actual_text=state["text"], preedit=state["preedit"])
                report["checks"].append(record)
                print("PASS " + name, flush=True)

            def committed(text):
                return wait(lambda state: state["text"] == text and not state["preedit"],
                            "expected committed text " + repr(text))

            def clear():
                send("key", "Escape")
                send("shortcut", "ctrl", "a")
                send("key", "BackSpace")
                return committed("")

            state = wait(lambda state: state["backend"] == "wayland" and not state["closed"],
                         "native editable trial window")
            passed("installed-native-trial-window", state)
            send("text", "nihao")
            state = wait(lambda state: state["preedit"] == "你好" and state["text"] == "",
                         "uncommitted Chinese preedit")
            passed("typed-ascii-produces-native-preedit", state)
            send("key", "space")
            passed("space-commits-to-real-gtk-entry", committed("你好"))
            send("text", "shijie")
            send("key", "space")
            passed("continued-typing-appends-chinese", committed("你好世界"))

            clear()
            send("text", "woxiangceshishuruxiaoguo")
            send("key", "space")
            passed("original-vocabulary-native-sentence", committed("我想测试输入效果"))

            clear()
            send("text", "nihaox")
            send("key", "BackSpace")
            send("key", "space")
            passed("backspace-corrects-live-composition", committed("你好"))
            clear()
            send("text", "nihao")
            send("key", "Escape")
            passed("escape-cancels-without-committing", committed(""))

            lines = native.command([binary, "candidates", "--text", "ni"]).stdout.splitlines()
            candidates = [line.split("\t", 1)[1] for line in lines]
            send("text", "ni")
            send("key", "Tab")
            send("key", "space")
            passed("tab-selects-live-candidate", committed(candidates[1]))

            clear()
            send("shortcut", "ctrl", "space")
            send("text", "hello42")
            send("key", "space")
            passed("ctrl-space-switches-to-direct-input", committed("hello42 "))
            send("shortcut", "ctrl", "space")
            send("text", "nihao")
            send("key", "space")
            passed("ctrl-space-restores-chinese", committed("hello42 你好"))

            clear()
            send("text", "woxiangceshixyz")
            send("key", "space")
            passed("unknown-suffix-keeps-complete-literal", committed("woxiangceshixyz"))
            clear()
            send("text", "woxiangceshishuruxiaoguo")
            send("key", "space")
            committed("我想测试输入效果")
            focused = json.loads(native.command(["hyprctl", "-j", "activewindow"], environment=nested).stdout)
            x, y = focused["at"]
            width, height = focused["size"]
            native.command(
                ["grim", "-g", f"{x},{y} {width}x{height}", output / "interactive-trial.png"],
                environment=nested,
            )
            # The real window handles this shortcut and exits its GTK event loop.
            keyboard.send("shortcut", "ctrl", "q")
            process.wait(timeout=15)
            if process.returncode != 0:
                raise RuntimeError(f"Trial close returned {process.returncode}")
            final = read_json(session_path)
            if not final or final["status"] != "closed" or not final["owned_processes_stopped"]:
                raise AssertionError("Trial did not clean up its owned processes")
            passed("ctrl-q-closes-window-and-owned-processes")
            if service_state() != before:
                raise AssertionError("The host input service state changed during the trial")
            passed("host-input-services-unchanged")
            report["passed"] = True
            report["input_text_recording"] = "explicit synthetic acceptance only; ordinary trial omits --state"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if keyboard:
            keyboard.stop()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report["launcher_stopped"] = process is None or process.poll() is not None
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("Report: " + str(output / "report.json"), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Interactive trial acceptance failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
