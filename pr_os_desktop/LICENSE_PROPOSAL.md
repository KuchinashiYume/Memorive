# Memorive 代码许可采用记录

作者于 2026-09-25 明确确认：“同意采用 AGPL-3.0，素材单独声明”。现已为有权授权的自有软件代码采用 GNU Affero General Public License v3.0（AGPL-3.0-only），标准全文见 [LICENSE](LICENSE)，适用范围见 [LICENSING.md](LICENSING.md)。第三方内容保留原许可；角色素材范围另见 [ASSET_NOTICE.md](ASSET_NOTICE.md)。保留本文件名以兼容已有引用。

## 已确认的许可范围划分

作者已确认代码与桌宠／表情等素材分别声明。代码范围包括桌宠的交互、动画与窗口管理程序；角色图像本身及其内嵌副本不随代码获得许可，详见 [ASSET_NOTICE.md](ASSET_NOTICE.md)。第三方代码和依赖保留各自许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。代码许可的采用不替代素材权利确认。

## 采用理由与版本边界

- r51 原包使用 PyMuPDF / PyMuPDF4LLM / PyMuPDF Layout 1.28.0。前两者提供 AGPL／商业双许可；Layout 1.28.0 是 PolyForm Noncommercial／商业双许可。
- [Artifex 官方公告](https://pymupdf.io/blog/open-source-all-the-way-down-pymupdf4llm-goes-fully-agpl)明确从 1.28.2 起将 Layout 改为 AGPL 并公开源码。本次下载的三个 1.28.2 wheel 的 METADATA 与 COPYING 也确认这一许可选项。
- 新候选将三者统一锁定 1.28.2，并加入其新增依赖 psutil 7.2.2；原始 r51 依赖锁保留。旧字体兼容已完成定向修复及有界检查，具体范围见发布说明；改依赖后的候选不冒充原 r51。
- 发布时需要提供对应源代码和构建所需材料，保留版权与许可通知；若修改后通过网络向用户提供服务，需审视 AGPL 的对应源码提供要求。最终采用全文和各依赖的实际条款为准。[Artifex 许可说明](https://artifex.com/licensing)

## 作者的非商业使用倡议

建议写入 README：

> Memorive 主要面向个人学习与研究，作者鼓励非商业使用。项目按现状提供，不提供商业支持或服务保障；使用和再分发权利以 LICENSE 为准。

该句是倡议，不限制 AGPL 已授予的权利。“仅限学习”“禁止商用”不能作为标准开源许可证的附加限制。[OSI 开源定义](https://opensource.org/osd)

## 公开交付前仍需完成

1. 代码许可已由作者确认；最终源码集合仍须保留贡献者和第三方的原有权利说明。
2. 已单列角色素材、第三方代码和依赖的声明入口；仍需按最终交付文件核对具体范围与兼容性。
3. 提供第三方对应版本源码／获取方式、构建说明和必要的版权声明；依赖名称清单本身不等于完成分发义务。
4. 绑定最终源码提交、安装包、SBOM、许可文件与 SHA256。当前准备文件和本地候选不构成发布批准。
