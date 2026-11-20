# ztype 0.4.2：候选界面与 ztty 输入验收

本轮把原生候选界面接到当前 Git 依赖基线，并分别验证 X11/XIM 与原生 Wayland 的 ztty 输入。运行时代码固定于 `053eb39eb115decc0cb046e901598cf0bc4645fb`；后续测试与文档提交不改变该提交的 Rust 源码及 Cargo 文件。完整文件哈希、二进制哈希和分阶段结果见[机器可读验收记录](evidence/ztty-ui-0.4.2-20261121/summary.json)。

## 界面变化

候选正文为 16 px，页内序号为 12 px，预编辑标题为 14 px；面板左右留白为 20 px，候选行高为 40 px。窗口宽度仍限制在 248–640 px，长候选只在显示时省略，提交文本保持完整。选中项同时使用浅蓝背景和加粗正文，数字选词、分页及焦点处理沿用原有行为。

浅色主题采用 Material 3 的语义颜色角色：表面 `#F8FAFD`、正文 `#1F1F1F`、次要文字 `#5F6368`、选中表面 `#D3E3FD`、选中文字 `#041E49`。四组实际文字配色的对比度分别为 15.764、5.786、12.568 和 4.919，均高于小字号文字的 4.5:1 目标。候选像素保持完全不透明，适配 X11 depth-24 与 Wayland surface。

依据为 Google 的 [Material 3 主题、颜色和字体说明](https://developer.android.com/develop/ui/compose/designsystems/material3)及[文字对比度指导](https://developer.android.com/guide/topics/ui/accessibility/apps)。具体字号、留白和配色是本项目对原生桌面输入法的选择。本轮覆盖浅色主题、1 倍缩放及记录中的实际画面，不能据此推断所有缩放比例、暗色主题或其他平台均已验收。

## ztty 首字丢失的原因与修复

原生 Wayland 中，连续输入 `nihaoshijie` 曾得到 `ihaoshijie`。协议日志显示，首个 `n` 已产生预编辑“你”，随后收到 `text_change_cause(OTHER)` 和 `done`，组合被清空；余下字母重新组成并作为原文提交。该问题发生在 IME 形成提交文本之前。

终端更新光标矩形只是在报告位置。修复在即时光标更新和等待确认后的延迟更新中，显式使用 `INPUT_METHOD` 作为这类批次的原因，避免已有 `OTHER` 标记再次使输入法丢弃组合。首次启用、真实外部编辑、焦点切换、serial/ack 和 UTF-8 提交逻辑保持原来的语义。

[Wayland text-input-v3 协议](https://cgit.freedesktop.org/wayland/wayland-protocols/tree/unstable/text-input/text-input-unstable-v3.xml)规定 change cause 在每次 commit 后恢复默认值。这里记录的是对实际组合中观察到的 OTHER 重用所作的客户端兼容处理；没有把所有合成器归为同一种行为。修复源码与实际使用的 ztty ELF 身份保留在验收记录中。

修复由 [ztty PR #23](https://github.com/LIghtJUNction/ztty/pull/23) 提供，实际验收源码为 `076bc6ac8ff9ca17effe25decac7a4dbc4ec0c43`。同一连续输入在修复前的两份二进制上均复现首字丢失；修复后的真实 PTY 收到“你好世界”，UTF-8 为 `e4bda0e5a5bde4b896e7958c0a`（末尾为换行）。最终使用的 ztty ELF SHA-256 为 `c5debb5f35d2563e1a3cdf7eeb18bb064439c864ad020fd452f0c730afc61197`。

## 验收范围

| 检查 | 结果 |
| --- | --- |
| Rust 全目标测试 | 72 通过；1 项需单独安装完整词库的测试按原规则跳过 |
| Clippy 与 release 构建 | 通过；记录了实际根 crate 的 clippy-driver 调用和 fresh=false 的编译产物 |
| 日常输入检查 | 8 通过，服务管理器使用测试替身 |
| 安装器测试 | 15 通过 |
| X11/XIM 到 ztty | 13/13 通过，使用修复后的同一 ztty ELF |
| Wayland GTK 原生输入与候选 | 28 通过 |
| 原生 Wayland ztty | 7/7 通过；原生 Wayland 窗口、真实 PTY 收到“你好世界” |
| 候选窗界面检查 | 真实 X11、Wayland 截图；候选文字、编号、选中态和不透明边缘已检查 |

XIM 验收通过独立 Xvfb、XTEST 和真实 PTY，包含后启动输入服务、多窗口焦点、数字选择、候选窗显示/关闭、服务停止时普通按键及重启重连。ztty 的 XIM 客户端未提供字符 spot 时使用窗口边缘回退位置，本轮不宣称该路径跟随终端内的字符光标。

Wayland 验收使用独立 Weston headless → Hyprland、私有 socket 和虚拟键盘。ztty 窗口通过 broker 的 Unix peer credential、可执行文件和进程身份绑定，再核对实际 Wayland 窗口、候选状态与 PTY 收据。输入仍为连续的 `nihaoshijie → Space → Return`，没有添加延时来改变通过条件。测试结束后核对本轮进程清理和二进制哈希。修复前后两份收据的第三个脚本条目覆盖不同；共同的 nested runner 与 ztty helper 哈希完全相同，命令仅改变输出目录和 ztty 二进制路径。具体差异见[收据补充说明](evidence/ztty-ui-0.4.2-20261121/receipt-script-coverage.json)，原收据保持原样。

原生 Wayland ztty 的提交前截图中，终端自己的行内预编辑曾覆盖提示行开头；提交后提示行恢复，中文出现在下一行。测试接收器的就绪标记与终端绘制之间仍存在时序差异，本轮未确定这个视觉偏移的原因。真实 PTY 提交和候选窗检查通过，终端行内预编辑的视觉定位保留为待核对项。

测试合成器复用了固定哈希的私有 Aquamarine 兼容库，仅用于子进程；系统库保持原样。当前桌面的 Fcitx5、输入法配置及已安装 ztype 不在本轮切换范围内。PNG 和 ELF 留作本地验收产物，Git 只保存源码、文字说明及哈希记录。

## 复现

先准备带 Wayland 支持的 ztty 和本轮 ztype 二进制，在具备测试工具的 Linux 环境运行：

```sh
python3 scripts/test_wayland_nested.py \
  --binary "$ZTYPE_BINARY" \
  --ztty-binary "$ZTTY_BINARY" \
  --output "$ZTYPE_PROOF" \
  --headless --popup --screenshot --trace-events \
  --aquamarine-library "$ZTYPE_TEST_AQUAMARINE"
```

测试输出包括整体报告、真实终端收据、协议日志和候选截图。日常安装与输入法服务配置仍见[安装与服务](service-installation.md)。
