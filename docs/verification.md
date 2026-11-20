# Verification record

The current dependency is published `zuse-computer 0.2.2`. Its [consumer record](zuse-0.2.2-consumer.zh-CN.md) contains the current registry provenance, 13 Rust tests, 14 CLI checks, 11 Linux GTK checks and the macOS desktop/plugin compile check. The full macOS GUI check was blocked by unavailable native Objective-C prerequisites.

The sections below preserve the earlier 0.2.1 dependency acceptance and initial cross-platform baseline, including their original artifacts and resource limits. The [0.2.1 delivery report](delivery-2026-11-20.zh-CN.md) explains that shared repair and its application results.

## Immutable sources

Accepted application source: [037de9972e169942ca6258d6aee6ba53711204f6](https://github.com/LIghtJUNction/ztype/commit/037de9972e169942ca6258d6aee6ba53711204f6). Cargo used ordinary registry resolution without private path patches. Metadata identified only ztype and its checked-in UI submodule as local packages. [Registry evidence](evidence/zuse-0.2.1-registry.json) records every dependency source and the actual commands.

| Package | Version and source |
| --- | --- |
| zinput | [0.1.3](https://crates.io/crates/zinput/0.1.3), source `37617313dd7aaabcca9dd567344a76c4cbe03907` |
| zplugin / zplugin-macros | [0.3.1](https://crates.io/crates/zplugin/0.3.1), source `00d566eedcff7b2932e90b9dfeb44910c783d305` |
| zsys | [0.1.1](https://crates.io/crates/zsys/0.1.1), source `808fcfdb73584ca7ca122e20b76f0209f56ab0f7` |
| zuse-computer | [0.2.1](https://crates.io/crates/zuse-computer/0.2.1), source `d930769005d307c42639e8abedc81ab039427e40` |
| zui-native | 0.1.0 from clean `vendor/zui`, commit `5e37fcfb791eec9ac523fc19373e95d0a513112f` of [LIghtJUNction/zui](https://github.com/LIghtJUNction/zui) |

The downloaded zuse archive, registry checksum and Cargo.lock agree on SHA-256 `8da9a00ef8468d196234a99cb4d7f47991d9fb1ef704f67da2ce63d46395ea04`. Its `.cargo_vcs_info.json` identifies the source above. The three relevant Linux files match the independently reviewed shared repair at [8cb97129](https://github.com/LIghtJUNction/zuse/commit/8cb97129feeed2eea82b52aefaaf0743a29122b1); their full hashes are in the registry evidence.

`zui-native` is imported as `zui`. Its explicit gitlink avoids relying on the unrelated crates.io package named `zui`. The published manifest requires no development-machine path or private verification source.

## Formal 0.2.1 application acceptance

The final run used Rust 1.100.0, passed Clippy for all application targets with `-D warnings`, and built the application plus the real kana dynamic library. The executable SHA-256 is `8fdabf0542aef5965213c766baecc466228b6017e82049105c6bdeab2d3ff65e`; the tested native script SHA-256 is `5728fc061c70c2b9ca2dea4baf23318303eb2d26b202bfb1481b4e3d02556dd4`. [Command results](evidence/zuse-0.2.1-registry.json) and [receiver results](evidence/zuse-0.2.1-native.json) preserve the observations.

The fixture creates a high-number Xvfb display, private authorization cookie, session bus, accessibility bus and real GTK 3 entry. It verifies the requested display number, owned server process and authenticated connection. It focuses explicit destinations and reads actual stored text after each operation. Host display sessions and input-method settings remain unchanged.

| Check | Input or action | Expected receiver result |
| --- | --- | --- |
| 1 | Literal `Z ` | Append `Z ` |
| 2 | Pinyin `nihao` | Append `你好` |
| 3 | Kana `konnichiha` | Append `こんにちは` |
| 4 | Literal ` مرحبا नमस्ते 🙂` | Append the exact supplied string |
| 5 | Actual kana plugin, `sekai` | Append `せかい` |
| 6 | Candidate panel, `nihao`, Enter | Append `你好` |
| 7 | Literal panel, Enter held down during commit | Append `ASCII Ztype` |
| 8 | Nine-entry table, next-page click, Enter | Append `候选六` |
| 9 | Ctrl+A, edit, Ctrl+C, then native paste | Append `好` |
| 10 | Invalid `U+D800` edit; empty or cancelled draft | Append nothing; never submit an earlier candidate |
| 11 | Read-only entry, mixed `ASCII prefix🙂` | Return an error and append nothing, including the ASCII prefix |

All 11 checks passed. The final GTK value contained exactly 96 UTF-8 bytes:

```text
Z 你好こんにちは مرحبا नमस्ते 🙂せかい你好ASCII Ztype候选六好
```

Panel tests use ordinary XTest keys, including a Return key held down throughout the ASCII commit. The [observed window properties](evidence/window-properties.txt) show a 520 × 112 minimum and `ztype` / `Ztype` identity. The existing [panel screenshot](panel.png) and [interface decisions](interface.md) describe the approved compact layout, preserved by this dependency update.

## Delivery behavior and shared verification

The shared X11 adapter preflights the whole string. Layout-external characters, or printable text submitted while the keyboard state is unsuitable for synthetic typing, use confirmed AT-SPI insertion. This path requires a uniquely identified, visible, focused editable control in the chosen local window. It preserves keyboard state and the regular clipboard without changing keyboard mappings. The [shared implementation record](https://github.com/LIghtJUNction/zuse/blob/8cb97129feeed2eea82b52aefaaf0743a29122b1/docs/x11-utf8-delivery.md) describes target binding and bounded selection checks.

Errors state the mutation phase. `Unsupported` before insertion means no input was sent. A focus change, application rewrite, filtering or timeout after insertion begins reports that text may have changed. ztype never retries uncertain operations automatically. Concurrent edits remain possible between accessibility calls; the sequence is not a transaction. Ordinary mapped key events retain application-dependent delivery semantics.

The shared repair passed [17 native cases](https://github.com/LIghtJUNction/zuse/blob/8cb97129feeed2eea82b52aefaaf0743a29122b1/docs/evidence/x11-utf8-review-2026-11-20.json), covering normal and paused receivers, selection/caret handling, changed focus or selection, wrong/noneditable targets, held modifiers, ASCII/shortcuts, filtering and uncertain timeout. The publisher then downloaded the formal Linux CLI and passed 17 GTK cases, 12 MCP checks, five PTY groups and 11 isolated USB C ABI groups. Those upstream results are recorded in its [formal release evidence](https://github.com/LIghtJUNction/zuse/blob/889e4c4c17c903159fb4616dd6ee565001919767/docs/evidence/release-0.2.1.json).

The [fixed four-case baseline](evidence/unicode-baseline.json) records incorrect receiver strings from zuse 0.1.1 and 0.2.0 despite successful submission return values. Current acceptance uses stored receiver text as its success criterion.

## Resource limits

The final run used its own systemd cgroup, with kernel limits checked before the workload: one Cargo job, one Rust test thread, nice 19, 768 MiB hard memory, 576 MiB memory high, zero swap and 75% of one CPU. The supervisor reserved 3 GiB of available host memory and would stop only its own unit if the reserve was crossed. [Resource evidence](evidence/zuse-0.2.1-resources.json) records the settings and usage.

Peak cgroup memory was 606,208,000 bytes (578.125 MiB). Minimum host MemAvailable was 5,000,527,872 bytes (about 4.66 GiB). The run exited successfully with zero OOM events or kills. These figures describe this warmed validation workload, not application typing latency.

## Historical baseline and platform scope

The initial baseline passed 10 composition invariant tests, three shared-engine contract tests and 14 CLI cases, including real native plugin loading. They cover composition lifetime, focus changes, cancellation, failed delivery without retry, UTF-8 boundaries, candidate ordering and pending-commit rules. [CI run 44634327653](https://github.com/LIghtJUNction/ztype/actions/runs/44634327653) passed native builds, contracts, Clippy and actual plugin/CLI checks on Linux, Windows and macOS. This dependency update reused that earlier coverage and reran the affected Linux build, Clippy and 11 native consumer checks.

| Platform/path | Recorded evidence | Remaining scope |
| --- | --- | --- |
| Linux X11, explicit target | Current formal registry build and 11 real GTK checks | Other application families need their own acceptance |
| Linux XWayland | Shared X11 adapter can be selected explicitly | No separate XWayland receiver run in this record |
| Windows / macOS | Historical native application, engine and CLI baseline above | This dependency update has no external-application test on these systems |
| Native Wayland and system IME services | Portable composition and plugin boundary exist | XIM, Wayland input-method, TSF and InputMethodKit frontends remain future work |

The application has no Fcitx5 dependency. Unicode storage, language conversion and glyph shaping are separate capabilities. The starter pinyin table and kana engine have bounded language scope, and zui's Linux renderer does not provide complete complex-script shaping. Full application coverage and WowType feature parity remain development goals.

Native validation is manually dispatched. Routine acceptance uses recorded local checks; this update requested no new workflow run. Official builds and release evidence remain separate from everyday iteration.

## Reproduce

```sh
git submodule update --init --recursive
cargo clippy --all-targets --locked -- -D warnings
cargo build --bin ztype --example kana_plugin --locked
python3 scripts/test_native.py --binary target/debug/ztype --plugin target/debug/examples/libkana_plugin.so
```

The native script requires Linux Xvfb, xauth, xdpyinfo, D-Bus tools, at-spi2-core, xdotool and GTK development files. The manual [workflow](../.github/workflows/ci.yml) lists the full build/fixture packages. Reports and images default to `.build/native-proof/`. Archived JSON replaces the absolute checkout prefix with `<checkout>`; observed values and source hashes are preserved.

Broader application checks remain available independently:

```sh
cargo test --all-targets --locked
python scripts/test_cli.py --directory target/debug
cargo test --no-default-features --locked
cargo run --release --no-default-features --example benchmark --locked
```

The CLI test selects DLL/dylib/so names per system and does not inject desktop input. The benchmark measures composition and table lookup; it excludes rendering, plugin loading and operating-system delivery.

## Claim review

| Question | Evidence and conclusion |
| --- | --- |
| Is the change useful? | Exact multilingual output, held-Enter ASCII and whole-string read-only refusal passed in the actual consumer. |
| Are responsibilities clear? | ztype owns composition flow; zuse owns delivery; zinput/zplugin retain the shared engine contract. No OS FFI was added to ztype. |
| Is success measured at the destination? | The 11-case fixture reads stored GTK UTF-8; baseline failures retain both submission results and incorrect receiver text. |
| Is coverage precise? | Current Linux checks and historical three-platform build/CLI checks are distinguished above. |
| Are failure boundaries explicit? | Unsupported before mutation and uncertain after mutation have distinct behavior; neither triggers automatic resend. |
