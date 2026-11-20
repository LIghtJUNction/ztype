# ztype

**基于 Project Z 的多语言输入应用。** 你可以在原生面板里输入、选词，再把文字交给指定的软件；也可以用命令行完成转换。ztype 不依赖 Fcitx5。

[English](README.md) · [验收记录](docs/verification.md) · [交付报告](docs/delivery-2026-11-20.zh-CN.md) · [界面调整](docs/interface.md)

![ztype 原生输入面板](docs/panel.png)

当前版本已经具备可用的面板、命令行、语言引擎和原生插件。目前采用显式目标提交；注册到系统的 XIM、Wayland input-method、Windows TSF 和 macOS InputMethodKit 前端仍待实现。目标是逐步具备替代 [WowType](https://github.com/Gloridust/WowType) 的能力，目前不声称功能或软件兼容性已经对等。

## 开始使用

需要 Rust 1.100 或更新版本。克隆时同时取出固定版本的 UI 子模块：

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked

./target/release/ztype panel
```

Linux 图形构建需要系统已安装的 C 编译器、pkg-config，以及 X11、OpenGL、FreeType、fontconfig 开发库。项目不会自动安装系统软件，也不会修改当前输入法设置。Windows 的原生构建需要 MSVC C 工具链；各平台配置可参考 [CI](.github/workflows/ci.yml)。

默认面板使用拼音。输入后按 Tab 或点击候选选词，Enter 提交；Shift+Tab 向前选择，Esc 清空。最多保留九个候选，按文字宽度紧凑横排，放不下时用左右箭头换页。右上角 ⋯ 展开目标窗口和快捷键说明，Esc 先关闭说明，再次按下才清空。数字标明候选顺序；使用 Tab 或鼠标选词。

没有指定目标时，Enter 会复制结果；**粘贴完成前请保持面板打开**。Ctrl+C 复制当前候选而不清空，Ctrl+A 清空输入。输入目前支持末尾追加与退格，光标显示实际的末尾位置。长候选的显示会省略，提交或复制仍保留全文。无效输入和空内容不能提交。

## 输入到其他软件

先列出窗口，再明确选择目标：

```sh
./target/release/ztype windows
./target/release/ztype panel --window 0x0400001
./target/release/ztype commit --window 0x0400001 --text nihao
./target/release/ztype doctor
```

上面的窗口 ID 只是示例，应替换为本机列表中的值。发送会先切换到选定窗口，再交给共享库 zuse。X11 下，键盘布局没有的文字，以及 Enter 等按键仍被按住时提交的文字，会通过 AT-SPI 一次插入完整的可打印 UTF-8，并核对文本与光标。因此这条路径需要正常工作的辅助功能服务，以及能明确识别、已聚焦且可编辑的控件。该确认插入路径在目标不明确、只读、密码框或缺少服务时会拒绝；插入开始后的不确定结果会说明文字可能已改变，不会自动重发。普通布局内按键仍取决于应用的事件处理。XWayland 桌面可用 `ZUSE_BACKEND=x11` 明确选择 X11/XWayland 路径，原生 Wayland 应用需要单独的前端与验收。

## 语言与词库

| 引擎 | 用法 | 当前范围 |
| --- | --- | --- |
| 拼音 `pinyin` | `ztype convert --text nihao` → 你好 | 原创小型起步词表，支持优先级、精确和前缀查询 |
| 日语 `kana` | `ztype convert --engine kana --text konnichiha` → こんにちは | 罗马字转平假名、片假名；尚无汉字转换 |
| Unicode `unicode` | `ztype convert --engine unicode --text 'U+4F60 U+597D'` → 你好 | 有效 Unicode 标量，拒绝代理区、越界与控制值 |
| 原文 `literal` | 原样提交 UTF-8 文字 | 原样传递已有文字；预测模型由具体语言引擎提供 |
| 自定义词库 `table` | `--engine table --dict phrases.tsv` | 按编码查询用户提供的文字 |
| 原生插件 | `--plugin PATH` | 使用共享 zinput 引擎接口加载明确指定、可信的本地库 |

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

编码允许小写 ASCII 字母、数字、撇号、连字符和下划线。单份词库上限为 16 MiB、100,000 条；一次组合、候选和提交文本上限为 4,096 个 UTF-8 字节。程序不发送网络请求，也不创建输入历史数据库。内置词表没有复制 WowType、Rime 或其他项目的模型、词典和语音资源。

## 公共能力放在公共库

| 项目 | 负责的部分 |
| --- | --- |
| zinput | 输入上下文、组合文本、有效修订、候选提交和共享引擎接口 |
| zplugin | 原生插件的类型接口校验与库生命周期 |
| zsys | zplugin 和 zuse 复用的系统动态加载边界 |
| zuse-computer | 找窗口、切换焦点、发送原生文字 |
| zui-native | 原生窗口、事件、绘制、字体与剪贴板 |
| ztype | 使用流程、语言策略、词库和上述能力的组合 |

ztype 没有另写一套操作系统 FFI。应用直接依赖和运行期 Rust 依赖均为 Project Z 库；插件接口生成在编译时会使用 proc-macro2、quote、syn、unicode-ident，因此不把整套构建描述成“完全没有第三方 crate”。

示例引擎是真正可加载的共享库：

```sh
cargo build --release --example kana_plugin --locked
./target/release/ztype convert --plugin target/release/examples/libkana_plugin.so --text sushi
# すし
```

macOS 使用 `.dylib`；Windows 使用不带 `lib` 前缀的 `.dll`。原生插件与应用处于同一进程，接口校验不提供沙箱隔离，应只加载可信来源。

## 已验证的范围

本轮正式 zuse 0.2.1 更新通过 Linux Clippy、应用及插件构建和全部 11 项 GTK 消费验收。下表的契约、命令行和三系统构建测试来自另行记录的首轮基线，具体来源见[验收记录](docs/verification.md)。

| 路径 | 证据与边界 |
| --- | --- |
| 组合和语言引擎 | 10 个边界/生命周期测试、3 个共享引擎契约测试、14 个命令行验收 |
| Linux X11 | 真实 GTK 输入框精确文本比较；涵盖中文、日文、阿拉伯文、印地文、emoji、实际插件、面板提交、按住 Enter 的 ASCII、候选翻页和只读拒绝，共 11 项检查 |
| Windows、macOS | [首轮 CI](https://github.com/LIghtJUNction/ztype/actions/runs/44634327653) 的原生应用构建、插件加载、命令行及契约测试通过；没有据此声称所有应用均能接收输入 |
| 原生 Wayland、系统输入法服务 | 尚未完成相应前端和应用覆盖验收 |

文字到达与字形正确显示是两件事。验收读取 GTK 保存的真实 UTF-8 内容；目标软件的字体、复杂文字排版和输入处理仍决定显示效果。zui 当前 Linux 渲染器还不提供完整复杂文字塑形。

```sh
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --bin ztype --example kana_plugin --locked
python scripts/test_cli.py --directory target/debug
python3 scripts/test_native.py --binary target/debug/ztype --plugin target/debug/examples/libkana_plugin.so
```

原生测试固定使用高位 Xvfb 显示号、独立互斥和私有认证，并启动自己的会话总线、辅助功能总线与 GTK 接收窗口，不接触当前显示器与输入法设置。测试还需要 xauth、xdpyinfo、D-Bus 工具、at-spi2-core 和 GTK 开发文件。后续工作包括复用的系统输入法前端、可授权的大词库、整句转换、用户适应和更多应用验收；这些进度会分别记录，不用单项核心基准代替端到端体验。

MIT 许可证。完整依赖来源和验证限制见[验收记录](docs/verification.md)。

