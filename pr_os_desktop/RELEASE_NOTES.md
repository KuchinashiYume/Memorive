# Memorive v1.01 · build139

面向个人学习与研究的 Windows x64 桌面文献工作台。此源码版本保留已经确认的 build139 程序；公开文件清理以 SHA256 区分，不新增 build 编号。

## AI 开发说明

Memorive 是由 AI 编程工具开发的项目。本项目自身的代码由 **OpenAI Codex 与 Anthropic Claude Code** 生成、修改和迭代；项目发起者负责提出需求、产品设计与测试验收，**未亲自编写代码**。第三方组件仍归其各自作者，详见第三方声明。

## 本次内容

- 文献处理、研究问答与引用查证、会话管理、网页读取、模型配置与用量记录。
- 中文、英文、日文界面及使用／设计文档。
- 安装向导在系统检查步骤提示缺少的组件，只提供对应的下载入口。
- 设置 → 关于与版本提供三语言许可与隐私说明的 HTML / Markdown。
- 修复主界面白屏：更新实际内联脚本的 CSP 哈希，并增加构建校验。
- 分发文件移除 Chroma 自带的测试源文件目录及开发阶段的文献兼容／截图检查结果。程序和安装引擎二进制沿用 build139。

## 已知限制

- Windows 10 22H2／Windows 11 x64；需要 .NET Framework 4.8、WebView2 Runtime、Visual C++ v14 x64。安装引导提供 Microsoft 官方链接。
- 程序和安装器未签名。SHA256 用于核对文件一致性，不代替数字签名。
- 模型、CLI 订阅及本地模型由使用者自行准备。模型回答和引用需要核对，费用记录不能替代服务商账单。
- 旧字体 PDF 仅在支持的字体和字形范围内恢复；未知或歧义字形会被拒绝。
- 已有业务验收和作者确认按其实际范围保留；不声称所有硬件、文献和模型服务都通过测试。
- 自有代码 AGPL-3.0-only；角色素材单独声明。详细范围见 [LICENSING.md](LICENSING.md) 和 [ASSET_NOTICE.md](ASSET_NOTICE.md)。

本版本已于 2026-09-26 公开发布：[下载 v1.01 · build139](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.01-build139)。
