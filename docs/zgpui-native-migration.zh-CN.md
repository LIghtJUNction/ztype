# 统一原生 UI 依赖

ztype 的候选面板改为从 `zgpui::native` 导入窗口、事件和绘制类型。
`gui` feature 启用可选依赖 `zgpui =0.1.1`，并显式选择库的
`native` feature。源码由 `vendor/zgpui` 子模块固定到提交；
应用不再使用旧包名的依赖别名。

这次迁移保留 zuse-computer 0.2.2、zinput 0.1.3 和 zplugin 0.3.1。
窗口操作和文本送达继续由既有共享库负责；本次没有在应用里增加操作系统 FFI。

## 验收范围

本次验收覆盖严格 Clippy、Rust 测试、应用与原生插件构建、CLI 检查，
以及隔离 Xvfb/GTK 环境里的真实文本接收。无默认 feature 的测试用于确认
命令行组合能力不依赖原生 UI。

[既有验收记录](verification.md)中的版本、提交和平台结果保持其历史范围。
本次不会把已有 Windows/macOS 结果当作新依赖的原生执行证据。本次实际命令和结果如下。

## Linux 验收（2026-11-20）

本次从 ztype 提交 `12e8a458d69b222a001a9e479823b65da840db6b` 开始，
固定 zgpui [`eb8dbbe7d9a44abd9a8683dfa0588200a0595edb`](https://github.com/LIghtJUNction/zgpui/commit/eb8dbbe7d9a44abd9a8683dfa0588200a0595edb)。
[机器可读记录](evidence/zgpui-native-validation.json)保存了每条命令、退出码和
GTK 实际接收结果。除了 UI 库改名及版本变化，其余 14 个注册表包的锁记录
（包括来源和校验和）均与基线一致。

| 验收边界 | 结果 |
| --- | --- |
| Rustfmt 和禁止警告的全目标 Clippy | 通过 |
| 默认 feature 的 Rust 测试 | 13 项通过 |
| 应用与 kana 原生插件 | 构建通过 |
| CLI 行为 | 14 项通过 |
| GTK 原生接收与候选面板 | 11 项通过，最终接收 96 字节 UTF-8 |
| 无默认 feature 的组合状态测试 | 10 项通过 |

原生检查包含多语文字、原生插件、按住 Enter 时提交 ASCII、翻页选词、
复制、无效输入防止陈旧提交，以及只读目标拒绝。实际窗口最小尺寸保持
520 × 112，WM_CLASS 为 `ztype` / `Ztype`。新依赖的 Windows/macOS
原生执行尚未验证，历史跨平台记录不能替代这一门禁。

## 最终发行源码

最终子模块固定到 [`cb50f2fe554622fdc984ae7fec9bcd844f175d67`](https://github.com/LIghtJUNction/zgpui/commit/cb50f2fe554622fdc984ae7fec9bcd844f175d67)。
它的 `src/`、`native/`、`build.rs` 和 `Cargo.lock` 与上面实际验收的
运行时逐字节相同；manifest 只扩充了打包文件清单。
[源码比对记录](evidence/zgpui-release-source.json)保留实际验收提交与最终发行源码
的区别。最终源码归档仍需包含新的子模块路径和完整原生源码。
