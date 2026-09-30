# Memorive v1.02 — release checks / 发布核对 / 公開前確認

This checklist records required evidence, not a completed test report. / 本清单是待核对范围，不是已通过报告。 / この一覧は確認範囲であり、合格報告ではありません。

| 范围 / Scope / 範囲 | Required evidence / 所需证据 / 必要な証拠 |
| --- | --- |
| 程序与源码 / Program and source / プログラムとソース | Same final source commit, runtime manifest, package identity and build inputs; exact documentation overlay. 同批源码、运行清单、包身份和构建输入及本文档增量。対応するソース、実行一覧、配布物、構築入力と本文書。 |
| 更新 / Updates / 更新 | Original public installation → target; accepted portable instance; subsequent update; mismatch fallback; tampering rejection; cancellation, interruption, rollback and data compatibility. 旧安装、便携、后续更新、回退、篡改拒绝、中断与资料兼容。旧版・ポータブル・後続更新、改ざん拒否、中断・復帰とデータ互換性。 |
| 三语 / Languages / 三言語 | Chinese, English and Japanese in installer, missing-runtime bootstrap, updater, maintenance and offline docs; language survives elevation/recovery without resetting data choices. 三语及提权、恢复和缺运行库界面；选择不重置路径。昇格・復旧・前提不足を含め、言語変更で保存先を初期化しない。 |
| 既有能力 / Existing functions / 既存機能 | Research data, retrieval, sources, branch/version history, archiving, model settings, reports and progress remain intact. 原资料、问答版本、归档、模型、报告及进度完整。元資料、会話の分岐・版、アーカイブ、モデル、報告、進捗を保持。 |
| 科研与解析限制 / Scientific and parsing limits / 研究・解析の制約 | Preserve original quality failures, missing coverage and OCR limits separately from engineering PASS. 不以工程通过替代科研资格。工学的な合格と研究品質を分ける。 |
| 许可与隐私 / Rights and privacy / 権利とプライバシー | AGPL scope; character-artwork exclusions; unchanged attribution and AI development disclosure; updated dependency/source inventory; no private libraries, credentials or histories. 许可证、素材排除、AI 开发、依赖源码与隐私。許諾、素材の除外、AI 開発、依存ソース、私的データ除外。 |
| 发行资产 / Release assets / 配布資産 | Real sizes, SHA256, update signature and independent Authenticode status; public/test trust separation; download readback after authorized publication. 真实身份、测试与正式信任根隔离、授权发布后下载回验。実際の識別、試験用と公開用の鍵の分離、公開後の再取得照合。 |

Old release checks remain historical evidence; they are not new v1.02 results. / 旧版结果只保留为历史，不作本版新结果。 / 旧版の結果を本版の新しい結果に置き換えません。
