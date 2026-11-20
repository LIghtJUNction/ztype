#!/usr/bin/env python3
"""Install Ztype and the input services supported by the supplied binary."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


class InstallError(RuntimeError):
    pass


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.is_file() and not path.is_symlink()
            and path.stat().st_size == len(data)
            and path.stat().st_mode & 0o7777 == mode
            and path.read_bytes() == data):
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def quote_exec(value: str, systemd: bool = False) -> str:
    if any(character in value for character in "\n\r\0"):
        raise ValueError("installation path contains a control character")
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("%", "%%")
    if systemd:
        value = value.replace("$", "$$")
    else:
        value = value.replace("`", "\\`").replace("$", "\\$")
    return '"' + value + '"'


def checked(command: list[str], purpose: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except OSError as error:
        raise InstallError(f"cannot {purpose}: {error}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or f"exit status {error.returncode}").strip()
        raise InstallError(f"cannot {purpose}: {detail}") from error


def binary_features(source: Path) -> tuple[str, dict]:
    version = checked([str(source), "--version"], "read binary version").stdout.strip()
    if not version.startswith("ztype "):
        raise InstallError("the binary does not identify itself as ztype")
    try:
        features = json.loads(checked([str(source), "features"], "read compiled features").stdout)
    except json.JSONDecodeError as error:
        raise InstallError("the supplied binary must support 'ztype features' JSON") from error
    required = ("gui", "desktop", "plugins", "wayland")
    optional = ("x11", "service_control")
    if (not isinstance(features, dict)
            or features.get("version") != version.removeprefix("ztype ")
            or any(not isinstance(features.get(key), bool) for key in required)
            or any(key in features and not isinstance(features[key], bool) for key in optional)):
        raise InstallError("the supplied binary returned invalid compiled feature information")
    features.setdefault("x11", False)
    features.setdefault("service_control", False)
    return version, features


def service_file(binary: Path, frontend: str) -> bytes:
    display = "WAYLAND_DISPLAY" if frontend == "wayland" else "DISPLAY"
    label = "Wayland" if frontend == "wayland" else "X11/XWayland"
    return (
        "[Unit]\n"
        f"Description=Ztype native {label} input method\n"
        "PartOf=graphical-session.target\n"
        "After=graphical-session-pre.target\n"
        f"ConditionEnvironment={display}\n"
        "StartLimitIntervalSec=30\n"
        "StartLimitBurst=3\n\n"
        "[Service]\n"
        "Type=exec\n"
        "UnsetEnvironment=WAYLAND_DEBUG ZTYPE_POPUP_STATE_PATH ZTYPE_RECEIVER_TRACE\n"
        f"ExecStart={quote_exec(str(binary), systemd=True)} {frontend}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "TimeoutStopSec=5\n"
        "UMask=0077\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    ).encode()


def add_trial(files: dict[Path, tuple[bytes, int]], prefix: Path, binary: Path) -> None:
    for program in ("Hyprland", "hyprctl", "dbus-run-session", "cc", "pkg-config"):
        if not shutil.which(program):
            raise InstallError(f"--with-trial requires {program}")
    source_root = Path(__file__).resolve().parent
    flags = shlex.split(checked(
        ["pkg-config", "--cflags", "--libs", "gtk+-3.0"],
        "find GTK3 development files for the optional trial",
    ).stdout)
    with tempfile.TemporaryDirectory(prefix="ztype-trial-build-") as directory:
        compiled = Path(directory) / "trial-receiver"
        checked([
            "cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
            str(source_root / "trial_receiver.c"), "-o", str(compiled), *flags,
        ], "build the optional trial receiver")
        files[prefix / "libexec/ztype/trial-receiver"] = (compiled.read_bytes(), 0o755)
    files[prefix / "libexec/ztype/try_wayland.py"] = (
        (source_root / "try_wayland.py").read_bytes(), 0o755,
    )
    files[prefix / "share/applications/ztype-try.desktop"] = ((
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Ztype Input Trial\n"
        "Name[zh_CN]=Ztype 输入试用\n"
        "Name[zh_TW]=Ztype 輸入試用\n"
        "Comment=Try native input in a separate typing window\n"
        "Icon=input-keyboard\n"
        f"Exec={quote_exec(str(binary))} try\n"
        "Terminal=false\n"
        "Categories=Utility;Accessibility;\n"
        "Keywords=input;keyboard;pinyin;ztype;\n"
    ).encode(), 0o644)


def install(args: argparse.Namespace) -> dict:
    prefix = args.prefix.expanduser().resolve()
    default_prefix = (Path.home() / ".local").resolve()
    if args.activate and prefix != default_prefix:
        raise InstallError("--activate is allowed only for the default ~/.local prefix; staging prefixes never control the user service manager")
    source = args.binary.expanduser().resolve(strict=True)
    version, features = binary_features(source)
    native = [frontend for frontend in ("wayland", "x11") if features[frontend]]
    if native and (not sys.platform.startswith("linux") or not features["service_control"]):
        raise InstallError("native service installation requires a Linux binary with service_control support; rebuild the binary before installing")
    if args.with_trial and (not features["wayland"] or not sys.platform.startswith("linux")):
        raise InstallError("--with-trial requires a Linux build with the wayland feature")

    active_units = [
        f"ztype-{frontend}.service" for frontend in native
        if os.environ.get("WAYLAND_DISPLAY" if frontend == "wayland" else "DISPLAY")
    ]
    if args.activate:
        if not active_units:
            raise InstallError("--activate requires a compiled native frontend and a matching graphical session")
        if not shutil.which("systemctl"):
            raise InstallError("--activate requires systemctl for the current user session")

    binary = prefix / "bin/ztype"
    desktop = prefix / "share/applications/ztype.desktop"
    wayland_service = prefix / "share/systemd/user/ztype-wayland.service"
    x11_service = prefix / "share/systemd/user/ztype-x11.service"
    trial_desktop = prefix / "share/applications/ztype-try.desktop"
    trial_launcher = prefix / "libexec/ztype/try_wayland.py"
    trial_receiver = prefix / "libexec/ztype/trial-receiver"
    supported = (
        (bool(native), desktop),
        (features["wayland"], wayland_service),
        (features["x11"], x11_service),
        (features["wayland"], trial_desktop),
        (features["wayland"], trial_launcher),
        (features["wayland"], trial_receiver),
    )
    unsupported = [path for capable, path in supported
                   if not capable and (path.exists() or path.is_symlink())]
    if unsupported:
        names = ", ".join(str(path) for path in unsupported)
        raise InstallError(
            f"this build cannot support existing entries: {names}; "
            "choose a different --prefix; existing files were left unchanged"
        )

    files = {binary: (source.read_bytes(), 0o755)}
    if native:
        files[desktop] = ((
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Ztype\n"
            "Name[zh_CN]=Ztype 输入法\n"
            "Comment=Start native multilingual keyboard input\n"
            "Icon=input-keyboard\n"
            f"Exec={quote_exec(str(binary))} start\n"
            "Terminal=false\n"
            "Categories=Utility;Accessibility;\n"
            "Keywords=input;keyboard;pinyin;kana;\n"
        ).encode(), 0o644)
    for frontend in native:
        files[prefix / f"share/systemd/user/ztype-{frontend}.service"] = (
            service_file(binary, frontend), 0o644,
        )
    if args.with_trial:
        add_trial(files, prefix, binary)
    for path, (contents, mode) in files.items():
        atomic_write(path, contents, mode)

    result = {
        "version": version,
        "features": features,
        "files": [str(path) for path in files],
    }
    if native:
        result.update({
            "services": [f"ztype-{frontend}.service" for frontend in native],
            "activation": "not requested; files installed",
            "start": str(binary) + " start",
            "stop": str(binary) + " stop",
            "status": str(binary) + " status",
        })
    if prefix == default_prefix:
        if native and shutil.which("systemctl"):
            checked(["systemctl", "--user", "daemon-reload"], "reload installed user service files")
        if (native or args.with_trial) and shutil.which("update-desktop-database"):
            checked(["update-desktop-database", str(desktop.parent)], "refresh desktop application entries")
        if args.activate:
            checked(["systemctl", "--user", "enable", *active_units], "enable the matching input services")
            # The installed command imports only the current display connection,
            # starts its supported units, and checks their actual ActiveState.
            started = checked([str(binary), "start"], "activate the installed input services (files are installed)")
            result.update({
                "activation": "enabled and active",
                "activated_services": active_units,
                "service_status": started.stdout.strip(),
            })
    if args.with_trial:
        result["try"] = str(binary) + " try"
        result["trial_entry"] = str(trial_desktop)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("target/release/ztype"))
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--activate", action="store_true",
                        help="Enable and start matching input services in the current desktop session (default ~/.local only)")
    parser.add_argument("--with-trial", action="store_true",
                        help="Also install the optional Wayland typing trial (requires GTK3 development files)")
    args = parser.parse_args(argv)
    try:
        result = install(args)
    except (InstallError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
