# ztype

**基于 Project Z 的 Linux 原生输入法与多语言输入应用。** 在支持 input-method-v2 的 Wayland 桌面上，Ztype 直接处理输入框里的组合文字、选词和上屏；也提供原生面板、命令行转换与显式窗口发送。运行时不依赖 Fcitx5。

[English](README.md) · [0.2.1 试用与验收](docs/interactive-trial.zh-CN.md) · [Wayland 使用与验收](docs/native-wayland.zh-CN.md) · [历史验收](docs/verification.md) · [界面调整](docs/interface.md)

0.2.1 增加了安装后可以直接打开的原生输入试用窗口。在 Wayland 桌面运行 `ztype` 或 `ztype try`，输入拼音再按空格，就能在窗口中看到确认后的文字。试用窗口使用自己的输入法和独立桌面会话，不替换日常桌面的输入法。

原生 Wayland 前端支持拼音、日语假名和自定义码表，当前候选显示为输入框内尚未确认的文字。Unicode、原文和原生插件继续通过面板或命令行使用。XIM、Windows TSF、macOS InputMethodKit、独立候选弹窗和大词库仍是后续工作。

## 安装并打开输入试用

需要 Rust 1.100 或更新版本。克隆固定版本的子模块，构建后安装试用组件：

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked
python3 scripts/install.py --binary target/release/ztype --with-trial

