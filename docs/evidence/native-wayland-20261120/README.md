# Evidence scope

These records are from the installed full-feature Ztype 0.2.0 binary.
Source commit: b69be3c6d0b6197a4c5d37a89e211dc3de1f48fd
Framework commit: 4c7ffce564d2dae0875b0b632bef59febc2d1476
Installed binary SHA-256: 49c5d11e2a3e967540c674ee0e218e426aff2b8a1769c97fc7f711b8412a8755

Absolute home/worktree paths are normalized in JSON and text logs; trailing empty lines in text logs are normalized. Values, outcomes and screenshots are unchanged. The GTK child runner's scope names its current Hyprland session; wayland-nested.json establishes that this session is the private nested instance, not the host compositor.

initial-regression.json and rust-tests.log record the complete 33-test suite before the final feature-report/install guard refinement. The controller and composition implementations did not change afterward. installed-regression.json, installer-variants.json, clippy-release.log and the Wayland/terminal records verify the final build.

Screenshots are original window captures. The red Hyprland banner warns that the isolated compositor was launched directly without start-hyprland. This avoids importing a new desktop into the host systemd session. The existing configuration check reports config ok; all input checks and terminal stdin checks passed. The nested compositor also logged buffer and optional icon-protocol warnings; these are not evidence of broad compositor compatibility.

resources.json contains three short observations each for idle and composing. RSS/PSS cover the IME process, not the compositor, GTK receiver or terminal. Minor faults are cumulative process counts. No latency, long-running stress, hardware diversity or comparative performance benchmark is claimed.
