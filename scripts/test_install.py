#!/usr/bin/env python3
"""Installer tests with isolated files and a strict subprocess substitute."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("ztype_installer", Path(__file__).with_name("install.py"))
installer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="ztype-installer-test-"))
        self.root = Path(directory)
        self.source = self.root / "source-ztype"
        self.source.write_bytes(b"isolated binary fixture\n")
        self.prefix = self.root / "staging"
        self.user = self.root / "user"
        self.default = self.user / ".local"
        self.features = {
            "version": "17.4.2", "gui": False, "desktop": False,
            "plugins": False, "wayland": False, "x11": False,
            "service_control": True,
        }
        self.commands = []
        self.failed_command = None
        self.stack.enter_context(mock.patch.object(installer.Path, "home", return_value=self.user))
        self.stack.enter_context(mock.patch.object(installer.sys, "platform", "linux"))
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(mock.patch.object(installer.shutil, "which", side_effect=lambda name: "/mock/bin/" + name))
        self.stack.enter_context(mock.patch.object(installer.subprocess, "run", side_effect=self.run_command))

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command == self.failed_command:
            raise subprocess.CalledProcessError(1, command, "", "fixture service failed")
        if command == [str(self.source), "--version"]:
            output = "ztype " + self.features["version"] + "\n"
        elif command == [str(self.source), "features"]:
            output = json.dumps(self.features)
        elif command[0] == "systemctl":
            self.assertEqual(command[1], "--user")
            self.assertIn(command[2], ("daemon-reload", "enable"))
            output = ""
        elif command[0] == "update-desktop-database":
            output = ""
        elif command == [str(self.default / "bin/ztype"), "start"]:
            self.assertEqual((self.default / "bin/ztype").read_bytes(), self.source.read_bytes())
            output = "ztype-wayland.service: active\nZtype is running.\n"
        else:
            self.fail(f"unexpected external command: {command!r}")
        return subprocess.CompletedProcess(command, 0, output, "")

    def install(self, *, prefix=None, activate=False, trial=False):
        return installer.install(argparse.Namespace(
            binary=self.source, prefix=prefix or self.prefix,
            activate=activate, with_trial=trial,
        ))

    def system_commands(self):
        return [command for command in self.commands if command[0] == "systemctl"]

    def test_featureless_build_installs_only_binary_even_when_gui_is_present(self):
        self.features["gui"] = True
        self.features["desktop"] = True
        os.environ["DISPLAY"] = ":80"
        result = self.install()
        self.assertEqual(result["files"], [str(self.prefix / "bin/ztype")])
        self.assertFalse((self.prefix / "share").exists())
        self.assertEqual(self.system_commands(), [])

    def test_native_menu_uses_start_without_gui_or_trial(self):
        self.features["wayland"] = True
        result = self.install()
        desktop = (self.prefix / "share/applications/ztype.desktop").read_text()
        self.assertIn('" start\n', desktop)
        self.assertNotIn(" panel", desktop)
        self.assertNotIn(" try", desktop)
        unit = (self.prefix / "share/systemd/user/ztype-wayland.service").read_text()
        self.assertIn("Type=exec\n", unit)
        self.assertIn("UnsetEnvironment=WAYLAND_DEBUG ZTYPE_POPUP_STATE_PATH ZTYPE_RECEIVER_TRACE\n", unit)
        self.assertIn('" wayland\n', unit)
        self.assertFalse((self.prefix / "share/systemd/user/ztype-x11.service").exists())
        self.assertEqual(result["activation"], "not requested; files installed")
        self.assertEqual(self.system_commands(), [])

    def test_x11_only_build_installs_only_its_real_service(self):
        self.features["x11"] = True
        self.install()
        unit = (self.prefix / "share/systemd/user/ztype-x11.service").read_text()
        self.assertIn("ConditionEnvironment=DISPLAY\n", unit)
        self.assertIn('" x11\n', unit)
        self.assertFalse((self.prefix / "share/systemd/user/ztype-wayland.service").exists())

    def test_unsupported_existing_entry_preserves_binary_and_all_files(self):
        paths = [
            "share/applications/ztype.desktop",
            "share/systemd/user/ztype-wayland.service",
            "share/systemd/user/ztype-x11.service",
            "share/applications/ztype-try.desktop",
            "libexec/ztype/try_wayland.py",
            "libexec/ztype/trial-receiver",
        ]
        for index, entry in enumerate(paths):
            with self.subTest(entry=entry):
                prefix = self.root / f"existing-{index}"
                binary = prefix / "bin/ztype"
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"working installed binary")
                path = prefix / entry
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"existing entry")
                with self.assertRaisesRegex(installer.InstallError, "existing files were left unchanged"):
                    self.install(prefix=prefix)
                self.assertEqual(binary.read_bytes(), b"working installed binary")
                self.assertEqual(path.read_bytes(), b"existing entry")
        self.assertEqual(self.system_commands(), [])

    def test_staging_activation_is_rejected_before_external_commands_or_writes(self):
        self.features["wayland"] = True
        os.environ["WAYLAND_DISPLAY"] = "wayland-test"
        with self.assertRaisesRegex(installer.InstallError, "default ~/.local"):
            self.install(activate=True)
        self.assertEqual(self.commands, [])
        self.assertFalse(self.prefix.exists())

    def test_default_activation_enables_only_matching_frontends_and_uses_installed_start(self):
        self.features.update(wayland=True, x11=True)
        os.environ["WAYLAND_DISPLAY"] = "wayland-test"
        os.environ["DISPLAY"] = ""
        result = self.install(prefix=self.default, activate=True)
        self.assertEqual(self.system_commands(), [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "ztype-wayland.service"],
        ])
        self.assertIn([str(self.default / "bin/ztype"), "start"], self.commands)
        self.assertEqual(result["activated_services"], ["ztype-wayland.service"])
        self.assertEqual(result["activation"], "enabled and active")

    def test_default_activation_covers_wayland_and_xwayland_when_both_exist(self):
        self.features.update(wayland=True, x11=True)
        os.environ.update(WAYLAND_DISPLAY="wayland-test", DISPLAY=":80")
        self.install(prefix=self.default, activate=True)
        self.assertIn(["systemctl", "--user", "enable", "ztype-wayland.service", "ztype-x11.service"], self.commands)

    def test_headless_activation_and_missing_start_capability_fail_before_installation(self):
        self.features["wayland"] = True
        with self.assertRaisesRegex(installer.InstallError, "matching graphical session"):
            self.install(prefix=self.default, activate=True)
        self.assertFalse(self.default.exists())
        self.features.pop("service_control")
        with self.assertRaisesRegex(installer.InstallError, "service_control"):
            self.install()
        self.assertFalse(self.prefix.exists())
        self.assertEqual(self.system_commands(), [])

    def test_service_start_failure_is_not_reported_as_activated(self):
        self.features["wayland"] = True
        os.environ["WAYLAND_DISPLAY"] = "wayland-test"
        self.failed_command = [str(self.default / "bin/ztype"), "start"]
        with self.assertRaisesRegex(installer.InstallError, "files are installed.*fixture service failed"):
            self.install(prefix=self.default, activate=True)
        self.assertTrue((self.default / "bin/ztype").exists())

    def test_invalid_feature_metadata_and_unsupported_trial_do_not_write_files(self):
        self.features["x11"] = "yes"
        with self.assertRaisesRegex(installer.InstallError, "invalid compiled feature"):
            self.install()
        self.features["x11"] = False
        with self.assertRaisesRegex(installer.InstallError, "with-trial requires"):
            self.install(trial=True)
        self.assertFalse(self.prefix.exists())

    def test_identical_reinstallation_preserves_files_and_modes(self):
        self.features["wayland"] = True
        first = self.install()
        identities = {path: (Path(path).stat().st_ino, Path(path).stat().st_mtime_ns) for path in first["files"]}
        self.install()
        self.assertEqual(identities, {path: (Path(path).stat().st_ino, Path(path).stat().st_mtime_ns) for path in first["files"]})
        self.assertEqual((self.prefix / "bin/ztype").stat().st_mode & 0o7777, 0o755)
        self.assertEqual((self.prefix / "share/applications/ztype.desktop").stat().st_mode & 0o7777, 0o644)


def check_cli(binary: Path) -> None:
    """Exercise the actual CLI; the temporary systemctl cannot start processes."""
    features = json.loads(subprocess.run([str(binary), "features"], check=True,
        capture_output=True, text=True, timeout=10).stdout)
    frontends = [name for name in ("wayland", "x11") if features.get(name)]
    if not features.get("service_control") or not frontends:
        raise SystemExit("--binary CLI checks require a native Linux service build")
    units = [f"ztype-{name}.service" for name in frontends]
    with tempfile.TemporaryDirectory(prefix="ztype-service-cli-test-") as temporary:
        directory = Path(temporary)
        mock_control = directory / "systemctl"
        mock_control.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ["ZTYPE_MOCK_LOG"]).open("a") as log:
    log.write(json.dumps(args)+"\\n")
path=Path(os.environ["ZTYPE_MOCK_STATE"])
states=json.loads(path.read_text())
if args[:2]==["--user", "import-environment"]:
    raise SystemExit(0)
if args[:2] in (["--user", "start"], ["--user", "stop"]):
    for unit in args[2:]:
        if unit not in states:
            raise SystemExit("unknown test service")
        states[unit] = os.environ.get("ZTYPE_MOCK_START_STATE", "active") if args[1]=="start" else "inactive"
    path.write_text(json.dumps(states))
elif args[:2]==["--user", "show"] and args[3:]==["--property=LoadState", "--property=ActiveState"]:
    if args[2] not in states:
        raise SystemExit("unknown test service")
    print("LoadState=loaded")
    print("ActiveState="+states[args[2]])
else:
    raise SystemExit("unexpected test service command")
''')
        mock_control.chmod(0o700)
        log = directory / "calls.jsonl"
        state = directory / "state.json"
        state.write_text(json.dumps({unit: "inactive" for unit in units}))
        env = dict(os.environ)
        env.update(PATH=str(directory) + os.pathsep + env.get("PATH", ""),
                   ZTYPE_MOCK_LOG=str(log), ZTYPE_MOCK_STATE=str(state))
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
            env.pop(name, None)
        if "wayland" in frontends:
            env["WAYLAND_DISPLAY"] = "ztype-service-mock-only"
        if "x11" in frontends:
            env["DISPLAY"] = ":65530"

        def run(args, *, session=None, error=None):
            result = subprocess.run([str(binary), *args], env=session or env,
                capture_output=True, text=True, timeout=10)
            if error:
                assert result.returncode != 0, (args, result.stdout)
                assert error in result.stderr, (args, result.stderr)
                assert not result.stdout, (args, result.stdout)
            else:
                assert result.returncode == 0, (args, result.stderr)
            return result

        # The no-argument desktop entry must call the actual start control path.
        first = run([])
        second = run([])
        assert "Ztype is running" in first.stdout and "Ztype is running" in second.stdout
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(call[:2] == ["--user", "start"] for call in calls) == 2, calls
        assert json.loads(state.read_text()) == {unit: "active" for unit in units}

        before = log.read_bytes()
        run(["start", "--engine", "kana"], error="do not accept conversion options")
        assert log.read_bytes() == before

        failed = dict(env, ZTYPE_MOCK_START_STATE="failed")
        run(["start"], session=failed, error="did not become active")

        # Stop/status work from a shell with no display variables and never need
        # to instantiate a graphical frontend or a trial process.
        headless = {name: value for name, value in env.items()
                    if name not in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")}
        state.write_text(json.dumps({unit: "active" for unit in units}))
        before = len(log.read_text().splitlines())
        status = run(["status"], session=headless)
        assert all(unit + ": active" in status.stdout for unit in units)
        calls = [json.loads(line) for line in log.read_text().splitlines()[before:]]
        assert all(call[:2] == ["--user", "show"] for call in calls), calls
        stopped = run(["stop"], session=headless)
        assert all(unit + ": inactive" in stopped.stdout for unit in units)
        assert json.loads(state.read_text()) == {unit: "inactive" for unit in units}
        print(json.dumps({"cli_checks": 5, "status": "passed", "services": units,
                          "real_service_manager": "not invoked"}))


if __name__ == "__main__":
    arguments = argparse.ArgumentParser(add_help=False)
    arguments.add_argument("--binary", type=Path)
    args, remaining = arguments.parse_known_args()
    result = unittest.main(argv=[sys.argv[0], *remaining], exit=False).result
    if not result.wasSuccessful():
        raise SystemExit(1)
    if args.binary:
        check_cli(args.binary.expanduser().resolve(strict=True))
