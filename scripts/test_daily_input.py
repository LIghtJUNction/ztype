#!/usr/bin/env python3
"""Check installed daily input using temporary XDG directories and synthetic words.

No desktop is opened and no real service manager is called. The native-service
CLI check reuses test_install.py's process-isolated systemctl substitute.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class CheckFailed(RuntimeError):
    pass


class Checks:
    def __init__(self, binary: Path, root: Path):
        self.binary = binary
        self.root = root
        self.results: list[dict] = []
        self.env = dict(os.environ)
        for name in list(self.env):
            if name.startswith("ZTYPE_") or name in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "WAYLAND_DEBUG"):
                self.env.pop(name)
        for name, directory in (
            ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
            ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
            ("XDG_DATA_DIRS", "system-data"), ("XDG_RUNTIME_DIR", "runtime"),
        ):
            path = root / directory
            path.mkdir(mode=0o700)
            self.env[name] = str(path)

    def run(self, *args: str, fails: bool = False, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        process = subprocess.run(
            [str(self.binary), *args], env=self.env,
            capture_output=True, text=True, timeout=timeout,
        )
        if (process.returncode != 0) != fails:
            raise CheckFailed(f"{list(args)!r}: exit {process.returncode}; stdout={process.stdout!r}; stderr={process.stderr!r}")
        return process

    def require(self, condition: bool, detail: str) -> None:
        if not condition:
            raise CheckFailed(detail)

    def record(self, name: str, **details: object) -> None:
        self.results.append({"check": name, "status": "passed", **details})

    def resolved(self, *args: str) -> dict[str, list[str]]:
        fields: dict[str, list[str]] = {}
        for line in self.run("config", *args).stdout.splitlines():
            key, separator, value = line.partition("=")
            self.require(bool(separator) and bool(key), f"invalid resolved config line: {line!r}")
            fields.setdefault(key, []).append(value)
        return fields

    def convert(self, text: str, *args: str) -> str:
        return self.run("convert", "--text", text, *args).stdout.rstrip("\n")

    def snapshot(self) -> dict[str, tuple]:
        # Inspect only this test's temporary files, never the user's dictionaries.
        result = {}
        for item in self.root.rglob("*"):
            stat = item.lstat()
            result[str(item.relative_to(self.root))] = (stat.st_mode, stat.st_size, stat.st_mtime_ns)
        return result


def dictionary_code(index: int) -> str:
    letters = []
    for _ in range(6):
        index, digit = divmod(index, 26)
        letters.append(chr(ord("a") + digit))
    return "zidianceshi" + "".join(reversed(letters))


def exercise(binary: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="ztype-daily-input-") as temporary:
        root = Path(temporary)
        checks = Checks(binary, root)
        config_dir = root / "config/ztype"
        config_dir.mkdir()
        default_config = config_dir / "config"
        default_config.write_text("engine=literal\nlearning=false\n", encoding="utf-8")
        explicit_dir = root / "explicit"
        explicit_dir.mkdir()
        first, second = explicit_dir / "first.tsv", explicit_dir / "second.tsv"
        texts = ["候选" + number for number in ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五", "十六")]
        rows = [f"ceshihouxuan\t{text}\t{2_000_000 - index}\n" for index, text in enumerate(texts)]
        first.write_text("".join(rows[:8]) + "qianbanci\t前半词条\t2000000\n", encoding="utf-8")
        second.write_text("".join(rows[8:]) + "houbanci\t后半词条\t2000000\n", encoding="utf-8")
        explicit_config = explicit_dir / "input.conf"
        explicit_config.write_text(
            f"engine=pinyin\ndictionary={first}\ndictionary={second}\nlearning=false\npage_size=5\n",
            encoding="utf-8",
        )
        automatic_dir = root / "data/ztype/dictionaries"
        automatic_dir.mkdir(parents=True)
        automatic = automatic_dir / "local.tsv"
        automatic.write_text("zidongci\t自动发现词条\t2000000\nautonly\t仅自动词库包含\t2000000\n", encoding="utf-8")
        override = explicit_dir / "override.tsv"
        override.write_text("zidongci\t显式词库词条\t2000000\n", encoding="utf-8")
        bad_config = explicit_dir / "bad.conf"
        bad_config.write_text("engine=pinyin\npage_size=not-a-number\n", encoding="utf-8")
        large = explicit_dir / "large.tsv"
        count = 50_000
        with large.open("w", encoding="utf-8") as stream:
            for index in range(count):
                stream.write(f"{dictionary_code(index)}\t词库条目{index:05d}\t2000000\n")

        version = checks.run("--version").stdout.strip()
        features = json.loads(checks.run("features").stdout)
        checks.require(version == "ztype " + features.get("version", ""), "version and features disagree")
        checks.require(all(isinstance(features.get(name), bool) for name in ("gui", "desktop", "plugins", "wayland", "x11", "service_control")), "features contains missing or nonboolean capabilities")
        checks.record("version_and_compiled_features", version=version, features=features)

        before = checks.snapshot()
        resolved = checks.resolved()
        checks.require(resolved.get("config") == [str(default_config)], f"wrong automatic config source: {resolved!r}")
        checks.require(checks.convert("nihao") == "nihao", "default XDG configuration was not loaded")
        disabled = checks.resolved("--no-config")
        checks.require(disabled.get("config") == ["disabled"], f"--no-config did not disable discovery: {disabled!r}")
        checks.require(disabled.get("dictionary_files") == ["0"], "--no-config loaded automatic dictionaries")
        checks.require(checks.convert("nihao", "--no-config") == "你好", "--no-config retained the configured literal engine")
        checks.record("xdg_configuration_and_no_config")

        selected = checks.resolved("--config", str(explicit_config))
        checks.require(selected.get("config") == [str(explicit_config)], f"explicit config source not reported: {selected!r}")
        checks.require(selected.get("dictionary_files") == ["2"], f"two explicit dictionaries were not merged: {selected!r}")
        checks.require(selected.get("learning") == ["false"] and selected.get("page_size") == ["5"], "configured options were not resolved")
        checks.require(checks.convert("qianbanci", "--config", str(explicit_config)) == "前半词条", "first dictionary is unavailable")
        checks.require(checks.convert("houbanci", "--config", str(explicit_config)) == "后半词条", "second dictionary is unavailable")
        checks.require(checks.convert("ceshihouxuan", "--config", str(explicit_config), "--candidate", "10") == "候选十", "candidate ten is unreachable or incorrectly ranked")
        checks.record("explicit_configuration_and_candidate_ten", dictionaries=2, candidates=16)

        conflict = checks.run("config", "--config", str(explicit_config), "--no-config", fails=True)
        checks.require("config" in conflict.stderr.lower(), "conflicting config flags have no useful diagnostic")
        invalid = checks.run("config", "--config", str(bad_config), fails=True)
        checks.require(str(bad_config) in invalid.stderr, "invalid configuration diagnostic omits its path")
        checks.require(any(token in invalid.stderr.lower() for token in (":2", "line 2", "line=2")), "invalid configuration diagnostic omits line 2")
        checks.record("configuration_error_diagnostics")

        started = time.monotonic()
        checks.require(checks.convert(dictionary_code(count - 1), "--no-config", "--dict", str(large)) == f"词库条目{count - 1:05d}", "large dictionary failed to load or resolve its final entry")
        checks.record("large_external_dictionary", entries=count, elapsed_ms=round((time.monotonic() - started) * 1000, 2))
        checks.require(checks.snapshot() == before, "learning=false commands wrote persistent data in temporary XDG directories")
        checks.record("learning_disabled_has_no_persistent_writes")

        # Change only the fixture to exercise discovery independently of literal mode.
        default_config.write_text("engine=pinyin\nlearning=false\n", encoding="utf-8")
        before = checks.snapshot()
        discovered = checks.resolved()
        checks.require(discovered.get("dictionary_files") == ["1"], f"automatic TSV discovery selected the wrong files: {discovered!r}")
        checks.require(checks.convert("zidongci") == "自动发现词条", "XDG data dictionary was not used")
        overridden = checks.resolved("--dict", str(override))
        checks.require(overridden.get("dictionary_files") == ["1"], "--dict failed to replace discovered dictionaries")
        checks.require(checks.convert("zidongci", "--dict", str(override)) == "显式词库词条", "explicit dictionary did not override discovery")
        checks.require("仅自动词库包含" not in checks.run("candidates", "--text", "autonly", "--dict", str(override)).stdout, "automatic dictionary leaked into explicit --dict selection")
        checks.require(checks.snapshot() == before, "dictionary discovery or override persisted files with learning disabled")
        checks.record("automatic_dictionary_and_explicit_override")

        checks.require(features.get("service_control") and (features.get("wayland") or features.get("x11")), "daily installed-binary acceptance requires a native service build")
        script = Path(__file__).with_name("test_install.py")
        service = subprocess.run(
            [sys.executable, "-c", "import runpy,sys; from pathlib import Path; runpy.run_path(sys.argv[1], run_name='daily_input_service_fixture')['check_cli'](Path(sys.argv[2]))", str(script), str(binary)],
            env=checks.env, capture_output=True, text=True, timeout=60,
        )
        checks.require(service.returncode == 0, f"isolated native-service CLI check failed: {service.stdout}\n{service.stderr}")
        service_result = json.loads(service.stdout)
        checks.require(service_result.get("status") == "passed" and service_result.get("cli_checks", 0) >= 6, "restart CLI acceptance did not complete")
        checks.record("installed_service_restart_with_mock_systemctl", evidence=service_result)
        return {"status": "passed", "checks": checks.results, "desktop_opened": False, "real_service_manager": "not invoked", "temporary_xdg": "removed on completion"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = exercise(args.binary.expanduser().resolve(strict=True))
    except (CheckFailed, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
