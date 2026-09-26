# Memorive 第三方内容与许可说明

更新日期：2026-09-25。当前为发布准备文件；已经收集的许可原文继续保留，完整依赖审核与最终公开包尚未完成。

## 三类内容分别说明

| 内容 | 许可说明 |
| --- | --- |
| 作者自有代码 | 已由作者确认采用 [AGPL-3.0-only](LICENSE)，适用范围见 [LICENSING.md](LICENSING.md)，采用理由见 [LICENSE_PROPOSAL.md](LICENSE_PROPOSAL.md)。 |
| 桌宠、表情、头像及相关角色图像 | 不纳入代码许可证，适用 [ASSET_NOTICE.md](ASSET_NOTICE.md)；该声明包含中文、英文和日文。原角色与第三方知识产权保持其原有权利归属。 |
| 第三方代码、库、字体及其他内容 | 按各自原始许可证或授权条件处理，并保留要求附带的版权、许可及 NOTICE。项目的代码许可证不替代这些文件。 |

## 已收集的依赖材料

- [候选构建依赖清单](dependency-inventory.json)：114 项包名、版本与已有的许可证据。
- [许可正文副本目录](licenses/)：保留收集到的第三方原文，不以本说明替代原文。
- [补充许可来源](supplemental-license-index.json)：已补齐 clr-loader、flatbuffers、magika、markitdown、proxy-tools、pythonnet、tokenizers 七项缺失的许可正文；另收集 Python 与 WebView2 SDK 的许可。proxy-tools 以运行时代码与 0.1.0 源码包逐字节匹配确认版本关联。
- [原生库来源核对](native-origin-review.json)：新版候选共 94 个原生文件，已按包／Python／Windows 系统／生成 EXE 识别来源；不将来源识别等同于完整再分发条件审核。
- [PDF.js 许可原文](licenses/PDFJS-LICENSE.txt)：Apache-2.0。
- 原始 r51 依赖与拟公开候选的 PyMuPDF 系列版本不同；具体版本、许可变化和验证范围见 [LICENSE_PROPOSAL.md](LICENSE_PROPOSAL.md)。

这些材料绑定本地候选，尚不能代表最终发布包的完整第三方声明。最终构建若改变依赖或资源，需随实际版本更新清单与对应源码材料。已登记的缺项及后续工作见 [RELEASE_PREPARATION.md](RELEASE_PREPARATION.md)。

## Microsoft 运行时

Microsoft WebView2 SDK 与实际的 WebView2 Runtime 分别适用其条款，不能用 SDK 的许可证替代 Runtime 的条款。Windows、Visual C++ 运行库也不改按本项目的代码许可证授权。

- [Microsoft WebView2 官方分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。
- [Visual C++ V14 Redistributable and Runtime 2026 条款入口](https://visualstudio.microsoft.com/license-terms/vs2026-ga-visualcpp-v14-redist-runtime/)。
- [Microsoft 关于可再分发 DLL 的说明](https://learn.microsoft.com/en-us/cpp/windows/determining-which-dlls-to-redistribute?view=msvc-170)。

2026-09-26 的公开候选改用官方前置组件：安装包不携带 WebView2 Runtime 安装器，不再复制本机 System32 中的 MSVCP140.dll 与 MSVCP140_1.dll。缺少组件时，安装界面提供 Microsoft 官方下载入口，由使用者阅读 Microsoft 条款并安装，然后返回检查。历史离线 Runtime 安装文件及其签名记录仅保留在原候选证据中。

安装欢迎页与“许可与隐私说明”中已加入 WebView2 更新、SmartScreen 和隐私说明；缺少组件时的原生提示也提供对应入口。未替作者或下载者接受 Microsoft 条款。WebView2 SDK 的桥接 DLL 仍随程序提供并保留 SDK 原许可；CPython 与 NumPy 原始发行包内所带组件保留发行包中的 Microsoft 等许可通知，未统一改授 AGPL。官方再分发背景见 [Microsoft 分发清单](https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution)。

本程序使用 WebView2。Microsoft Defender SmartScreen 在 WebView2 默认配置下会收集并向 Microsoft 发送终端用户信息；相关处理见 [Microsoft 隐私声明](https://aka.ms/privacy)和 [Edge SmartScreen 说明](https://learn.microsoft.com/en-us/microsoft-edge/privacy-whitepaper#smartscreen)。Evergreen Runtime 还可能自行检查和安装更新。这些 Microsoft 组件保持其独立条款，不纳入本项目 AGPL 授权。

## 作者的使用倡议

Memorive 主要面向个人学习与研究，作者鼓励非商业使用。项目按现状提供，不提供商业支持或服务保障。使用和再分发权利以实际适用的许可证与素材授权条件为准；该倡议不对标准开源许可证追加商用限制。
