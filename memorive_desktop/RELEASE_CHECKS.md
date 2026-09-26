# Memorive v1.01 核对范围

本版本使用正式版本号和文件 SHA256 标识。源码、安装载荷、文档与扩展按同一清单核对。项目维护者已授权公开发布，当前发布状态和下载文件以 GitHub Release 为准。

## 功能测试

已在隔离的本机用户目录，使用安装后的产品服务和历史模型配置处理两篇真实 PDF。两篇均完成文献处理、卡片生成与复核、检索、分析、判定复核及人工审核包生成；四份卡片／分析结果可列出并加入研究资料库。人工审核包的生成不代表人工科研审核通过。

用户实际录制的浏览器扩展导出文件经过导入、重复导入、四条消息顺序与内容对照；不完整录制标识保留。对话精炼覆盖全部消息，AI 陈述仍标为待核实。三组研究问答覆盖单篇提问、两篇比较与无证据的泛化主张；24 条保存的引用按原文、PDF 页码和文件哈希读回一致。

计费记录包含本地嵌入、模型调用及失败尝试。部分用量尚无价格或完整 token 信息，费用估算不等于实际账单。检索回答基于选中片段，不能替代完整文献的人工评审。

## 最终封装

功能测试期间产品文件保持冻结。随后清理注释、版本溯源字段、策略生效版本和不可使用的旧机器专属测试代码；处理文献、会话精炼和研究问答的实现及提示词保持原测试内容。策略加载检查、错误版本拒绝检查和三项回归检查通过。最终安装文件另作安装、空白状态与命名完整性核对。

统一命名后发现的页面根容器高度和动态路由查找问题已修复；九个页面通过浏览器实际切换及尺寸核对，用户确认本机安装版首页与设置正文正常。内置证书恢复为原始字节并加入打包完整性门禁；公开元数据刷新已取回排行榜记录，个别上游限流按服务返回的时间等待。当前主机原生截图接口不可用，因此不声称已完成全部原生窗口的自动截图或新的虚拟机验收。

## 命名与隐私核对

检查文件名、文本、PDF、第一方编译代码对象、安装包成员及落盘哈希。首三轮为必查；后续必须连续两轮没有真实命名遗漏才能结束。协议／文档版本、外部模型名称及原始第三方版权通知按各自语义保留。

密钥检查覆盖已知格式、实际环境变量的精确及常见编码匹配、编译常量、PDF 文本与元数据、私人输入及数据库文件哈希。不读取 Windows 凭据管理器。空白启动验证用户内容与模型配置为空；公开包不包含私人配置、文献、会话或测试运行数据。

静态检查和哈希核对有明确范围，不构成任意编码方式下绝对不存在秘密的数学证明。

## Companion integration review

Obsidian and Zotero companion sources and packages are included. Seven naming/privacy review rounds passed with two consecutive clean final rounds. Checks covered source text, manifests, package contents, license notices, and correspondence between source and packaged JavaScript. Tests used isolated generated notes and metadata; no personal host configuration or API credentials were copied.

The current EXE was called through both plugins' real transport code with test host adapters. Connection discovery, state, evidence search and error propagation passed. Source service checks covered preview, import, deduplication, citation reads, managed-note conflict protection and idempotent question queueing. No model was called. These are transport and service checks, not a new full Obsidian/Zotero GUI acceptance. The installer engine and installed plugin paths are checked separately in the local delivery record.
