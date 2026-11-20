"""Native ztty PTY input acceptance on an already-owned private Wayland seat."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid

import test_wayland as native_tools


RECEIVER = '''import json, os, sys, time
from pathlib import Path
result = Path(sys.argv[1])
ready = result.with_suffix(".ready.json")
release = result.with_suffix(".release")
def write(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    temporary.replace(path)
identity = {
    "pid": os.getpid(), "ppid": os.getppid(),
    "stdin_isatty": sys.stdin.isatty(),
    "tty_name": os.ttyname(0) if sys.stdin.isatty() else None,
}
write(ready, identity)
print("ztype native ztty input verification", flush=True)
line = sys.stdin.readline()
write(result, {
    "schema": "ztype.ztty-terminal-stdin.v1", **identity,
    "text": line.rstrip("\\n"), "received_utf8_hex": line.encode("utf-8").hex(),
})
print("Received through terminal stdin: " + line.rstrip("\\n"), flush=True)
# Keep the actual PTY and its window alive until the harness finishes observing.
deadline = time.monotonic() + 120
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
'''


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _run(argv, environment, timeout=5):
    return subprocess.run([str(item) for item in argv], env=environment,
                          capture_output=True, text=True, timeout=timeout)


def _stat(pid):
    fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
            "start_ticks": int(fields[19])}


class _Process:
    """An exact Linux process reference; signals use its pidfd, never a new PID."""
    def __init__(self, pid):
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            raise RuntimeError("invalid owned process PID")
        self.identity = _stat(pid)
        self.pid = pid
        self.fd = os.pidfd_open(pid)
        try:
            if _stat(pid)["start_ticks"] != self.identity["start_ticks"]:
                raise RuntimeError("process changed while binding its identity")
        except BaseException:
            os.close(self.fd)
            raise

    def alive(self):
        return not select.select([self.fd], [], [], 0)[0]

    def wait(self, seconds):
        return bool(select.select([self.fd], [], [], seconds)[0])

    def stop(self):
        if self.alive():
            try:
                signal.pidfd_send_signal(self.fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if not self.wait(2):
                try:
                    signal.pidfd_send_signal(self.fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.wait(2)

    def reap(self):
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass


class _RequestError(RuntimeError):
    pass


class _Broker:
    def __init__(self, endpoint, binary, binary_digest, owned):
        self.address = "\0" + endpoint[1:]
        self.binary = binary
        self.binary_digest = binary_digest
        self.owned = owned
        self.process = None
        self.peer = None

    def request(self, request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(self.address)
            pid, uid, gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != os.getuid():
                raise RuntimeError("private broker peer belongs to a different user")
            if self.process is None:
                process = _Process(pid)
                try:
                    executable = Path("/proc") / str(pid) / "exe"
                    if not os.path.samefile(executable, self.binary) or _digest(executable) != self.binary_digest:
                        raise RuntimeError("private broker is not running the selected ztty executable")
                    if not process.alive():
                        raise RuntimeError("private broker exited during identity verification")
                except BaseException:
                    os.close(process.fd)
                    raise
                self.process = process
                self.owned[pid] = process
                self.peer = {"pid": pid, "uid": uid, "gid": gid,
                             "start_ticks": process.identity["start_ticks"],
                             "binary_sha256": self.binary_digest}
            elif pid != self.process.pid or not self.process.alive():
                raise RuntimeError("the private broker peer changed")
            connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            data = bytearray()
            while b"\n" not in data:
                block = connection.recv(65536)
                if not block:
                    raise RuntimeError("private broker closed an incomplete response")
                data.extend(block)
                if len(data) > 1048576:
                    raise RuntimeError("private broker response exceeded the bounded frame")
            response = json.loads(bytes(data).split(b"\n", 1)[0])
            if response.get("ok") is not True:
                raise _RequestError(f"private broker rejected {request['op']}: {response!r}")
            return response["result"]


def _private_context(environment):
    if environment.get("ZTYPE_ACCEPTANCE_PRIVATE") != "1":
        raise RuntimeError("ztty acceptance requires the owned headless private compositor")
    if environment.get("DISPLAY") or environment.get("WAYLAND_SOCKET"):
        raise RuntimeError("host display handles must be absent from the terminal environment")
    runtime = Path(environment["ZTYPE_ACCEPTANCE_RUNTIME"]).resolve(strict=True)
    if Path(environment["XDG_RUNTIME_DIR"]).resolve(strict=True) != runtime:
        raise RuntimeError("terminal runtime differs from the private compositor runtime")
    if runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
        raise RuntimeError("terminal runtime must be private to the current user")
    display = environment["WAYLAND_DISPLAY"]
    if Path(display).is_absolute() or "/" in display:
        raise RuntimeError("expected the owned compositor's relative Wayland socket")
    if display == environment.get("ZTYPE_ACCEPTANCE_PARENT_SOCKET") or not (runtime / display).is_socket():
        raise RuntimeError("terminal Wayland socket is not the owned nested display")
    pid = int(environment["ZTYPE_ACCEPTANCE_COMPOSITOR_PID"])
    signature = environment["HYPRLAND_INSTANCE_SIGNATURE"]
    instances = _run(["hyprctl", "-j", "instances"], environment)
    if instances.returncode or not any(
        item.get("pid") == pid and item.get("instance") == signature
        and item.get("wl_socket") == display for item in json.loads(instances.stdout)
    ):
        raise RuntimeError("Hyprland instance does not match the owned compositor and socket")
    return _stat(pid)


def _window(environment, broker_pid):
    response = _run(["hyprctl", "-j", "clients"], environment)
    if response.returncode:
        raise RuntimeError("cannot inspect the private compositor clients")
    matches = [item for item in json.loads(response.stdout)
               if item.get("pid") == broker_pid and item.get("mapped") is not False
               and all(size > 0 for size in item.get("size", [0, 0]))]
    if len(matches) > 1:
        raise RuntimeError("private broker owns multiple mapped windows; refusing ambiguous input")
    if not matches:
        return None
    client = matches[0]
    if client.get("xwayland") is not False:
        raise RuntimeError("owned ztty window is not a native Wayland client")
    if not re.fullmatch(r"0x[0-9a-fA-F]+", client.get("address", "")):
        raise RuntimeError("invalid observed Hyprland window address")
    return client


def _focus(environment, compositor, broker, address):
    if _stat(compositor["pid"])["start_ticks"] != compositor["start_ticks"]:
        raise RuntimeError("owned compositor identity changed; no more keys were sent")
    if not broker.process.alive():
        raise RuntimeError("owned ztty broker exited; no more keys were sent")
    response = _run(["hyprctl", "-j", "activewindow"], environment)
    focused = json.loads(response.stdout) if response.returncode == 0 else {}
    if (focused.get("pid") != broker.process.pid or focused.get("address") != address
            or focused.get("xwayland") is not False):
        raise RuntimeError("owned ztty window lost private focus; no more keys were sent")


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _descends_from(pid, ancestor):
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        try:
            pid = _stat(pid)["ppid"]
        except (FileNotFoundError, ProcessLookupError):
            return False
    return False


def _collect_owned(environment, owned, launcher):
    """Bind broker descendants and marked orphans adopted by this subreaper."""
    markers = [f"{key}={environment[key]}".encode() for key in (
        "ZTTY_SOCKET", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"
    )]
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        pid = int(directory.name)
        if pid in owned or pid == os.getpid() or (launcher is not None and pid == launcher.pid):
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            observed = _stat(pid)
            related = any(process.alive() and _descends_from(pid, process.pid)
                          for process in list(owned.values()))
            if not related:
                if not _descends_from(pid, os.getpid()):
                    continue
                values = (directory / "environ").read_bytes().split(b"\0")
                related = all(marker in values for marker in markers)
            if related:
                process = _Process(pid)
                if process.identity["start_ticks"] != observed["start_ticks"]:
                    os.close(process.fd)
                    continue
                owned[pid] = process
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue


def ztty_acceptance(environment, output, binary, popup_state=None, screenshot=False):
    """Verify physical-protocol input -> IME -> native ztty -> actual PTY stdin."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    proof = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    result = {"schema": "ztype.ztty-wayland-acceptance.v1", "passed": False,
              "terminal": "ztty", "backend": "wayland", "proof_directory": str(proof),
              "input_ascii": "nihaoshijie", "checks": [], "cleanup": {}}
    def check(name, passed, **details):
        result["checks"].append({"name": name, "passed": bool(passed), **details})
        if not passed:
            raise RuntimeError(name)

    launcher = None
    keyboard = None
    temporary = None
    broker = None
    owned = {}
    log = None
    previous_subreaper = None
    libc = None
    terminal_environment = None
    compositor = None
    address = None
    release = proof / "terminal-stdin.release"
    binary_path = None
    try:
        compositor = _private_context(environment)
        check("private_compositor_identity", True, **compositor)
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise RuntimeError("exact owned-process cleanup requires Linux pidfd support")
        binary_path = Path(binary).resolve(strict=True)
        result["binary"] = str(binary_path)
        result["binary_sha256_before"] = _digest(binary_path)
        libc = ctypes.CDLL(None, use_errno=True)
        state = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(state), 0, 0, 0) != 0:
            raise RuntimeError("cannot read the acceptance process's subreaper state")
        previous_subreaper = state.value
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError("cannot own and reap the terminal broker's double fork")

        token = uuid.uuid4().hex
        name = "ztype-wayland-" + token
        endpoint = "@ztype-ztty-" + token
        terminal_environment = dict(environment, ZTTY_SOCKET=endpoint, PYTHONUTF8="1")
        # No existing user broker may be reused even if an improbable name collision occurs.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.connect("\0" + endpoint[1:])
            except (ConnectionRefusedError, FileNotFoundError):
                pass
            else:
                raise RuntimeError("the fresh private broker endpoint is already occupied")
        broker = _Broker(endpoint, binary_path, result["binary_sha256_before"], owned)
        result.update(session=name, private_broker_socket=endpoint)
        receiver = proof / "terminal_receiver.py"
        receiver.write_text(RECEIVER, encoding="utf-8")
        receipt = proof / "terminal-stdin.json"
        ready_path = receipt.with_suffix(".ready.json")
        config = proof / "ztty.toml"
        config.write_text("# Private native Wayland acceptance; application defaults.\n", encoding="utf-8")
        argv = [str(binary_path), "--backend", "wayland", "--name", name,
                "--config", str(config), "--geometry", "80x24", "-e",
                sys.executable, "-u", str(receiver), str(receipt)]
        result["command"] = argv
        log = (proof / "launcher.log").open("w", encoding="utf-8")
        launcher = subprocess.Popen(argv, env=terminal_environment, stdout=log,
                                    stderr=subprocess.STDOUT)
        result["launcher_pid"] = launcher.pid
        deadline = time.monotonic() + 15
        client = None
        last_probe = None
        while time.monotonic() < deadline:
            if launcher.poll() not in (None, 0):
                raise RuntimeError(f"ztty launcher exited with {launcher.returncode}; see launcher.log")
            try:
                info = broker.request({"op": "info", "session": name})
                if info.get("broker_pid") != broker.process.pid:
                    raise RuntimeError("broker info PID differs from authenticated socket peer")
                result["broker_info"] = info
                client = _window(environment, broker.process.pid)
                if client:
                    break
            except (ConnectionRefusedError, FileNotFoundError, TimeoutError, _RequestError) as error:
                last_probe = str(error)
            time.sleep(0.05)
        if client is None:
            raise RuntimeError(f"owned native ztty window did not become ready: {last_probe}")
        result["broker_peer"] = broker.peer
        result["window"] = client
        check("authenticated_native_wayland_window", True, broker_pid=broker.process.pid,
              address=client["address"], xwayland=client["xwayland"])
        deadline = time.monotonic() + 6
        ready = None
        while time.monotonic() < deadline:
            ready = _json(ready_path)
            if ready:
                break
            if not broker.process.alive():
                raise RuntimeError("owned broker exited before the PTY receiver became ready")
            time.sleep(0.05)
        if ready is None:
            raise RuntimeError("owned PTY receiver did not become ready")
        receiver_pid = ready.get("pid")
        check("receiver_is_broker_descendant", isinstance(receiver_pid, int)
              and _descends_from(receiver_pid, broker.process.pid), receiver_pid=receiver_pid)
        if receiver_pid not in owned:
            owned[receiver_pid] = _Process(receiver_pid)
        result["receiver_ready"] = ready
        result["receiver_identity"] = owned[receiver_pid].identity
        check("receiver_stdin_is_a_real_tty", ready.get("stdin_isatty") is True,
              tty_name=ready.get("tty_name"))
        _collect_owned(terminal_environment, owned, launcher)
        address = client["address"]
        focused = _run(["hyprctl", "dispatch", f'hl.dsp.focus({{window="address:{address}"}})'], environment)
        if focused.returncode:
            raise RuntimeError("could not focus the observed private ztty window")
        deadline = time.monotonic() + 3
        while True:
            try:
                _focus(environment, compositor, broker, address)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        temporary = tempfile.TemporaryDirectory(prefix="ztty-keyboard-", dir=environment["TMPDIR"])
        executable = native_tools.build_keyboard(Path(temporary.name))
        keyboard = native_tools.Keyboard(executable, proof / "terminal-keymap.xkb", environment)
        result["input_keyboard"] = keyboard.identity
        popup = Path(popup_state) if popup_state is not None else None
        baseline = _json(popup) if popup is not None else None
        baseline_sequence = baseline.get("sequence", -1) if baseline else -1
        _focus(environment, compositor, broker, address)
        keyboard.send("text", "nihaoshijie")
        _focus(environment, compositor, broker, address)
        composing = None
        if popup is not None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                composing = _json(popup)
                if (composing and composing.get("schema") == "ztype.popup-state.v1"
                        and composing.get("sequence", -1) > baseline_sequence
                        and composing.get("requested_visible") is True
                        and composing.get("candidate_count", 0) > 0
                        and composing.get("width", 0) > 0 and composing.get("height", 0) > 0
                        and composing.get("presentation_result") == "presented"):
                    break
                time.sleep(0.05)
            else:
                result["popup_composing"] = composing
                raise RuntimeError("fresh candidate popup presentation was not observed during composition")
            result["popup_composing"] = composing
            check("fresh_candidate_presentation_metadata", True, **composing)
        if screenshot and shutil.which("grim"):
            _focus(environment, compositor, broker, address)
            capture = _run(["grim", "-o", environment["ZTYPE_ACCEPTANCE_OUTPUT"],
                            proof / "candidates-before-commit.png"], environment)
            result["candidate_screenshot_saved"] = capture.returncode == 0
            result["candidate_pixels_require_visual_review"] = True
        _focus(environment, compositor, broker, address)
        keyboard.send("key", "space")
        _focus(environment, compositor, broker, address)
        if popup is not None:
            deadline = time.monotonic() + 5
            hidden = None
            while time.monotonic() < deadline:
                hidden = _json(popup)
                if (hidden and hidden.get("schema") == "ztype.popup-state.v1"
                        and hidden.get("sequence", -1) > composing["sequence"]
                        and hidden.get("requested_visible") is False
                        and hidden.get("presentation_result") == "hidden"):
                    break
                time.sleep(0.05)
            else:
                result["popup_after_commit"] = hidden
                raise RuntimeError("candidate popup did not report a fresh hidden state after commit")
            result["popup_after_commit"] = hidden
            check("candidate_hidden_after_commit_metadata", True, **hidden)
        _focus(environment, compositor, broker, address)
        keyboard.send("key", "Return")
        _focus(environment, compositor, broker, address)
        deadline = time.monotonic() + 6
        actual = None
        while time.monotonic() < deadline:
            actual = _json(receipt)
            if actual:
                break
            if not owned[receiver_pid].alive():
                raise RuntimeError("owned PTY receiver exited before writing its receipt")
            time.sleep(0.05)
        result["stdin_receipt"] = actual
        check("native_chinese_text_reached_owned_pty", bool(actual)
              and actual.get("pid") == receiver_pid and actual.get("stdin_isatty") is True
              and actual.get("text") == "你好世界"
              and actual.get("received_utf8_hex") == "你好世界\n".encode("utf-8").hex())
        if screenshot and shutil.which("grim"):
            _focus(environment, compositor, broker, address)
            current = _window(environment, broker.process.pid)
            if current is None or current["address"] != address:
                raise RuntimeError("owned window changed before its final screenshot")
            x, y = current["at"]
            width, height = current["size"]
            capture = _run(["grim", "-g", f"{x},{y} {width}x{height}",
                            proof / "terminal-ztty.png"], environment)
            result["terminal_screenshot_saved"] = capture.returncode == 0
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
        if screenshot and compositor is not None and address is not None and shutil.which("grim"):
            try:
                _focus(environment, compositor, broker, address)
                capture = _run(["grim", "-o", environment["ZTYPE_ACCEPTANCE_OUTPUT"],
                                proof / "failed-state.png"], environment)
                result["failed_state_screenshot_saved"] = capture.returncode == 0
            except Exception as capture_error:
                result["failed_state_screenshot_error"] = str(capture_error)
    finally:
        cleanup_errors = []
        if keyboard is not None:
            try:
                keyboard.stop()
            except Exception as error:
                cleanup_errors.append(f"keyboard: {error}")
        if terminal_environment is not None:
            try:
                _collect_owned(terminal_environment, owned, launcher)
            except Exception as error:
                cleanup_errors.append(f"owned process discovery: {error}")
        if broker is not None and broker.process is not None and broker.process.alive():
            try:
                result["cleanup"]["close_session"] = broker.request({"op": "close", "session": name})
            except Exception as error:
                result["cleanup"]["close_session_error"] = str(error)
        try:
            release.touch()
        except OSError as error:
            cleanup_errors.append(f"receiver release: {error}")
        if launcher is not None:
            try:
                if launcher.poll() is None:
                    launcher.terminate()
                try:
                    launcher.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=2)
                result["launcher_exit_code"] = launcher.returncode
            except Exception as error:
                cleanup_errors.append(f"launcher: {error}")
        # Closing a session need not stop its shared broker or PTY workers.
        # Only exact processes proven to belong to this private run are stopped.
        result["cleanup"]["broker_termination_required"] = (
            broker is not None and broker.process is not None and broker.process.alive()
        )
        for _ in range(2):
            if terminal_environment is not None:
                try:
                    _collect_owned(terminal_environment, owned, launcher)
                except Exception as error:
                    cleanup_errors.append(f"owned process discovery: {error}")
            for process in list(owned.values()):
                try:
                    process.stop()
                    process.reap()
                except Exception as error:
                    cleanup_errors.append(f"owned PID {process.pid}: {error}")
        result["cleanup"]["processes"] = [
            {**process.identity, "stopped": not process.alive()} for process in owned.values()
        ]
        stopped = all(not process.alive() for process in owned.values())
        for process in owned.values():
            try:
                process.reap()
            except OSError as error:
                cleanup_errors.append(f"owned PID {process.pid} reap: {error}")
            os.close(process.fd)
        if previous_subreaper is not None and libc.prctl(36, previous_subreaper, 0, 0, 0) != 0:
            cleanup_errors.append("could not restore the original subreaper state")
        if log is not None:
            log.close()
        if temporary is not None:
            temporary.cleanup()
        result["owned_processes_stopped"] = stopped and (launcher is None or launcher.poll() is not None)
        result["cleanup"]["errors"] = cleanup_errors
        if cleanup_errors or not result["owned_processes_stopped"]:
            result["passed"] = False
            result.setdefault("error", "owned terminal cleanup did not complete")
        if binary_path is not None:
            try:
                result["binary_sha256_after"] = _digest(binary_path)
                if result["binary_sha256_after"] != result.get("binary_sha256_before"):
                    result["passed"] = False
                    result["error"] = "the selected ztty binary changed during acceptance"
            except OSError as error:
                result["passed"] = False
                result["error"] = f"cannot verify the selected ztty binary after acceptance: {error}"
        (proof / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