~/.local/bin/ztype
# 也可以明确打开试用：
~/.local/bin/ztype try
```

如果 `~/.local/bin` 已加入 `PATH`，直接运行 `ztype` 或 `ztype try` 即可。桌面菜单提供 **Ztype 输入试用**；安装试用组件后，通用 **Ztype** 入口也遵循相同默认行为：Wayland 打开试用窗口，X11 打开候选面板。需要旧面板时，明确运行 `ztype panel`。试用命令需要安装后的辅助组件，只运行构建目录中的二进制不会自动安装这些组件。

试用需要已登录的 Wayland 桌面、支持 Lua 配置的 Hyprland、`hyprctl`、`dbus-run-session`、Python 3 和 GTK 3。`--with-trial` 安装时还需要 C 编译器、`pkg-config` 与 GTK 3 开发文件，用于编译小型输入窗口。完整 Linux GUI 构建另需 X11、OpenGL、FreeType、fontconfig 开发库。安装器会检查依赖，不会自行安装系统软件或启动桌面输入法服务。

### 直接打字看效果

点击输入框，输入下面任一串拼音，再按空格。试下一句前可以点击 **清空**。

| 输入 | 按空格后的文字 |
| --- | --- |
| `nihao` | 你好 |
| `woxiangceshishuruxiaoguo` | 我想测试输入效果 |
| `jixushiyongzhongwenshurufa` | 继续使用中文输入法 |

| 按键 | 正在组合文字时的行为 |
| --- | --- |
| Space / Enter | 确认当前候选 |
| Tab / Shift+Tab | 下一项 / 上一项 |
| 1–9 | 拼音、假名选词；码表中数字保留为编码 |
| Backspace | 删除最后一个输入字符 |
| Escape | 取消尚未确认的文字 |
| Ctrl+Space | 切换组合输入与原始按键输入 |
| Ctrl+Q | 结束试用 |

点击 **结束试用** 也会关闭窗口，并停止这次试用自己的输入法和独立桌面进程。正常试用的输入内容只留在内存中，不会保存。隐藏的 `--state` 选项供自动验收记录合成测试文本使用；正常菜单和 `ztype try` 不启用它。

其他引擎可以这样打开：

```sh
ztype try --engine kana
ztype try --engine table --dict phrases.tsv
```

这里的试用范围是独立输入窗口。向其他桌面应用输入需要下面的原生服务，并取决于桌面协议与应用的 text-input 接入情况。

## X11 候选面板

```sh
ztype panel
```

![独立的 X11 候选面板](docs/panel.png)

默认面板使用拼音。输入后按 Tab 或点击候选选词，Enter 提交；Shift+Tab 向前选择，Esc 清空。最多保留九个候选，按文字宽度紧凑横排，放不下时用左右箭头换页。右上角 ⋯ 展开目标窗口和快捷键说明，Esc 先关闭说明，再次按下才清空。数字标明候选顺序；使用 Tab 或鼠标选词。

没有指定目标时，Enter 会复制结果；**粘贴完成前请保持面板打开**。Ctrl+C 复制当前候选而不清空，Ctrl+A 清空输入。输入目前支持末尾追加与退格，光标显示实际的末尾位置。长候选的显示会省略，提交或复制仍保留全文。无效输入和空内容不能提交。

## 在当前桌面运行原生 Wayland 输入

以下命令把输入法接入当前桌面；上面的试用窗口会自行管理独立会话。

桌面需要提供 `input-method-v2`、`virtual-keyboard-v1` 和 seat，目标应用需要接入 Wayland text-input。先检查当前桌面，再启动服务：

```sh
./target/release/ztype wayland-doctor
./target/release/ztype wayland
# 日语假名
./target/release/ztype wayland --engine kana
# 自定义码表
./target/release/ztype wayland --engine table --dict phrases.tsv
```

同一个 seat 同时由一个输入法服务接管。已运行的输入法需要先正常退出；Ztype 在席位占用时给出错误，不会抢占。密码、PIN 与应用标记为敏感的字段直接接收原始按键。窗口焦点改变会取消未确认文字。

| 操作 | 结果 |
| --- | --- |
| 输入拼音 | 在当前输入框显示未确认候选 |
| Space / Enter | 确认当前候选；空组合时原键交给应用 |
| Tab / Shift+Tab | 下一项 / 上一项 |
| 1–9 | 拼音、假名选词；码表中数字保留为编码 |
| Backspace / Escape | 修改拼音 / 取消组合 |
| Ctrl+Space | 在组合输入和直接英文输入间切换 |
| Ctrl、Alt、Super 快捷键 | 取消未确认组合，再交给应用 |

`nihaoshijie` 可以组合为“你好世界”，带撇号的 `ni'hao'shijie` 也可输入。拼音组合仅使用已有小词表；未知尾缀保留原文。假名保留长音和日文标点，例如 `ko-hi-,nihongo.` → `こーひー、にほんご。`。

Wayland 前端每次文本传送最多 4,000 UTF-8 字节；超过前端容量的编辑或提交保持原组合，供继续修改。独立面板与命令行仍使用 4,096 字节上限。

## 安装

```sh
python3 scripts/install.py --binary target/release/ztype
```

这是不带试用组件的基础安装：安装 `~/.local/bin/ztype`，在二进制包含 GUI 时提供面板入口，包含 Wayland 时提供 `ztype-wayland.service` 用户服务。添加 `--with-trial` 会同时安装试用辅助程序、GTK 输入窗口和专用菜单入口，并让通用 Ztype 菜单遵循 Wayland/X11 默认行为。安装不会启动服务或设置自启动。启动当前桌面的服务前，原有输入法需要先释放 seat；日常桌面仍可继续使用原来的输入法。

```sh
systemctl --user start ztype-wayland.service
systemctl --user stop ztype-wayland.service
# 确认当前桌面的原生输入配置后，可开启登录自启动
systemctl --user enable ztype-wayland.service
```

只有原生输入服务的构建可使用 `cargo build --release --no-default-features --features wayland`。`ztype features` 用 JSON 报告实际编译功能，安装器据此生成入口：无 GUI 时不装面板入口，无 Wayland 时不装服务。如果精简构建会使已有入口失效，安装器会在写入前拒绝，并提示另选安装目录。

## 面板输入到其他软件

先列出窗口，再明确选择目标：

```sh
./target/release/ztype windows
./target/release/ztype panel --window 0x0400001
./target/release/ztype commit --window 0x0400001 --text nihao
./target/release/ztype doctor
```

上面的窗口 ID 只是示例，应替换为本机列表中的值。发送会先切换到选定窗口，再交给共享库 zuse。X11 下，键盘布局没有的文字，以及 Enter 等按键仍被按住时提交的文字，会通过 AT-SPI 一次插入完整的可打印 UTF-8，并核对文本与光标。因此这条路径需要正常工作的辅助功能服务，以及能明确识别、已聚焦且可编辑的控件。该确认插入路径在目标不明确、只读、密码框或缺少服务时会拒绝；插入开始后的不确定结果会说明文字可能已改变，不会自动重发。普通布局内按键仍取决于应用的事件处理。XWayland 桌面可用 `ZUSE_BACKEND=x11` 明确选择 X11/XWayland 路径，原生 Wayland 输入使用上面的 `wayland` 服务；应用覆盖按实际验收分别记录。

## 语言与词库

| 引擎 | 用法 | 当前范围 |
| --- | --- | --- |
| 拼音 `pinyin` | `ztype convert --text nihao` → 你好 | 114 条原创入门词条，支持优先级、精确/前缀查询和完整已知词组的组合 |
| 日语 `kana` | `ztype convert --engine kana --text konnichiha` → こんにちは | 罗马字转平假名、片假名；尚无汉字转换 |
| Unicode `unicode` | `ztype convert --engine unicode --text 'U+4F60 U+597D'` → 你好 | 有效 Unicode 标量，拒绝代理区、越界与控制值 |
| 原文 `literal` | 原样提交 UTF-8 文字 | 原样传递已有文字；预测模型由具体语言引擎提供 |
| 自定义词库 `table` | `--engine table --dict phrases.tsv` | 按编码查询用户提供的文字 |
| 原生插件 | `--plugin PATH` | 使用共享 zinput 引擎接口加载明确指定、可信的本地库 |

内置词表共有 114 条原创入门词条，包含一小组常用输入和反馈用语。它仍是小词表，不能覆盖日常中文的全部词汇。上面的例句由已知词组合成；无法识别的完整输入，例如 `woxiangceshixyz`，保留原文。

词库每行由编码、文字和可选优先级组成，用 Tab 分隔。优先级越大越靠前：

```text
hello	你好	100
hello	مرحبا	80
hello	नमस्ते	70
```

```sh
ztype candidates --engine table --dict phrases.tsv --text hello
ztype convert --engine table --dict phrases.tsv --text hello --candidate 2
```

编码允许小写 ASCII 字母、数字、撇号、连字符和下划线。单份词库上限为 16 MiB、100,000 条；引擎一次组合和候选上限为 4,096 个 UTF-8 字节，Wayland 前端另外遵守 4,000 字节的传送上限。程序不发送网络请求，也不创建输入历史数据库。内置词表没有复制 WowType、Rime 或其他项目的模型、词典和语音资源。

## 公共能力放在公共库

| 项目 | 负责的部分 |
| --- | --- |
| zinput | 输入上下文、组合文本、有效修订、候选提交和共享引擎接口 |
| zinput-wayland | Wayland 输入法注册、键盘布局、按键转发、预编辑、文本提交和敏感字段处理 |
| zplugin | 原生插件的类型接口校验与库生命周期 |
| zsys | zplugin 和 zuse 复用的系统动态加载边界 |
| zuse-computer | 找窗口、切换焦点、发送原生文字 |
| zgpui（native feature） | 原生窗口、事件、绘制、字体与剪贴板 |
| ztype | 使用流程、语言策略、词库和上述能力的组合 |

Rust 应用复用 Project Z 的操作系统适配器；可选试用窗口是由安装器编译的小型 GTK 3 接收程序。应用直接依赖和运行期 Rust 依赖均为 Project Z 库；插件接口生成在编译时会使用 proc-macro2、quote、syn、unicode-ident，因此不把整套构建描述成“完全没有第三方 crate”。

示例引擎是真正可加载的共享库：

```sh
cargo build --release --example kana_plugin --locked
./target/release/ztype convert --plugin target/release/examples/libkana_plugin.so --text sushi
# すし
```

macOS 使用 `.dylib`；Windows 使用不带 `lib` 前缀的 `.dll`。原生插件与应用处于同一进程，接口校验不提供沙箱隔离，应只加载可信来源。

## 已验证的范围

本轮 0.2.1 通过 39 项 Rust 测试、14 项安装后 CLI、13 项交互试用、14 项原生 GTK，以及 foot 终端真实标准输入验收。命令、截图和证据见[安装与实测记录](docs/interactive-trial.zh-CN.md)。

以下平台结果对应各自记录中的源码版本，不能据此推断 0.2.1 已兼容所有桌面应用。

此前 Ztype 0.2.0 Linux 应用通过 33 项 Rust 测试、Clippy、14 项 CLI 和全部 11 项隔离 X11/GTK 检查。原生 Wayland、实际安装与资源占用单独记录在[0.2.0 原生验收](docs/native-wayland.zh-CN.md)。此前的 [zuse 0.2.2 消费验收](docs/zuse-0.2.2-consumer.zh-CN.md)还包含 macOS arm64 的 desktop/plugins 编译检查；完整 macOS GUI 与真实桌面尚未验证。历史平台结果保留原有范围。

| 路径 | 证据与边界 |
| --- | --- |
| 组合和语言引擎 | 16 项组合测试、14 项增量输入控制器测试、3 项共享引擎契约测试、14 项命令行验收 |
| Linux X11 | 真实 GTK 输入框精确文本比较；涵盖中文、日文、阿拉伯文、印地文、emoji、实际插件、面板提交、按住 Enter 的 ASCII、候选翻页和只读拒绝，共 11 项检查 |
| Windows、macOS | [首轮 CI](https://github.com/LIghtJUNction/ztype/actions/runs/44634327653) 的原生应用构建、插件加载、命令行及契约测试通过；没有据此声称所有应用均能接收输入 |
| 原生 Wayland 输入法服务 | 已实现独立 input-method-v2 服务；GTK 与 foot 的实测见[0.2.0 原生验收](docs/native-wayland.zh-CN.md)，应用支持取决于桌面协议与 text-input 接入 |

文字到达与字形正确显示是两件事。验收读取 GTK 保存的真实 UTF-8 内容；目标软件的字体、复杂文字排版和输入处理仍决定显示效果。zgpui 当前 Linux 渲染器还不提供完整复杂文字塑形。

```sh
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --bin ztype --example kana_plugin --locked
python scripts/test_cli.py --directory target/debug
python3 scripts/test_native.py --binary target/debug/ztype --plugin target/debug/examples/libkana_plugin.so
```

原生测试固定使用高位 Xvfb 显示号、独立互斥和私有认证，并启动自己的会话总线、辅助功能总线与 GTK 接收窗口，不接触当前显示器与输入法设置。测试还需要 xauth、xdpyinfo、D-Bus 工具、at-spi2-core 和 GTK 开发文件。共享 Wayland 适配器已经放在 `zinput/native/wayland`。后续工作包括独立候选弹窗、Linux XIM、更多桌面和应用验收、可授权的大词库、整句转换与用户适应；这些进度分别记录。

MIT 许可证。完整依赖来源和验证限制见[验收记录](docs/verification.md)。


[统一原生 UI 迁移记录](docs/zgpui-native-migration.zh-CN.md)说明 zgpui 固定源码、保留的依赖版本和本次验收范围。
