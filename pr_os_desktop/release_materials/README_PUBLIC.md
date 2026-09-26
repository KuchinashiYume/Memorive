# Memorive

[English](README_PUBLIC.en.md) · [日本語](README_PUBLIC.ja.md)

[项目主页](https://github.com/KuchinashiYume/Memorive) · [版本与下载](https://github.com/KuchinashiYume/Memorive/releases) · [问题反馈](https://github.com/KuchinashiYume/Memorive/issues)

Memorive 是面向个人学习与研究的桌面文献工作台，将资料整理、研究问答与来源查证放在同一个应用中。

## AI 开发说明

Memorive 是由 AI 编程工具开发的项目。本项目自身的代码由 **OpenAI Codex 与 Anthropic Claude Code** 生成、修改和迭代；项目发起者负责提出需求、产品设计与测试验收，**未亲自编写代码**。第三方组件仍归其各自作者，详见第三方声明。

## 可以做什么

- **整理资料**：导入文献和网页，查看处理进度，整理资料库。
- **研究问答**：围绕已有资料提问，结合引用与来源定位核对回答。
- **管理会话**：整理研究过程中的对话，保留可继续使用的工作记录。
- **连接模型**：按使用文档配置 API 服务、受支持的 CLI 或本地模型。
- **查看使用情况**：查看模型榜单、用量及费用记录；第三方价格与榜单可能变化。

模型回答和引用仍需使用者核对。费用记录依赖服务商与执行通道返回的信息，不能替代服务商账单。

## 安装与开始使用

适用系统：Windows 10 22H2 或更新版本、Windows 11，x64；需要 .NET Framework 4.8、Microsoft WebView2 Runtime 和 Visual C++ v14 x64（14.51.36247 或更新版本）。应用自带 Python 运行时，日常使用无需另装 Python。

安装器会检查运行库。缺少时，请通过安装界面的 Microsoft 官方链接准备组件，然后重新检查并继续。首次准备组件可能需要联网；在已具备组件的电脑上，安装器可离线安装程序。WebView2 的更新、SmartScreen 与数据处理说明见 [第三方说明](THIRD_PARTY_NOTICES.md)。

1. 从本仓库的 Releases 页面获取安装器和 SHA256 校验文件。
2. 安装时分别选择程序目录和数据目录。公开版本以空白配置开始，不附带作者的文献、会话或模型凭据。
3. 启动后进入“设置”，配置所需模型服务。云端服务、CLI 订阅或本地模型由使用者自行准备；开源软件本身不提供免费 API 额度。
4. 导入一份资料，在研究问答中检查回答的引用与来源，再逐步扩展资料库。

使用文档、设计文档及本地网页提供中文、英文和日文版本，随应用提供。构建方法见 [BUILDING.md](BUILDING.md)。

## 隐私与数据

本地保存数据不代表所有操作都离线完成。使用云端模型、网页读取或外部检索时，相关请求需要联网；请依据所选服务的规则决定适合发送哪些资料。不要在 Issue 或截图中公开 API Key、个人文献、会话内容或其他敏感信息。

## 许可与角色素材

Memorive 是非官方的个人项目，与 Nexon、Nexon Games 或 Yostar 无关联。部分角色图像为参考《Blue Archive》的 AI 辅助二次创作；原角色、名称和标识等权利归各自权利人所有。

自有软件代码采用 [AGPL-3.0-only](LICENSE)，范围见 [LICENSING.md](LICENSING.md)。桌宠、表情、头像及角色图标单独适用 [ASSET_NOTICE.md](ASSET_NOTICE.md)，不随代码许可证获得授权。第三方内容见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Memorive 主要面向个人学习与研究，作者鼓励非商业使用。项目按现状提供，不提供商业支持或服务保障；该倡议不对标准开源许可证追加限制。

## 当前版本

本地正式源码版本为 v1.01（build139）。安装步骤见 [INSTALL.md](INSTALL.md)；远端发布以 Releases 为准。已知限制及待完成事项见 [RELEASE_NOTES.md](RELEASE_NOTES.md)和 [RELEASE_PREPARATION.md](RELEASE_PREPARATION.md)。
