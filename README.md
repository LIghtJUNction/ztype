# ztype

[简体中文](README.zh-CN.md) · [Installation and services](docs/service-installation.md) · [Current acceptance record](docs/ztty-ui-0.4.2.zh-CN.md) · [Historical verification](docs/verification.md)

**A Linux native input method built on zinput.** Version 0.4.2 provides X11/XWayland XIM and Wayland input-method-v2 frontends, a separate candidate window, editable composition, paging, external weighted dictionaries and persistent configuration. It composes and commits text in the focused application; opening the standalone panel is optional.

Version 0.4.2 gives the native candidate window clearer typography, 20 px horizontal spacing and light Material 3 color roles. Real ztty input is verified through both XIM and native Wayland; the latter uses the cursor-update compatibility fix in [ztty PR #23](https://github.com/LIghtJUNction/ztty/pull/23). The [acceptance record](docs/ztty-ui-0.4.2.zh-CN.md) identifies the exact sources, executable hashes and tested scope.

[Native Wayland candidate window with the installed vocabulary](docs/evidence-archive-20261120.md)

## Build and install

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked
python3 scripts/install_dictionary.py --source rime-ice
python3 scripts/install.py --binary target/release/ztype
~/.local/bin/ztype config
~/.local/bin/ztype convert --text shurufa
# 输入法
```

Use the Rust version specified by `Cargo.toml`. Native Linux builds require a C compiler, pkg-config and the relevant X11/XCB, XKB, Wayland, FreeType, fontconfig and graphics development libraries. The portable CLI can be built with `--no-default-features`.

The vocabulary installer verifies pinned public [Rime Ice sources](https://github.com/iDvel/rime-ice/tree/927e1cf1d3eeb9bf4be85e3f684dba4dd485b6f6), retains their GPL-3.0 license and provenance, and writes 573,194 code/text records to the user data directory. This includes 21,014 apostrophe aliases; it is not a count of distinct words. The small bundled table supports basic conversion and tests. Install an external vocabulary for ordinary pinyin use. Downloaded language data and this program retain their separate licenses. The input service does not depend on Rime or Fcitx5 libraries or daemons.

Installation writes the binary, configuration example and services supported by the binary. It does not switch the active input method unless `--activate` is explicitly supplied. After the previous input method releases the native endpoint:

```sh
ztype wayland-doctor
ztype start
ztype status
ztype restart
ztype stop
```

Service readiness is reported after dictionary and native connection initialization. Upgrades with `--activate` restart the services so the old executable cannot remain silently active. See [service installation](docs/service-installation.md) for XDG locations and lifecycle details.

X11/XWayland applications select `XMODIFIERS=@im=ztype` at launch. `ZTYPE_XIM_NAME` configures the service endpoint. Existing applications retain their original environment; a compatible endpoint name can be used after its previous owner exits. Native Wayland applications need text-input support in addition to the compositor protocols. Starting a browser with a Wayland rendering backend alone does not establish input-method support.

## Native input controls

| Key | Default action |
| --- | --- |
| Space | Commit the selected candidate |
| 1–9 | Select an item on the current page; table engines retain numeric codes |
| PageUp / PageDown, pinyin `-` / `=` | Change candidate page |
| Tab / Shift+Tab, Up / Down | Change candidate |
| Left / Right, Home / End | Move within the composition |
| Backspace / Delete | Delete before / after the composition cursor |
| Enter | Commit raw pinyin; configurable to commit the candidate |
| Escape | Cancel composition |
| Ctrl+Space | Toggle Chinese and direct input |

The default page size is five, configurable from one to nine. The candidate set holds up to 128 items. Page labels match number-key selection. Chinese punctuation is configurable. Sensitive Wayland fields pass through original keys, and focus changes invalidate composition.

The candidate surface does not take input focus. Wayland positions it using the text-input rectangle. XIM uses the client's spot location when supplied and a window-edge fallback otherwise. Current Ztty XIM clients use the fallback; terminal character-caret following is not claimed for that path.

## Configuration and dictionaries

`ztype config` prints resolved settings and dictionary paths. The default file is `$XDG_CONFIG_HOME/ztype/config`, or `~/.config/ztype/config`. Invalid settings report the file and line.

```ini
engine=pinyin
page_size=5
initial_mode=chinese
chinese_punctuation=true
enter_behavior=raw
learning=false
# dictionary=/absolute/path/common.tsv
# dictionary=/absolute/path/domain.tsv
# user_dictionary=~/.local/share/ztype/user.tsv
```

Without explicit dictionaries, Ztype discovers `ztype/dictionaries/*.tsv` in system and user XDG data directories. Repeat `dictionary` to merge sources. Relative paths are relative to the configuration file. `--dict PATH` overrides automatic/configured dictionaries; `--config PATH` selects a configuration; `--no-config` disables user settings and automatic discovery for reproducible tests.

TSV records contain `code<TAB>text<TAB>frequency`, with an optional frequency. Larger frequencies rank first. Pinyin lookup merges duplicate entries, handles apostrophe boundaries and can combine known words into an unstored sentence. It uses word-frequency segmentation, not a neural language model, so sentence disambiguation is limited. Limits are 64 MiB and 2,000,000 records per file, with at most 32 source files.

```sh
ztype candidates --text ni
ztype convert --text wozaitushuguanxuexi
# 我在图书馆学习
ztype convert --text "xi'an"
# 西安
ztype convert --engine table --dict phrases.tsv --text hello --candidate 10
```

Learning is disabled by default. When enabled with an explicit user dictionary, it stores selected code/text/count records using a private file, process locking and atomic replacement. It does not import personal data from another input method. Existing user dictionaries can also be loaded without enabling writes. Only the separately invoked vocabulary installer downloads public data.

## Engines and framework

| Component | Responsibility |
| --- | --- |
| `zinput` | Composition buffer, focused context, candidates, commit lifecycle and engine interface |
| `zinput-x11` | XIM transport, key repeat, XKB map changes and native candidate positioning |
| `zinput-wayland` | input-method-v2, virtual-keyboard-v1, preedit/commit, state and popup surface |
| `ztype` | Vocabulary, ranking, user preferences, editing, candidate content and service installation |
| `zgpui` | Native graphics and candidate text rendering |

Native services support pinyin, kana and table engines. Kana is romaji transliteration without kanji conversion. Unicode-scalar conversion, literal UTF-8 and explicitly trusted native plugins are available through the CLI or optional panel. The standalone panel has its own controls and is not required for normal input.

| Environment | Scope |
| --- | --- |
| Linux X11 / XWayland | XIM implemented; real Ztty versions are covered by current acceptance tests |
| Hyprland / input-method-v2 compositors | Protocol path implemented; actual tested applications and settings are listed in the acceptance record |
| KDE Plasma native Wayland | Its input-method-v1 frontend is not implemented |
| GNOME native Wayland | GNOME/IBus integration is not implemented |
| Windows / macOS | Portable components and historical build checks exist; TSF and InputMethodKit IME frontends are not implemented |

Distribution portability depends on the listed build/runtime libraries. Desktop compatibility depends on compositor and client input protocols. These are separate requirements. Protocol references: [KDE Plasma Keyboard](https://github.com/KDE/plasma-keyboard) and [IBus Wayland desktop documentation](https://github.com/ibus/ibus/wiki/WaylandDesktop).

## Verification

```sh
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
python3 scripts/test_install.py
python3 scripts/test_daily_input.py --binary target/release/ztype
```

Native tests verify actual UTF-8 received by applications, candidate pixels, focus transitions and process cleanup. The tests run their own display sessions and do not need to replace the user's active input method. The [current record](docs/ztty-ui-0.4.2.zh-CN.md) documents commands, tested versions, evidence and remaining limitations. Earlier panel, plugin and platform results remain in [historical verification](docs/verification.md); they are not presented as fresh native-IME acceptance.

The [0.4.0 daily-input record](docs/daily-input-2026-11-20.zh-CN.md) remains available for the previous dependency and application baseline.

The [0.2.1 interactive trial record](docs/interactive-trial.zh-CN.md) remains available as historical evidence.
