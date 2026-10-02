Memorive Test Console v1.03

用于自行改进 Memorive 时复现问题、检查功能行为并验证修改。控制台不自动修改代码；请在开发工具中编辑和重新构建。

先运行“协议自测”，再把完整程序目录中的 Memorive.exe 拖入连接页。在控制台启动的临时本体中开启“允许接入控制台”，运行对应测试。结束后正常关闭并检查清理状态。

当前本体有15个操作；research.qa.simulate 尚未实现，研究问答样例仅供参考端测试。模拟结果不能代替真实文献和模型验证。

支持符合当前身份清单和桥接协议的自编译版本，不要求官方签名或固定哈希；清单检查不构成官方来源认证。

代码 AGPL-3.0-only。由 OpenAI Codex 与 Anthropic Claude Code 开发。图标、第三方组件与 WebView2 隐私说明见 NOTICE.txt、LICENSE、THIRD_PARTY_NOTICES.md。

完整安装、使用、构建与三语说明：
https://github.com/KuchinashiYume/Memorive/tree/main/memorive_console

一键安全回归不调用模型，模型单项测试需逐次允许，可能产生费用。设置副本和临时业务内容在正常结束后清理；诊断记录保留，分享前请脱敏。安装包与 EXE 未签名。

1.03：右上角地球图标切换中文 / English / 日本語。审核与结果提供九类人工检查和 Markdown / JSON 摘要。完整三语使用说明随包附带。
