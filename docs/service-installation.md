# Native service installation

Ztype installs one service for each native frontend compiled into the supplied binary. `ztype-wayland.service` handles Wayland text-input clients; `ztype-x11.service` handles X11 and XWayland clients through XIM.

## Install or upgrade

```sh
cargo build --release --locked
python3 scripts/install_dictionary.py --source rime-ice
python3 scripts/install.py --binary target/release/ztype --activate
ztype status
```

Install a public vocabulary before activating pinyin. The dictionary installer verifies a pinned source revision, keeps the original license and writes to the XDG data directory. `ztype config` shows which files the foreground commands and services will load. An explicit `--dict` can select a different vocabulary.

`--activate` enables the services matching the current display session and restarts them with the installed executable. Restarting during an upgrade replaces any process still running the previous binary. Without `--activate`, the installer writes files and reloads user unit definitions; it leaves activation to `ztype start`.

The service waits for a readiness datagram from its main process after dictionary loading, engine initialization and native registration. Startup fails if readiness does not arrive within 30 seconds.

Use `ztype restart` after changing configuration. `ztype stop` and `ztype status` also work from a terminal without graphical display variables. A failed or still-starting service produces an error with its actual state. `status` reports an older or different running executable and includes the systemd result and exit code when a service fails.

## Paths and existing configuration

| Artifact | User installation | Staging with `--prefix /path` |
| --- | --- | --- |
| Executable | `~/.local/bin/ztype` | `/path/bin/ztype` |
| Desktop entry | `$XDG_DATA_HOME/applications/ztype.desktop` | `/path/share/applications/ztype.desktop` |
| Service units | `$XDG_DATA_HOME/systemd/user/` | `/path/share/systemd/user/` |
| Configuration | `$XDG_CONFIG_HOME/ztype/config` | `/path/etc/ztype/config` |

Unset or relative XDG paths use `~/.local/share` and `~/.config`. The installer creates a commented configuration example with mode `0600` only when the configuration path is absent. Existing files and symlinks are preserved. A staging prefix never controls the running user service manager or writes to the user's XDG directories.

`start` and `restart` import the current display connection and explicitly present XDG configuration/data/state paths and `ZTYPE_CONFIG` into the user service manager. The foreground `wayland` and `x11` commands and the installed services read the same Ztype configuration.

## Application connection

Wayland applications need the compositor's text-input support. X11 and XWayland applications select the XIM endpoint through `XMODIFIERS=@im=ztype`, or the name configured by `ZTYPE_XIM_NAME`. Apply this variable in the desktop session before launching those applications. Already-running applications retain their launch environment.

The service manager controls process lifecycle and prevents duplicate starts of the same unit. A running service's status describes the process; actual application input is checked by the native Wayland and XIM acceptance tests.

## Targeted checks

```sh
python3 scripts/test_install.py --binary target/release/ztype
cargo test --bin ztype service::tests
```

Installer tests use temporary paths and a substituted service manager. They cover upgrades, simultaneous Wayland/XWayland activation, XDG directories, configuration preservation and isolated staging. Service tests cover restart, exit-state diagnostics, stale-executable detection, and stop/status without a display connection.
