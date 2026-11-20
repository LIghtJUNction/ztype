#!/usr/bin/env python3
"""Verify a running ztype Wayland IME through a real native GTK input field.

This test uses the current Hyprland session. Start "ztype wayland" beforehand
with the seat available, then pass --allow-host-input. The script creates only
its own receiver, injects ASCII keys through a separate virtual keyboard,
compares the actual GTK entry contents, and restores the previous window focus.
It never starts or stops an installed input method or writes clipboard text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


def command(argv, *, environment=None, timeout=10):
    completed = subprocess.run(
        [str(item) for item in argv], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
    )
    if completed.returncode:
        detail = (completed.stderr.strip() or completed.stdout.strip())[:2000]
        raise RuntimeError(
            f"{Path(str(argv[0])).name} failed with status {completed.returncode}: {detail}"
        )
    return completed


def hypr_json(name):
    return json.loads(command(["hyprctl", "-j", name]).stdout)


def focus_window(address, lua_dispatch):
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", address):
        raise RuntimeError("compositor returned an invalid window address")
    if lua_dispatch:
        action = f'hl.dsp.focus({{window="address:{address}"}})'
        command(["hyprctl", "dispatch", action])
    else:
        command(["hyprctl", "dispatch", "focuswindow", "address:" + address])



def build_keyboard(scratch):
    fixtures = Path(__file__).resolve().parents[1] / "tests"
    protocol = fixtures / "protocols" / "virtual-keyboard-unstable-v1.xml"
    header = scratch / "virtual-keyboard-unstable-v1-client-protocol.h"
    generated = scratch / "virtual-keyboard-unstable-v1-protocol.c"
    executable = scratch / "wayland-keyboard"
    command(["wayland-scanner", "client-header", protocol, header])
    command(["wayland-scanner", "private-code", protocol, generated])
    flags = shlex.split(command(
        ["pkg-config", "--cflags", "--libs", "wayland-client", "xkbcommon"]
    ).stdout)
    command([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", scratch,
        fixtures / "wayland_keyboard.c", generated, "-o", executable, *flags,
    ], timeout=30)
    return executable


class Keyboard:
    """An independent persistent client with an unchanged, complete US keymap."""
    def __init__(self, executable, keymap_path, environment=None):
        self.sequence = 0
        self.errors = keymap_path.with_suffix(".stderr").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(executable), str(keymap_path)], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors, text=True,
        )
        try:
            self.identity = self.response(timeout=6)
            if not self.identity.get("ready"):
                raise RuntimeError("persistent test keyboard did not become ready")
            fixtures = Path(__file__).resolve().parents[1] / "tests"
            self.identity.update(
                keymap_sha256=hashlib.sha256(keymap_path.read_bytes()).hexdigest(),
                injector_source_sha256=hashlib.sha256(
                    (fixtures / "wayland_keyboard.c").read_bytes()).hexdigest(),
                protocol_xml_sha256=hashlib.sha256(
                    (fixtures / "protocols/virtual-keyboard-unstable-v1.xml").read_bytes()).hexdigest(),
            )
        except Exception:
            self.stop()
            raise

    def response(self, timeout=6):
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not ready:
            raise RuntimeError("persistent test keyboard did not acknowledge input")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(
                f"persistent test keyboard exited with {self.process.poll()}; see keyboard stderr"
            )
        return json.loads(line)

    def send(self, action, *arguments):
        line = " ".join([action, *arguments])
        if not line.isascii() or any(character in line for character in "\r\n\x00"):
            raise ValueError("The native test keyboard accepts one ASCII command at a time.")
        self.sequence += 1
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        result = self.response()
        if result.get("acknowledged") != self.sequence or not result.get("ok"):
            raise RuntimeError("persistent keyboard command was not acknowledged")

    def stop(self):
        if self.process.poll() is None:
            try:
                self.send("quit")
                self.process.wait(timeout=3)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        self.errors.close()


class Receiver:
    def __init__(self, process, path, title, keyboard):
        self.process = process
        self.path = path
        self.title = title
        self.acknowledged = 0
        self.keyboard = keyboard

    def state(self):
        if self.process.poll() is not None:
            raise RuntimeError(f"GTK receiver exited with {self.process.returncode}")
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def wait(self, predicate, description, *, timeout=6):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            latest = self.state()
            if latest is not None and predicate(latest):
                return latest
            time.sleep(0.02)
        raise AssertionError(
            f"{description}; last receiver state: "
            + json.dumps(latest, ensure_ascii=False)
        )

    def control(self, value):
        self.acknowledged += 1
        self.process.stdin.write(value + "\n")
        self.process.stdin.flush()
        return self.wait(
            lambda state: state["acknowledged"] >= self.acknowledged,
            f"receiver did not acknowledge {value}",
        )

    def ensure_focus(self):
        focused = hypr_json("activewindow")
        if focused.get("pid") != self.process.pid:
            observed_pid = focused.get("pid")
            observed_class = focused.get("class", "")
            raise RuntimeError(
                "The test receiver lost desktop focus; no more keys were sent. "
                f"Observed pid={observed_pid!r}, class={observed_class!r}; "
                f"expected pid={self.process.pid}."
            )

    def keys(self, *arguments):
        self.ensure_focus()
        self.keyboard.send(*arguments)
        self.ensure_focus()

    def text(self, text):
        if not text.isascii():
            raise ValueError("The input test sends ASCII keys only.")
        self.keys("text", text)

    def key(self, name):
        self.keys("key", name)

    def shortcut(self, modifier, key):
        self.keys("shortcut", modifier, key)

    def empty_normal(self):
        self.control("writable")
        self.control("normal")
        self.key("Escape")
        self.control("clear")
        return self.wait(
            lambda state: state["normal"]["focused"]
            and state["normal"]["text"] == ""
            and state["normal"]["preedit"] == "",
            "normal field did not become empty and focused",
        )

    def composed(self):
        return self.wait(
            lambda state: bool(state["normal"]["preedit"])
            and state["normal"]["text"] == "",
            "ASCII keys did not produce uncommitted native preedit",
        )

    def committed(self, expected, field="normal"):
        return self.wait(
            lambda state: state[field]["text"] == expected
            and state[field]["preedit"] == "",
            f"{field} did not receive {expected!r}",
        )

    def stop(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write("quit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=3)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)


def copy_check(state):
    return {
        "normal": {
            "text": state["normal"]["text"],
            "preedit": state["normal"]["preedit"],
            "preedit_changes": state["normal"]["preedit_changes"],
        },
        "password": {
            "text": state["password"]["text"],
            "preedit": state["password"]["preedit"],
            "preedit_changes": state["password"]["preedit_changes"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True,
                        help="The ztype build used by the already running IME")
    parser.add_argument("--output", type=Path, default=Path(".build/wayland-proof"))
    parser.add_argument("--allow-host-input", action="store_true",
                        help="Explicitly target the current Hyprland desktop")
    parser.add_argument("--screenshot", action="store_true",
                        help="Capture only the test receiver while preedit is visible")
    parser.add_argument("--trace-events", action="store_true",
                        help="Record synthetic receiver key/preedit/selection event ordering")
    args = parser.parse_args()
    if not args.allow_host_input:
        parser.error("This live desktop test requires --allow-host-input.")
    for program in ("hyprctl", "wayland-scanner", "cc", "pkg-config"):
        if shutil.which(program) is None:
            raise RuntimeError(f"required test program not installed: {program}")
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("a current native Wayland session is required")
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).resolve().parents[1] / "tests" / "wayland_receiver.c"
    compositor = hypr_json("version")
    version = compositor.get("version") or compositor.get("tag") or compositor.get("branch", "")
    version_match = re.search(r"(\d+)\.(\d+)", version)
    if not version_match:
        raise RuntimeError("cannot determine the Hyprland dispatcher API version")
    lua_dispatch = tuple(map(int, version_match.groups())) >= (0, 57)
    previous_window = hypr_json("activewindow").get("address")
    title = "ztype Wayland verification " + secrets.token_hex(4)
    environment = dict(os.environ)
    environment.update(GDK_BACKEND="wayland", GTK_IM_MODULE="wayland",
                       GIO_USE_VFS="local")
    if args.trace_events:
        environment["ZTYPE_RECEIVER_TRACE"] = "1"
    report = {
        "schema": "ztype.native-wayland-acceptance.v1",
        "binary_version": command([binary, "--version"]).stdout.strip(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "receiver_backend": "native GTK3 Wayland",
        "compositor_version": version,
        "input_path": "ASCII evdev keys from a persistent independent US keyboard -> input-method-v2 -> GTK text-input",
        "scope": "one generated test window on the existing Hyprland session",
        "checks": [],
        "passed": False,
    }

    def passed(name, state, **extra):
        report["checks"].append(
            dict(name=name, passed=True, **copy_check(state), **extra)
        )
        print(f"PASS {name}", flush=True)

    receiver = None
    keyboard = None
    try:
        with tempfile.TemporaryDirectory(prefix="ztype-wayland-check-") as temporary:
            scratch = Path(temporary)
            executable = scratch / "receiver"
            keyboard_executable = build_keyboard(scratch)
            flags = shlex.split(command(["pkg-config", "--cflags", "--libs", "gtk+-3.0"]).stdout)
            command(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                     fixture, "-o", executable, *flags], timeout=30)
            keyboard = Keyboard(keyboard_executable, output / "keyboard-keymap.xkb", environment)
            report["input_keyboard"] = keyboard.identity
            with (output / "receiver.stderr").open("w", encoding="utf-8") as errors:
                process = subprocess.Popen(
                    [str(executable), str(output / "receiver.json"), title],
                    env=environment, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=errors, text=True,
                )
                receiver = Receiver(process, output / "receiver.json", title, keyboard)
                state = receiver.wait(
                    lambda state: state["pid"] == process.pid
                    and state["backend"] == "wayland",
                    "native Wayland receiver did not start",
                )
                deadline = time.monotonic() + 8
                own_window = None
                focused = False
                focus_error = ""
                while time.monotonic() < deadline:
                    receiver.state()
                    own_window = next(
                        (item for item in hypr_json("clients")
                         if item.get("pid") == process.pid
                         and item.get("mapped") is not False
                         and all(size > 0 for size in item.get("size", [0, 0]))),
                        None,
                    )
                    if own_window:
                        if own_window.get("xwayland"):
                            raise RuntimeError("test receiver unexpectedly uses XWayland")
                        try:
                            focus_window(own_window["address"], lua_dispatch)
                            focused = hypr_json("activewindow").get("pid") == process.pid
                            if focused:
                                break
                        except RuntimeError as error:
                            focus_error = str(error)
                    time.sleep(0.05)
                if not focused:
                    raise RuntimeError(
                        "receiver did not become mapped and focusable"
                        + (f": {focus_error}" if focus_error else "")
                    )
                receiver.control("normal")
                state = receiver.wait(
                    lambda state: state["normal"]["focused"],
                    "mapped receiver's normal field did not receive keyboard focus",
                )
                receiver.ensure_focus()
                passed("native-wayland-receiver", state)

                receiver.empty_normal()
                receiver.text("nihao")
                state = receiver.composed()
                passed("composition-is-preedit-before-commit", state)
                if args.screenshot:
                    if shutil.which("grim") is None:
                        raise RuntimeError("--screenshot requires grim")
                    window = next(
                        item for item in hypr_json("clients") if item.get("pid") == process.pid
                    )
                    x, y = window["at"]
                    width, height = window["size"]
                    command(["grim", "-g", f"{x},{y} {width}x{height}",
                             output / "wayland-preedit.png"])
                receiver.key("space")
                passed("pinyin-nihao-commit", receiver.committed("你好"))

                receiver.empty_normal()
                receiver.text("nihaox")
                receiver.key("BackSpace")
                receiver.key("space")
                passed("backspace-edits-composition", receiver.committed("你好"))

                receiver.empty_normal()
                receiver.text("nihao")
                receiver.composed()
                receiver.key("Escape")
                state = receiver.committed("")
                time.sleep(0.15)
                passed("escape-cancels-without-commit", state)

                receiver.empty_normal()
                receiver.text("nihaoshijie")
                receiver.key("space")
                passed("pinyin-phrase-combination", receiver.committed("你好世界"))

                candidate_lines = command(
                    [binary, "candidates", "--text", "ni"]
                ).stdout.splitlines()
                candidates = [line.split("\t", 1)[1] for line in candidate_lines]
                if len(candidates) < 2:
                    raise AssertionError("candidate-cycle fixture requires at least two ni candidates")
                receiver.empty_normal()
                receiver.text("ni")
                receiver.key("Tab")
                receiver.key("space")
                passed("tab-selects-next-candidate", receiver.committed(candidates[1]))

                receiver.empty_normal()
                receiver.text("ni")
                receiver.text("2")
                passed("number-selects-candidate", receiver.committed(candidates[1]))

                receiver.empty_normal()
                receiver.text("nihao")
                receiver.composed()
                receiver.control("password")
                receiver.wait(lambda state: state["password"]["focused"],
                              "password field did not receive focus")
                receiver.text("nihao")
                state = receiver.committed("nihao", "password")
                if state["normal"]["text"] != "":
                    raise AssertionError("focus change committed stale normal-field composition")
                passed("password-bypasses-composition", state)

                receiver.control("normal")
                receiver.wait(lambda state: state["normal"]["focused"],
                              "normal field did not regain focus")
                receiver.text("shijie")
                receiver.key("space")
                passed("focus-change-discards-old-preedit", receiver.committed("世界"))

                receiver.empty_normal()
                receiver.text("nihao")
                receiver.key("space")
                receiver.committed("你好")
                if args.trace_events:
                    receiver.shortcut("ctrl", "a")
                    report["shortcut_without_preedit"] = receiver.control("snapshot")["normal"]
                    receiver.key("End")
                receiver.text("tianqi")
                receiver.wait(
                    lambda state: bool(state["normal"]["preedit"]),
                    "shortcut fixture did not create pending composition",
                )
                report["shortcut_before_cancel"] = receiver.control("snapshot")["normal"]
                receiver.shortcut("ctrl", "a")
                receiver.wait(
                    lambda state: state["normal"]["preedit"] == ""
                    and state["normal"]["text"] == "你好",
                    "Ctrl+A did not cancel pending composition before selecting text",
                )
                report["shortcut_selection"] = receiver.control("snapshot")["normal"]
                receiver.text("s")
                report["shortcut_first_preedit"] = receiver.control("snapshot")["normal"]
                receiver.text("hijie")
                report["shortcut_composing"] = receiver.control("snapshot")["normal"]
                receiver.key("space")
                passed("ctrl-a-shortcut-and-replacement", receiver.committed("世界"))

                receiver.empty_normal()
                receiver.shortcut("ctrl", "space")
                receiver.text("hello42")
                receiver.key("space")
                passed("ctrl-space-latin-input", receiver.committed("hello42 "))
                receiver.shortcut("ctrl", "space")
                receiver.text("nihao")
                receiver.key("space")
                passed("ctrl-space-restores-pinyin", receiver.committed("hello42 你好"))

                receiver.empty_normal()
                receiver.control("readonly")
                receiver.text("nihao")
                receiver.key("space")
                time.sleep(0.2)
                state = receiver.state()
                if state["normal"]["text"] or state["normal"]["preedit"]:
                    raise AssertionError("read-only field accepted text or preedit")
                passed("readonly-field-unchanged", state)
                receiver.control("writable")
                report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        if receiver:
            try:
                report["failure_state"] = receiver.state()
            except Exception:
                pass
        raise
    finally:
        restore_focus = False
        if receiver:
            try:
                restore_focus = hypr_json("activewindow").get("pid") == receiver.process.pid
            except Exception:
                pass
            receiver.stop()
        if keyboard:
            keyboard.stop()
        if restore_focus and previous_window:
            try:
                focus_window(previous_window, lua_dispatch)
            except Exception:
                pass
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Report: {output / 'report.json'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Wayland acceptance failed: {error}", file=sys.stderr)
        raise SystemExit(1)
