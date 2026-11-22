# Memorive 角色素材声明 / Character Artwork Notice / キャラクター素材に関する表示

更新日期：2026-11-22。本文落实作者确认的“代码与桌宠、表情等角色素材分开声明”方案。自有代码采用 [AGPL-3.0-only](LICENSE)，范围见 [LICENSING.md](LICENSING.md)，公开分发准备状态见 [发布准备](RELEASE_PREPARATION.md)。

## 中文

Memorive 是个人开发的免费、非盈利项目，主要面向学习与研究，与 Nexon、Nexon Games 或 Yostar 无隶属、赞助或官方认可关系。

v2.00 以前版本的桌宠、表情、头像及部分角色图标为参考《Blue Archive》角色、使用 AI 图像工具完成的二次创作。原角色、名称、标识及相关知识产权归 NEXON／NEXON Games 及其他相应权利人所有。AI 辅助创作不表示取得原角色的全部权利，也不表示这些图像是官方素材。

自 v2.00 起，桌宠和表情是颜文字，由文字直接绘制；程序图标和界面标志是 Memorive 的书页 M 标志。它们不含角色图像，不适用本声明。使用说明里的部分截图仍是旧画面，重新截图前仍适用本声明。

**上述角色图像不纳入本项目代码开源许可证的授权范围。** 本项目不通过代码许可证授予原角色、第三方美术或商标的使用权。素材的使用、修改与再分发须依据适用的官方二创规则及权利人的授权条件；不能仅凭代码许可证推定可以商业使用或任意再许可。

本声明仅划分许可范围，不是官方授权证明，也不自行授予素材分发权。如权利人对具体素材提出异议，维护者将核实并处理。

## English

Memorive is a free, non-profit personal project intended primarily for learning and research. It is not affiliated with, sponsored by, or endorsed by Nexon, Nexon Games, or Yostar.

In versions before v2.00, the desktop pet, expressions, avatars, and certain character icons are AI-assisted fan creations inspired by Blue Archive characters. Rights in the underlying characters, names, marks, and related intellectual property remain with NEXON/NEXON Games and the respective rights holders. AI-assisted creation does not confer all rights in those characters or make these images official artwork.

From v2.00, the desktop pet and expressions are kaomoji drawn as text, and the program icon and interface mark are Memorive's book-page M. They contain no character artwork and are outside this notice. Some screenshots in the user guide still show the old artwork; this notice applies to them until they are retaken.

**These character images are excluded from the project's code license.** That license grants no rights in the underlying characters, third-party artwork, or trademarks. Use, modification, and redistribution of the artwork depend on the applicable official fan-content rules and permissions from the relevant rights holders. The code license alone does not establish permission for commercial use or unrestricted relicensing of the artwork.

This notice defines licensing scope; it is not evidence of official authorization and does not itself grant distribution rights. The maintainer will review and address concerns raised by rights holders.

## 日本語

Memorive は、主に学習・研究を目的として個人が開発する、無料・非営利のプロジェクトです。Nexon、Nexon Games、Yostar との提携、スポンサー関係、公式な承認はありません。

v2.00 より前のバージョンのデスクトップマスコット、表情、アバター、および一部のキャラクターアイコンは、『ブルーアーカイブ』のキャラクターを参考に、AI 画像生成ツールを用いて制作した二次創作です。原キャラクター、名称、標章などの知的財産権は、NEXON／NEXON Games その他の各権利者に帰属します。AI の利用により原キャラクターのすべての権利を取得したことにはならず、これらの画像は公式素材ではありません。

v2.00 以降、デスクトップマスコットと表情は文字で描く顔文字に、プログラムアイコンと画面のマークは Memorive の本のページ形の M になりました。これらはキャラクター画像を含まず、本表示の対象外です。使い方ガイドの一部のスクリーンショットには旧画像が残っており、撮り直すまで本表示が適用されます。

**これらのキャラクター画像は、本プロジェクトのコードに適用するオープンソースライセンスの対象外です。** コードのライセンスは、原キャラクター、第三者の美術作品、商標に関する使用権を付与しません。素材の使用・改変・再配布には、適用される公式の二次創作ガイドラインおよび各権利者の許諾条件が適用されます。コードのライセンスのみを根拠として、素材の商用利用や無制限の再許諾が認められるとは限りません。

本表示はライセンスの範囲を明示するもので、公式の許諾を証明するものではなく、それ自体が配布の権利を付与するものでもありません。権利者から指摘があった場合、管理者は内容を確認し、対応します。

## 适用范围 / Scope / 対象範囲

路径相对于本目录。以下是现有源码中的定位入口；声明适用于其中的角色图像及其副本，不把承载图像的整个 HTML、JavaScript 文件或目录都改为受限素材。

| 内容 | 现有位置或识别方式 |
| --- | --- |
| 截图、使用文档与网站 | `product/desktop/help/` 及公开文档、网页中仍含旧角色图像的截图，包括 PDF、PNG、SVG、HTML 内嵌图片 |
| v2.00 以前的版本 | 这些版本的 `product/desktop/assets/illustrations/`、`assets/decorations/`、`product/web/assets/assistant/`、帮助页 `wink.svg`，以及组合文件、安装器图像和缩略图中的角色图像 |
| 不适用 | v2.00 起的颜文字（`assets/illustrations/` 中的文字 SVG、页面和桌宠窗口里绘制的文字），程序图标 `memorive.ico` 和书页 M 标志 `memorive-symbol.svg` |

桌宠的动画、交互与窗口管理代码，以及普通界面代码、非角色流程图、作者自有的文字说明，不因与角色图像存放在一起而自动适用本素材声明；其许可按具体文件和代码／文档许可说明判断。第三方库、字体及其许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

现有中文素材名保持原样。最终公开文件清单需要覆盖独立文件和内嵌副本；本表不是对全部文件已完成版权审查的声明。

## 官方规则与核对记录

- [Nexon Korea 游戏 IP 使用指南](https://m.nexon.com/terms/743)：第⑤条第2项要求就使用游戏 IP 制作非盈利应用程序等数字内容单独联系客服。
- [Blue Archive 日服二次创作指南](https://bluearchive.jp/fankit/guidelines)。
- [国服同人创作指引](https://www.taptap.cn/moment/426033992362361796)。
- 各地区规则的适用范围、AI 创作条款及咨询草稿见 [素材规则核对](ARTWORK_RIGHTS.md)。不同地区规则不相互替代。
