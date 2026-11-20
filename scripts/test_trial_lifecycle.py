#!/usr/bin/env python3
"""Check trial environment and process ownership with real substitute children.

The production launcher and system dbus-run-session run unchanged. Hyprland,
hyprctl, the IME and GTK window are private Python substitutes, so no desktop
window or host input is used. Every temporary file lives below /var/tmp.
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
import tempfile
import time

DIAGNOSTICS = ("WAYLAND_DEBUG", "ZTYPE_POPUP_STATE_PATH", "ZTYPE_RECEIVER_TRACE")

SUBSTITUTE = r'''#!/usr/bin/env python3
import json, os, signal, socket, sys, time
from pathlib import Path
directory = Path(os.environ["ZTYPE_LIFECYCLE_FIXTURE"])
role = Path(sys.argv[0]).name

def write(name, value):
    target = directory / name
    temporary = target.with_suffix(".tmp-" + str(os.getpid()))
    temporary.write_text(json.dumps(value))
    temporary.replace(target)

def environment(name):
    write(name, {key: os.environ.get(key) for key in (
        "WAYLAND_DEBUG", "ZTYPE_POPUP_STATE_PATH", "ZTYPE_RECEIVER_TRACE"
    )})

if role == "notify-send":
    raise SystemExit(0)
if role == "hyprctl":
    info = directory / "compositor.json"
    compositor = json.loads(info.read_text()) if info.exists() else None
    request = sys.argv[-1]
    if request == "instances":
        print(json.dumps([compositor] if compositor else []))
    elif request == "monitors":
        print("[]" if os.environ.get("ZTYPE_LIFECYCLE_DELAY_READY") else '[{"name":"SUBSTITUTE-1"}]')
    elif request == "clients":
        state = directory / "receiver.json"
        receiver = json.loads(state.read_text()) if state.exists() else None
        print(json.dumps([{
            "pid": receiver["pid"], "mapped": True, "address": "0xabc",
            "size": [640, 400],
        }] if receiver else []))
    else:
        raise SystemExit(2)
    raise SystemExit(0)

if role == "Hyprland" and "--verify-config" in sys.argv:
    environment("verify-environment.json")
    raise SystemExit(0)
if role == "Hyprland":
    environment("compositor-environment.json")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    name = "substitute-wayland-" + str(os.getpid())
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(runtime / name))
    listener.listen(1)
    instance = "substitute-" + str(os.getpid())
    (runtime / "hypr" / instance).mkdir(parents=True)
    write("compositor.json", {
        "pid": os.getpid(), "instance": instance, "wl_socket": name,
    })
elif role == "ztype":
    environment("ime-environment.json")
    write("ime.json", {"pid": os.getpid()})
    print("native Wayland input method ready", flush=True)
elif role == "trial-receiver":
    environment("receiver-environment.json")
    write("receiver.json", {"pid": os.getpid()})
else:
    raise SystemExit(2)
while True:
    if role == "trial-receiver" and (directory / "close-window").exists():
        raise SystemExit(0)
    time.sleep(0.025)
'''


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def identity(pid):
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[19]), fields[0])
    except (FileNotFoundError, ProcessLookupError):
        return None


def descendants(pid):
    result = {}
    pending = [pid]
    while pending:
        current = pending.pop()
        observed = identity(current)
        if observed is None or current in result:
            continue
        result[current] = observed[0]
        try:
            children = (Path("/proc") / str(current) / "task" / str(current) / "children").read_text()
            pending.extend(int(child) for child in children.split())
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def live_owned(processes):
    result = []
    for pid, started in processes.items():
        current = identity(pid)
        if current and current[0] == started and current[1] != "Z":
            result.append(pid)
    return result


def wait_for(predicate, message, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError(message)


def cleanup(process, owned):
    if process and process.poll() is None:
        owned.update(descendants(process.pid))
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    # A failing baseline may leave substitutes behind. These identities were
    # observed only in this test's descendant tree, with start-time checks.
    for signum in (signal.SIGTERM, signal.SIGKILL):
        for pid in live_owned(owned):
            try:
                os.kill(pid, signum)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 2
        while live_owned(owned) and time.monotonic() < deadline:
            time.sleep(0.025)


def run_case(launcher, name):
    process = None
    owned = {}
    record = {"name": name, "passed": False}
    with tempfile.TemporaryDirectory(prefix="ztype-trial-lifecycle-", dir="/var/tmp") as temporary:
        root = Path(temporary)
        scripts = root / "prefix/libexec/ztype"
        scripts.mkdir(parents=True)
        fixture = root / "fixture"
        fixture.mkdir()
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        bin_path = root / "bin"
        bin_path.mkdir()
        copied = scripts / "try_wayland.py"
        copied.write_bytes(launcher.read_bytes())
        for program in ("Hyprland", "hyprctl", "notify-send"):
            target = bin_path / program
            target.write_text(SUBSTITUTE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
            target.chmod(0o755)
        binary = root / "prefix/bin/ztype"
        binary.parent.mkdir()
        binary.write_text(SUBSTITUTE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        binary.chmod(0o755)
        receiver = scripts / "trial-receiver"
        receiver.write_text(SUBSTITUTE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        receiver.chmod(0o755)
        session = root / "session.json"
        environment = dict(os.environ)
        environment.update(
            PATH=str(bin_path) + os.pathsep + os.environ["PATH"],
            XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY="parent-placeholder",
            ZTYPE_LIFECYCLE_FIXTURE=str(fixture), TMPDIR=str(root),
            WAYLAND_DEBUG="1", ZTYPE_POPUP_STATE_PATH=str(root / "unexpected-popup.json"),
            ZTYPE_RECEIVER_TRACE="1",
        )
        environment.pop("ZTYPE_LIFECYCLE_DELAY_READY", None)
        if name == "sigterm-during-startup":
            environment["ZTYPE_LIFECYCLE_DELAY_READY"] = "1"
        with (root / "launcher.log").open("w+") as log:
            try:
                process = subprocess.Popen(
                    [sys.executable, str(copied), "--binary", str(binary),
                     "--session-file", str(session)],
                    env=environment, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                if name == "sigterm-during-startup":
                    wait_for(lambda: read_json(fixture / "compositor.json"),
                             "substitute compositor did not start")
                else:
                    wait_for(lambda: (read_json(session) or {}).get("status") == "ready",
                             "trial launcher did not reach ready")
                owned.update(descendants(process.pid))
                record["owned_process_count"] = len(owned)
                if name == "environment-and-window-close":
                    for component in ("verify", "compositor", "ime", "receiver"):
                        observed = read_json(fixture / (component + "-environment.json"))
                        if observed is None or any(observed.get(key) is not None for key in DIAGNOSTICS):
                            raise AssertionError(f"{component} inherited diagnostics: {observed}")
                    record["inherited_diagnostics_removed"] = list(DIAGNOSTICS)
                    (fixture / "close-window").touch()
                else:
                    process.terminate()
                started = time.monotonic()
                process.wait(timeout=12)
                wait_for(lambda: not live_owned(owned), "owned substitute processes remained alive", timeout=5)
                state = read_json(session)
                if not state or state.get("status") != "closed" or not state.get("owned_processes_stopped"):
                    raise AssertionError(f"cleanup receipt missing from session state: {state}")
                compositor = read_json(fixture / "compositor.json")
                if compositor:
                    if (runtime / compositor["wl_socket"]).exists():
                        raise AssertionError("owned Wayland socket was not removed")
                    if (runtime / "hypr" / compositor["instance"]).exists():
                        raise AssertionError("owned compositor instance was not removed")
                record.update(
                    passed=True, exit_code=process.returncode,
                    cleanup_seconds=round(time.monotonic() - started, 3),
                    remaining_live_owned_pids=live_owned(owned),
                    owned_processes_stopped=state["owned_processes_stopped"],
                )
            except Exception as error:
                record["error"] = str(error)
                log.flush()
                log.seek(0)
                record["launcher_log_tail"] = log.read()[-1500:]
            finally:
                cleanup(process, owned)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=Path,
                        default=Path(__file__).with_name("try_wayland.py"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=("all", "environment-and-window-close",
                        "sigterm-when-ready", "sigterm-during-startup"), default="all")
    args = parser.parse_args()
    if shutil.which("dbus-run-session") is None:
        parser.error("dbus-run-session is required")
    launcher = args.launcher.resolve(strict=True)
    names = ("environment-and-window-close", "sigterm-when-ready", "sigterm-during-startup")
    if args.case != "all":
        names = (args.case,)
    checks = []
    for name in names:
        result = run_case(launcher, name)
        checks.append(result)
        print(("PASS " if result["passed"] else "FAIL ") + name, flush=True)
    report = {
        "schema": "ztype.trial-lifecycle-substitutes.v1",
        "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
        "scope": "real private D-Bus and substitute child processes; no native desktop or input",
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
