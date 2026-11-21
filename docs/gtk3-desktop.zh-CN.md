# GTK3 应用接入与桌面控制设计

## 本次可用的入口

在已经运行 Ztype Wayland 前端、支持 text-input-v3 的会话中，可以为单个新 GTK3 进程指定原生输入路径。

```sh
ztype gtk3-wayland mousepad --disable-server notes.txt
```

该入口在应用进程中设置 `GDK_BACKEND=wayland` 和 `GTK_IM_MODULE=wayland`，直接传递程序及其参数。Mousepad 的 `--disable-server` 保证新窗口使用这些环境变量。启动器不安装或启动服务。普通 Wayland 显示连接会先检查输入法所需的座席、input-method-v2 和 virtual-keyboard-v1。遇到不支持的组合会返回错误。继承的专用 `WAYLAND_SOCKET` 由 GTK 独占，启动器会直接传递它。应用是否使用 text-input，以及是否发送敏感字段和外部修改通知，仍取决于客户端。

## 当前互通证据

云端对照使用同一个锁定依赖构建的 Ztype 0.4.2、Sway 1.10.1 无头输出和 Mousepad 0.6.3（GTK 3.24.49）。Wayland 路径核对每次输入前的窗口 PID 与 xdg-shell，XWayland 路径使用带独立认证的 X 服务器。接收结果取自编辑器保存的 UTF-8 文件。全部词条和字段内容均为测试数据。[源码、二进制哈希与逐项读回](validation/gtk3-interop-20261121.json)

| 场景 | 观察结果 |
| --- | --- |
| 原生 Wayland，继承 GTK simple 模块 | `nihao`、空格、回车保存为 `6e6968616f200a`。 |
| 相同模块环境，经新入口启动 | 窗口 A 显示候选后转到 B，A 的候选隐藏。B 输入 `ta` 并按 2、回车，只保存“她”与一个换行（`e5a5b90a`）。回到 A 后只保存一个换行（`0a`）。 |
| 私有前端停止，修改配置并重新启动 | 配置读回 `initial_mode=english`，新进程发送 `READY=1`。同一编辑器先保存英文 `nihao `，按 Ctrl+Space 切换后保存“你好”，各有一个换行。 |
| 原生 Wayland，GTK3 假密码框 | 合成器及输入法让该控件直接收到原始 ASCII，未观察到非空预编辑。 |
| 原生 Wayland，GTK3 在预编辑期间调用 reset_im_context 并替换文本 | 此 GTK3/Sway 组合实际发送 surrounding-text 和 change-cause。字段保留替换值，候选隐藏，焦点代次改变。此前缺少通知的组合仍属未支持。 |
| Weston 14.0.2 无头配置 | 探针读到 seat、input-method-v2 和 virtual-keyboard-v1 均不可用，诊断与启动器都返回 1，目标程序未执行。 |
| XWayland，Mousepad 仅设置 XMODIFIERS | 此环境保存拼音与空格。进程显式选择 GTK 的 xim 模块后，普通文本选词及跨窗口取消通过。 |
| XWayland，GTK3 假密码框，显式选择 xim | 假拼音被转成中文，密码旁路不兼容。XIM 没有标准的输入用途提示。 |

实际编辑器回归可用 `python3 scripts/test_gtk3_wayland.py --binary PATH` 重跑。需要带 wayland-popup 的构建、Sway、Mousepad、C 编译器、wayland-scanner 和 Wayland/XKB 开发库。脚本自建无头会话，在私有目录启动、停止和重启前端；不连接用户服务管理器。此轮没有验证 Hyprland、Chromium、Electron 或 ztty 的新版本兼容性。既有的客户端通知缺口与 ztty 行内预编辑位置分别保留原验收范围。[既有 GTK 记录](daily-input-2026-11-20.zh-CN.md) · [ztty 0.4.2 记录](ztty-ui-0.4.2.zh-CN.md)

## 独立状态入口和设置页设计（待实现）

建议提供独立的 `ztype indicator` 和 `ztype settings`。indicator 在用户会话总线上注册 StatusNotifierItem，左键打开设置，菜单显示 Wayland 与 XIM 的状态和用户明确选择的操作。提供可直接启动设置的桌面菜单项，便于没有托盘宿主的会话使用。打开任一入口只读取状态，停止输入法后仍可查看失败原因和配置。[StatusNotifierItem 规范](https://specifications.freedesktop.org/status-notifier-item/latest-single/)

| 控件 | 实际状态来源和写入方式 |
| --- | --- |
| 服务 | 将 `src/service.rs` 的查询结果暴露为结构化记录，显示每个 unit 的 LoadState、ActiveState、SubState、MainPID、二进制身份和退出原因。启动、停止、重启走现有控制层，动作后重新查询。端点注册以新进程的 readiness 为准；托盘分别显示运行、停止、失败或管理器不可用。 |
| 当前中英文模式 | 由各前端从 `InputMethod::enabled()` 提供实时状态与变更通知。新增按当前用户和前端实例区分的运行时控制通道，以相同的取消预编辑逻辑实现目标模式设置和 Ctrl+Space。界面按前端列出回执，连接中断时显示未知；单独显示配置里的启动模式。消息只含模式和实例标识。 |
| 快捷键 | 初版显示当前生效的 Ctrl+Space、数字选词和翻页键。可编辑版本先在共享键处理层增加配置和冲突校验，再开放保存；修改后用真实输入验证新绑定，并验证旧绑定恢复为应用按键。 |
| 词库与偏好 | 复用 `src/config.rs` 的解析、路径解析及自动发现顺序，显示实际配置路径、词库加载顺序、学习开关和用户词库。保存时保留注释及重复的 dictionary 项，在同目录校验临时文件后原子替换，再读回解析值。词库错误定位到具体文件，不显示输入历史。 |
| 生效与重启 | 保存后区分“已保存”和“运行中已使用”。用户选择对运行服务应用时，调用现有 restart 流程，等待 readiness 并核对新 PID、程序身份及运行配置版本。保持原先停止的前端处于停止状态；现有 CLI 会按会话选取多个 unit，UI 实现前需给控制层提供明确的目标集合。 |

验收应包含托盘宿主重新出现，已停止服务打开设置，双前端中只有一个运行，启动失败，重启失败并保留诊断，以及改词库后由真实编辑器选到新词。当前私有前端已验证配置读回、进程退出、新进程就绪和切换后落盘；托盘交互、运行时模式通道及 systemd 用户 unit 的桌面联测仍待实现和验证。
