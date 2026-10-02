# Memorive v1.03

[Windows installer](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Setup-1.03-x64.exe) · [Complete portable package](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Portable-1.03-x64.zip) · [Test Console](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Test-Console-Setup-1.03-x64.exe)

## Changes in v1.03

Research Q&A can reuse originals and converted text prepared by the memo-research Skill, read and compare sources for the current question, and return source-checked answers to the same conversation. The Skill also works independently for one paper or agent-led comparisons. The Test Console adds three languages, a review-and-results view, and shareable summaries.

This version retains the settings-save and three-part version fixes from 1.02.01. The research Skill complements question-led reading and source reuse; no low-latency or full-workflow replacement claim is made. Source integration checks and two real agent returns are recorded separately from package checks; they do not establish scientific quality, full business regression, clean-machine or online-upgrade acceptance.

Back up data and settings before upgrading with the installer or complete portable package; never manually overlay a delta ZIP. Install or fully extract the console separately. First access still requires permission in Memo; changing the console language neither changes Memo language nor restores model-call permission.

## v1.03 更新内容

研究问答现可复用 memo-research Skill 的原件与转换文本，围绕当前问题读取、比较和回查引用；经过来源与范围核对的回答可回到原对话继续追问。Skill 可独立处理单篇或由 Agent 组织多篇比较。测试控制台补齐三语界面、审核与结果页面及可分享摘要。

本版保留 1.02.01 的设置保存与三级版本识别修复。研究 Skill 是按问题阅读与来源复用的补充，尚无低延迟或取代全量流程的性能结论。来源级集成检查和两轮真实 Agent 回流已有记录；发布包检查另行记录，不代表科研质量、全量业务、干净机器或在线升级全链路已验收。

升级前请备份资料与设置，使用安装器或完整便携包；不要手动覆盖增量 ZIP。控制台独立安装或完整解压，首次连接仍需在 Memo 中授权；切换控制台语言不会改变 Memo 的语言，也不会恢复模型调用许可。

## v1.03 の変更点

研究Q&Aで memo-research Skill が準備した原本と変換テキストを再利用し、現在の質問に沿った読解・比較・引用確認を行えます。出典と範囲を確認した回答を元の会話へ戻し、追加質問を続けられます。Skill単体の読解やAgentによる複数文献の比較にも対応。テストコンソールには三言語、レビュー・結果画面、共有用要約を追加しました。

1.02.01の設定保存と三段階版番号の修正を継承します。研究Skillは質問に沿った読解と出典再利用を補助するもので、低遅延や全処理の代替性能は未確認です。ソース統合の確認と実Agentによる2回の返却を記録しています。配布物の確認は別途記録し、科学的品質・全機能・クリーン環境・オンライン更新の全経路の合格を意味しません。

更新前に資料と設定をバックアップし、インストーラーまたは完全なポータブル版を使用してください。差分ZIPを手動で上書きしないでください。コンソールは別途インストールまたは完全に展開し、初回接続はMemo側の許可が必要です。コンソールの言語変更はMemoの言語やモデル呼出し許可を変更しません。

## Research Skill

[Standalone memo-research Skill v0.2.0](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Research-Skill-0.2.0.zip). Also bundled under `integrations/skills/memo-research`. The agent must support local skills; standalone PDF conversion needs the optional dependencies in `scripts/requirements-pdf.txt`. Memo reuse of an existing bundle does not install or require these optional agent dependencies.

## Downloads and distribution

[Three-language PDF, Markdown and local web guides](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-1.03-Documentation.zip) · [Corresponding application and console source](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-1.03-desktop-source.zip) · [Checksums](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/SHA256SUMS.txt). Use the installer to run the application; GitHub Source code ZIP/TAR.GZ downloads are source archives.

Update metadata uses the existing production signing key; Windows Authenticode remains unsigned. Private settings, credentials and research data are excluded. Original code remains AGPL-3.0-only; existing artwork and third-party notices remain in force. Marker is optional, with its runtime and model weights configured separately. See the retained Marker attribution and limitations in the project page.

[Unchanged third-party source collection](https://github.com/KuchinashiYume/Memorive/releases/download/v1.02/Memorive-1.02-third-party-sources.zip).

## Companion plugins

[Obsidian](https://github.com/KuchinashiYume/Memorive/releases/download/v1.01/Memorive-Obsidian-Companion.zip) · [Zotero](https://github.com/KuchinashiYume/Memorive/releases/download/v1.01/Memorive-Zotero-Companion.xpi). Test Console 1.03 retains the existing installer, installation identity and permissions.

## Version history

[v1.02.01](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.02.01): settings-save and version-parsing fixes.

[v1.02](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.02): discovery, AI Updates, custom CLI, multilingual output, archiving and signed updates.

[v1.01](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.01): document processing and research Q&A.
