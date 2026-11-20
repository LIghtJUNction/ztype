# Zinput 与 Ztype：Ztype 0.4.0 修复与验收

日期：2026 年 11 月 20 日（Asia/Taipei）

## 交付结果

Ztype 0.4.0 已安装到开发机的 `~/.local/bin/ztype`，包含 Wayland、Wayland 独立候选窗、X11/XWayland XIM、服务管理、GUI 和插件功能。完整拼音词库已安装，现有配置保留，学习默认关闭。安装后实际执行 `shurufa` 和 `wozaitushuguanxuexi`，分别输出“输入法”和“我在图书馆学习”。

按照用户暂时使用 Fcitx5 的安排，安装没有激活 Ztype。交付检查时，`ztype-wayland.service` 和 `ztype-x11.service` 均为 inactive，`fcitx5-remote` 返回 2。图形验收使用独立显示会话，不向用户活跃窗口注入测试按键。

最终安装文件与验收文件 SHA-256 相同：

```text
066902ba0497f53c5ec963bb94e5deb00ade361a14d232501ab01c7d7604d5fe
```

框架最终固定至 [zinput 003829a](https://github.com/LIghtJUNction/zinput/commit/003829a5e7ea45d9827376d89e37c9e6cfbf4a12)。应用运行代码的集成提交为 [ztype 5aa632d](https://github.com/LIghtJUNction/ztype/commit/5aa632d)，随后追加的提交保存测试、依赖来源一致性及文档。仓库入口为 [zinput](https://github.com/LIghtJUNction/zinput) 和 [ztype](https://github.com/LIghtJUNction/ztype)。

## 原因与实现

此前机器运行的仍是旧版 Ztype，已编译的候选窗没有进入实际服务。旧安装流程使用 start，无法保证升级后替换已有进程；缺少日常词库时也容易把“能启动”误当成“能正常输入”。应用的初始词表和候选数量限制，使它在熟悉的演示词之外表现不足。

这次把日常输入需要的数据和行为接入了实际服务。词库从外部文件加载，支持多来源合并、频率排序、拼音词语组合和隔音符号。候选集合上限提高到 128，默认每页五项，页大小可以配置；数字键选择当前页项目。组合支持左右移动、Home/End、前后删除和中间插入。Enter 默认提交原始拼音，Space 提交候选，中文标点和初始中英文模式可以配置。

配置由 CLI、Wayland 和 XIM 共同读取，采用 XDG 路径。错误标明文件和行号。切换到假名、Unicode 或原文引擎时，不继承拼音词库及学习设置；显式传入不支持的词库参数会报错。试用窗口的配置参数贯穿启动器和内部服务。没有外部拼音词库时，正常原生服务明确失败并给出安装指令；显式 `--no-config` 可用于基础词库诊断，并打印说明。

安装使用原子文件替换。激活升级时执行 restart，服务以 readiness 通知确认初始化完成，status 可以发现旧进程和真实失败状态。默认安装保留现有配置，不激活服务。

框架层修复了 XIM 长按识别和运行中的 XKB 键盘布局更新。最终 Wayland 验收还捕获了一个间歇性首键丢失问题：收到敏感输入状态后，旧 keyboard grab 的本地代理过早销毁，仍在传输中的按键被丢弃。[63ad9f1](https://github.com/LIghtJUNction/zinput/commit/63ad9f12a7995aff9fb4b0267a845977bea24b55) 改为先释放服务端抓取，经有界同步收齐旧事件，按原顺序透传并配对释放按键，最后销毁本地代理。旧事件不会进入新输入上下文的中文组合。

## 验收结果与范围

下表包含不同阶段的测试分组，部分覆盖重叠，不相加成一个夸大的总数。

| 项目 | 结果 | 具体范围 |
| --- | --- | --- |
| 应用 Rust 全目标测试 | 72 项通过 | 输入编辑、组合、词库、插件契约、配置、候选绘制、服务生命周期 |
| 最终应用入口测试 | 22 项通过 | 最后入口修订后重新运行二进制模块测试 |
| 最终 CLI 定向验收 | 8 个真实调用通过 | 引擎覆盖、用户数据不写入、非法词库、缺词库在连接前失败、诊断顺序 |
| 试用参数检查 | 7 项通过 | 参数解析、互斥及进程参数传播；不是图形试用实测 |
| 安装器 | 15 项测试及 6 项 CLI mock 通过 | 升级、重启、XDG 路径、现有配置保留及 staging 隔离 |
| Wayland 框架定向测试 | 13 项通过 | 包括旧连接生命周期、失败清理和不同上下文的事件顺序 |
| 严格 Clippy | 通过 | 最终应用 all-targets 及修改后的 Wayland 框架，使用 `-D warnings` |
| 最终 Ztty 0.5.2 | 12/12 通过 | 真实安装映像，原生 XIM 到 PTY，候选实际文字、多窗口和重连 |
| 最终 Ztty 0.5.0 | 12/12 通过 | 用户当前套件中的旧映像，同样的原生输入验收 |
| 最终敏感焦点回归 | 20/20 轮通过 | 每轮密码框收到完整 `nihao`，无预编辑，旧组合清除，候选隐藏 |
| GTK 常规输入与候选 | 28 项通过 | 中文上屏、原始拼音、标点、中间插入、翻页、焦点及候选位置 |
| 浏览器 | 12 项通过 | 实际为 Google Chrome 158.0.8127.36，原生 Wayland，未额外开启 IME flags |
| foot | 通过 | 真正的终端 PTY 收到“你好世界” |
| 完整词库图形输入 | 通过 | GTK 实际输入“我在图书馆学习”，候选窗有真实文字像素 |

Ztty 的最终验收使用上述 `066902…` 安装文件。终端先于输入服务打开，随后连接；服务停止后普通按键可用，重启后原窗口恢复输入。“你好”“世界”“输入法”均进入真实 PTY，不以窗口标题或模拟输出代替收字验证。候选窗检查等待实际文字绘制后才截图，两代终端均没有测试进程残留。

GTK、浏览器、foot 和完整词库的图形证据来自前一个已验证构建 `113feeb6…`。最后修改仅涉及入口配置和 grab 生命周期；最终构建针对这些影响做了 CLI、服务模块、敏感焦点和 Ztty 回归，没有把早期截图描述为最终版本的全量重跑。敏感焦点的旧版本 20 轮常规运行也曾通过，因此把原问题记录为有明确失败日志的间歇性竞态，不宣称稳定的每次必现失败。

Wayland 图形测试运行于私有 headless Weston 上的嵌套 Hyprland。外层 Weston 只提供测试显示环境，不表示 Ztype 已支持 Weston 的原生输入法协议。测试通过仅注入子进程的私有 Aquamarine 兼容库解决测试显示协议版本差异，未替换系统库。

### 实际候选窗

[GTK 完整拼音输入截图](https://github.com/LIghtJUNction/ztype/blob/main/docs/evidence/daily-2026-11-20/gtk-pinyin.png)展示整句候选；[浏览器截图](https://github.com/LIghtJUNction/ztype/blob/main/docs/evidence/daily-2026-11-20/chromium-candidates.png)展示文字光标附近的候选窗；[最终 Ztty 截图](https://github.com/LIghtJUNction/ztype/blob/main/docs/evidence/daily-2026-11-20/ztty-candidates.png)展示拼音、页码和五个中文候选。

Wayland 的候选窗会依据文字位置移动，测试中输入框移动 96 像素后，候选窗也移动相同距离。Ztty 当前没有提供 XIC spot location，候选窗使用窗口左下方的备用位置；本轮没有宣称它跟随终端内的字符光标。

## 词库、性能与数据写入

安装器使用 [Rime Ice 固定版本](https://github.com/iDvel/rime-ice/tree/927e1cf1d3eeb9bf4be85e3f684dba4dd485b6f6)的公开基础词表，保留原始文件、GPL-3.0 许可证、来源和哈希。生成文件包含 552,180 条基础编码记录及 21,014 条隔音符号别名，共 573,194 条；和内置基础条目合并后，实际引擎有 573,195 条记录。记录数不是不同汉语词语的数量。

```text
rime-ice.tsv
大小：15,654,275 bytes
SHA-256：504e9f69e794584375bbfedef937230e5dc76faff9c7696cc9285aff64cc18d9
```

完整词库门禁实际验证了“图书馆”“软件”“电脑”“学校”“开发者”“输入法”“西安”，以及未要求整句预存的“我在图书馆学习”“今天我想去北京”。本机单次 release 验收中，词库加载为 389.42 ms；114 次逐键转换中位数 0.050 ms、p95 1.171 ms、最大 1.605 ms。进程记录的 RSS 为 62,744 kB，峰值为 83,232 kB。这些是词库和转换测试进程的测量，不包括图形渲染、应用上屏或所有桌面服务，也不是跨机器性能保证。

学习默认关闭。本轮没有读取其他输入法的个人词库或输入历史。主动启用后，仅保存已选择的编码、候选和计数，使用独立用户文件、进程锁和原子写入。拼音组合仍是词频与分词方法，不具有完整神经语言模型的消歧能力。

## 当前机器的使用方式

查看实际安装与配置：

```sh
~/.local/bin/ztype features
~/.local/bin/ztype config
~/.local/bin/ztype convert --text shurufa
```

准备切换时，先退出 Fcitx5，待其释放输入端点后启动 Ztype。现有 Ztty 进程使用 `@im=fcitx`，可以让 Ztype 提供这个可配置的 XIM 名称，已用真实终端验证晚连接和重连：

```sh
fcitx5-remote -e
ZTYPE_XIM_NAME=fcitx ~/.local/bin/ztype start
~/.local/bin/ztype status
```

以上切换步骤没有在交付过程中执行。新启动的 X11 应用也可以使用默认的 `XMODIFIERS=@im=ztype`，前提是服务使用相同端点名称。修改配置后执行 `ztype restart`。要回到 Fcitx5，先 `ztype stop`，再启动 Fcitx5。

默认配置为 `~/.config/ztype/config`，词库为 `~/.local/share/ztype/dictionaries/rime-ice.tsv`；自定义 XDG 路径同样支持。默认五候选一页，Space 选词、Enter 发送原始拼音、PageUp/PageDown 或拼音模式下的 `-` / `=` 翻页，Ctrl+Space 切换中英文。更多配置见 [中文 README](https://github.com/LIghtJUNction/ztype/blob/main/README.zh-CN.md)。

## 桌面兼容与保留的边界

输入法分成三层：桌面协议把按键交给框架，语言引擎把编码变成候选，框架再把选择后的文字提交给应用。发行版决定系统库和安装方式，桌面环境及应用决定输入协议。因此“不绑定某个发行版”和“支持所有桌面”不能合并成一个承诺。

| 环境 | 本轮结论 |
| --- | --- |
| Linux X11 / XWayland | XIM 路径已实现；真实 Ztty 在私有 X11 会话验收通过，用户活跃 XWayland 会话未被替换 |
| Hyprland 原生 Wayland | input-method-v2 / virtual-keyboard-v1 路径已实测 |
| 其他提供兼容协议的 Wayland 合成器 | 协议设计可复用，仍需逐桌面验收 |
| KDE Plasma 原生 Wayland | 当前未实现 input-method-v1 适配，不能声称全面支持 |
| GNOME 原生 Wayland | 当前未实现 GNOME/IBus 集成，不能声称全面支持 |
| Windows / macOS | TSF / InputMethodKit 输入法前端尚未实现 |

协议依据为 [KDE Plasma Keyboard 官方说明](https://github.com/KDE/plasma-keyboard)和 [IBus 的 Wayland 桌面说明](https://github.com/ibus/ibus/wiki/WaylandDesktop)。它们解释了不同桌面需要不同适配层，不能由 Hyprland 的通过结果外推。

另有一个明确保留的客户端边界：本轮 GTK3 接收程序直接改写字段，即使调用 `gtk_entry_reset_im_context`，也未向 Wayland 发送新的 surrounding-text 或 change-cause 通知。输入法看不到这次内部变动，因此旧预编辑无法自动失效。原失败和双侧协议日志保留，诊断结果标为 `unsupported/client-notification-unavailable`、`passed=false`，没有伪装成通过。`--probe-external-edit` 仍是显式、失败会返回非零的诊断项。框架收到 OTHER 通知后的失效行为由协议状态单元测试覆盖，不把它称为 GTK 端到端通过。

Project Z 聚合源码图也保留了明确边界。检查时总仓 main 为 `0d638519117e375410743e61013114a6187b7f96`，canonical zgpui 为 0.1.2，新 Ztype 固定依赖 zgpui 0.1.5/raster；Ztty 及套件中的 zinput 来源也仍需同一轮来源更新。只更新两个顶层 gitlink 无法完成全图一致性。本轮完成两个独立仓库及本机安装，没有强行升级其他 UI 消费者，也没有声称聚合 `sync/check/resolve --all` 已通过。具体快照见 [框架验收与来源报告](https://github.com/LIghtJUNction/zinput/blob/003829a5e7ea45d9827376d89e37c9e6cfbf4a12/docs/reports/20261120/README.md)。

## 可复核材料

- [应用测试、安装、CLI、词库及候选截图证据目录](https://github.com/LIghtJUNction/ztype/tree/main/docs/evidence/daily-2026-11-20)
- [最终敏感焦点与 GTK 通知边界的结构化记录](https://github.com/LIghtJUNction/ztype/blob/main/docs/validation/native-focus-20261120.json)
- [Zinput 最终 Ztty 与框架验收报告](https://github.com/LIghtJUNction/zinput/blob/003829a5e7ea45d9827376d89e37c9e6cfbf4a12/docs/reports/20261120/README.md)

主要复现命令：

```sh
cargo test --release --all-targets --locked
cargo clippy --release --all-targets --locked -- -D warnings
python3 scripts/test_install.py --binary target/release/ztype
python3 scripts/test_daily_input.py --binary target/release/ztype

# 在具备私有嵌套显示依赖的开发环境中
python3 scripts/test_wayland_nested.py \
  --binary target/release/ztype --headless --popup \
  --focus-race-rounds 20 --output .build/focus-proof
```
