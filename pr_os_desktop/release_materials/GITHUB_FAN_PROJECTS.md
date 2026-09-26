# Blue Archive 相关 GitHub 项目的许可声明做法

检索日期：2026-09-25。查阅对象为仓库 README、LICENSE 或第三方声明；以下描述各作者公开写明的做法，不代表已核实其全部素材权利或安装包内容。未安装、运行或引入这些项目的代码及资源。

| 项目 | 实际声明与处理方式 | 对 Memorive 的参考价值 |
| --- | --- | --- |
| [SRC114514/arona-agent](https://github.com/SRC114514/arona-agent#版权声明) | AI 助手与阿罗娜桌宠。主体代码采用 MIT；README 将 `assets/blue-archive/` 的相关美术、音频、文本及衍生素材排除在代码许可之外，声明非官方，并要求遵守官方同人规则。 | 与本项目形态接近。可以借鉴代码与角色素材分别声明的结构；本次未核实其全部分发包是否附带素材。 |
| [Sadowski-Krystian/Blue-Archive-Theme-KDE-Plasma](https://github.com/Sadowski-Krystian/Blue-Archive-Theme-KDE-Plasma#license) | 代码、脚本和配置采用 GPL-3.0；角色图片、官方素材及商标明确排除。README 声称已取得原画师仅限该主题使用的许可，并列出画师与来源。 | 可以按内容区分许可。画师限定许可属于该项目作者的声明，不能当作 Nexon 授权，更不能转用于 Memorive。 |
| [kaede-basement/kaede](https://github.com/kaede-basement/kaede#demonstration) | 仓库标示 GPL-3.0。README 说明 Blue Archive 素材仅用于展示定制能力，不包含在启动器中，并声明无官方关联。 | 展示截图与软件附带素材可以分开处理；本次依据 README，不是对发布包的独立审计。 |
| [2031313557ye/blue-archive-title-generator-windows](https://github.com/2031313557ye/blue-archive-title-generator-windows/blob/main/THIRD_PARTY_NOTICES.md) | MIT 仅适用于维护者有权授权的内容。单列上游版本及许可，保留 Blue Archive 的角色与商标权利；使用原创几何图标、系统字体，声明不分发角色图或私人图标。 | 第三方声明的边界较具体，可借鉴“自有代码、上游代码、图像与商标”分别说明。 |
| [arumi-s/ba-armory](https://github.com/arumi-s/ba-armory#about-this-tool) | 仓库采用 MIT；README 另写非商业用途，并注明游戏图片、数据及素材归 Nexon Games／Yostar。 | 权利人标注可参考。但若把“非商业”解释为对所有 MIT 代码的附加限制，就会产生许可范围不清的问题，不照搬其笼统措辞。 |

上述项目证明这种声明结构已有实际使用案例；仓库公开存在、长期可访问或使用某个开源许可证，均不等于权利人已批准该仓库所有素材的分发。本次查看的文件未提供可以适用于 Memorive 的权利方授权。

## 补充检索：官方规则、作品标注与软件声明

补充检索日期：2026-09-25。以下是公开页面和 README 的抽样，不是对全部二创作品或各地区社区的统计。

### Blue Archive 官方规则要求什么

[Nexon Korea《游戏 IP 使用指南》](https://m.nexon.com/terms/743)把粉丝画作、小说等列入 UGC，并规定：发布 UGC 时应注明来源（Nexon 及相应游戏名）；不得让内容被误认为 Nexon 官方内容；使用游戏 IP 制作非盈利数字内容（例如应用程序）需要通过客服单独咨询。该指南没有要求把整份政策原文贴到每一张图或每个作品页面。这里的“注明来源”仍是发布 UGC 时的要求，不能把“没有长篇免责声明”直接等同于“没有任何来源标注”。

[国服项目组《同人创作指引》](https://www.taptap.cn/moment/426033992362361796)把普通个人非商业图片、音乐、视频等直接创作与需要事前申报的类别分开；“电子软件”出现在需事前申报的类别中。对于完成申报并获批的作品，规则要求在作品和宣传中标注“同人创作”，并避免使用会造成官方误认的表述。Memorive 是否属于该类别仍应按具体用途向项目组确认，不能仅凭这条概括为所有软件自动禁止或自动获准。

这说明官方政策文本、每件作品的来源标注、以及软件发布页的非官方／权利范围声明，是三个不同层次。地区版本的规则也不应互相代替。

### 新增的 Blue Archive 软件案例

| 项目 | README 中的处理 | 证据边界 |
| --- | --- | --- |
| [U1805/momotalk（MomoTalk Editor）](https://github.com/U1805/momotalk) | Web 聊天生成器。README 用简短段落说明应用与 Yostar、NEXON Games 无关联，并写明信息和素材版权归各自作者；另列出角色资料／素材来源网站。 | 能看到作者如何在软件项目首页集中放置简短说明和来源列表；不是权利方授权文件。 |
| [bluekiseki/BA-mobilization-trends-public（Yuzu Trends）](https://github.com/bluekiseki/BA-mobilization-trends-public) | README 说明这是同人数据库，标明 Blue Archive 的版权归 Nexon／NEXON Games／Yostar；GitHub 标示代码使用 MIT，并说明部分数据未放入仓库。 | 展示了代码许可、原作权利归属和未随源码提供的数据分层；不能据此推断线上服务里的每项数据或素材都获得许可。 |
| [Sunset-Edu-Tech-Group/BA-AD](https://github.com/Sunset-Edu-Tech-Group/BA-AD) | 游戏资源下载工具。README 附有较长的非官方关系及版权声明，指出游戏资产归其权利人。 | 声明文字较完整，但仓库自述的是下载游戏资源的工具；声明本身不授予提取或再分发资产的权利，不作为本项目的许可范本。 |
| [respectZ/blue-archive-viewer](https://github.com/respectZ/blue-archive-viewer) | 游戏资源查看器。README 只用一句话表示与 Yostar 无关联，并介绍 CG 与 Live2D 查看功能。 | 展示了公开项目中确有很短的免责声明写法；README 未进一步说明素材许可范围。存在声明不代表权利方批准。 |

### 对“社区里看不到声明”的解释

Fan art 帖子通常要传达作品内容、作者和作品归属；软件仓库还要说明代码许可证、随包素材、第三方资产和是否官方产品，因此后者更常把较长的声明集中写在 README、版权页或独立的 `ASSET_NOTICE.md`。Nexon Korea 的规则要求发布作品时注明来源，但并未规定必须复制整份官方指南。于是，社区帖子里看不到完整政策段落并不反常；要判断是否符合某条规则，应看其要求的是来源标注、同人身份标注，还是额外授权，而不能只按“有没有一段统一格式的声明”判断。

对 Memorive 来说，[ASSET_NOTICE.md](ASSET_NOTICE.md) 是维护者自己的素材范围说明，不是 Nexon 的“官方声明”，也不是许可证明。它目前清楚区分了代码许可证与 Blue Archive 角色图像，并明示未获官方授权；但 Nexon Korea 指南对非盈利应用单独咨询的要求仍需处理。GitHub 上其他项目的 README 或长期存在也不能替代这一步。

### GameKee 与 KivoWiki：社区规则和站点声明

| 平台 | 公开可见的处理方式 | 证据边界 |
| --- | --- | --- |
| [GameKee 全站用户协议](https://www.gamekee.com/wiki/155685.html) | 用户上传作品时保证有合法著作权或授权，并承担上传内容责任；平台可因权利主张删除、屏蔽或断链。协议还授予 GameKee 对公开上传内容较宽的免费使用许可。 | 这是 GameKee 平台服务条款，不能替代 Nexon／Yostar 对游戏 IP 的规则；协议条款本身也不等于原作权利方授权。公开页面标注发布日期为 2023-10-31。 |
| [GameKee 碧蓝档案 Wiki 活动](https://www.gamekee.com/gensoueclipse/671039_253316.html) | 2025 年周年活动接受同人文、贺图、漫画、表情包及视频等投稿，规定内容须为原创或获得明确授权的二次创作，并禁止抄袭。2023 年周年活动还收集绘画、漫画、考据、小说、Momotalk 等内容，规则要求原创。 | 活动规则处理的是社区投稿资格；所查规则没有要求每件作品附上一份统一格式的“官方版权声明”。 |
| [GameKee BA Wiki 管理者说明](https://www.gamekee.com/ba/597364.html) | 管理者区分了游戏方拥有的游戏立绘、图像和文案，与编辑／翻译者的整理、排版、翻译劳动以及攻略作者原创内容；文章欢迎使用 Wiki 图片和数据进行原创二创或衍生攻略，同时反对照搬、不署名转载，并说明广告费用用于服务器和运维。 | 这是 BA Wiki 管理者对社区实践的公开说明，不是 Nexon／Yostar 的授权文件，也不能推出所有素材均可自由再分发。 |
| [KivoWiki《使用条款和声明》](https://kivo.wiki/license) | 采用按来源和内容类型分层的方式：游戏内原始素材及官方衍生素材的权利归 Nexon（MX Studio）和 Yostar，站方称仅在 Wiki 职能范围内作必要引用，并要求用户遵循对应地区官方同人指引；本站原创文本按 CC BY-SA 4.0 署名、相同方式共享。页面还指出文章通常归原作者，翻译内容归译者，具体署名及授权需看作者要求。 | 页面说明是 KivoWiki 自身的许可与声明，不是权利方给所有二创的通用许可。页面提到传播或引用游戏素材时可以不署名 KivoWiki；这不免除官方来源标注或第三方作者对其作品另设的条件。 |

KivoWiki 的站点介绍由其维护者公开称为玩家共建的非盈利资料站，并称获得官方后勤／服务器支持；这说明项目背景，但“后勤支持”不能当作每项游戏素材或每种衍生用途都已获授权的证据。[KivoWiki 重建介绍](https://www.bilibili.com/opus/809510239057477657)

这两个站点都把规则放在站级协议、活动投稿细则或内容来源说明中，而不是给每张同人图复制同一段免责声明。单件作品页面即使没有显眼的“官方声明”，也不能据此判断其是否获授权或是否满足来源标注要求；反过来，作品写了“非官方”也不能自行取得 IP 使用许可。

## SchaleDB：用户指定项目的补充核查

核查日期：2026-09-25。对象为 [SchaleDB/SchaleDB](https://github.com/SchaleDB/SchaleDB) 的公开仓库、首页源文件、语言文件及维护者回复；未引入其代码、图片或数据。

- 首页模板 `html/home.html` 引用阿罗娜图片，并在页脚加载 `home_footer_text`。语言文件将项目称为非官方数据库和计算工具；当前线上首页也实际显示“非官方数据库”，页脚声明与 Yostar、Nexon、Nexon Games 无关联，游戏作品、信息和素材归各自作者。[首页模板](https://github.com/SchaleDB/SchaleDB/blob/main/html/home.html)、[英文语言文件](https://github.com/SchaleDB/SchaleDB/blob/main/data/en/localization.json)、[线上首页](https://schaledb.com/)
- 维护者 lonqie 在 [Discussion #77](https://github.com/SchaleDB/SchaleDB/discussions/77) 回复一位开发 iOS 应用并计划复用其 JSON 数据的用户时，表示允许使用数据，但文件字段可能按 SchaleDB 需要增删修改；他不建议项目直接热链这些数据，因为更新可能破坏兼容性。该讨论还引用了 [Issue #32](https://github.com/SchaleDB/SchaleDB/issues/32) 中的旧答复：非游戏原生的社区翻译属于贡献者作品，需取得译者许可；维护者进一步确认，部分日文界面翻译由贡献者提供。这个维护者许可针对数据文件的复用及其变动风险，不能扩展解释为 Nexon／Yostar 对游戏素材或其他项目内容的授权。
- 所查仓库根目录未见项目级 LICENSE，也未见 GitHub 标示的项目许可证；不能把其公开源码直接归为 MIT、GPL 或 AGPL。依赖自己的许可证与项目整体授权应分别判断。
- 在以上页面未找到 Nexon／Yostar 向该项目出具的专门授权文件；这表示本次未核实，不能据此断言其从未取得授权。
- GitHub 标示仓库于 2025-06-03 归档。所查页面没有说明归档原因，不将归档解释为侵权下架或官方处理。

对 Memorive 的参考结论：这是“首页简短非官方声明、素材权利归属、贡献者内容分别处理”的直接实例。它把声明放在首页页脚，没有给每项数据库条目重复长免责声明。当前网站“关于”弹窗仅展示联系渠道，未见单独的素材许可页面；公开仓库根目录也未见 README／LICENSE，且 GitHub 标记仓库于 2025-06-03 归档。GitHub 的说明是：没有许可证时，默认版权仍适用；公开仓库允许站内查看和 fork，不等同于授予一般复制、分发或改作许可。[GitHub 许可说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository) 可借鉴其站级声明的放置方式与内容分类，但不能据此推断代码或素材获准复用；Memorive 的自有代码仍按作者已批准的 AGPL-3.0-only 明确许可，角色素材单列。案例本身不用于替代官方政策或证明素材获得官方许可。

## 本项目采用的做法

作者已确认采用代码与桌宠／表情等素材分别声明：

1. 在 README 列清代码、角色素材和第三方依赖各自的许可入口。
2. 用 [ASSET_NOTICE.md](ASSET_NOTICE.md) 单列角色素材，覆盖头像、图标、内嵌图和文档截图中的同类图像；保留原有图片和中文文件名。
3. 桌宠程序代码继续归代码许可处理，不因使用角色图片而把整个桌宠模块列为受限素材。
4. 非商业使用作为作者倡议，不向标准开源代码许可追加限制。[OSI 开源定义](https://opensource.org/osd)
5. 素材分发条件仍按官方规则核对。[Nexon Korea 指南](https://m.nexon.com/terms/743)第⑤条第2项单列非盈利应用程序的咨询要求；分开声明不替代这一要求，也不能据此认定必须删除现有形象。
