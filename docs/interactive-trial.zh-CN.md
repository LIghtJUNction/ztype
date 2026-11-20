# Ztype 0.2.1 安装与实测记录

日期：2026-11-20（Asia/Taipei）

## 现在怎么用

Ztype 0.2.1 已安装到 `~/.local/bin/ztype`，可用 `ztype --version` 查看版本。当前桌面的 `ztype-wayland.service` 正在运行；独立体验窗口通过 `ztype try` 打开。两种入口的用途如下。

| 想做什么 | 使用方式 |
| --- | --- |
| 在独立窗口里体验输入、选词和删除 | 运行 `ztype try`，或打开菜单中的 **Ztype 输入试用** |
| 在当前桌面的应用里输入 | 保持 `ztype-wayland.service` 运行，在支持原生 Wayland text-input 的输入框中输入 |
| 使用原有 X11/XWayland 候选面板 | 运行 `ztype panel` |
| 只把拼音转换成文本 | 运行 `ztype convert --text nihao` |

在当前 Wayland 会话中，不带参数的 `ztype` 会打开试用窗口。这修正了此前默认进入 X11 面板、出现 “Cannot open X11 display” 的使用障碍。试用需要安装辅助组件；本次已一并安装。

点击试用输入框，输入下面的拼音，然后按空格：

| 键入内容 | 确认后的文字 |
| --- | --- |
| `nihao` | 你好 |
| `woxiangceshishuruxiaoguo` | 我想测试输入效果 |
| `jixushiyongzhongwenshurufa` | 继续使用中文输入法 |

| 按键 | 行为 |
| --- | --- |
| Space / Enter | 确认当前候选 |
| Tab / Shift+Tab | 切换下一项 / 上一项 |
| Backspace | 删除最后一个输入字符 |
| Escape | 取消尚未确认的组合 |
| Ctrl+Space | 切换组合输入和直接输入 |
| Ctrl+Q | 关闭试用窗口 |

试用窗口还提供 **清空** 和 **结束试用** 按钮。关闭后，其自有输入法、窗口和嵌套桌面进程会退出。普通试用不保存输入内容；自动验收仅通过显式 `--state` 保存合成测试文本。

其他引擎入口：

```sh
ztype try --engine kana
ztype try --engine table --dict phrases.tsv
```

## 本轮改变

**增加可直接使用的原生试用入口。** 安装器现在可通过 `--with-trial` 安装 GTK 输入窗口、启动辅助程序和桌面菜单。试用使用独立的 Wayland 连接和会话，不改变当前桌面的输入法服务。正常试用接受用户键盘输入；自动验收模式禁用嵌套桌面继承的键盘和指针，防止真人操作与合成按键混入同一测试。

**修复假名组合的容量处理。** ASCII 罗马字转换成假名后，UTF-8 字节数会增长。原来只按原始输入长度限制，可能让转换结果超过前端容量。本轮在接受编辑前核对展开后的大小；超限时保留原组合与已选候选，允许继续退格修改。相关回归测试覆盖小缓冲区、多字符按键和超过引擎容量的展开。

**扩充原创入门词表。** 内置拼音词条从 82 条增加到 114 条，增加输入、测试和反馈相关常用词。示例句由已知词组合成，未把整句作为固定答案。无法完整识别的输入，例如 `woxiangceshixyz`，仍完整保留原文。

