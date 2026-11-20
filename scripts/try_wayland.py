#!/usr/bin/env python3
"""Open Ztype in a disposable native Wayland session for interactive typing."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import select
import stat
import subprocess
import sys
import tempfile
import threading
import time

CONFIG = """hl.monitor({
    output = "", mode = "preferred", position = "auto", scale = "1",
})
hl.config({
    input = { follow_mouse = 0, kb_layout = "us" },
    general = { gaps_in = 0, gaps_out = 0, border_size = 0 },
    decoration = { rounding = 0, shadow = { enabled = false } },
    animations = { enabled = false },
    xwayland = { enabled = false },
    misc = { disable_hyprland_logo = true, disable_splash_rendering = true,
             disable_watchdog_warning = true },
})
"""


def run(argv, environment, timeout=10):
    return subprocess.run(
        [str(item) for item in argv], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
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



_shutdown_requested = False


class OwnedSession:
    """A private process group with an EOF shutdown request and cleanup receipt."""
    def __init__(self, command, environment, errors):
        control_read, self.control_write = os.pipe()
        self.receipt_read, receipt_write = os.pipe()
        self.cleanup_acknowledged = False
        try:
            self.process = subprocess.Popen(
                [*command, "--owner-fd", str(control_read),
                 "--owner-ack-fd", str(receipt_write)],
                env=environment, stderr=errors,
                pass_fds=(control_read, receipt_write), start_new_session=True,
            )
        except BaseException:
            os.close(self.control_write)
            os.close(self.receipt_read)
            raise
        finally:
            os.close(control_read)
            os.close(receipt_write)

    def signal_group(self, signum):
        # This group was created by this exact Popen call, never by a name lookup.
        try:
            os.killpg(self.process.pid, signum)
        except ProcessLookupError:
            pass

    def close(self):
        global _shutdown_requested
        _shutdown_requested = True
        os.close(self.control_write)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                ready, _, _ = select.select(
                    [self.receipt_read], [], [], max(0.0, min(0.25, deadline - time.monotonic()))
                )
                if ready:
                    receipt = os.read(self.receipt_read, 1)
                    self.cleanup_acknowledged = receipt == b"1"
                    break
            if not self.cleanup_acknowledged:
                self.signal_group(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.signal_group(signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.signal_group(signal.SIGKILL)
                    self.process.wait(timeout=5)
            if not self.cleanup_acknowledged:
                # The group can outlive its D-Bus launcher; remove only this
                # owned group if it did not confirm that its children stopped.
                self.signal_group(signal.SIGKILL)
        finally:
            os.close(self.receipt_read)


def watch_owner(args):
    if args.owner_fd is None and args.owner_ack_fd is None:
        return None
    if args.owner_fd is None or args.owner_ack_fd is None:
        raise RuntimeError("incomplete trial ownership channel")
    for descriptor in (args.owner_fd, args.owner_ack_fd):
        if not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise RuntimeError("trial ownership channel is not a pipe")
        os.set_inheritable(descriptor, False)
    stopping = threading.Event()

    def disconnected():
        try:
            while os.read(args.owner_fd, 1):
                pass
        except OSError:
            pass
        if not stopping.is_set():
            os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=disconnected, daemon=True).start()
    return stopping


def acknowledge_owner(args, stopping, stopped):
    if stopping is None:
        return
    stopping.set()
    try:
        os.write(args.owner_ack_fd, b"1" if stopped else b"0")
    except BrokenPipeError:
        pass
    finally:
        os.close(args.owner_ack_fd)
        os.close(args.owner_fd)


def write_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def trial_environment():
    """Do not inherit protocol tracing or test diagnostics into a user trial."""
    environment = dict(os.environ)
    for name in ("WAYLAND_DEBUG", "ZTYPE_POPUP_STATE_PATH", "ZTYPE_RECEIVER_TRACE"):
        environment.pop(name, None)
    return environment


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--engine", choices=("pinyin", "kana", "table"), default="pinyin")
    parser.add_argument("--dict", type=Path)
    configuration = parser.add_mutually_exclusive_group()
    configuration.add_argument("--config", type=Path)
    configuration.add_argument("--no-config", action="store_true")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--owner-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--owner-ack-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--state", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--session-file", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def input_arguments(engine, dictionary, configuration, no_config):
    """Use the same input preferences at both subprocess boundaries."""
    options = ["--engine", engine]
    if dictionary is not None:
        options += ["--dict", str(dictionary)]
    if no_config:
        options += ["--no-config"]
    elif configuration is not None:
        options += ["--config", str(configuration)]
    return options


def main():
    args = arguments()
    binary = args.binary.expanduser().resolve(strict=True)
    receiver = Path(__file__).resolve().with_name("trial-receiver")
    if not receiver.is_file() or not os.access(receiver, os.X_OK):
        raise RuntimeError("试用窗口尚未安装；请用 scripts/install.py --with-trial 安装。")
    for program in ("Hyprland", "hyprctl", "dbus-run-session"):
        if shutil.which(program) is None:
            raise RuntimeError(f"试用窗口需要已安装的 {program}。")
    if not os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("XDG_RUNTIME_DIR"):
        raise RuntimeError("请在已登录的 Wayland 桌面中打开 Ztype 输入试用。")
    dictionary = args.dict.expanduser().resolve(strict=True) if args.dict else None
    configuration = args.config.expanduser().resolve(strict=True) if args.config else None
    input_options = input_arguments(args.engine, dictionary, configuration, args.no_config)
    if not args.inside:
        command = [
            "dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()),
            "--inside", "--binary", str(binary), *input_options,
        ]
        for option, value in (("--state", args.state), ("--session-file", args.session_file)):
            if value:
                command += [option, str(value.expanduser().resolve())]
        environment = trial_environment()
        environment.pop("DBUS_SESSION_BUS_ADDRESS", None)
        with tempfile.TemporaryFile(mode="w+t", dir="/var/tmp") as errors:
            owned = OwnedSession(command, environment, errors)
            try:
                result = owned.process.wait()
                if result not in (0, 130):
                    errors.seek(0)
                    detail = errors.read()[-2000:].strip()
                    raise RuntimeError(detail or f"试用窗口启动失败（{result}）。")
                return result
            finally:
                owned.close()

    os.umask(0o077)
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("未能建立独立试用会话。")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    state_dir = runtime / "ztype"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    session_file = args.session_file or state_dir / "trial-session.json"
    session_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = session_file.with_suffix(".lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("Ztype 输入试用已打开，请切换到已有窗口。") from error
    owner_stopping = watch_owner(args)
    host_socket = os.environ["WAYLAND_DISPLAY"]
    compositor = ime = app = None
    socket_path = instance = None
    temporary = None
    state = {
        "schema": "ztype.interactive-session.v1",
        "status": "starting", "launcher_pid": os.getpid(),
        "host_input_method_modified": False, "input_text_recorded": bool(args.state),
        "engine": args.engine,
    }
    write_state(session_file, state)
    try:
        temporary = tempfile.TemporaryDirectory(prefix="ztype-try-", dir="/var/tmp")
        scratch = Path(temporary.name)
        config = scratch / "hyprland.lua"
        configuration = CONFIG
        if args.state:
            # Automated acceptance owns a virtual keyboard. Inherited host
            # input must not mix with it, even before the first window maps.
            configuration += (
                '\nhl.device({name="wl_keyboard", enabled=false})\n'
                'hl.device({name="wl_pointer", enabled=false})\n'
            )
        config.write_text(configuration)
        environment = trial_environment()
        for name in (
            "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_SOCKET", "DISPLAY",
            "NOTIFY_SOCKET", "MANAGERPID", "GTK_IM_MODULE", "QT_IM_MODULE",
            "XMODIFIERS", "ZTYPE_RECEIVER_TRACE",
        ):
            environment.pop(name, None)
        environment.update(
            HYPRLAND_NO_SD_VARS="1", HYPRLAND_NO_SD_NOTIFY="1",
            HYPRLAND_NO_SD_TARGET="1", GIO_USE_VFS="local", GSETTINGS_BACKEND="memory",
            LIBSEAT_BACKEND="seatd", SEATD_SOCK=str(scratch / "unused-seatd.sock"),
            TMPDIR=str(scratch / "tmp"), XDG_CONFIG_HOME=str(scratch / "config"),
            XDG_CACHE_HOME=str(scratch / "cache"), XDG_STATE_HOME=str(scratch / "state"),
        )
        for name in ("tmp", "config", "cache", "state"):
            (scratch / name).mkdir()
        verified = run(["Hyprland", "--verify-config", "--config", config], environment)
        if verified.returncode:
            raise RuntimeError("试用桌面配置未通过检查：" + verified.stderr[-500:])
        with (scratch / "compositor.log").open("w") as compositor_log, \
                (scratch / "ime.log").open("w") as ime_log, \
                (scratch / "window.log").open("w") as app_log:
            compositor = subprocess.Popen(
                ["Hyprland", "--config", str(config)], env=environment,
                stdout=compositor_log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if compositor.poll() is not None:
                    raise RuntimeError("独立试用桌面启动失败。")
                known = run(["hyprctl", "-j", "instances"], environment)
                if known.returncode == 0:
                    try:
                        instance = next((item for item in json.loads(known.stdout)
                                         if item.get("pid") == compositor.pid), None)
                    except json.JSONDecodeError:
                        instance = None
                if instance:
                    socket_name = instance.get("wl_socket")
                    if not socket_name or socket_name == host_socket:
                        raise RuntimeError("未取得独立的试用输入连接。")
                    socket_path = runtime / socket_name
                    if socket_path.is_socket():
                        nested = dict(environment)
                        nested.update(
                            WAYLAND_DISPLAY=socket_name,
                            HYPRLAND_INSTANCE_SIGNATURE=instance["instance"],
                            GDK_BACKEND="wayland", GTK_IM_MODULE="wayland", NO_AT_BRIDGE="1",
                            ZTYPE_TRIAL_ENGINE=args.engine,
                        )
                        monitors = run(["hyprctl", "-j", "monitors"], nested)
                        if monitors.returncode == 0 and json.loads(monitors.stdout):
                            break
                time.sleep(0.05)
            else:
                raise RuntimeError("试用桌面启动超时。")

            ime_command = [str(binary), "wayland", *input_options]
            ime = subprocess.Popen(
                ime_command, env=nested, stdout=ime_log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if ime.poll() is not None:
                    raise RuntimeError("Ztype 输入服务启动失败：" + (scratch / "ime.log").read_text()[-500:])
                if "native Wayland input method ready" in (scratch / "ime.log").read_text():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("Ztype 输入服务未能就绪。")
            app_command = [str(receiver)]
            if args.state:
                app_command += ["--state", str(args.state)]
            app = subprocess.Popen(app_command, env=nested, stdout=app_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 10
            window = None
            while time.monotonic() < deadline:
                if app.poll() is not None:
                    raise RuntimeError("试用输入框启动失败：" + (scratch / "window.log").read_text()[-500:])
                clients = run(["hyprctl", "-j", "clients"], nested)
                if clients.returncode == 0:
                    window = next((item for item in json.loads(clients.stdout)
                                   if item.get("pid") == app.pid and item.get("mapped")), None)
                    if window:
                        break
                time.sleep(0.05)
            else:
                raise RuntimeError("试用输入框未能显示。")
            state.update(
                status="ready", compositor_pid=compositor.pid, ime_pid=ime.pid,
                receiver_pid=app.pid, wayland_display=socket_name,
                compositor_instance=instance["instance"], window_address=window["address"],
            )
            write_state(session_file, state)
            print("Ztype 输入试用已打开。试试 nihao，再按空格；关闭窗口即可退出。", flush=True)
            while app.poll() is None and compositor.poll() is None:
                if ime.poll() is not None:
                    raise RuntimeError("Ztype 输入服务已停止，请重新打开试用窗口。")
                time.sleep(0.25)
            if app.poll() not in (None, 0):
                raise RuntimeError(f"试用输入框异常退出（{app.returncode}）。")
            return 0
    except Exception as error:
        state.update(status="error", error=str(error))
        raise
    finally:
        global _shutdown_requested
        _shutdown_requested = True
        if owner_stopping:
            owner_stopping.set()
        stop(app)
        stop(ime)
        stop(compositor)
        if compositor is not None and compositor.poll() is not None and instance:
            if socket_path is not None and socket_path != runtime / host_socket:
                socket_path.unlink(missing_ok=True)
                Path(str(socket_path) + ".lock").unlink(missing_ok=True)
            instance_path = runtime / "hypr" / instance["instance"]
            if instance_path.is_dir():
                shutil.rmtree(instance_path)
        if temporary:
            temporary.cleanup()
        state.update(
            status="error" if state.get("status") == "error" else "closed",
            owned_processes_stopped=all(p is None or p.poll() is not None for p in (app, ime, compositor)),
        )
        write_state(session_file, state)
        lock.close()
        acknowledge_owner(args, owner_stopping, state["owned_processes_stopped"])


def interrupted(_signum, _frame):
    global _shutdown_requested
    if not _shutdown_requested:
        _shutdown_requested = True
        raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        message = str(error)
        print("Ztype: " + message, file=sys.stderr)
        if "--inside" not in sys.argv and shutil.which("notify-send"):
            subprocess.run(["notify-send", "--app-name=Ztype", "Ztype 输入试用", message], check=False)
        raise SystemExit(1)
