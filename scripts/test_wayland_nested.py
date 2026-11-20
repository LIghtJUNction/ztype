#!/usr/bin/env python3
"""Run the native GTK acceptance suite in a disposable nested Hyprland session.

Requires an existing Wayland desktop, Hyprland 0.57+, dbus-run-session, wayland-scanner,
GTK3 development files and the tools used by test_wayland.py. The host input
method and keyboard focus are never used for injected test keys. All keyboard
input targets a new Wayland socket and the matching child compositor instance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tempfile
import time

import test_wayland as native_tools


CONFIG = """hl.monitor({
    output = "",
    mode = "preferred",
    position = "auto",
    scale = "1",
})
hl.config({
    input = { follow_mouse = 0, kb_layout = "us" },
    animations = { enabled = false },
    xwayland = { enabled = false },
    misc = { disable_hyprland_logo = true, disable_splash_rendering = true },
})
"""


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv, environment, timeout=10):
    return subprocess.run(
        [str(arg) for arg in argv], env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=timeout,
    )


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)



def process_sample(process, phase):
    """Read Linux accounting for the IME child owned by this launcher."""
    if process.poll() is not None:
        raise RuntimeError("owned IME exited during resource observation")
    directory = Path("/proc") / str(process.pid)
    stat_fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
    totals = {}
    for line in (directory / "smaps_rollup").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("Rss:", "Pss:"):
            totals[parts[0][:-1].lower() + "_kib"] = int(parts[1])
    return {
        "phase": phase,
        "monotonic_ns": time.monotonic_ns(),
        "pid": process.pid,
        "process_start_ticks": int(stat_fields[19]),
        "minor_faults": int(stat_fields[7]),
        **totals,
    }


def watch_composition(process, state_path, samples, errors, finished):
    while not finished.wait(0.05):
        try:
            state = json.loads(state_path.read_text())
            if (not state.get("closed") and
                    bool(state.get("normal", {}).get("preedit"))):
                samples.append(process_sample(process, "composing"))
                if len(samples) == 3:
                    return
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as error:
            errors.append(str(error))
            return


def owned_window(environment, process):
    result = run(["hyprctl", "-j", "clients"], environment)
    if result.returncode:
        return None
    for client in json.loads(result.stdout):
        if (client.get("pid") == process.pid and client.get("mapped") is not False
                and all(n > 0 for n in client.get("size", [0, 0]))):
            return client
    return None


def require_owned_focus(environment, process):
    result = run(["hyprctl", "-j", "activewindow"], environment)
    focused = json.loads(result.stdout) if result.returncode == 0 else {}
    if focused.get("pid") != process.pid:
        raise RuntimeError(
            "owned terminal lost nested focus; no more keys were sent. "
            f"observed pid={focused.get('pid')!r}, class={focused.get('class', '')!r}"
        )


TERMINAL_RECEIVER = """import json, os, sys
from pathlib import Path
result = Path(sys.argv[1])
print("ztype native terminal input verification", flush=True)
line = sys.stdin.readline()
record = {
    "schema": "ztype.terminal-stdin.v1",
    "pid": os.getpid(),
    "stdin_isatty": sys.stdin.isatty(),
    "text": line.rstrip("\\n"),
    "received_utf8_hex": line.encode("utf-8").hex(),
}
result.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\\n")
print("Received through terminal stdin: " + record["text"], flush=True)
"""


def terminal_acceptance(environment, output, screenshot):
    """Start foot directly with an owned stdin receiver, without a shell."""
    if shutil.which("foot") is None:
        return {"passed": False, "error": "foot is not installed"}
    receiver = output / "terminal_receiver.py"
    receiver.write_text(TERMINAL_RECEIVER, encoding="utf-8")
    result_path = output / "terminal-stdin.json"
    result_path.unlink(missing_ok=True)
    log_path = output / "terminal.log"
    result = {"passed": False, "terminal": "foot", "input_ascii": "nihaoshijie"}
    process = None
    keyboard = None
    temporary = None
    try:
        temporary = tempfile.TemporaryDirectory(prefix="terminal-keyboard-", dir=environment["TMPDIR"])
        executable = native_tools.build_keyboard(Path(temporary.name))
        keyboard = native_tools.Keyboard(executable, output / "terminal-keymap.xkb", environment)
        result["input_keyboard"] = keyboard.identity
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    "foot", "--config=/dev/null", "--hold",
                    "--title=ztype native terminal verification",
                    "--app-id=ztype-terminal-verification",
                    sys.executable, "-u", str(receiver), str(result_path),
                ],
                env=environment, stdout=log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 8
            client = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"foot exited with {process.returncode}; see terminal.log")
                client = owned_window(environment, process)
                if client:
                    break
                time.sleep(0.05)
            if client is None:
                raise RuntimeError("owned foot window did not become mapped")
            address = client["address"]
            if not address.startswith("0x") or any(
                char not in "0123456789abcdefABCDEF" for char in address[2:]
            ):
                raise RuntimeError("invalid owned terminal window address")
            focus = run(
                ["hyprctl", "dispatch", f'hl.dsp.focus({{window="address:{address}"}})'],
                environment,
            )
            if focus.returncode:
                raise RuntimeError("could not focus the owned terminal")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    require_owned_focus(environment, process)
                    break
                except RuntimeError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("owned foot window did not receive nested focus")
            for arguments in (["text", "nihaoshijie"], ["key", "space"], ["key", "Return"]):
                require_owned_focus(environment, process)
                keyboard.send(*arguments)
                require_owned_focus(environment, process)
            deadline = time.monotonic() + 6
            while not result_path.is_file() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("foot exited before stdin reception")
                time.sleep(0.05)
            if not result_path.is_file():
                raise RuntimeError("foot stdin receiver did not receive a completed line")
            actual = json.loads(result_path.read_text())
            result.update(
                actual_text=actual["text"],
                stdin_isatty=actual["stdin_isatty"],
                received_utf8_hex=actual["received_utf8_hex"],
                passed=actual["stdin_isatty"] and actual["text"] == "你好世界",
            )
            if not result["passed"]:
                result["error"] = "native terminal stdin did not receive the expected Chinese text"
            if screenshot and shutil.which("grim"):
                time.sleep(0.1)
                require_owned_focus(environment, process)
                client = owned_window(environment, process)
                x, y = client["at"]
                width, height = client["size"]
                saved = run(
                    ["grim", "-g", f"{x},{y} {width}x{height}", output / "terminal-foot.png"],
                    environment,
                )
                result["screenshot_saved"] = saved.returncode == 0
    except Exception as error:
        result["error"] = str(error)
    finally:
        stop(process)
        if keyboard:
            keyboard.stop()
        if temporary:
            temporary.cleanup()
        result["owned_terminal_stopped"] = process is None or process.poll() is not None
    (output / "terminal-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def parse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".build/wayland-nested-proof"))
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--trace-events", action="store_true")
    parser.add_argument("--terminal", action="store_true",
                        help="Also verify Chinese input through an owned foot terminal stdin")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main():
    args = parse()
    for program in ("Hyprland", "hyprctl", "dbus-run-session"):
        if shutil.which(program) is None:
            raise RuntimeError(f"required program is not installed: {program}")
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("nested Hyprland requires an existing Wayland display")
    if not args.inside:
        command = [
            "dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()),
            "--inside", "--binary", str(args.binary.resolve()),
            "--output", str(args.output.resolve()),
        ]
        if args.screenshot:
            command.append("--screenshot")
        if args.trace_events:
            command.append("--trace-events")
        if args.terminal:
            command.append("--terminal")
        environment = dict(os.environ)
        environment.pop("DBUS_SESSION_BUS_ADDRESS", None)
        return subprocess.run(command, env=environment).returncode

    os.umask(0o077)
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("private session D-Bus was not established")
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    host_socket = os.environ["WAYLAND_DISPLAY"]
    socket_name = None
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    socket_path = None
    root = Path(__file__).resolve().parents[1]
    report = {
        "schema": "ztype.nested-wayland-acceptance.v1",
        "binary_sha256_before": digest(binary),
        "binary_sha256_after": None,
        "isolation": {
            "private_wayland_socket": True,
            "separate_hyprland_instance": True,
            "private_session_bus": True,
            "physical_seat_disabled": True,
            "host_input_method_modified": False,
            "host_keyboard_injection": False,
        },
        "passed": False,
        "checks_passed": 0,
    }
    compositor = None
    ime = None
    instance = None
    returncode = 1
    compositor_log = None
    ime_log = None
    temporary = None
    try:
        temporary = tempfile.TemporaryDirectory(prefix="ztype-nested-", dir="/var/tmp")
        scratch = Path(temporary.name)
        config = output / "nested.lua"
        config.write_text(CONFIG, encoding="utf-8")
        environment = dict(os.environ)
        for name in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_SOCKET", "DISPLAY",
                     "NOTIFY_SOCKET", "MANAGERPID"):
            environment.pop(name, None)
        environment.update(
            HYPRLAND_NO_SD_VARS="1",
            HYPRLAND_NO_SD_NOTIFY="1",
            HYPRLAND_NO_SD_TARGET="1",
            GIO_USE_VFS="local",
            GSETTINGS_BACKEND="memory",
            LIBSEAT_BACKEND="seatd",
            SEATD_SOCK=str(scratch / "unused-seatd.sock"),
            TMPDIR=str(scratch / "tmp"),
            XDG_CONFIG_HOME=str(scratch / "config"),
            XDG_CACHE_HOME=str(scratch / "cache"),
            XDG_STATE_HOME=str(scratch / "state"),
        )
        for directory in ("config", "cache", "state", "tmp"):
            (scratch / directory).mkdir()
        verify = run(["Hyprland", "--verify-config", "--config", config], environment, timeout=15)
        (output / "config-check.log").write_text(
            verify.stdout + verify.stderr, encoding="utf-8"
        )
        if verify.returncode:
            raise RuntimeError(f"nested configuration validation failed ({verify.returncode})")
        compositor_log = (output / "compositor.log").open("w", encoding="utf-8")
        compositor = subprocess.Popen(
            ["Hyprland", "--config", str(config)],
            env=environment, stdout=compositor_log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        print("Started private nested compositor", flush=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if compositor.poll() is not None:
                raise RuntimeError(
                    f"nested Hyprland exited with {compositor.returncode}; see compositor.log"
                )
            instances = run(["hyprctl", "-j", "instances"], environment)
            if instances.returncode == 0:
                try:
                    known = json.loads(instances.stdout)
                except json.JSONDecodeError:
                    known = []
                instance = next(
                    (item for item in known if item.get("pid") == compositor.pid), None
                )
            if instance:
                socket_name = instance.get("wl_socket")
                if not socket_name or socket_name == host_socket:
                    raise RuntimeError("nested instance did not allocate a distinct Wayland socket")
                socket_path = runtime / socket_name
            if instance and socket_path.is_socket():
                nested = dict(environment)
                nested.update(
                    WAYLAND_DISPLAY=socket_name,
                    HYPRLAND_INSTANCE_SIGNATURE=instance["instance"],
                    GDK_BACKEND="wayland",
                    GTK_IM_MODULE="wayland",
                    NO_AT_BRIDGE="1",
                )
                monitors = run(["hyprctl", "-j", "monitors"], nested)
                if monitors.returncode == 0:
                    try:
                        available_monitors = json.loads(monitors.stdout)
                        if available_monitors:
                            report["nested_outputs"] = [
                                monitor.get("name") for monitor in available_monitors
                            ]
                            break
                    except json.JSONDecodeError:
                        pass
            time.sleep(0.1)
        else:
            raise RuntimeError("nested compositor did not expose its private socket and monitor")
        report["nested_pid"] = compositor.pid
        version = run(["hyprctl", "-j", "version"], nested)
        if version.returncode:
            raise RuntimeError("cannot read the nested compositor version")
        report["compositor_version"] = json.loads(version.stdout).get("version")
        ime_log = (output / "ime.log").open("w", encoding="utf-8")
        ime = subprocess.Popen(
            [str(binary), "wayland"], env=nested,
            stdout=ime_log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if ime.poll() is not None:
                raise RuntimeError(f"private ztype service exited with {ime.returncode}; see ime.log")
            if "native Wayland input method ready" in (output / "ime.log").read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("private ztype service did not become ready")
        report["ime_pid"] = ime.pid
        resource_usage = {
            "schema": "ztype.owned-process-observation.v1",
            "binary_sha256": report["binary_sha256_before"],
            "units": {"rss_kib": "KiB", "pss_kib": "KiB",
                      "minor_faults": "cumulative process count"},
            "idle": [], "composing": [], "errors": [],
        }
        for _ in range(3):
            resource_usage["idle"].append(process_sample(ime, "idle"))
            time.sleep(0.05)
        print("Running native GTK checks on the private Wayland socket", flush=True)
        command = [
            sys.executable, str(root / "scripts" / "test_wayland.py"),
            "--binary", str(binary), "--output", str(output), "--allow-host-input",
        ]
        if args.screenshot:
            command.append("--screenshot")
        if args.trace_events:
            command.append("--trace-events")
        finished = threading.Event()
        observer = threading.Thread(
            target=watch_composition,
            args=(ime, output / "receiver.json", resource_usage["composing"],
                  resource_usage["errors"], finished),
            daemon=True,
        )
        observer.start()
        try:
            acceptance = run(command, nested, timeout=120)
        finally:
            finished.set()
            observer.join(timeout=1)
            (output / "resource-usage.json").write_text(
                json.dumps(resource_usage, indent=2) + "\n", encoding="utf-8"
            )
        report["resource_observations"] = {
            "idle_samples": len(resource_usage["idle"]),
            "composing_samples": len(resource_usage["composing"]),
        }
        (output / "runner.stdout").write_text(acceptance.stdout, encoding="utf-8")
        (output / "runner.stderr").write_text(acceptance.stderr, encoding="utf-8")
        print(acceptance.stdout, end="", flush=True)
        if acceptance.stderr:
            print(acceptance.stderr, file=sys.stderr, end="", flush=True)
        report["test_exit_code"] = acceptance.returncode
        result_path = output / "report.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            report["checks_passed"] = sum(
                check.get("passed") is True for check in result.get("checks", [])
            )
            report["native_passed"] = result.get("passed", False)
            if result.get("error"):
                report["error"] = result["error"]
        report["passed"] = acceptance.returncode == 0 and report.get("native_passed", False)
        if args.terminal and report["passed"]:
            report["terminal"] = terminal_acceptance(nested, output, args.screenshot)
            report["passed"] = report["passed"] and report["terminal"]["passed"]
        returncode = 0 if report["passed"] else 1
    except Exception as error:
        report["error"] = str(error)
        print(f"Nested Wayland acceptance failed: {error}", file=sys.stderr, flush=True)
    finally:
        stop(ime)
        stop(compositor)
        if ime_log:
            ime_log.close()
        if compositor_log:
            compositor_log.close()
        if compositor is not None and compositor.poll() is not None:
            if socket_path is not None:
                for own_path in (socket_path, Path(str(socket_path) + ".lock")):
                    own_path.unlink(missing_ok=True)
            if instance:
                instance_path = runtime / "hypr" / instance["instance"]
                log_path = instance_path / "hyprland.log"
                if log_path.is_file():
                    shutil.copyfile(log_path, output / "hyprland.log")
                if instance_path.is_dir():
                    shutil.rmtree(instance_path)
        if temporary:
            temporary.cleanup()
        report["binary_sha256_after"] = digest(binary)
        if report["binary_sha256_after"] != report["binary_sha256_before"]:
            report["passed"] = False
            report["error"] = "the tested binary changed during native acceptance"
            returncode = 1
        report["owned_processes_stopped"] = all(
            process is None or process.poll() is not None for process in (ime, compositor)
        )
        (output / "nested-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Nested report: {output / 'nested-report.json'}", flush=True)
    return returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Nested Wayland acceptance failed: {error}", file=sys.stderr)
        raise SystemExit(1)