**修正自动验收的焦点回执。** 原 GTK 接收程序在本地切换焦点后就确认控制命令。本轮在确认前完成一次 GDK 显示同步，让显示服务器先处理已发送的输入目的和状态，再允许测试键盘开始下一段输入。所有密码框、焦点切换、快捷键和只读断言保留。该同步保证的是发送方请求到达显示服务器；不把它描述成所有应用与输入法之间的全局就绪保证。[GDK 官方说明](https://docs.gtk.org/gdk3/method.Display.sync.html)

## 实测结果

以下结果来自本轮安装的 0.2.1 二进制。最终原生 GTK 与 foot 验收未启用协议追踪，输入来自独立虚拟键盘，目标是自有嵌套 Wayland 会话中的真实应用。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Rust 测试 | **39 项通过**：17 项增量控制、19 项组合、3 项插件契约 | [Rust 日志](verification-0.2.1/rust-tests.log) |
| Clippy | **通过**，release / all-targets，警告视为错误 | [Clippy 日志](verification-0.2.1/clippy.log) |
| 格式检查 | **通过** | [汇总](verification-0.2.1/summary.json) |
| 安装后 CLI | **14 项通过**，包括实际原生插件加载 | [CLI 结果](verification-0.2.1/installed-cli.json) |
| 安装后的试用窗口 | **13 项通过** | [交互报告](verification-0.2.1/trial-report.json) |
| 原生 GTK 输入 | **14 项通过** | [GTK 报告](verification-0.2.1/native-report.json) |
| foot 终端输入 | **通过**，真实 TTY 标准输入收到 `你好世界` | [终端报告](verification-0.2.1/terminal-report.json) |
| 重装保持运行状态 | **通过**，二进制内容与 inode、已有服务进程保持一致 | [安装检查](verification-0.2.1/installation-idempotence.json) |
| 自有进程清理 | **通过**，自动窗口和自有服务已退出 | [嵌套会话报告](verification-0.2.1/nested-report.json) |

试用窗口的 13 项检查包括未确认组合、空格提交、连续输入、完整例句、退格、取消、Tab 选词、中英切换、未知尾缀保留，以及 Ctrl+Q 关闭与进程清理。自动验收前后，宿主输入法服务状态和进程一致。

GTK 的 14 项检查还包括数字选词、密码框直接输入、切换焦点丢弃旧组合、Ctrl+A 替换、只读字段不变。终端测试键入的是 ASCII `nihaoshijie`，通过原生输入法转换，再由 foot 内的 Python 接收程序从真实标准输入读取。收到的 UTF-8 字节为 `e4bda0e5a5bde4b896e7958c0a`，末尾是换行。

### 实际截图

下面是自动验收中真实接收窗口和终端的截图。截图内仅有合成测试文本。

[原生试用输入框收到我想测试输入效果](evidence-archive-20261120.md)

[foot 标准输入收到你好世界](evidence-archive-20261120.md)

## 安装与复现

在包含固定子模块的仓库中：

```sh
cargo build --release --locked
python3 scripts/install.py --binary target/release/ztype --with-trial
ztype try
```

试用需要当前 Wayland 桌面、支持 Lua 配置的 Hyprland、hyprctl、dbus-run-session、Python 3 和 GTK 3。安装时还需要 C 编译器、pkg-config 与 GTK 3 开发文件。完整 GUI 构建的其余依赖见 [README](../README.zh-CN.md)。

复现自动验收时，为输出选择尚未使用的目录：

```sh
cargo test --locked
cargo clippy --release --all-targets --locked -- -D warnings
cargo fmt --all -- --check
python3 scripts/test_trial.py --binary ~/.local/bin/ztype --output .build/trial-reproduction
python3 scripts/test_wayland_nested.py --binary ~/.local/bin/ztype --output .build/native-reproduction --terminal --screenshot
```

图形验收会在当前桌面显示临时窗口，完成后自动关闭。普通使用不需要运行这些验收脚本。

本次安装写入六个文件：主程序、通用菜单、Wayland 用户服务、试用辅助程序、GTK 接收程序、专用试用菜单。完整列表见 [安装记录](verification-0.2.1/installation.json)。

验收时的服务状态是：Ztype **active / disabled**，Fcitx5 **inactive / enabled**。前者表示 Ztype 当前运行、尚未设为自启动。用户已在本轮手动启动原生服务；安装新版后刷新过该服务，随后重装辅助组件保留了原进程。本轮没有更改自启动选择。

管理当前桌面的服务：

```sh
systemctl --user start ztype-wayland.service
systemctl --user stop ztype-wayland.service
```

如需恢复本机原先的 Fcitx5：

```sh
systemctl --user stop ztype-wayland.service
systemctl --user start fcitx5.service
```

## 范围与限制

本轮验证环境是 Linux、Hyprland 0.57.2、原生 GTK 3 和 foot；二进制同时包含 GUI、desktop、plugins 和 Wayland 功能。当前选中候选显示在输入框的预编辑位置，尚无独立 Wayland 候选弹窗。

114 条词表适合框架验证和上述例句，词汇覆盖与整句预测尚未达到成熟中文输入法的水平。应用是否接收原生输入仍取决于 compositor 协议和应用的 text-input 接入。本报告不把 GTK 与 foot 的通过外推到全部浏览器、Qt、Electron、终端或其他桌面。Windows TSF、macOS InputMethodKit 和 XIM 仍未实现。

## 源码与证据标识

- [核心修复与词表：36d217a](https://github.com/LIghtJUNction/ztype/commit/36d217a86b4259995991833f012577c1b9d1b385)
- [0.2.1 原生试用入口：a8d9b29](https://github.com/LIghtJUNction/ztype/commit/a8d9b29948081f22b27d2f53435f2608cfaacac8)
- [验收焦点同步：b55b980](https://github.com/LIghtJUNction/ztype/commit/b55b9806bca9e50e097d9e0cbd1399fd7c4272aa)
- 安装二进制 SHA-256：`d39571c75b308aa5d24ed339fdf71756c7cc7f23e378aa3d1d1792a9eaa63440`
- [结构化汇总](verification-0.2.1/summary.json)

公开证据保留测试输入、结果和内容指纹；本机用户名、临时进程标识与原始桌面日志未纳入公开报告。
