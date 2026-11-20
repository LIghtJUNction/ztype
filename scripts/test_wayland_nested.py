#!/usr/bin/env python3
"""Run the native GTK acceptance suite in a disposable nested Hyprland session.

Use --headless with Weston to avoid any host windows. Requires Hyprland 0.57+,
dbus-run-session, wayland-scanner, GTK3 development files and test_wayland.py tools.
Without --headless an existing Wayland parent display is required. The host input
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
import signal
import subprocess
import sys
import threading
import tempfile
import time

import test_wayland as native_tools
import try_wayland as session_tools


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
    misc = { disable_hyprland_logo = true, disable_splash_rendering = true,
             disable_watchdog_warning = true },
})
hl.device({name="wl_keyboard", enabled=false})
hl.device({name="wl_pointer", enabled=false})
"""


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv, environment, timeout=10):
    return subprocess.run(
        [str(arg) for arg in argv], env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=timeout,
    )


def run_owned(argv, environment, timeout):
    """Give an owned suite time to execute its finally block on interruption."""
    with subprocess.Popen(
        [str(arg) for arg in argv], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        except BaseException:
            stop(process)
            raise


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
    parser.add_argument("--trace-events", action="store_true",
                        help="Record synthetic fixture and private input-method protocol events")
    parser.add_argument("--terminal", action="store_true",
                        help="Also verify Chinese input through an owned foot terminal stdin")
    parser.add_argument("--ztty-binary", type=Path,
                        help="Also verify native ztty PTY input on the owned headless compositor")
    parser.add_argument("--chromium", action="store_true",
                        help="Also run the separate native Chromium input suite")
    parser.add_argument("--aquamarine-library", type=Path,
                        help="Private test-only Aquamarine library directory for Hyprland")
    parser.add_argument("--headless", action="store_true",
                        help="Run inside an owned headless Weston parent; no host windows")
    parser.add_argument("--popup", action="store_true",
                        help="Add candidate-popup checks and test-only metadata diagnostics")
    parser.add_argument("--dictionary", type=Path, help="Explicit TSV for the deterministic native suite")
    parser.add_argument("--large-dictionary", type=Path, help="Also load this real lexicon in a separate private IME")
    parser.add_argument("--probe-external-edit", action="store_true",
                        help="Run the explicit GTK client notification diagnostic")
    parser.add_argument("--focus-race-rounds", type=int, default=0,
                        help="Run the immediate sensitive-focus regression for N rounds")
    parser.add_argument("--large-text", default="nihao")
    parser.add_argument("--large-expected", default="你好")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--owner-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--owner-ack-fd", type=int, help=argparse.SUPPRESS)
    return parser.parse_args()


def main():
    global shutting_down
    args = parse()
    args.screenshot = args.screenshot or args.popup or args.chromium
    for program in ("Hyprland", "hyprctl", "dbus-run-session"):
        if shutil.which(program) is None:
            raise RuntimeError(f"required program is not installed: {program}")
    if args.headless and shutil.which("weston") is None:
        raise RuntimeError("--headless requires the standard Weston compositor")
    if not args.headless and not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("use --headless, or provide an existing Wayland parent display")
    if (args.popup or args.chromium or args.ztty_binary) and not args.headless:
        raise RuntimeError("popup, Chromium and ztty verification require --headless to avoid host windows")
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
        if args.headless:
            command.append("--headless")
        if args.popup:
            command.append("--popup")
        if args.chromium:
            command.append("--chromium")
        if args.ztty_binary:
            command.extend(["--ztty-binary", str(args.ztty_binary.resolve(strict=True))])
        if args.probe_external_edit:
            command.append("--probe-external-edit")
        for option, path in (("--dictionary", args.dictionary), ("--large-dictionary", args.large_dictionary)):
            if path:
                command.extend([option, str(path.resolve(strict=True))])
        command.extend(["--large-text", args.large_text, "--large-expected", args.large_expected])
        command.extend(["--focus-race-rounds", str(args.focus_race_rounds)])
        if args.aquamarine_library:
            command.extend(["--aquamarine-library", str(args.aquamarine_library.resolve(strict=True))])
        environment = session_tools.trial_environment()
        environment.pop("DBUS_SESSION_BUS_ADDRESS", None)
        if args.headless:
            for name in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"):
                environment.pop(name, None)
        # This bus serves only the test processes. In particular, omit the
        # standard service directories so browser probes cannot auto-start
        # desktop portal or GVFS services with the parent's environment.
        with tempfile.TemporaryDirectory(prefix="ztype-bus-", dir="/var/tmp") as bus_directory:
            bus_config = Path(bus_directory) / "session.conf"
            bus_config.write_text(
                '<busconfig><type>session</type>'
                f'<listen>unix:tmpdir={bus_directory}</listen>'
                '<auth>EXTERNAL</auth><policy context="default">'
                '<allow send_destination="*"/><allow receive_sender="*"/>'
                '<allow own="*"/></policy></busconfig>', encoding="utf-8",
            )
            command[1:1] = ["--config-file", str(bus_config)]
            owned = session_tools.OwnedSession(command, environment, sys.stderr)
            try:
                return owned.process.wait()
            finally:
                shutting_down = True
                owned.close()

    os.umask(0o077)
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("private session D-Bus was not established")
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    host_socket = os.environ.get("WAYLAND_DISPLAY")
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
            "session_bus_service_activation": False,
            "physical_seat_disabled": True,
            "parent_wayland_input_disabled": ["wl_keyboard", "wl_pointer"],
            "host_input_method_modified": False,
            "host_keyboard_injection": False,
            "headless_parent": args.headless,
        },
        "passed": False,
        "checks_passed": 0,
    }
    compositor = None
    parent_compositor = None
    parent_log = None
    ime = None
    instance = None
    returncode = 1
    compositor_log = None
    ime_log = None
    temporary = None
    stopping = session_tools.watch_owner(args)
    try:
        temporary = tempfile.TemporaryDirectory(prefix="zt-", dir="/var/tmp")
        scratch = Path(temporary.name)
        config = output / "nested.lua"
        config.write_text(CONFIG, encoding="utf-8")
        environment = session_tools.trial_environment()
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
            XDG_DATA_HOME=str(scratch / "data"),
            XDG_DATA_DIRS=str(scratch / "system-data"),
            GTK_THEME="Adwaita",
        )
        for directory in ("config", "cache", "state", "tmp", "data", "system-data"):
            (scratch / directory).mkdir()
        if args.headless:
            # A short runtime also keeps Hyprland IPC paths below sockaddr_un.sun_path.
            runtime = scratch
            runtime.chmod(0o700)
            environment["XDG_RUNTIME_DIR"] = str(runtime)
            environment.pop("WAYLAND_DISPLAY", None)
            parent_name = "ztype-headless-parent"
            parent_log = (output / "parent.stdout.log").open("w", encoding="utf-8")
            parent_argv = [
                "weston", "--backend=headless", "--renderer=gl", "--fake-seat",
                "--no-config", "--shell=kiosk-shell.so", "--socket=" + parent_name,
                "--width=1280", "--height=800", "--idle-time=0",
                "--log=" + str(output / "parent-weston.log"),
            ]
            parent_compositor = subprocess.Popen(
                parent_argv, env=environment, stdout=parent_log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if parent_compositor.poll() is not None:
                    raise RuntimeError("headless Weston exited; see parent-weston.log")
                if (runtime / parent_name).is_socket():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("headless Weston did not expose its private socket")
            environment["WAYLAND_DISPLAY"] = parent_name
            host_socket = parent_name
            report["headless_parent"] = {
                "pid": parent_compositor.pid, "command": parent_argv,
                "version": run(["weston", "--version"], environment).stdout.strip(),
                "runtime_is_temporary_and_private": True,
                "host_display_removed": True,
            }
        hypr_environment = dict(environment)
        if args.aquamarine_library:
            library = args.aquamarine_library.resolve(strict=True)
            if not library.is_dir():
                raise RuntimeError("--aquamarine-library must be a private library directory")
            files = sorted({path.resolve() for path in library.glob("libaquamarine.so*") if path.is_file()})
            if not files:
                raise RuntimeError("private Aquamarine directory contains no shared library")
            hypr_environment["LD_LIBRARY_PATH"] = str(library)
            report["private_aquamarine"] = {
                "directory": str(library),
                "files": [{"path": str(path), "sha256": digest(path)} for path in files],
                "scope": "owned Hyprland process only; system libraries unchanged",
            }
        verify = run(["Hyprland", "--verify-config", "--config", config], hypr_environment, timeout=15)
        (output / "config-check.log").write_text(
            verify.stdout + verify.stderr, encoding="utf-8"
        )
        if verify.returncode:
            raise RuntimeError(f"nested configuration validation failed ({verify.returncode})")
        compositor_log = (output / "compositor.log").open("w", encoding="utf-8")
        compositor = subprocess.Popen(
            ["Hyprland", "--config", str(config)],
            env=hypr_environment, stdout=compositor_log, stderr=subprocess.STDOUT,
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
        if args.headless:
            nested.update(
                ZTYPE_ACCEPTANCE_PRIVATE="1",
                ZTYPE_ACCEPTANCE_COMPOSITOR_PID=str(compositor.pid),
                ZTYPE_ACCEPTANCE_OUTPUT=report["nested_outputs"][0],
                ZTYPE_ACCEPTANCE_PARENT_SOCKET=host_socket,
                ZTYPE_ACCEPTANCE_RUNTIME=str(runtime),
            )
        version = run(["hyprctl", "-j", "version"], nested)
        if version.returncode:
            raise RuntimeError("cannot read the nested compositor version")
        report["compositor_version"] = json.loads(version.stdout).get("version")
        dictionary = args.dictionary.resolve(strict=True) if args.dictionary else output / "acceptance.tsv"
        if not args.dictionary:
            words = ["你", "呢", "泥", "妮", "拟", "逆", "倪", "腻", "匿", "霓", "溺", "昵"]
            rows = [f"ni\t{word}\t{2000000 - index}\n" for index, word in enumerate(words)]
            rows += ["nihao\t你好\t2000000\n", "shijie\t世界\t2000000\n",
                     "tianqi\t天气\t2000000\n", "ta\t他\t2000000\n",
                     "ta\t她\t1999999\n", "ta\t它\t1999998\n"]
            dictionary.write_text("".join(rows), encoding="utf-8")
        report["dictionary"] = {"path": str(dictionary), "bytes": dictionary.stat().st_size, "automatic_discovery": False}
        ime_log = (output / "ime.log").open("w", encoding="utf-8")
        ime_environment = dict(nested)
        if args.trace_events:
            ime_environment["WAYLAND_DEBUG"] = "client"
        if args.popup:
            ime_environment["ZTYPE_POPUP_STATE_PATH"] = str(output / "popup-state.json")
            (output / "popup-state.json").unlink(missing_ok=True)
        ime = subprocess.Popen(
            [str(binary), "wayland", "--no-config", "--dict", str(dictionary)], env=ime_environment,
            stdout=ime_log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
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
            "--private-compositor-pid", str(compositor.pid),
            "--private-output", report["nested_outputs"][0],
            "--dictionary", str(dictionary),
            "--focus-race-rounds", str(args.focus_race_rounds),
        ]
        if args.screenshot:
            command.append("--screenshot")
        if args.trace_events:
            command.append("--trace-events")
        if args.popup:
            command.extend(["--popup-state", str(output / "popup-state.json")])
        if args.probe_external_edit:
            command.append("--probe-external-edit")
        finished = threading.Event()
        observer = threading.Thread(
            target=watch_composition,
            args=(ime, output / "receiver.json", resource_usage["composing"],
                  resource_usage["errors"], finished),
            daemon=True,
        )
        observer.start()
        try:
            acceptance = run_owned(command, nested, timeout=120)
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
        if args.terminal:
            try:
                report["terminal"] = terminal_acceptance(nested, output, args.screenshot)
            except Exception as error:
                report["terminal"] = {"passed": False, "error": str(error)}
            report["passed"] = report["passed"] and report["terminal"]["passed"]
        if args.ztty_binary:
            from test_ztty_wayland import ztty_acceptance
            try:
                report["ztty"] = ztty_acceptance(
                    nested, output / "ztty", args.ztty_binary,
                    output / "popup-state.json" if args.popup else None,
                    args.screenshot,
                )
            except Exception as error:
                report["ztty"] = {"passed": False, "error": str(error),
                                  "owned_processes_stopped": False}
            report["passed"] = report["passed"] and report["ztty"]["passed"]
        if args.chromium:
            # The browser suite owns a fresh IME on this same private seat.
            stop(ime)
            report["gtk_ime_stopped_before_chromium"] = ime.poll() is not None
            browser_output = output / "chromium"
            browser_output.mkdir(exist_ok=True)
            browser = run_owned([
                sys.executable, str(root / "scripts/test_wayland_chromium.py"),
                "--binary", str(binary), "--output", str(browser_output),
                "--dictionary", str(dictionary),
            ], nested, timeout=90)
            (output / "chromium-runner.stdout").write_text(browser.stdout, encoding="utf-8")
            (output / "chromium-runner.stderr").write_text(browser.stderr, encoding="utf-8")
            browser_report = browser_output / "report.json"
            if browser_report.is_file():
                report["chromium"] = json.loads(browser_report.read_text(encoding="utf-8"))
            else:
                report["chromium"] = {"passed": False, "error": "Chromium report was not written"}
            report["passed"] = report["passed"] and browser.returncode == 0 and report["chromium"].get("passed") is True
        if args.large_dictionary:
            stop(ime)
            ime_log.close()
            large_dictionary = args.large_dictionary.resolve(strict=True)
            large_output = output / "large-dictionary"
            large_output.mkdir(exist_ok=True)
            ime_log = (large_output / "ime.log").open("w", encoding="utf-8")
            large_environment = dict(nested)
            large_environment["ZTYPE_POPUP_STATE_PATH"] = str(large_output / "popup-state.json")
            ime = subprocess.Popen(
                [str(binary), "wayland", "--no-config", "--dict", str(large_dictionary)],
                env=large_environment, stdout=ime_log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if ime.poll() is not None:
                    raise RuntimeError("large-dictionary IME exited; see large-dictionary/ime.log")
                if "native Wayland input method ready" in (large_output / "ime.log").read_text():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("large-dictionary IME did not become ready")
            smoke = run_owned([
                sys.executable, str(root / "scripts/test_wayland.py"),
                "--binary", str(binary), "--output", str(large_output), "--allow-host-input",
                "--private-compositor-pid", str(compositor.pid),
                "--private-output", report["nested_outputs"][0],
                "--dictionary", str(large_dictionary), "--smoke-only",
                "--smoke-text", args.large_text, "--smoke-expected", args.large_expected,
                "--screenshot", "--popup-state", str(large_output / "popup-state.json"),
            ], nested, timeout=90)
            (large_output / "runner.stdout").write_text(smoke.stdout)
            (large_output / "runner.stderr").write_text(smoke.stderr)
            path = large_output / "report.json"
            report["large_dictionary"] = json.loads(path.read_text()) if path.exists() else {"passed": False}
            report["large_dictionary"]["lexicon_bytes"] = large_dictionary.stat().st_size
            report["passed"] = report["passed"] and smoke.returncode == 0 and report["large_dictionary"].get("passed") is True
        returncode = 0 if report["passed"] else 1
    except KeyboardInterrupt:
        report["error"] = "owned acceptance session was interrupted"
        returncode = 130
    except Exception as error:
        report["error"] = str(error)
        print(f"Nested Wayland acceptance failed: {error}", file=sys.stderr, flush=True)
    finally:
        shutting_down = True
        if stopping:
            stopping.set()
        stop(ime)
        stop(compositor)
        stop(parent_compositor)
        if parent_log:
            parent_log.close()
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
            process is None or process.poll() is not None for process in (ime, compositor, parent_compositor)
        ) and report.get("ztty", {}).get("owned_processes_stopped", True)
        (output / "nested-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        session_tools.acknowledge_owner(args, stopping, report["owned_processes_stopped"])
        print(f"Nested report: {output / 'nested-report.json'}", flush=True)
    return returncode


if __name__ == "__main__":
    shutting_down = False

    def interrupted(signum, frame):
        global shutting_down
        if not shutting_down:
            shutting_down = True
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print(f"Nested Wayland acceptance failed: {error}", file=sys.stderr)
        raise SystemExit(1)
