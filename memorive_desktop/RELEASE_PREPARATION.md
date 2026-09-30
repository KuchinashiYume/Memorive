# Memorive v1.02 — release preparation / 发布准备 / 公開準備

## 中文

本地准备稿，未发布。文档涵盖已确定的更新维护边界；最终程序验收、三语页面实物核对、发行身份与签名仍须闭环。用户确认 GitHub 的原许可、素材和隐私声明没有修改；当前按留存发布快照沿用，未把它写成新的在线核验结果。2026 年 10 月 1 日前不重试 GitHub CLI。

准备顺序：完整程序验收 → 合入本次文档 → 冻结对应源码及程序 → 构建安装器、助手、完整包与单跳差分 → 核对三语和实际入口 → 最终隐私与许可证检查 → 填写真实哈希、大小、来源和签名 → 恢复 GitHub 访问后核对远端声明与仓库 → 获得发布指令后上传并下载回验。只准备材料不产生发布授权。

保留旧版及其标签和资产。不用新文件覆盖旧 v1.01。同号不同内容按准确文件清单识别。插件、浏览器扩展、测试控制台、源码与第三方对应源码分别列明，应用更新不把它们作为普通运行文件无条件下载。最终发行清单尚无真实文件的字段保持待完成，禁止填占位哈希或伪造通过。

## English

Local preparation only; nothing has been published. Documentation covers the agreed update-maintenance scope, while final acceptance, localized screen checks, release identity and signing remain to be completed. The user confirms that GitHub license, artwork and privacy notices are unchanged. The retained release snapshot is reused without claiming a fresh online verification. Do not retry GitHub CLI before 1 October 2026.

Sequence: accept the complete program; integrate these documents; freeze corresponding source and runtime; build installer, helper, full package and one-hop delta; check localized screens and actual entry points; inspect privacy and licenses; bind actual hashes, sizes, provenance and signatures; check the remote repository and notices after access returns; upload and read back only when publication is authorized. Preparation grants no publication authority.

Keep previous tags and assets, including v1.01. Distinguish different files bearing the same version by exact manifests. List companion plugins, browser extensions, test console, project source and required third-party source separately; normal application updates do not indiscriminately download them. Missing final files remain pending, without placeholder hashes or invented PASS results.

## 日本語

ローカルの公開準備であり、まだ公開していません。文書は合意済みの更新保守範囲を扱いますが、最終受入れ、三言語の実画面、配布物の識別と署名は別途完了させます。GitHub の許諾・素材・プライバシー声明は変更していないと利用者が確認しています。保管済みの公開記録を使い、新たなオンライン確認済みとは表記しません。2026 年 10 月 1 日より前に GitHub CLI を再試行しません。

完全なプログラムの受入れ、本文書の組み込み、ソースと実行物の凍結、インストーラー・ヘルパー・完全包・一段の差分の作成、実画面と入口、プライバシーとライセンス、実際のハッシュ・容量・来歴・署名の順に確認します。接続再開後に遠隔の声明を確認し、公開指示を受けてからアップロードと再取得を行います。準備だけで公開を許可したことにはなりません。

v1.01 を含む旧タグと資産は維持します。同じ版でも内容が違うものは正確な一覧で識別します。連携プラグイン、拡張、テストコンソール、自作コードと必要な第三者ソースを別に示し、通常の更新で一律に取得しません。最終ファイルがない欄は未完了のままとし、仮のハッシュや合格結果は書きません。
