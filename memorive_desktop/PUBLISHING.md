# v1.03 release preparation

See PUBLIC_RELEASE.md and RELEASE_NOTES.md for current identity, assets and validation. Publication is pending.

# v1.02 release authorization

The maintainer authorized publication on 2026-09-30. This source binds the production update public key. Build-stage flags remain conservative clean-candidate metadata; publication and uploaded asset identities are recorded by the separate release receipt. Private keys are never included.

v1.01 users must install v1.02 once before using the new in-app updater. Windows Authenticode signing is separate and remains unavailable.

# Memorive v1.02 — publishing notes / 发布说明 / 公開手順

Project: https://github.com/KuchinashiYume/Memorive

Releases: https://github.com/KuchinashiYume/Memorive/releases

Issues: https://github.com/KuchinashiYume/Memorive/issues

## 中文

这是发布前操作说明，不执行上传。保留已存在仓库与旧版本历史，在已核对的公共源码树上提交本次明确的源文件，不把整个私有工作区、沙盒或运行记录导入。拟用正式标签 v1.02；实际标签、提交和文件须在最后核对。README 默认英文，并保留中文和日文入口；许可与角色素材范围沿用原声明。

Release 分别列安装器、完整便携包、更新助手、更新索引与分离签名、适配旧版的一跳差分、源码和必要第三方对应源码、SHA256SUMS。实际文件名以冻结发行清单为准。插件、扩展与测试控制台按自身兼容与身份列明，不因主程序版本变化强改它们的版本。更新索引只能引用已核对资产，签名私钥不进入源码、包或日志。

GitHub 访问恢复后先核对仓库、已有声明与远端状态；完成最终验收、脱敏、签名和授权后才建立新的发行与上传。上传后按真实下载地址重新获取并核对哈希，再判断是否完整公开。旧资产不覆盖；草稿或本地候选不写成已发布。

## English

These are preparatory instructions, not an upload. Keep the existing repository and release history. Commit the reviewed public-source delta without importing private workspace history, sandboxes or run records. The proposed tag is v1.02; bind the actual tag, commit and files at final review. English remains the default README, with Chinese and Japanese entry points. Existing code and artwork licensing boundaries remain in force.

List the installer, full portable package, update helper, update index and detached signature, supported one-hop delta, project and required third-party source, and SHA256SUMS separately. Exact names come from the frozen release manifest. Companion plugins, extensions and the test console keep their own compatibility and identity; do not change their versions merely to match the application. The signed index references verified assets. Signing secrets must never enter source, packages or logs.

Once access resumes, check the repository, notices and remote state. Create a release and upload only after acceptance, privacy review, signing and authorization. Download the actual published assets and compare hashes before recording publication as complete. Do not replace old assets or describe drafts and local candidates as public releases.

## 日本語

これは準備用の手順で、アップロードは行いません。既存のリポジトリと公開履歴を保持し、確認した公開ソースの差分だけを追加します。私的な作業履歴、沙盒、実行記録は取り込みません。予定タグは v1.02 とし、実際のタグ・コミット・ファイルを最終確認で結び付けます。README は英語を既定とし、中国語・日本語の入口と既存のコード・素材の許諾範囲を維持します。

インストーラー、完全ポータブル版、更新ヘルパー、更新情報と分離署名、対応する一段の差分、自作コードと必要な第三者ソース、SHA256SUMS を分けて掲載します。ファイル名は凍結済みの配布一覧に従います。連携プラグイン、拡張、テストコンソールは固有の互換性と版を保持します。署名対象は確認済み資産だけとし、秘密鍵をソース、配布物、ログに含めません。

接続再開後にリポジトリ、声明と遠隔状態を確認し、受入れ、個人情報確認、署名、公開許可を終えてから新しいリリースを作ります。実際のダウンロード URL から再取得して照合してから完了と記録します。旧資産は上書きせず、草稿やローカル候補を公開済みとしません。
