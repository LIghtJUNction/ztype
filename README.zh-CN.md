# ztype

[English](README.md) · [安装与服务](docs/service-installation.md) · [本轮验收](docs/ztty-ui-0.4.2.zh-CN.md) · [历史记录](docs/verification.md)

**基于 zinput 的 Linux 原生输入法。** Ztype 0.4.2 提供 X11/XWayland XIM 和 Wayland input-method-v2 两条输入路径。按键在当前应用中组合，独立候选窗显示拼音、当前页和候选，确认后通过输入法协议提交文字。日常输入不需要打开独立演示面板。

0.4.2 为原生候选窗调整文字层级、20 px 左右留白与 Material 3 浅色配色。ztty 的 XIM 与原生 Wayland 两条路径均完成真实终端输入验收；Wayland 路径使用 [ztty PR #23](https://github.com/LIghtJUNction/ztty/pull/23) 的光标更新兼容修复，具体源码和二进制身份见[本轮记录](docs/ztty-ui-0.4.2.zh-CN.md)。

词库、候选页大小、初始中英文模式、标点和 Enter 行为均可配置。拼音支持外部加权词库、多词库合并、词语组合、隔音符号和可选的用户选词学习。语言数据与程序分开安装，Ztype 运行时不依赖 Rime 或 Fcitx5 服务。

[完整词库在真实 Wayland 输入框中的候选窗](docs/evidence-archive-20261120.md)

## 构建并安装

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked

# 下载固定版本的公开词库，校验来源并保留许可证
python3 scripts/install_dictionary.py --source rime-ice

# 安装程序、配置示例及编译进去的原生服务；此命令不切换输入法
python3 scripts/install.py --binary target/release/ztype
~/.local/bin/ztype config
~/.local/bin/ztype convert --text shurufa
# 输入法
```

Linux 原生构建使用系统 C 编译器、pkg-config、X11/XCB、XKB、Wayland、FreeType、fontconfig 及图形开发库。Rust 最低版本以 `Cargo.toml` 的 `rust-version` 为准。命令行核心可以用 `--no-default-features` 独立构建。

词库安装器固定读取 [Rime Ice](https://github.com/iDvel/rime-ice/tree/927e1cf1d3eeb9bf4be85e3f684dba4dd485b6f6) 的两个公开词表，合并后生成 573,194 条记录，其中 21,014 条是隔音符号别名，例如 `xi'an`。这不是 57 万个不同汉语词语：同一词语可以有多个拼音编码。词表、来源记录和 GPL-3.0 许可证保留在用户数据目录；仓库内的程序许可证与下载词库的许可证分别保留。内置小词表只用于无外部数据时的基础转换与测试，不代表日常词库。

## 启动与应用连接

在原输入法释放对应的 Wayland seat 或 XIM 名称后，再启动 Ztype：

```sh
ztype wayland-doctor
ztype start
ztype status
# 修改配置或升级后
ztype restart
# 停止
ztype stop
```

安装器的 `--activate` 会启用当前会话支持的服务并重启它们，确保旧进程实际被新程序替换。常规安装不启动服务。服务通过 readiness 通知确认词库和原生输入连接已初始化；`status` 会报告失败和仍运行旧二进制的情况。完整说明见[安装与服务](docs/service-installation.md)。

X11/XWayland 应用通过启动时的 `XMODIFIERS=@im=ztype` 选择默认端点。`ZTYPE_XIM_NAME` 可以指定其他端点名称。已有应用不会自动改变自己的环境变量；如果它们已经选择 `@im=fcitx`，可在 Fcitx5 完全退出后，以 `ZTYPE_XIM_NAME=fcitx ztype start` 提供相同名称。服务会拒绝占用已有输入法的名称。名称可配置不等于依赖 Fcitx5。

原生 Wayland 应用还需要自身的 text-input 支持。浏览器的 Wayland 显示后端和输入法协议开关是两件事，具体启动选项和实测结果见本轮验收记录。

## 日常操作

| 按键 | 默认行为 |
| --- | --- |
| 字母 | 在当前组合位置输入拼音 |
| Space | 提交当前候选 |
| 数字 1–9 | 选择当前页对应候选；码表引擎保留数字编码 |
| PageUp / PageDown、拼音模式下的 `-` / `=` | 翻页 |
| Tab / Shift+Tab、↑ / ↓ | 切换候选 |
| ← / →、Home / End | 移动组合中的光标 |
| Backspace / Delete | 删除光标前 / 后的字符 |
| Enter | 提交原始拼音；可配置为提交候选 |
| Escape | 取消组合 |
| Ctrl+Space | 切换中英文输入 |

默认每页 5 个候选，可设置为 1–9 个；候选集合最多保留 128 个，页内数字与显示序号一致。中文模式可输出中文标点，英文模式传递原键。焦点切换和敏感字段会清理组合；Wayland 密码/PIN 等敏感字段透传按键，不参与学习。

候选窗通过原生输入协议显示，不抢走应用焦点。Wayland 使用应用报告的文字位置；XIM 客户端提供 spot location 时按该位置显示。未提供位置的客户端使用窗口边缘回退位置，目前 Ztty 的 XIM 路径属于这种情况，不能把该路径描述为跟随终端内的字符光标。

## 配置与词库

配置默认位于 `$XDG_CONFIG_HOME/ztype/config`，未设置时为 `~/.config/ztype/config`。`ztype config` 显示实际路径、词库来源及解析后的选项；错误会标明配置文件和行号。

```ini
engine=pinyin
page_size=5
initial_mode=chinese
chinese_punctuation=true
enter_behavior=raw
learning=false

# 显式词库可以写多次；相对路径相对于本配置文件
# dictionary=/absolute/path/common.tsv
# dictionary=/absolute/path/domain.tsv

# 仅在主动开启学习时写入已选词及次数
# user_dictionary=~/.local/share/ztype/user.tsv
```

没有显式 `dictionary` 时，自动发现 XDG 系统与用户数据目录中 `ztype/dictionaries/*.tsv`。`--dict PATH` 只使用指定词库；`--config PATH` 使用指定配置；`--no-config` 关闭用户配置和自动词库发现，适用于可重复的测试。

词表格式为 `code<TAB>text<TAB>frequency`，第三列可省略。同一编码按频率排序，重复编码和文字合并，普通拼音可从词语组合成未预存的句子。这是词频与词语切分方法，没有神经语言模型，也不保证所有句子都能自动消歧。单文件最多 64 MiB、2,000,000 条记录，一次最多加载 32 个词库。

```sh
ztype candidates --text ni
ztype convert --text wozaitushuguanxuexi
# 我在图书馆学习
ztype convert --text "xi'an"
# 西安
ztype convert --engine table --dict phrases.tsv --text hello --candidate 10
```

学习默认关闭。主动启用时只记录用户提交的编码、候选和次数，使用独立文件、进程锁和原子保存；不会自动读取其他输入法的个人数据。已有用户词库也可以只读加载。普通输入进程不下载词库；联网下载只由显式运行的安装脚本完成。

## 框架与平台范围

| 部分 | 职责与当前范围 |
| --- | --- |
| `zinput` 核心 | 组合缓冲区、焦点上下文、候选和提交生命周期，以及引擎接口 |
| `zinput-x11` | XIM 连接、按键与长按、XKB 布局更新、候选窗口适配 |
| `zinput-wayland` | input-method-v2、virtual-keyboard-v1、焦点状态、预编辑/提交及候选 surface |
| `ztype` | 词库与排序、配置、编辑与选词规则、候选内容、服务和安装流程 |
| `zgpui` | 原生图形与候选文字绘制 |

输入框架以操作系统协议为边界，不把 Arch、某个用户名或固定词条写成运行条件。但“可跨发行版编译”和“所有桌面环境都已支持”是不同的验收范围。

| 环境 | 支持边界 |
| --- | --- |
| Linux X11 / XWayland | 已实现 XIM；真实 Ztty 多版本已验收，其他客户端仍取决于其 XIM 支持 |
| Hyprland 等 input-method-v2 合成器 | 已实现所需协议；本轮的实际应用结果见验收记录 |
| KDE Plasma 原生 Wayland | 当前未实现其 input-method-v1 适配，不能声称全面支持 |
| GNOME 原生 Wayland | 当前未实现 GNOME/IBus 集成，不能声称全面支持 |
| Windows / macOS | 部分便携核心和历史构建记录存在；TSF / InputMethodKit 输入法前端尚未实现 |

桌面协议依据：[KDE Plasma Keyboard](https://github.com/KDE/plasma-keyboard)、[IBus Wayland 桌面说明](https://github.com/ibus/ibus/wiki/WaylandDesktop)。应用和桌面验收只覆盖记录中的版本与配置。

## 其他引擎和开发检查

`kana` 提供罗马字到假名转换，不做日文汉字转换；`unicode` 解析 Unicode 标量；`literal` 保留原始 UTF-8；`table` 使用用户码表。原生服务支持 pinyin、kana、table；其余引擎和显式可信原生插件通过 CLI 或独立 `panel` 使用。面板是辅助工具，操作方式与原生候选窗不同。

```sh
ztype convert --engine kana --text konnichiha
ztype convert --engine unicode --text 'U+1F30D U+4F60 U+597D'
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
python3 scripts/test_install.py
python3 scripts/test_daily_input.py --binary target/release/ztype
```

真实输入验收同时检查应用收到的 UTF-8、候选窗实际像素、焦点与进程清理，不能只以服务处于 running 状态判断可用。本轮证据、命令、依赖版本和剩余限制记录在[日常输入验收](docs/ztty-ui-0.4.2.zh-CN.md)，早期结果保留在[历史验证](docs/verification.md)。

[0.4.0 日常输入记录](docs/daily-input-2026-11-20.zh-CN.md)保留此前的依赖和应用基线。

[0.2.1 交互试用记录](docs/interactive-trial.zh-CN.md)作为历史验收保留。
