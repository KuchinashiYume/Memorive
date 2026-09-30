# 安装、首次配置与更新 / Installation and updates / インストールと更新

v1.02 · 发布前准备 / Pre-release / 公開前

## 中文

从项目的 Releases 选择最终发布的安装器或完整便携目录，并取得同批 SHA256SUMS。核对文件名和 SHA256；一致性校验与发布者数字签名不同。本文的更新步骤须在最终程序验收后一起发布。

1. 全新安装时，通过欢迎页内的地球图标选择中文、English 或日本語，不另开语言弹窗。没有 WebView2 时，原生前置界面也提供语言入口。按“系统检查”准备 Microsoft 组件；Visual C++ 选择 **x64**，完成后返回重新检查。
2. 核对程序目录与数据目录。没有旧配置时，安装语言作为首次默认；复用旧数据时保留原配置。完整便携版须保留 Memorive.exe、_internal 和同目录资源，不要只移动 EXE。
3. 首次使用在设置中添加自己的 API、CLI 或本地模型，再选流程模型。先用一篇熟悉的材料核对原文、卡片、回答和引用。包内不应有作者的私有账号、文献或会话。
4. 旧 v1.01 首次升级使用与目标版本同批的 Memorive.Update.exe，明确识别原安装与数据目录。不要先卸载旧版。后续在“设置 → 关于与版本”检查并下载更新，确认大小与说明后选择“退出后更新”，打开助手，再正常退出当前实例；助手完成后重新打开 memo 核对。不匹配的差分可以改用完整包，签名或身份失败不能绕过。
5. 更新前结束正在运行的任务并备份重要资料。检查本次的数据兼容与恢复说明；程序回退不等于把旧备份强行覆盖新研究记录。更新后核对版本、原资料目录、模型配置与最近对话。

应用、安装器和更新助手自己的界面提供三语；Windows UAC、系统选择器和 Microsoft 安装程序使用其自身语言规则。维护界面的临时语言选择不覆盖已有 memo 内容语言。卸载程序与删除资料是不同操作，先读提示再处理自己的数据。

“设置 → 关于与版本”包含三语使用指南、总体设计及许可与隐私。反馈故障时提供版本、系统、步骤与脱敏错误，不上传整个资料目录或凭据。

## English

Choose the final installer or complete portable directory from the project's Releases and obtain SHA256SUMS from the same release. Match filenames and hashes; integrity checks and publisher signatures are different. Publish these update instructions only after they match the accepted final program.

1. On a fresh installation, use the inline globe control on the welcome screen to choose 中文, English or 日本語; no separate language dialog is required. The native prerequisite screen also offers this control when WebView2 is missing. Follow System Check, install **x64** Visual C++ if needed, then recheck.
2. Confirm the application and data folders. With no prior configuration, installation language becomes the first-launch default; reused data keeps its existing settings. Keep Memorive.exe, _internal and adjacent resources together in a portable directory.
3. Add your own API, CLI or local model, then select workflow models. Start with a familiar document and check its source, card, answer and citations. Packages should contain no author's private account, literature or session data.
4. For the original v1.01, first run Memorive.Update.exe from the target release and identify the existing installation and data folder. Do not uninstall first. Later, check and download in Settings → About and Version, review notes and size, and choose Update after exit. Open the assistant, exit the current instance normally, then reopen memo after the assistant finishes and check the result. A mismatched delta may use a full package instead; trust or identity failures must stop the update.
5. Finish running work and back up important material first. Read data-compatibility and recovery instructions. Application rollback is not permission to overwrite newer research records with an old backup. Afterwards confirm the version, original data location, models and recent conversations.

First-party installation and maintenance screens support three languages. Windows UAC, system pickers and Microsoft installers follow their own language settings. A temporary maintenance language does not change existing memo content-language settings. Removing the application and deleting research data are separate decisions.

Settings → About and Version includes localized guides, design and license/privacy notices. Report versions, system, reproduction steps and redacted errors; do not upload an entire data directory or credentials.

## 日本語

プロジェクトの Releases から最終公開版のインストーラーまたは完全なポータブルフォルダーを選び、同じ版の SHA256SUMS を取得します。ファイル名とハッシュを確認してください。同一性の確認と発行者の電子署名は別です。更新手順は最終プログラムの検証後に対応を確認して公開します。

1. 新規インストールでは、ようこそ画面内の地球アイコンで中文、English、日本語を選びます。言語専用のポップアップは開きません。WebView2 がない場合のネイティブ前提確認画面にも入口があります。システムチェックに従い、Visual C++ は **x64** を選んで、導入後に再確認します。
2. プログラムとデータの保存先を確認します。設定がない場合は選択言語を初回の既定値とし、既存データでは設定を保持します。ポータブル版は Memorive.exe、_internal と周辺ファイルを一緒に保持してください。
3. 自分の API、CLI、ローカルモデルと処理モデルを設定します。まず内容を知っている一つの資料で原文、カード、回答、引用を確認します。配布物に作者の私的なアカウント、文献、会話データを含めません。
4. 従来の v1.01 では対象版と同時に配布される Memorive.Update.exe を使い、既存のプログラムとデータの場所を特定します。先にアンインストールしないでください。以後は設定の「バージョン情報」で確認・取得し、説明と容量を確認して「終了後に更新」を選びます。アシスタントを開き、現在のインスタンスを通常の操作で終了し、完了後に memo を開き直して確認します。差分が合わない場合は完全パッケージを使えますが、署名・識別の失敗は回避せず停止します。
5. 実行中の作業を終え、重要な資料をバックアップします。データ互換性と復旧手順を読み、旧プログラムへの復帰を理由に新しい研究記録を古いバックアップで上書きしないでください。更新後は版、元のデータ保存先、モデル、最近の会話を確認します。

独自のインストール・保守画面は三言語に対応します。Windows UAC、システムの選択画面、Microsoft のインストーラーはそれぞれの言語規則に従います。保守中の一時的な言語選択は既存の memo の内容言語を変えません。アプリの削除と研究データの削除は別の操作です。

設定のバージョン情報に三言語のガイド、設計、ライセンス・プライバシー説明があります。不具合報告では版、環境、再現手順と機密情報を除いたエラーを伝え、資料フォルダー全体や認証情報は送らないでください。
