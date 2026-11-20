# ztype

[English](README.md) · [0.4.1 安装与验收](docs/installed-native-0.4.1.zh-CN.md) · [源码许可证](LICENSE)

**Ztype 0.4.1 是基于 Project Z 的 Linux 输入法，提供原生 Wayland 与 X11/XWayland 服务。** 输入时在目标应用中维护组合文字，独立候选窗显示拼音、选词和页码，不抢键盘焦点。新版采用 Google 风格的白色界面、蓝色选中项和留白；实测 1× 布局高 112 px，内边距 20 px。

拼音使用单独安装的本地词库，也支持日语假名和自定义码表。运行输入法不需要 Fcitx 或 Rime 服务。`panel` 是独立的组合输入辅助窗口；日常原生输入由 `wayland` 或 `x11` 服务负责。

![Ztype 0.4.1 在原生 Wayland 浏览器中的浅色候选窗](docs/evidence/installed-native-0.4-20261120/stage-b/attempt-7708/wayland/chromium-preedit.png)

## 构建与安装

需要 Rust 1.100 或更新版本，并取出固定的框架、UI 子模块：

```sh
git clone --recurse-submodules --branch fix/wayland-focus-replay-20261120 https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked

# 下载并校验固定版本、单独授权的拼音数据。
python3 scripts/install_dictionary.py
python3 scripts/install.py --binary target/release/ztype

~/.local/bin/ztype features
~/.local/bin/ztype config
~/.local/bin/ztype wayland-doctor
~/.local/bin/ztype status
```

完整 Linux 构建需要 C 编译器、`pkg-config`、X11/OpenGL、FreeType/fontconfig、XCB 及 xcb-imdkit/Shape，以及 Wayland/XKB 系统库。可选组件见 [Cargo 功能配置](Cargo.toml)；脚本不会代为安装系统软件包。

词库默认安装到 `$XDG_DATA_HOME/ztype/dictionaries`，未设置时使用 `~/.local/share/ztype/dictionaries`，并保留原始源文件、许可证及 `rime-ice-source/provenance.json`。脚本支持 `--destination DIR`、默认的 `--source rime-ice`，或 `--source local --source-dir DIR` 从本地读取同一固定版本的源文件。使用自定义目标目录时，应在配置中指定词库或使用 `--dict`。

应用默认安装到 `~/.local/bin/ztype`，按 XDG 路径写入用户服务、执行 `ztype start` 的 **Ztype** 桌面入口，并在没有配置时创建注释示例。已有配置会保留。**仅安装不会启动服务，也不会启用自启动。** 显式添加 `--activate` 才会在当前图形会话启用并重启相应服务；`--with-trial` 可另装独立试用窗口。安装器不会停止 Fcitx，也不会抢占已被使用的输入法席位。

## 启动原生输入

Wayland 桌面需提供 `input-method-v2`、`virtual-keyboard-v1` 和 seat，目标应用需接入 Wayland text-input。同一 seat 已有输入法时，需要先由原输入法释放。XIM 路径要求 X11/XWayland 应用使用相应的 XIM 名称；该名称已有所有者时，Ztype 会拒绝注册。

决定在当前会话使用 Ztype 后：

```sh
~/.local/bin/ztype start
~/.local/bin/ztype status
```

修改设置、词库或升级程序后使用 `ztype restart`，停止服务使用 `ztype stop`。`status` 会报告启动失败及仍在运行的旧程序。控制命令根据编译功能和当前显示环境管理 `ztype-wayland.service`、`ztype-x11.service`；启动不等于设置登录自启动。在支持的 Linux 图形环境中，直接执行 `ztype` 也会选择 `start`。

需要在终端前台运行时，从下面选择对应命令，不要与同一已安装服务重复启动：

```sh
ztype wayland
ztype x11
# 两个前端都可显式选择引擎：
ztype wayland --engine kana
ztype wayland --engine table --dict phrases.tsv
```

