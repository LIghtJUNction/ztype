# Ztype 0.2.0：原生 Wayland 输入、安装与实测

日期：2026-11-20（Asia/Taipei）

**本轮已经把 Ztype 作为独立 Wayland 输入法安装到开发机，并验证了原生 GTK 输入框和 foot 终端的实际文本。** 安装后的同一个程序通过 14 项 Wayland 检查、14 项 CLI 检查和 11 项隔离 X11/GTK 检查；foot 的真实标准输入收到 `你好世界\n`。测试前后的程序 SHA-256 相同。[安装证据](evidence/native-wayland-20261120/installation.json) · [Wayland 总记录](evidence/native-wayland-20261120/wayland-nested.json) · [终端接收记录](evidence/native-wayland-20261120/terminal-stdin.json)

## 这一轮实现了什么

输入路径分成三个职责明确的部分：框架从桌面接收按键并维护焦点，语言引擎把编码变成候选，应用决定何时修改预编辑、切换候选或提交。只有确认后的文字才进入目标应用的正文。

| 部分 | 本轮变化 |
| --- | --- |
| `zinput` | 保留 0.1.3 核心 API、默认 feature 与无操作系统依赖的构建；核心 `src/` 没有变化 |
| `zinput-wayland` | 新增独立适配器，处理 input-method-v2 注册、virtual-keyboard-v1 转发、xkb 布局、焦点代次、预编辑、提交与敏感字段 |
| `ztype` | 接入原生服务、逐键组合控制器、模式切换、原位候选、按功能安装和复现脚本 |
| 拼音引擎 | 在已有小词表上组合完整已知词，例如 `nihaoshijie → 你好世界`、`zhongwenshurufa → 中文输入法`；未知尾缀保持原文 |

