# zuse 0.2.2 消费验收

日期：2026-11-20（Asia/Taipei）。

**ztype 已将文字送达依赖固定为正式发布的 zuse-computer 0.2.2。** 本轮使用注册表下载的真实包完成 Linux 应用、插件和接收窗口验证，并编译检查 macOS 的 desktop、plugins 功能。完整 macOS GUI 构建因本机缺少 Objective-C 编译组件而未完成，没有记录为通过。

## 来源与改动

应用以 [91edc97](https://github.com/LIghtJUNction/ztype/commit/91edc97aab1bea18f15f56e1ee2ec0651cc85916) 为基线，只更新 zuse 的精确约束与 Cargo.lock 包记录；其他 15 个锁定包保持不变。窗口聚焦修复由共享库提供，应用继续使用既有送达接口。

| 对象 | 已验证身份 |
| --- | --- |
| zuse 正式源码 | [94c837551a7a2fd8ec9f577a3e2f57309bd5ce5a](https://github.com/LIghtJUNction/zuse/commit/94c837551a7a2fd8ec9f577a3e2f57309bd5ce5a) |
| 注册表版本 | [zuse-computer 0.2.2](https://crates.io/crates/zuse-computer/0.2.2)，核查时未 yanked |
| crate SHA-256 | `16d3bf8c73bf64721672bf0483899b796967c8d3b53fa0ea934b3e705d6e2fde` |
| 归档大小 | 387,761 字节 |
| UI 子模块 | `5e37fcfb791eec9ac523fc19373e95d0a513112f`，保持不变 |

官方 API、独立下载归档、Cargo 实际缓存归档与锁文件的 checksum 一致。归档的版本信息与 `.cargo_vcs_info.json` 对应正式提交，32 个源码文件逐字匹配；实际用于构建的 Cargo 缓存源码也逐项匹配。Cargo 元数据只有 ztype 与固定的 UI 子模块属于本地包，没有私有 zuse 路径替换。[来源及实际依赖记录](evidence/zuse-0.2.2-registry.json)

## 验证结果

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| Clippy all-targets，警告视为错误 | 通过 | Linux 当前消费者 |
| Rust all-targets 测试 | 13 通过，0 失败 | 10 项组合状态、3 项插件契约 |
| 应用与 kana 动态库构建 | 通过 | 实际可执行文件与原生插件 |
| CLI | 14/14 通过 | 转换、插件、错误输入和命令约束 |
| 私有 X11/GTK 接收端 | 11/11 通过 | 多语言、面板、held-Enter、选区/剪贴板、取消与只读拒绝 |
| macOS arm64 desktop + plugins | type-check 通过 | 实际功能代码；不包含 GUI，不是 macOS 执行 |
| macOS arm64 完整 GUI | 环境阻断 | 既有 zui-native 的 Objective-C 构建缺少 `cc1obj` |

GTK 验收读取控件保存的实际 UTF-8，最终接收 96 字节。显示、认证、session bus 与 accessibility bus 均属于夹具；没有操作日常桌面或输入设置。原生结果逐例保留预期文本、返回值与拒绝结果。[接收端记录](evidence/zuse-0.2.2-native.json)

完整 GUI 的第一次 macOS 检查失败后，保留了该结果，再以项目支持的 `desktop,plugins` 功能组合验证文字送达消费者。没有替换编译器输出、跳过 native build 脚本或把跨平台编译称为真机通过。全部命令、退出码、二进制摘要及范围记录在[消费验收 JSON](evidence/zuse-0.2.2-validation.json)；上游浏览器和 Android 修复的验证属于 zuse 自己的验收范围。

## 复现

在本提交的干净检出中初始化子模块，再运行：

```sh
git submodule update --init --recursive
cargo clippy --all-targets --locked -- -D warnings
cargo test --all-targets --locked
cargo build --bin ztype --example kana_plugin --locked
python3 scripts/test_cli.py --directory target/debug
python3 scripts/test_native.py --binary target/debug/ztype --plugin target/debug/examples/libkana_plugin.so
cargo check --all-targets --no-default-features --features desktop,plugins --target aarch64-apple-darwin --locked
```

本次执行使用 Rust 1.100.0、一个 Cargo job、一个 Rust 测试线程和 nice 19；没有沿用旧报告中的 cgroup 限额或性能测量。脚本使用的 Linux 图形工具与运行要求仍见 README。

## 证据自查

| 问题 | 结论与依据 |
| --- | --- |
| 是否实际消费新版本？ | manifest、lock、官方归档、Cargo 缓存和源码提交一致。 |
| 是否测了真实接收结果？ | 11 项 GTK 检查读取实际存储文本，不能只由 submitted 返回值判定成功。 |
| 测试数量是否重复相加？ | Rust、CLI、GTK 和编译检查分层列出，没有合成一个总通过数。 |
| 平台覆盖是否准确？ | Linux 实际执行；macOS 只编译 desktop/plugins，完整 GUI 和真实桌面未验证。 |
| 是否完成安装部署？ | 本记录覆盖源码依赖与消费者验证；没有修改用户安装或发布新的 ztype 发行包。 |