XIM 客户端必须在进程启动前继承 `XMODIFIERS=@im=ztype`。已有进程及终端 broker 会保留旧环境。保留现有 PTY、另开独立 Ztty broker 可使用：

```sh
XMODIFIERS=@im=ztype ZTTY_SOCKET=@ztty-ztype ztty --backend x11
```

在新 shell 中修改环境变量，不会让已经运行的应用自动迁移。Ztype 不会自动修改桌面输入法设置或替换 Fcitx。

## 原生输入快捷键

下表适用于有未确认组合的情况；组合为空时，普通按键交给目标应用。

| 按键 | 默认行为 |
| --- | --- |
| Space | 上屏当前候选 |
| 1–9 | 上屏当前页对应的候选；仅拼音、假名，且该项需要存在 |
| Tab / Shift+Tab、Down / Up | 下一项 / 上一项 |
| PageDown / PageUp | 下一页 / 上一页 |
| `=` / `-` | 拼音模式下一页 / 上一页 |
| Left / Right / Home / End | 移动拼写中的光标 |
| Backspace / Delete | 编辑拼写 |
| Enter | **拼音默认上屏原始拼写**，不选择候选 |
| Escape | 取消组合 |
| Ctrl+Space | 切换组合输入与直接输入，并清空未确认组合 |

码表保留数字、连字符和下划线作为编码，使用 Tab、Space 选词。假名支持长音与标点，例如 `ko-hi-,nihongo.` → `こーひー、にほんご。`。命令快捷键会取消未确认组合并交给应用；单独按下修饰键不会清空组合。

`ztype config` 显示解析后的配置，不是编辑器。修改 `$XDG_CONFIG_HOME/ztype/config`（通常为 `~/.config/ztype/config`）后重启。默认值包括 `page_size=5`（可设 1–9）、`initial_mode=chinese`、`chinese_punctuation=true`、`enter_behavior=raw`、`learning=false`。设为 `enter_behavior=candidate` 可让 Enter 选择拼音候选。`--config PATH` 指定其他配置；`--no-config` 关闭用户配置与自动词库发现。原生拼音通常要求外部词库；显式 `--no-config` 才使用很小的基础词表。

## 词库与候选原理

本轮安装的 Rime Ice 转换词库含 **573,194 个唯一编码/文字组合**，包括音节边界别名，不等于这么多不同的中文词语。词库保存在程序外部。拼音在已排序的编码表中进行精确和前缀查询；没有整段精确匹配时，用有界动态规划尝试一个完整的已知词组组合。排序依据词库优先级和可选的本地选择次数，不运行神经网络整句模型。日常组合输入不发网络请求，安装词库的步骤会下载公开数据。

本地学习默认关闭。启用时必须同时设置 `learning=true` 和明确的 `user_dictionary` 路径，保存的是本地选择次数。实现见[词库加载](src/dictionary.rs)与[候选生成](src/engine.rs)。

自定义 UTF-8 TSV 每行格式为 `编码<TAB>文字<TAB>优先级`，最后一项可省略，为非负整数。编码允许小写 ASCII 字母、数字、撇号、连字符和下划线。单个词库文件上限为 64 MiB，加载器总条目上限为 2,000,000；候选池最多 128 项，原生候选页显示 1–9 项。核心文本上限为 4,096 UTF-8 字节；原生 Wayland 传输上限为 4,000 字节。

```sh
ztype candidates --text nihao
ztype convert --text nihao
ztype convert --engine kana --text konnichiha
ztype convert --engine unicode --text 'U+4F60 U+597D'
ztype candidates --engine table --dict phrases.tsv --text hello
ztype convert --engine table --dict phrases.tsv --text hello --candidate 2
```

原生服务支持 `pinyin`、`kana`、`table`。`unicode`、`literal` 和显式加载的原生插件可通过命令行或辅助面板使用。假名引擎是音译转换，尚无日语汉字转换。原生插件运行在应用进程内，只应加载可信来源；接口校验不提供沙箱。

## 辅助面板与开发