原生服务支持拼音、日语假名和用户码表。当前候选显示在目标输入框的预编辑位置，尚无独立 Wayland 候选弹窗。Unicode、原文和动态插件继续通过面板或 CLI 使用。拼音组合采用有界的已知词分段，当前不提供大词库、学习排序或神经网络整句预测。[实现源码](https://github.com/LIghtJUNction/ztype/commit/b69be3c6d0b6197a4c5d37a89e211dc3de1f48fd) · [框架源码](https://github.com/LIghtJUNction/zinput/commit/4c7ffce564d2dae0875b0b632bef59febc2d1476)

## 已安装的内容与当前状态

| 项目 | 位置或状态 |
| --- | --- |
| 完整应用 | `~/.local/bin/ztype`，版本 0.2.0 |
| 面板入口 | `~/.local/share/applications/ztype.desktop` |
| 用户服务 | `~/.local/share/systemd/user/ztype-wayland.service` |
| 本轮结束时服务状态 | `loaded / disabled / inactive` |
| 日常桌面输入 | 原有 `fcitx5.service` 保持 active |
| 实际原生验证 | 使用安装文件在独立 Hyprland 会话中运行，验收结束后清理自建进程 |

安装器不会自动启动或启用服务；也不会让已有 active 服务自动变成 inactive。本次记录中的 inactive/disabled 是实际回读状态。原有桌面输入保留，因为本轮尚未覆盖全部 XWayland、Qt、浏览器和编辑器场景。[安装回读](evidence/native-wayland-20261120/installation.json)

```sh
~/.local/bin/ztype --version
~/.local/bin/ztype features
~/.local/bin/ztype wayland-doctor
~/.local/bin/ztype panel
```

原生服务要求桌面提供 input-method-v2、virtual-keyboard-v1 和 seat，目标应用接入 Wayland text-input。当前输入法正常退出并释放 seat 后，可运行：

```sh
~/.local/bin/ztype wayland
# 或交给用户服务管理器
systemctl --user start ztype-wayland.service
systemctl --user stop ztype-wayland.service
```

确认日常应用覆盖和桌面环境导入后，可另行启用 `systemctl --user enable ztype-wayland.service`。同一 seat 已有输入法时，Ztype 返回占用错误。

从源码安装：

```sh
git clone --recurse-submodules https://github.com/LIghtJUNction/ztype.git
cd ztype
cargo build --release --locked
python3 scripts/install.py --binary target/release/ztype
```

安装器根据 `ztype features` 的 JSON 生成入口。纯 CLI 只安装程序；service-only 构建安装程序与服务；完整构建再加入面板入口。精简版本若会使已有入口失效，会在写入前拒绝覆盖。三种安装与三种不兼容覆盖均已实际验证。[安装变体验收](evidence/native-wayland-20261120/installer-variants.json)

## 操作方式

| 输入或按键 | 行为 |
| --- | --- |
| 拼音字母 | 在当前输入框显示未确认候选 |
| Space / Enter | 确认候选；没有组合时交给应用 |
| Tab / Shift+Tab | 下一候选 / 上一候选 |
| 1–9 | 拼音、假名选词；码表保留数字作为编码 |
| Backspace / Escape | 删除一个输入字符 / 取消组合 |
| Ctrl+Space | 切换组合输入与直接输入 |
| Ctrl、Alt、Super 快捷键 | 取消未确认组合，再交给应用 |
| 窗口焦点改变 | 清理旧上下文中的未确认组合 |
| 密码、PIN、敏感字段 | 不进行语言组合，按原始键盘路径处理 |

Wayland 前端为预编辑和提交设置 4,000 UTF-8 字节上限；超出容量时保留组合供继续修改。面板和 CLI 的引擎上限仍为 4,096 字节。假名逐键保留长音和标点，例如 `ko-hi-,nihongo. → こーひー、にほんご。`。码表支持数字、下划线和连字符编码。[控制器与回归用例](https://github.com/LIghtJUNction/ztype/blob/b69be3c6d0b6197a4c5d37a89e211dc3de1f48fd/src/input_method.rs)

## 实测结果与证据

环境为 Omarchy Linux x86_64、内核 7.3.4-arch1-1、Rust 1.100.0、Hyprland 0.57.2。原生验收使用单独的 Wayland socket、Hyprland 实例与会话总线，不停止宿主 Fcitx，不向宿主注入测试按键。[环境](evidence/native-wayland-20261120/environment.json) · [隔离与清理记录](evidence/native-wayland-20261120/wayland-nested.json)

| 验证层 | 结果 | 证据 |
| --- | --- | --- |
| Rust 应用测试 | 33 通过：14 控制器、16 组合、3 插件契约 | [完整输出](evidence/native-wayland-20261120/rust-tests.log) |
| Clippy | 最终版本 release/all-targets，warnings 为错误，通过 | [输出](evidence/native-wayland-20261120/clippy-release.log) |
| 安装后 CLI | 14 项通过 | [输出](evidence/native-wayland-20261120/installed-cli.log) |
| 安装后 X11/GTK | 11 项通过 | [接收文本](evidence/native-wayland-20261120/x11-gtk.json) |
| 安装后原生 Wayland/GTK | 14 项通过 | [逐项记录](evidence/native-wayland-20261120/wayland-gtk.json) |
| 安装后 foot | tty 的实际 stdin 收到“你好世界”及换行 | [终端记录](evidence/native-wayland-20261120/wayland-terminal.json) |
| 安装与功能变体 | 3 种安装、3 种降级拒绝均通过 | [逐项记录](evidence/native-wayland-20261120/installer-variants.json) |

33 项 Rust 测试完成后，最终只增加了编译功能 JSON 与安装覆盖检查，组合和控制器实现没有再改动；随后重新构建、运行 Clippy，并用最终安装文件完成 CLI、X11、Wayland 和终端验证。[证据范围说明](evidence/native-wayland-20261120/README.md)

Wayland 的 14 项检查覆盖：原生接收窗口、确认前仅有 preedit、你好提交、退格、Esc、你好世界组合、Tab、数字选词、密码框 ASCII 直通、焦点改变、Ctrl+A 全选替换、切入英文、切回中文和只读保护。读取的是 GTK 保存的实际文字与选区状态。foot 则通过私有 Python 接收器的 `sys.stdin.readline()` 获取文本；发送端仅发拼音 ASCII、Space、Return，没有直接向目标写入中文。

验收输入器保持固定完整 US 键盘布局，由独立的系统 libwayland/libxkbcommon 客户端实现，未复用 Ztype/Zinput。早期逐次使用 wtype 的单键映射会影响 GTK 快捷键绑定；无组合文字的对照也能复现，换成完整固定布局后，同一个未修改的产品二进制通过原有检查。因此没有为这一测试工具问题改动产品事件顺序。

原始截图：[GTK 原位预编辑](evidence-archive-20261120.md) · [foot 实际接收](evidence-archive-20261120.md)。截图右上的红色提示来自直接启动独立 Hyprland 时的启动器告警；配置检查为 `config ok`。原图未作修饰，详细范围见证据说明。

### 程序大小与短时资源观测

最终完整可执行文件为 **863,336 字节，约 843.1 KiB**。采样对象仅为安装后的 `ztype wayland` 进程。

| 状态 | 样本数 | RSS | PSS |
| --- | ---: | ---: | ---: |
| 空闲 | 3 | 5,136 KiB | 1,872 KiB |
| 正在组合 | 3 | 6,064 KiB | 2,372 KiB |

三个样本在各自状态内数值相同。RSS 是该进程驻留的内存，PSS 按比例计算共享页面；这些数值不包含桌面合成器或接收应用，也不是长期内存上限。此次没有测量端到端延迟或与其他输入法比较性能。[原始资源记录](evidence/native-wayland-20261120/resources.json)

安装文件 SHA-256：

```text
49c5d11e2a3e967540c674ee0e218e426aff2b8a1769c97fc7f711b8412a8755
```

## 如何复现原生验收

```sh
python3 scripts/test_wayland_nested.py \
  --binary ~/.local/bin/ztype \
  --output .build/wayland-nested-proof \
  --terminal \
  --screenshot
```

需要已有 Wayland 桌面、Hyprland 0.57+、hyprctl、dbus-run-session、Python 3.11+、cc、pkg-config、wayland-scanner，以及 GTK 3、Wayland client、libxkbcommon 的开发文件和 Linux 输入键码头文件。终端检查需要 foot，截图需要 grim。脚本不依赖 wtype，测试焦点丢失后停止发送。

源码提交分别为：[应用 b69be3c](https://github.com/LIghtJUNction/ztype/commit/b69be3c6d0b6197a4c5d37a89e211dc3de1f48fd)、[框架 4c7ffce](https://github.com/LIghtJUNction/zinput/commit/4c7ffce564d2dae0875b0b632bef59febc2d1476)、[拼音组合 887b7ed](https://github.com/LIghtJUNction/ztype/commit/887b7edadb24c3f82d8abacc20dd3f58771729db)。逐文件摘要见 [manifest.json](evidence/native-wayland-20261120/manifest.json)。

## Project Z 集成状态

独立协调分支已经固定本轮应用和框架源码，通过 41 个仓库、61 个包的一致性检查，以及 36 个 Rust 仓库的 `resolve --all`。两份嵌套源码分别与 canonical zinput、zgpui 的精确提交一致。zinput 核心 `src/` 的 Git tree 保持相同，裸机目标的 no-default-features 编译检查通过。[源码快照与门禁](evidence/native-wayland-20261120/project-z-snapshot.json)

[协调提交 5d775f3](https://github.com/LIghtJUNction/project-z/commit/5d775f3fa356159e88be0ce078cd4689bf44a43b) 位于 `coord/ztype-wayland-20261120`，继承原有已发布的 UI 集成分支。它尚未合入 project-z/main，合并顺序依赖先接纳该 UI 集成；zinput/main 与 ztype/main 的本轮源码已经推送。此处的聚合依赖解析不等同于重新运行所有消费者的功能测试。

## 当前边界与后续优先级

本轮证明了该环境下独立原生输入链路可用。GNOME/KDE、Qt、浏览器、编辑器、不同键盘布局、休眠恢复、多 seat，以及同一输入框内由鼠标或应用外部修改文字的完整交互仍需要单独验证；框架尚未把 `text_change_cause` 的外部编辑原因转换为组合失效事件。

下一轮优先增加独立候选弹窗与更多实际应用覆盖，再完善 XIM、词库和用户排序。Windows TSF、macOS InputMethodKit、日语汉字转换、语音和大词库均不在本轮完成范围。当前仍保留原来的日常输入法，不把一次原生 GTK/终端验收当成全面替换 WowType/Fcitx 的证明。
