#!/usr/bin/env python3
"""Install the built ztype application and its optional user service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("target/release/ztype"))
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    args = parser.parse_args()
    source = args.binary.expanduser().resolve(strict=True)
    prefix = args.prefix.expanduser().resolve()
    version = subprocess.run(
        [str(source), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not version.startswith("ztype "):
        raise SystemExit("the binary does not identify itself as ztype")

    binary = prefix / "bin/ztype"
    desktop = prefix / "share/applications/ztype.desktop"
    service = prefix / "share/systemd/user/ztype-wayland.service"
    try:
        features = json.loads(subprocess.run(
            [str(source), "features"], check=True, capture_output=True, text=True
        ).stdout)
    except (json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit("the supplied binary must support 'ztype features' JSON") from error
    if (
        not isinstance(features, dict)
        or features.get("version") != version.removeprefix("ztype ")
        or any(not isinstance(features.get(key), bool)
               for key in ("gui", "desktop", "plugins", "wayland"))
    ):
        raise SystemExit("the supplied binary returned invalid compiled feature information")

    unsupported_entries = [
        path for feature, path in (("gui", desktop), ("wayland", service))
        if not features[feature] and (path.exists() or path.is_symlink())
    ]
    if unsupported_entries:
        names = ", ".join(str(path) for path in unsupported_entries)
        raise SystemExit(
            f"this build cannot support existing entries: {names}; "
            "choose a different --prefix; existing files were left unchanged"
        )

    files = {
        binary: (source.read_bytes(), 0o755),
        desktop: ((
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Ztype\n"
            "Name[zh_CN]=Ztype 输入法\n"
            "Comment=Compose and copy multilingual text\n"
            "Icon=input-keyboard\n"
            f"Exec={quote_exec(str(binary))} panel\n"
            "Terminal=false\n"
            "Categories=Utility;Accessibility;\n"
            "Keywords=input;keyboard;pinyin;kana;\n"
        ).encode(), 0o644),
        service: ((
            "[Unit]\n"
            "Description=Ztype native Wayland input method\n"
            "PartOf=graphical-session.target\n"
            "After=graphical-session-pre.target\n"
            "ConditionEnvironment=WAYLAND_DISPLAY\n"
            "StartLimitIntervalSec=30\n"
            "StartLimitBurst=3\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={quote_exec(str(binary), systemd=True)} wayland\n"
            "Restart=on-failure\n"
            "RestartSec=3\n"
            "TimeoutStopSec=5\n"
            "UMask=0077\n\n"
            "[Install]\n"
            "WantedBy=graphical-session.target\n"
        ).encode(), 0o644),
    }
    if not features["gui"]:
        del files[desktop]
    if not features["wayland"]:
        del files[service]
    for path, (contents, mode) in files.items():
        atomic_write(path, contents, mode)

    if prefix == (Path.home() / ".local").resolve():
        if features["wayland"] and shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        if features["gui"] and shutil.which("update-desktop-database"):
            subprocess.run(
                ["update-desktop-database", str(desktop.parent)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
    result = {
        "version": version,
        "features": features,
        "files": [str(path) for path in files],
    }
    if features["wayland"]:
        result.update({
            "wayland_service": "installed; activation is separate from file installation",
            "start": "systemctl --user start ztype-wayland.service",
            "stop": "systemctl --user stop ztype-wayland.service",
            "autostart": "systemctl --user enable ztype-wayland.service",
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