`ztype panel` 打开自己的组合输入窗口。未指定目标时，Enter 复制选中的结果，粘贴前需要保持窗口打开；指定目标时，通过独立的 `zuse` 发送路径切换焦点并提交。这个辅助窗口不注册原生输入法服务，其 Enter 行为也与原生拼音不同。

```sh
ztype panel
ztype windows
ztype panel --window 0x0400001
ztype commit --window 0x0400001 --text nihao
ztype doctor
```

窗口 ID 应换成 `windows` 输出的实际值。辅助发送中的 X11 完整 UTF-8 确认插入路径使用 AT-SPI，需要能明确识别、已聚焦、可编辑的辅助功能控件；目标不明确、只读或密码框时会拒绝。这条路径与 XIM 独立，也不会开启原生 Wayland text-input。历史面板检查见[验收记录](docs/verification.md)。

```sh
# 原生服务及候选窗，不构建辅助 GUI 和插件加载器：
cargo build --release --locked --no-default-features --features wayland-popup,x11
# 仅命令行：
cargo build --release --locked --no-default-features

cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --release --locked --example kana_plugin
./target/release/ztype convert --plugin target/release/examples/libkana_plugin.so --text sushi
```

安装器根据 `ztype features` 的 JSON 只安装受支持入口；精简构建会让已有入口失效时，在写入前拒绝覆盖。系统 FFI 放在共享适配器中：`zinput-wayland`、`zinput-x11` 处理原生协议，`zgpui-raster` 绘制候选像素，Ztype 负责组合策略和排版；辅助面板使用 `zgpui`、`zuse`，类型化插件使用 `zplugin`。直接及运行期 Rust 依赖均为 Project Z 库；可选插件代码生成在编译时还使用第三方过程宏依赖。

## 已验证范围与现存边界

[已安装 0.4.1 报告](docs/installed-native-0.4.1.zh-CN.md)保存具体二进制、词库哈希、复现命令和原始结果。受测二进制 SHA-256 以 `7708a88b` 开头，安装、服务启用和验收结果分别记录。

| 受测路径 | 记录结果 |
| --- | --- |
| GTK 原生 Wayland | 23 项通过 |
| Google Chrome 158.0.8127.36，原生 Wayland | 7 项通过 |
| foot | 中文到达真实 PTY |
| Ztty XIM | 8 项输入、6 项重连通过；服务恢复期间 broker、窗口和 PTY 保持不变 |
| compositor 确认焦点后的敏感字段输入 | 接收器先完成协议 roundtrip 再确认焦点，随即发键，20/20 轮通过 |

**另一种未同步的焦点抢跑仍有失败记录。** 接收器只确认 GTK 本地焦点、立即发键时，第 4 轮的密码首键进入旧组合，并在密码框出现该组合的预编辑。trace 显示首键早于 IME 收到新的敏感状态。20/20 使用明确的 compositor 焦点确认边界，不表示原先仅本地 ACK 的场景已经修复或通过。

Wayland 敏感字段直通依赖应用的用途、提示信息已传到 IME。XIM 不提供等价的密码用途元数据，因此不宣称自动识别 XIM 密码框。XIM 候选窗优先使用客户端提供的位置，缺少位置时退回窗口边缘；本轮 Ztty 截图属于后者。实际兼容性还取决于桌面协议、工具包接入、字体和文字塑形。Windows TSF、macOS InputMethodKit 前端尚未实现，不据本轮结果承诺所有桌面、所有应用或固定性能。

## 许可证

Ztype 应用代码及原创基础词表使用 [MIT 许可证](LICENSE)。可选的 [Rime Ice 词库](https://github.com/iDvel/rime-ice/tree/927e1cf1d3eeb9bf4be85e3f684dba4dd485b6f6)保留 **GPL-3.0** 与原始来源记录。[词库安装器](scripts/install_dictionary.py)校验固定源文件哈希并保留源文件及许可证，不把该数据重新标为 MIT，也不引入 Rime 服务依赖。共享库保留各自许可证。
