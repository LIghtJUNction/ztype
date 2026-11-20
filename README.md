# ztype

[简体中文](README.zh-CN.md) · [Verification](docs/verification.md) · [Delivery report](docs/delivery-2026-11-20.zh-CN.md) · [Interface changes](docs/interface.md)

**A multilingual composition application built on Project Z.** ztype combines a native candidate window, command-line conversion, language engines and explicit delivery into another application. It has no Fcitx5 dependency.

The first release is a practical composition app and desktop adapter. The long-term goal is a complete input method that can replace software such as [WowType](https://github.com/Gloridust/WowType). System-wide XIM, Wayland input-method, Windows TSF and macOS InputMethodKit frontends are not implemented in this release. Coverage is reported by tested application and delivery path, rather than assumed from Unicode support.

![Native ztype candidate window](docs/panel.png)

## Try it

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked

./target/release/ztype convert --text nihao
# 你好
./target/release/ztype convert --engine kana --text konnichiha
# こんにちは
./target/release/ztype convert --engine unicode --text 'U+1F30D U+4F60 U+597D'
# 🌍你好

./target/release/ztype panel
```

Rust 1.100 or newer is required. On Linux the GUI builds against the installed X11, OpenGL, FreeType and fontconfig development libraries; `cc` and `pkg-config` must be available. The project does not install system software or change input-method settings.

`panel` opens a compact light candidate strip with a composition line and a horizontal candidate row. Type, choose a candidate with Tab or a click, and press Enter. Shift+Tab moves back. Up to nine candidates are available. Short choices sit next to one another; overflow uses page arrows. The small ⋯ menu shows the explicit target, focus behavior and keyboard help. Candidate numbers identify their position, not number-key shortcuts. Without a target, Enter copies the result; keep the window open until another application has pasted it. Escape closes help if it is open; otherwise it clears the composition. Ctrl+C copies the selected candidate without clearing it. Ctrl+A clears the input field. Invalid input and an empty composition disable submission.

The input caret shows the current end position: this release supports appending and backspacing, not arbitrary cursor editing. Long input scrolls to its end; long candidate labels are shortened visually, while copying or submitting preserves the full text.

To submit directly to another application, select an explicit destination:

```sh
./target/release/ztype windows
./target/release/ztype panel --window 0x0400001
./target/release/ztype commit --window 0x0400001 --text nihao
./target/release/ztype doctor
```

**Send focuses the chosen target window.** The shared `zuse` adapter handles delivery. On X11, text outside the keyboard layout—or a commit made while a key such as Enter is still held—uses AT-SPI to insert and confirm the complete printable UTF-8 string. This requires a working accessibility service and an identifiable, focused editable control. This confirmed insertion path rejects missing, ambiguous, password or read-only targets; errors after insertion starts state that text may have changed and are never retried automatically. Ordinary mapped key events still depend on application handling. On an XWayland desktop, `ZUSE_BACKEND=x11` chooses the X11/XWayland target path; it does not enable input into native Wayland applications.

For a command-line build without GUI, native desktop or plugin loading:

```sh
cargo build --release --no-default-features
printf '你好 / مرحبا / नमस्ते / 🙂' | ./target/release/ztype convert --engine literal --text -
```

## Language engines

| Engine | What it does | Scope |
| --- | --- | --- |
| `pinyin` | Ranked exact and prefix lookup, with a small original Chinese starter table | No neural sentence prediction, large dictionary or learned user model |
| `kana` | Hepburn-style romaji to hiragana, katakana and literal candidates | Transliteration; no kanji conversion |
| `unicode` | Converts Unicode scalar values such as `U+1F642` and `4F60` | Rejects surrogate, out-of-range and control values |
| `literal` | Commits the supplied UTF-8 string | Enables text in any represented script; it does not create a language model |
| `table` | Uses a user-supplied UTF-8 dictionary | Any language whose entries fit the code-to-text table model |
| native plugin | Loads a typed `zinput::engine::InputEngine` implementation | Explicitly chosen trusted library; up to nine candidates per composition |

User tables contain `code<TAB>text` and an optional nonnegative priority. Larger priorities rank first for the same code. Codes use lowercase ASCII letters, digits, apostrophes, hyphens and underscores. Entries are sorted once; lookup uses a binary search and a bounded candidate page. The application accepts at most 100,000 entries and 16 MiB per table. Text, preedit and commits are bounded to 4,096 UTF-8 bytes, with at most nine candidates per composition.

```text
hello	你好	100
hello	مرحبا	80
hello	नमस्ते	70
```

```sh
ztype candidates --engine table --dict phrases.tsv --text hello
ztype convert --engine table --dict phrases.tsv --text hello --candidate 2
```

The bundled table is original project data. No WowType model, Rime dictionary, voice model or other third-party language asset is copied into this repository. The application performs no network requests and creates no typing-history database.

## Shared libraries and plugins

| Library | Responsibility |
| --- | --- |
| `zinput` | Caller-owned preedit buffer, focused context, revision validity, candidate selection and commit lifecycle; shared engine interface |
| `zplugin` | Generated typed native ABI, validated interface binding and library lifetime |
| `zsys` | Shared system loader used by zplugin and zuse |
| `zuse-computer` (`zuse`) | Native window discovery, explicit focus and text delivery |
| `zui-native` (`zui`) | Native window, events, GPU drawing, text and clipboard |
| `ztype` | Product flow, built-in language policies, user tables and orchestration |

ztype declares no operating-system FFI. It reuses the existing adapters, and exposes its composition engine as a Rust library as well as an application. Native plugins use the same `InputEngine` trait as the built-in adapter:

```sh
cargo build --release --example kana_plugin
ztype convert --plugin target/release/examples/libkana_plugin.so --text sushi
# すし
```

On macOS the extension is `.dylib`; on Windows it is `.dll` without the `lib` prefix. Only load a trusted library: a native plugin executes inside the application process. The generated interface checks do not sandbox native code. The application recomputes a plugin composition after each input edit and invokes its select/commit methods when a candidate is chosen. Short output buffers preserve pending commits; failed target delivery is never automatically retried.

Direct and runtime Rust dependencies are Project Z libraries. The optional typed plugin interface uses `zplugin-macros`, which currently depends on `proc-macro2`, `quote`, `syn` and `unicode-ident` **during compilation**. This distinction is intentional and documented; the complete build is not described as having zero third-party crates.

## Compatibility and verification

The current formal zuse 0.2.1 update passed Linux Clippy, the application/plugin build and all 11 GTK consumer checks. Contract and three-platform CLI/build results below are the separately recorded initial baseline. See the [fixed source and acceptance records](docs/verification.md).

| Path | Implemented in ztype | Validation status |
| --- | --- | --- |
| Conversion, tables and composition lifecycle | Portable Rust library and CLI | 10 invariant tests passed on Linux |
| Shared native engine contract | Built-in adapter and loadable kana example | 3 contract tests and actual native plugin-to-GTK input passed |
| Linux X11 / XWayland | Native panel, explicit focus and shared key/confirmed AT-SPI delivery | 11 isolated X11/GTK checks passed, including held-Enter ASCII, multilingual/plugin input, paging, stale-input protection and read-only rejection; XWayland applications need separate acceptance tests |
| Native Wayland applications | Existing zuse compositor-specific path may be selected | No native Wayland IME frontend or broad Wayland acceptance claim |
| Windows | zuse Win32 delivery, typed engine loading and zui native window source paths | Historical native build and actual plugin/CLI baseline passed in [CI](https://github.com/LIghtJUNction/ztype/actions/runs/44634327653); these platforms were not rerun for the 0.2.1 dependency update, and external-application input is not claimed |
| macOS | zuse Quartz delivery and zui native window source paths | Historical native build and actual plugin/CLI baseline passed in [CI](https://github.com/LIghtJUNction/ztype/actions/runs/44634327653); these platforms were not rerun for the 0.2.1 dependency update, and external-application input is not claimed |

Unicode delivery and font rendering are separate concerns. The receiving application's fonts, shaping and input handling determine how a script appears. zui's current Linux text renderer does not provide complete complex-script shaping. The native acceptance test compares the actual UTF-8 text stored by a GTK entry, rather than a screenshot of glyphs.

```sh
cargo test --all-targets --locked
cargo build --bin ztype --example kana_plugin --locked
python scripts/test_cli.py --directory target/debug
python3 scripts/test_native.py \
  --binary target/debug/ztype \
  --plugin target/debug/examples/libkana_plugin.so
cargo run --release --no-default-features --example benchmark
```

The native test uses an explicit high-number Xvfb display with a per-user lease and private authentication, separate session/accessibility buses, and a real GTK receiver. It checks stored text after each insertion, exercises the candidate panel when `xdotool` is available, and terminates only its own processes. It requires Xvfb, xauth, xdpyinfo, D-Bus tools, at-spi2-core and GTK development files. Its report and optional screenshot are written under `.build/native-proof/`. Existing display sessions and input settings are not touched. The benchmark measures composition and table lookup only; it excludes rendering and operating-system delivery.

## Next frontend work

Broad compatibility needs proper integration with each desktop's text-input service. The next architecture step is reusable Linux XIM and compositor input-method frontends below the application, followed by TSF and InputMethodKit adapters. The existing focus/token and plugin APIs provide the shared engine boundary for those frontends. A large licensed lexicon, adaptive ranking, full Chinese sentence conversion, richer Japanese conversion and speech are separate language-engine projects.

Primary references: [WowType source and feature scope](https://github.com/Gloridust/WowType), [X.Org XIM protocol](https://www.x.org/releases/X11R7.7/doc/libX11/XIM/xim.html), [Wayland protocol definitions](https://gitlab.freedesktop.org/wayland/wayland-protocols).

