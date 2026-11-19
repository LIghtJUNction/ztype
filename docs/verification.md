# Verification record

This record describes the first ztype composition-app release. It distinguishes actual application text delivery, native build checks and capabilities that remain unimplemented.

## Immutable dependency sources

The final local build used a new private Cargo cache and ordinary registry resolution, without path patches. A Cargo metadata gate required the five released Z packages below to come from crates.io; the only permitted local packages were ztype itself and its checked-in UI submodule.

| Package | Version and source |
| --- | --- |
| zinput | [0.1.3](https://crates.io/crates/zinput/0.1.3), source `37617313dd7aaabcca9dd567344a76c4cbe03907` |
| zplugin / zplugin-macros | [0.3.1](https://crates.io/crates/zplugin/0.3.1), source `00d566eedcff7b2932e90b9dfeb44910c783d305` |
| zsys | [0.1.1](https://crates.io/crates/zsys/0.1.1), source `808fcfdb73584ca7ca122e20b76f0209f56ab0f7` |
| zuse-computer | [0.1.1](https://crates.io/crates/zuse-computer/0.1.1), source `3d7cb7e8f2ebb4c831fbea1c24685261b66980f6` |
| zui-native | 0.1.0 from clean `vendor/zui`, commit `5e37fcfb791eec9ac523fc19373e95d0a513112f` of [LIghtJUNction/zui](https://github.com/LIghtJUNction/zui) |

Registry archive checksums are recorded in Cargo.lock and were checked by Cargo. Package source commits were read from the downloaded archives' `.cargo_vcs_info.json`. zui-native is aliased as `zui` in Rust. Its explicit gitlink avoids relying on the unrelated crates.io package named `zui`. No development-machine path or private verification source is required by the published manifest.

## Observed local results

The formal dependency build passed formatting, Clippy for all application targets with `-D warnings`, 10 composition invariant tests and 3 shared engine contract tests. The executable passed 14 CLI cases, including actual native plugin loading, nonfirst candidate selection, multilingual stdin, invalid UTF-8 dictionaries and argument/error boundaries.

The tests cover exact output, single consumption, focus changes, cancellation, failed target delivery without automatic retry, invalid/oversized edits, dictionary order, UTF-8 selection, scalar boundaries, kana conversion and fixed candidate/storage limits. Plugin tests prove that a short output buffer does not modify output bytes or consume pending text, that another event cannot overwrite a pending commit, and that blur clears composition and pending text.

The native UI was rebuilt on the shared canonical UI commit above. A subsequent compact light interface passed Clippy and native build checks and nine isolated native acceptance checks. The screenshot in `docs/panel.png` comes from that actual native window; `docs/panel-before.png` preserves the prior interface. [Interface decisions](interface.md) explain the layout and interaction changes.

## External application acceptance

The native test uses its own Xvfb display and a real GTK 3 entry. It selects an explicit destination and uses the existing zuse focus-and-type path. The compact UI keeps the explicit destination and focus behavior under its ⋯ help control. The native fixture also reads real WM_NORMAL_HINTS and WM_CLASS properties, requiring a 520 × 112 minimum and ztype/Ztype identity.

| Case | Input | Exact text appended to GTK |
| --- | --- | --- |
| ASCII | literal `Z ` | `Z ` |
| Chinese engine | pinyin `nihao` | `你好` |
| Japanese engine | kana `konnichiha` | `こんにちは` |
| Multiscript literal | ` مرحبا नमस्ते 🙂` | ` مرحبا नमस्ते 🙂` |
| Actual native plugin | kana plugin `sekai` | `せかい` |
| Candidate window | `nihao`, then Enter | `你好` |
| Candidate paging | Nine-entry table, next-page click, then Enter | `候选六` |
| Native edit and clipboard | Ctrl+A, type a different valid code, Ctrl+C, then paste in GTK | `好` |
| Invalid or empty draft | Edit a valid Unicode value to `U+D800`, then Enter; clear and Enter again | No text appended |

The final GTK value was exactly `Z 你好こんにちは مرحبا नमस्ते 🙂せかい你好候选六好`, or 85 UTF-8 bytes. The script checks stored text after every insertion and verifies that invalid or empty drafts do not submit an earlier candidate. Panel keyboard tests explicitly focus the isolated window and use ordinary XTest keyboard input. A diagnostic using direct XSendEvent keys did not trigger the post-click panel action; it is retained as failed fixture evidence, not reported as successful input acceptance. The test waits for a viewable window before focus and for a rendered frame before taking the screenshot. It terminates only its own processes and display. Existing input-method settings and display sessions are not changed.

An earlier background XSendEvent experiment on zuse 0.1.0 returned success but delivered neither ASCII nor Chinese in this receiver. That observation is why this application uses the foreground path and treats a native adapter return value as submission rather than an application acknowledgement. It does not establish that every background path fails, or that every foreground application accepts input.

## Platform evidence

The CI workflow builds the native application and runs the real shared-engine conversion tests on Linux, Windows and macOS. External application keyboard acceptance is exercised only on Linux through the isolated GTK test. A Windows or macOS compile/CLI/plugin result must not be interpreted as a successful text insertion into an arbitrary application.

The baseline [CI run 44634327653](https://github.com/LIghtJUNction/ztype/actions/runs/44634327653) passed the native application build, 13 contract/composition tests, Clippy and 14 actual plugin/CLI checks on Linux, Windows and macOS. The current branch's runs are listed at [ztype Actions](https://github.com/LIghtJUNction/ztype/actions). The native input report and image are uploaded from the specifically scoped proof directory, including its hidden parent directory.

System-wide XIM, Wayland input-method, TSF and InputMethodKit frontends are not part of this release. Pure Wayland, individual application families, complex-script rendering and application-specific input restrictions require separate acceptance work.

## Performance scope

One development release-mode measurement of 100,000 starter-table composition edits reported p50 178 ns, p95 249 ns and p99 267 ns, with 22,182,707 ns total measured loop time. This samples the composition/table logic on one development machine. It excludes rendering, plugin calls and operating-system delivery, and does not claim superiority over another input method.

The native delivery path has different costs and constraints. The earlier zuse 0.1.0 X11 implementation used a temporary keyboard-map row and waits for unmapped Unicode scalars. Improvements to native protocols and delivery belong in the shared libraries; the core benchmark is not an end-to-end typing-latency claim.

## Reproduce

```sh
git submodule update --init --recursive
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --bin ztype --example kana_plugin --locked
python scripts/test_cli.py --directory target/debug
python3 scripts/test_native.py --binary target/debug/ztype --plugin target/debug/examples/libkana_plugin.so
cargo run --release --no-default-features --example benchmark --locked
```

The CLI test selects the correct DLL/dylib/so name on each system and never injects desktop input. The native script requires the Linux dependencies listed in CI and creates its own display. Local final-run logs are retained under `.build/final-evidence/`; baseline native evidence is under `.build/final-native-proof/`, and the compact interface's nine-check evidence is under `.build/compact-zui-proof/`. CI uses `.build/native-proof/` for its artifact.

## Claim review

| Claim | Evidence | Limit |
| --- | --- | --- |
| Composition and candidate boundaries are enforced | 10 invariant tests | Framework/application contract |
| Built-ins implement the shared engine interface | 3 contract tests and real kana dynamic library | Trusted native plugins |
| Executable options and Unicode conversion work | 14 CLI cases | No desktop injection in these cases |
| Text reaches another native app | Nine native checks with exact GTK text comparisons | The recorded Linux X11 receiver/path |
| Runtime Rust dependencies are Project Z libraries | Formal dependency graph and locked registry sources | Macro compilation uses proc-macro2, quote, syn and unicode-ident |
| All applications or all input languages are fully supported | No such evidence | Not claimed |

The review asks five questions: is the delivered software usable (CLI, panel, engines and real target tests); is the framework/app/frontend distinction clear (responsibility table); is delivery established by target text (exact GTK contents); is coverage stated precisely (platform limits above); and is platform code duplicated (ztype uses zuse/zui and zinput/zplugin without new OS FFI).
