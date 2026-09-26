# Memorive Test Console

[English](README.en.md) · [日本語](README.ja.md) · [返回 Memorive](../README.md)

Memorive 鼓励使用者根据自己的学习与研究需求修改、扩展和优化项目。配套控制台提供测试与诊断入口，帮助复现问题、检查功能行为，并验证修改后的结果。测试结果只覆盖所选版本、配置与场景，不代表全部功能和使用环境均已验证。

## 可以做什么

- 快速冒烟、一键安全回归和按类别选择的诊断场景。
- 模拟消息、任务事件、成功／失败流程，检查消息、通知与桌宠联动。
- 检查报告、导入与状态操作，预览桌宠及页面组件。
- 查看版本清单、可用操作、结构化回执和审核摘要。

控制台辅助验证代码修改。修改代码、审阅补丁和重新编译需要使用你自己的开发工具。**协议自测**使用内置参考端；连接 **Memorive.exe** 才是在测试相应本体。模拟结果不能替代真实文献、研究问答质量或模型性能测试。

## 安装与第一次使用

1. 在本项目 Releases 获取 `Memorive-Test-Console.exe`，或使用 `Memorive-Test-Console-Setup-1.01-x64.exe` 安装。保持本体完整目录与 `_internal` 内容；插件和控制台都是可选配套工具。
2. 使用 Windows x64，准备 Microsoft WebView2 Runtime。发布文件尚未数字签名；仓库私有期间，下载需要相应访问权限。
3. 先打开控制台选择「协议自测」，了解测试项目及回执；该模式不会连接你的正式工作区。
4. 将准备测试的 `Memorive.exe` 拖入「连接 memo」，或手动选择文件。程序会读取清单并计算实际文件 SHA256，建立单独的临时会话。
5. 在控制台启动的临时 Memorive 实例中，进入隐私设置，打开「允许接入控制台」，然后回到控制台重新检查。这个开关不能由控制台远程代开。
6. 先跑快速冒烟，查看每一项的成功、失败或待接入原因。需要进一步验证时，再选择对应的单项操作或测试集合。
7. 结束后选择「结束并清理」，或正常关闭窗口；确认清理状态。卸载保留诊断目录，便于自行查看与删除。

## 如何测试自己修改的 Memorive

按照[本体构建说明](../memorive_desktop/BUILDING.md)从源码生成完整程序目录。控制台不要求官方签名或固定的官方 EXE 哈希；身份清单检查用于确定接入格式，**不构成官方来源认证**。

接入要求：

- 入口名为 `Memorive.exe`，使用普通文件，拒绝快捷方式、符号链接和其他重解析入口。
- 同目录或 `_internal` 对应位置带 `release_identity_binding.json`，采用 `DesktopReleaseIdentityBinding-v1`；保留主程序名、`release_version`、`display_version`、发布通道、许可状态及验收状态等必需字段。本体构建工具会生成这些内容。
- 带 `memorive-console-capabilities.json`，采用 `Memorive-ConsoleBuildCapabilities-v1`、协议 `1.0`、`ENV_V1` 和 `EPHEMERAL_ONLY`。
- 实现 `/memorive/test-bridge/v1`，使用 `MEMORIVE_TEST_CONSOLE_*` 启动参数环境、会话令牌与 `X-Memorive-Session`。握手需对应所选 EXE 哈希及本次临时目录，并保持自动执行关闭。
- 接口功能以本体实际声明为准。当前本体没有 `research.qa.simulate`；研究问答样例仅在参考端可用，连接本体时会显示待接入。不要通过伪造能力清单来隐藏缺失实现。

协议结构见 [bridge.schema.json](source/bridge.schema.json)。通常修改业务代码不需要修改桥接协议；新增测试能力时应同步实现、声明及验证。

## 测试数据与模型费用

控制台按六个文件的白名单复制本机设置到临时空间，保持原配置只读；它不会自动复制你的文献、会话或整个资料库。导入资料时使用副本，建议选择可以公开的测试文件。

一键安全回归、消息和事件模拟不调用模型；需要模型的单项操作必须逐次授权，可能产生实际费用，并受本体配置与服务条款影响。模拟问答并不验证真实模型回答。

正常结束会话后清理临时业务内容和设置副本，保留用于排查的回执和摘要。异常退出可能需要重新打开控制台完成清理。向 Issues 提交结果前检查路径、标识、错误内容与截图，移除密钥、私人内容和完整工作区数据；无需公开整个诊断目录。

## 许可与开发方式

自有代码采用 [AGPL-3.0-only](LICENSE)，鼓励个人学习、研究及非商业使用，该倡议不限制许可证授予的权利。代码由 **OpenAI Codex** 与 **Anthropic Claude Code** 生成、修改和迭代，发起者负责需求、设计与验收。

图标为用户提供插画的处理副本，原图标注 Pixiv: GRADIUS、X: @Riza2201；原作者标注不等于公开分发授权。它不属于代码许可，也不能被称为项目自有 AI 原创素材。详细说明见 [NOTICE.txt](NOTICE.txt)；第三方组件见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 构建与验证

Windows x64、Python 3.13.14，在独立虚拟环境安装 `build_requirements.lock`。在本目录执行 `python -m PyInstaller --noconfirm --clean console.spec`，将 `dist/Memorive-Test-Console.exe` 复制到 `output/`，再使用 Inno Setup 6.7.3 执行 `ISCC.exe installer.iss`。Inno Setup 自身适用其许可，源码中不附带编译器。

源码测试：安装 pytest 8.3.5 后，在 `source/` 执行 `python -m pytest -q`。打包工具和依赖版本必须与锁文件匹配。测试生成物、个人设置、日志及编译目录不纳入源码或安装包。
