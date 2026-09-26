(() => {
  'use strict';

  const root = document.getElementById('desktop-settings-preview');
  if (!root) return;

  const SETTINGS_METHODS = Object.freeze([
    'settings.leaderboard_get', 'settings.leaderboard_save', 'settings.leaderboard_refresh',
    'settings.get_contract', 'settings.get_state', 'settings.register_directory',
    'settings.external_sources_get', 'settings.external_sources_save',
    'settings.external_sources_refresh', 'settings.external_model_reference',
    'settings.preview', 'settings.save', 'settings.save_cli_services', 'settings.revert', 'settings.reset_scope',
    'settings.export_redacted', 'settings.import_redacted', 'settings.capability_state',
    'settings.credential_create', 'settings.credential_replace',
    'settings.credential_status', 'settings.credential_delete', 'settings.model_service_remove',
    'settings.verify_api_model', 'settings.verify_cli_model', 'settings.test_workflow_node',
    'settings.start_api_verification', 'settings.api_verification_status',
    'settings.start_workflow_exam', 'settings.workflow_exam_status', 'settings.cancel_workflow_exam',
    'settings.start_cli_verification', 'settings.cli_verification_status',
    'settings.cancel_cli_verification',
    'settings.diagnostics', 'settings.support_bundle', 'settings.effect_metrics',
    'local_models.bootstrap', 'local_models.list', 'local_models.discover', 'local_models.endpoint_save',
    'local_models.endpoint_remove', 'local_models.verify', 'local_models.effect_metrics',
    'sessions.refinement_settings_get', 'sessions.refinement_settings_save',
    'assistant.preference_get', 'assistant.preference_save'
  ]);
  const allowedMethods = new Set(SETTINGS_METHODS);
  const SENSITIVE_RESET_SCOPES = new Set(['api', 'cli', 'local-models', 'workflow', 'refinement']);
  const RESETTABLE_SCOPES = new Set(['viewer', 'notice', 'behavior', 'network', 'privacy', 'appearance']);
  const RESET_COUNTDOWN_SECONDS = 10;
  const nodeByStep = new Map([
    ['ingest', 'ingest'], ['embedding', 'chunk_embedding'], ['card', 'card_distill'],
    ['card-review', 'transport_review'], ['admission', 'card_admission'],
    ['context', 'context_pack'], ['analysis', 'analysis'],
    ['judgment-review', 'judgment_review'], ['human', 'human_judgment']
  ]);
  const modelSegmentPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
  const apiModelPattern = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
  const cliModelPattern = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
  const apiProtocolValues = new Set(['AUTO', 'OPENAI_COMPATIBLE', 'ANTHROPIC', 'GEMINI']);
  const apiProtocolLabels = Object.freeze({
    AUTO: '官方接口（自动）',
    OPENAI_COMPATIBLE: 'OpenAI 兼容 / 中转站',
    ANTHROPIC: 'Anthropic 兼容',
    GEMINI: 'Gemini 兼容'
  });
  const KIMI_API_ENDPOINTS = Object.freeze({
    CN: 'https://api.moonshot.cn/v1',
    AI: 'https://api.moonshot.ai/v1'
  });

  let state = null;
  let contract = null;
  let capabilityState = null;
  let effects = null;
  let localModelsState = null;
  let refinementState = null;
  let assistantPreferences = null;
  let browserCompanionState = null;
  let browserCompanionDraft = null;
  let browserCompanionReloadState = 'idle';
  let browserCompanionSetupInFlight = false;
  let browserCompanionSyncState = 'idle';
  let browserCompanionActiveSyncMode = null;
  let browserCompanionFullSyncState = 'idle';
  let browserCompanionProgressState = { mode:'idle', attempt:0, total:0, error:'' };
  let localModelBusy = false;
  let requestSequence = 0;
  let lastReceipt = null;
  let lastMutationReceipt = null;
  let bootstrapPromise = null;
  let toastTimer = 0;
  let applyingState = false;
  let cliDraftDirty = false;
  let cliSaveInFlight = false;
  let settingsSaveInFlight = false;
  let cliValidationInFlight = false;
  let apiValidationInFlight = false;
  let replacingCredentialRef = null;
  let replacingConfigId = null;
  let credentialDraftGeneration = 0;
  let settingsAppliedOnce = false;
  let activeResetScope = 'none';
  let resetArmedScope = null;
  let resetArmedRevision = null;
  let resetDeadline = 0;
  let resetCountdownTimer = 0;
  const confirmedRiskIds = new Set();
  const developerEditorMetrics = {
    save_count: 0,
    validation_failure_count: 0,
    secret_field_block_count: 0,
    credential_write_count: 0,
    api_key_scrub_count: 0,
    last_scope: null,
    last_status: 'READY',
    last_error: null,
    dirty: { api: false, cli: false, workflow: false },
    baseline: { api: '', cli: '', workflow: '' },
    diff_open: { api: false, cli: false, workflow: false },
    syntax_token_count: { api: 0, cli: 0, workflow: 0 },
    diff_added_count: { api: 0, cli: 0, workflow: 0 },
    diff_removed_count: { api: 0, cli: 0, workflow: 0 }
  };

  const q = (selector) => root.querySelector(selector);
  const qa = (selector) => [...root.querySelectorAll(selector)];
  const activeSettingsMode = () => {
    const mode = q('.desktop-settings-shell')?.dataset.settingsMode;
    return ['normal', 'advanced', 'developer'].includes(mode) ? mode : 'normal';
  };
  const persistedSettingsMode = (mode) => (
    mode === 'developer' ? 'DEVELOPER' : mode === 'advanced' ? 'ADVANCED' : 'NORMAL'
  );
  const SETTINGS_TRANSLATIONS = Object.freeze({
    'en-US': Object.freeze({
      'category.api.help': 'Save providers, model names, and credential references',
      'category.cli.help': 'Configure command-line tools and their available models',
      'category.local-models.help': 'Discover local services and verify their model lists',
      'category.workflow.help': 'Assign models to workflow nodes and review relationships',
      'category.refinement.help': 'Choose a refinement model and reviewable draft rules',
      'category.viewer.help': 'Set workspace, output folders, and external viewers',
      'category.notice.help': 'Configure completion, error, and quiet-hour alerts',
      'category.behavior.help': 'Configure launch, close confirmation, and view restoration',
      'category.network.help': 'Configure proxy settings and test local and egress networks',
      'category.privacy.help': 'Manage log retention, storage, and diagnostic exports',
      'category.appearance.help': 'Set language, font size, hints, and desktop assistant behavior',
      'category.update.help': 'Check versions, update channels, and installation status',
      'local.custom.title': 'Add another local service',
      'local.protocol.aria': 'View supported local model service protocols',
      'local.protocol.help': 'Memorive auto-detects common local model services. The three presets use two model-list protocols: native Ollama and OpenAI-compatible. llama.cpp is a dedicated OpenAI-compatible preset; compatible products are not limited to these brands.',
      'local.name.label': 'Display name',
      'local.name.placeholder': 'For example, My LM Studio',
      'local.kind.label': 'Service type',
      'local.kind.ollama': 'Ollama (native API)',
      'local.kind.openai': 'OpenAI-compatible (LM Studio, vLLM, LocalAI, Jan, etc.)',
      'local.kind.llama': 'llama.cpp Server (OpenAI-compatible preset)',
      'local.endpoint.label': 'Local service address',
      'local.endpoint.help': 'Ollama uses /api/tags; the other two presets use /v1/models. Only 127.0.0.1 or ::1 is accepted.',
      'local.add': 'Add',
      'local.detect': 'Detect local services',
      'local.detecting': 'Detecting',
      'local.detecting.status': 'Detecting local services…',
      'local.adapter.ollama': 'Ollama native',
      'local.adapter.llama': 'llama.cpp · OpenAI-compatible',
      'local.adapter.openai': 'OpenAI-compatible',
      'local.verify': 'Verify model list',
      'local.verify.again': 'Verify again',
      'local.verifying': 'Verifying…',
      'local.remove': 'Remove',
      'local.reachable': 'Local service reachable',
      'local.unreachable': 'Local service not responding',
      'local.models': 'Models: {models}',
      'local.models.none': 'No models were returned.',
      'local.summary': '{services} service(s) connected, {models} model(s) available.',
      'local.summary.none': 'No available local service detected.',
      'local.detect.failed': 'Detection failed. Please try again.',
      'local.detect.complete': 'Detection complete: {models} model(s).',
      'local.detect.empty': 'Detection complete: no service responded.',
      'local.added': 'Local service added.',
      'local.verify.reachable': 'Service reachable; {models} model(s) available.',
      'local.verify.unreachable': 'Local service not responding: {error}',
      'local.removed': 'Custom local model endpoint removed.',
      'workflow.status.disabled': 'Disabled',
      'workflow.status.conflict': 'Duplicates the upstream production model',
      'workflow.status.review-unconfigured': 'Heterogeneous reviewer not configured',
      'workflow.status.api-unconfigured': 'Saved API model not assigned',
      'workflow.status.ready': 'Ready',
      'workflow.status.model-unavailable': 'Verification required',
      'refinement.model.title': 'Refinement model',
      'refinement.model.help': 'Only saved, available models are listed. Refinement does not run until a model is configured.',
      'refinement.model.none': 'No model configured',
      'refinement.content.title': 'Refinement content',
      'refinement.content.help': '',
      'refinement.tasks.title': 'Extract tasks',
      'refinement.tasks.help': '',
      'refinement.risks.title': 'Extract risks',
      'refinement.risks.help': '',
      'refinement.status.configured': 'Configured; results still require human review.',
      'refinement.status.unconfigured': 'Unavailable until a model is configured.',
      'refinement.status.saved': 'Saved; results still require human review.',
      'refinement.saved.toast': 'Conversation refinement settings saved.',
      'browser.title': 'Web conversation library',
      'browser.help': 'Load the read-only bridge once. Later updates are automatic. Sync reads conversations in dedicated windows, one site at a time, and closes those windows when finished.',
      'browser.progress.aria': 'Web conversation library status',
      'browser.progress.ready': '{stage}',
      'browser.progress.not-started': 'Not started',
      'browser.progress.identity': 'Waiting for browser connection',
      'browser.progress.sync': 'Reading web conversations {attempt}/{total}',
      'browser.progress.setup': 'Preparing browser component',
      'browser.progress.error': 'Connection failed',
      'browser.error.timeout': 'Connection failed. Reload the extension and retry.',
      'browser.error.disconnected': 'The browser bridge is disconnected. Confirm that the extension is enabled, then try again.',
      'browser.providers.aria': 'Web conversation source sync status',
      'browser.setup': 'Connect browser',
      'browser.setup.continue': 'Continue bridge setup',
      'browser.setup.update': 'Update read-only bridge',
      'browser.setup.current': 'Bridge configured',
      'browser.reload': 'Reconnect read-only bridge',
      'browser.reload.starting': 'Reconnecting read-only bridge',
      'browser.reload.triggered': 'Read-only bridge reconnection triggered',
      'browser.sync': 'Enabled-site preflight (2 per site)',
      'browser.sync.starting': 'Starting enabled-site preflight',
      'browser.sync.triggered': 'Enabled-site preflight triggered',
      'browser.incremental': 'Incremental sync (up to 10 per site)',
      'browser.incremental.starting': 'Starting enabled-site incremental sync',
      'browser.incremental.triggered': 'Enabled-site incremental sync triggered',
      'browser.full': 'Full sync',
      'browser.full.starting': 'Starting full sync',
      'browser.full.triggered': 'Full sync triggered',
      'browser.guidance': 'First read: 2 per site. Later runs sync only new or changed conversations (up to 10 per site).',
      'browser.badge.unconfigured': 'Not configured',
      'browser.badge.prepared': 'Awaiting connection',
      'browser.badge.reload': 'Bridge reload required',
      'browser.badge.connected': 'Connected',
      'browser.badge.syncing': 'Connected · syncing',
      'browser.badge.empty': 'Connected · 0 conversations',
      'browser.badge.synced': 'Synced',
      'browser.badge.update': 'Update available',
      'browser.badge.disconnected': 'Connection failed',
      'browser.stage.prepared': 'Component ready',
      'browser.stage.wait-prepared': 'Waiting for component',
      'browser.stage.connected': '{browser} connected',
      'browser.stage.connected-short': 'Browser connected',
      'browser.stage.wait-connected': 'Waiting for bridge identity',
      'browser.stage.synced': 'Conversations synced',
      'browser.stage.wait-synced': 'Waiting for conversation sync',
      'browser.provider.synced': '{count} synced',
      'browser.provider.failed': '{count} failed',
      'browser.provider.empty': 'Sync complete · 0 found',
      'browser.provider.wait-sync': 'Waiting for sync',
      'browser.provider.wait-connect': 'Waiting for connection',
      'browser.provider.safe.pass': 'Safe · {count} read',
      'browser.provider.safe.human': 'Human verification · full sync blocked',
      'browser.provider.safe.failed': '{count} failed · full sync blocked',
      'browser.provider.safe.empty': 'No content read · full sync blocked',
      'browser.provider.safe.required': 'Preflight required',
      'browser.gate.ready': 'All enabled sites passed. Full sync can be started manually.',
      'browser.gate.human': 'Human verification detected. Full sync is blocked.',
      'browser.gate.pending': 'Preflight did not pass: every site must read content with zero failures. No automatic upgrade.',
      'browser.gate.required': 'Complete the enabled-site preflight to check full-sync eligibility.',
      'browser.status.reloading': 'Reloading the read-only bridge. Waiting for the {version} identity receipt; no sync will start.',
      'browser.status.reload-required': 'Reload the read-only bridge, then wait for the {version} identity receipt.',
      'browser.status.update': 'A bridge update is available. Run one-click setup before reloading the bridge.',
      'browser.status.synced': 'Bridge identity and local import have both been verified.',
      'browser.status.empty': 'All three sources completed, but no conversations matched: prefer the last 14 days, or the latest 30 sidebar items when dates are unavailable.',
      'browser.status.connected': 'Read-only bridge identity verified; preflight is available.',
      'browser.status.incremental': 'Read-only bridge identity verified; existing archives will now receive incremental updates.',
      'browser.status.manual': 'Load the unpacked extension once, then verify its identity receipt.',
      'browser.status.unconfigured': 'No bridge identity receipt detected.',
      'browser.toast.manual': 'The extension manager and prepared folder are open. Load the unpacked extension once; Memorive will verify it automatically.',
      'browser.toast.reload': 'Reloading the read-only bridge. No sync has started.',
      'browser.toast.identity': 'Read-only bridge identity {version} verified. Preflight is now available.',
      'browser.toast.identity-required': 'Verify the current read-only bridge identity before preflight.',
      'browser.toast.prepared': 'The extension folder is ready. Load it once from the Edge or Chrome extension manager.',
      'browser.toast.current': 'The read-only bridge is already configured. No repeated setup is needed.',
      'browser.toast.triggered': 'Enabled-site preflight triggered, with at most 5 items per site.',
      'browser.toast.incremental-triggered': 'Enabled-site incremental sync triggered, with at most 10 items per site.',
      'browser.toast.preflight-ready': 'All enabled sites passed. Full sync is now available manually.',
      'browser.toast.full-triggered': 'Full sync triggered. Waiting for the background conversation package.',
      'browser.toast.full-blocked': 'Full sync remains blocked until the latest preflight passes all enabled sites.',
      'browser.toast.synced': 'The web conversation library has been verified and synced.',
      'browser.toast.connected': 'Extension connected; waiting for a conversation package.',
      'browser.toast.unconfigured': 'No extension handshake detected. Complete the one-time unpacked-extension load first.',
      'assistant.title': 'Desktop assistant',
      'assistant.help': '',
      'assistant.enabled.aria': 'Enable desktop assistant',
      'assistant.show': 'Show assistant',
      'assistant.hide': 'Hide assistant',
      'assistant.status.enabled': 'Enabled; the navigation logo and these controls stay in sync.',
      'assistant.status.disabled': 'Disabled; settings are retained, but the assistant will not appear automatically.',
      'assistant.saved.toast': 'Desktop assistant settings saved and synchronized.',
      'assistant.action.shown': 'Desktop assistant shown.',
      'assistant.action.hidden': 'Desktop assistant hidden.',
      'shortcut.name.placeholder': 'Keep it under 10 characters'
    }),
    'ja-JP': Object.freeze({
      'category.api.help': 'サービス、モデル名、資格情報参照を保存します',
      'category.cli.help': 'CLI ツールと利用可能なモデルを設定します',
      'category.local-models.help': 'ローカルサービスを検出し、モデル一覧を検証します',
      'category.workflow.help': 'フローノードのモデルと照合関係を設定します',
      'category.refinement.help': '精製モデルとレビュー用下書きルールを選択します',
      'category.viewer.help': 'ワークスペース、出力先、外部ビューアーを設定します',
      'category.notice.help': '完了、異常、サイレント時間の通知を設定します',
      'category.behavior.help': '起動、終了確認、画面復元を設定します',
      'category.network.help': 'プロキシを設定し、ローカル回線と出口を確認します',
      'category.privacy.help': 'ログ保存期間、保存先、診断出力を管理します',
      'category.appearance.help': '言語、文字サイズ、ヒント、デスクトップ助手を設定します',
      'category.update.help': 'バージョン、更新チャネル、インストール状態を確認します',
      'local.custom.title': '別のローカルサービスを追加',
      'local.protocol.aria': '対応するローカルモデルサービスのプロトコルを表示',
      'local.protocol.help': 'Memorive は一般的なローカルモデルサービスを自動検出します。3 つのプリセットは、Ollama ネイティブと OpenAI 互換の 2 種類のモデル一覧プロトコルを使用します。llama.cpp は OpenAI 互換の専用プリセットで、対応製品はこの 3 ブランドに限定されません。',
      'local.name.label': '表示名',
      'local.name.placeholder': '例：マイ LM Studio',
      'local.kind.label': 'サービスタイプ',
      'local.kind.ollama': 'Ollama（ネイティブ API）',
      'local.kind.openai': 'OpenAI 互換（LM Studio、vLLM、LocalAI、Jan など）',
      'local.kind.llama': 'llama.cpp Server（OpenAI 互換プリセット）',
      'local.endpoint.label': 'ローカルサービスアドレス',
      'local.endpoint.help': 'Ollama は /api/tags、ほかの 2 プリセットは /v1/models を使用します。127.0.0.1 または ::1 のみ受け付けます。',
      'local.add': '追加',
      'local.detect': 'ローカルサービスを検出',
      'local.detecting': '検出中',
      'local.detecting.status': 'ローカルサービスを検出しています…',
      'local.adapter.ollama': 'Ollama ネイティブ',
      'local.adapter.llama': 'llama.cpp・OpenAI 互換',
      'local.adapter.openai': 'OpenAI 互換',
      'local.verify': 'モデル一覧を検証',
      'local.verify.again': '再検証',
      'local.verifying': '検証中…',
      'local.remove': '削除',
      'local.reachable': 'ローカルサービスに接続可能',
      'local.unreachable': 'ローカルサービスが応答しません',
      'local.models': 'モデル：{models}',
      'local.models.none': 'モデルを取得できませんでした。',
      'local.summary': '{services} 件のサービスに接続し、{models} 件のモデルを利用できます。',
      'local.summary.none': '利用可能なローカルサービスは検出されませんでした。',
      'local.detect.failed': '検出に失敗しました。もう一度お試しください。',
      'local.detect.complete': '検出完了：{models} 件のモデル。',
      'local.detect.empty': '検出完了：応答するサービスはありません。',
      'local.added': 'ローカルサービスを追加しました。',
      'local.verify.reachable': 'サービスに接続できました。{models} 件のモデルを利用できます。',
      'local.verify.unreachable': 'ローカルサービスが応答しません：{error}',
      'local.removed': 'カスタムのローカルモデルエンドポイントを削除しました。',
      'workflow.status.disabled': '無効',
      'workflow.status.conflict': '上流の生成モデルと重複',
      'workflow.status.review-unconfigured': '異種照合モデル未設定',
      'workflow.status.api-unconfigured': '保存済み API モデル未割り当て',
      'workflow.status.ready': '準備完了',
      'workflow.status.model-unavailable': '検証が必要',
      'refinement.model.title': '精製モデル',
      'refinement.model.help': '保存済みで利用可能なモデルだけを表示します。未設定の場合、精製は実行されません。',
      'refinement.model.none': 'モデル未設定',
      'refinement.content.title': '精製する内容',
      'refinement.content.help': '',
      'refinement.tasks.title': 'タスクを抽出',
      'refinement.tasks.help': '',
      'refinement.risks.title': 'リスクを抽出',
      'refinement.risks.help': '',
      'refinement.status.configured': '設定済みです。結果は人による確認が必要です。',
      'refinement.status.unconfigured': 'モデルを設定するまで利用できません。',
      'refinement.status.saved': '保存しました。結果は人による確認が必要です。',
      'refinement.saved.toast': '会話精製の設定を保存しました。',
      'browser.title': 'Web 会話ライブラリ',
      'browser.help': '読取専用ブリッジは初回だけ読み込みます。以後の更新は自動です。同期は専用ウィンドウでサイトを順番に読み取り、完了後にそのウィンドウを閉じます。',
      'browser.progress.aria': 'Web 会話ライブラリの状態',
      'browser.progress.ready': '{stage}',
      'browser.progress.not-started': '未開始',
      'browser.progress.identity': 'ブラウザー接続を待機中',
      'browser.progress.sync': 'Web 会話を読取中 {attempt}/{total}',
      'browser.progress.setup': 'ブラウザーコンポーネントを準備中',
      'browser.progress.error': '接続に失敗しました',
      'browser.error.timeout': '接続できませんでした。拡張機能を再読み込みして、もう一度お試しください。',
      'browser.error.disconnected': 'ブラウザーブリッジが切断されています。拡張機能が有効か確認して、もう一度お試しください。',
      'browser.providers.aria': 'Web 会話ソースの同期状態',
      'browser.setup': 'ブラウザーに接続',
      'browser.setup.continue': 'ブリッジ設定を続行',
      'browser.setup.update': '読取専用ブリッジを更新',
      'browser.setup.current': 'ブリッジ設定済み',
      'browser.reload': '読取専用ブリッジを再接続',
      'browser.reload.starting': '読取専用ブリッジを再接続中',
      'browser.reload.triggered': '読取専用ブリッジの再接続を開始',
      'browser.sync': '有効なサイト試読（各 5 件）',
      'browser.sync.starting': '有効なサイト試読を開始中',
      'browser.sync.triggered': '有効なサイト試読を開始しました',
      'browser.incremental': '増分同期（各サイト最大 10 件）',
      'browser.incremental.starting': '有効なサイト増分同期を開始中',
      'browser.incremental.triggered': '有効なサイト増分同期を開始しました',
      'browser.full': '完全同期',
      'browser.full.starting': '完全同期を開始中',
      'browser.full.triggered': '完全同期を開始しました',
      'browser.guidance': '初回は各サイト 5 件を試読し、以後は新規・変更分だけを同期します（各サイト最大 10 件）。',
      'browser.badge.unconfigured': '未設定',
      'browser.badge.prepared': '接続待ち',
      'browser.badge.reload': 'ブリッジ再読込が必要',
      'browser.badge.connected': '接続済み',
      'browser.badge.syncing': '接続済み・同期中',
      'browser.badge.empty': '接続済み・会話 0 件',
      'browser.badge.synced': '同期済み',
      'browser.badge.update': '更新あり',
      'browser.badge.disconnected': '接続失敗',
      'browser.stage.prepared': 'コンポーネント準備済み',
      'browser.stage.wait-prepared': 'コンポーネント準備待ち',
      'browser.stage.connected': '{browser} 接続済み',
      'browser.stage.connected-short': 'ブラウザー接続済み',
      'browser.stage.wait-connected': 'ブリッジ ID の確認待ち',
      'browser.stage.synced': '会話を同期済み',
      'browser.stage.wait-synced': '会話同期待ち',
      'browser.provider.synced': '{count} 件同期',
      'browser.provider.failed': '{count} 件失敗',
      'browser.provider.empty': '同期完了・0 件',
      'browser.provider.wait-sync': '同期待ち',
      'browser.provider.wait-connect': '接続待ち',
      'browser.provider.safe.pass': '安全 · {count} 件読取',
      'browser.provider.safe.human': '人による確認 · 完全同期をブロック',
      'browser.provider.safe.failed': '{count} 件失敗 · 完全同期をブロック',
      'browser.provider.safe.empty': '内容なし · 完全同期をブロック',
      'browser.provider.safe.required': '試読が必要',
      'browser.gate.ready': '有効なサイトすべてが通過しました。完全同期を手動で開始できます。',
      'browser.gate.human': '人による確認を検出したため、完全同期をブロックしました。',
      'browser.gate.pending': '試読未通過です。各サイトで内容を取得し、失敗 0 件が必要です。自動移行はしません。',
      'browser.gate.required': '有効なサイト試読後に完全同期の資格を確認します。',
      'browser.status.reloading': '読取専用ブリッジを再読込中です。{version} の ID 受領票を待っています。同期は開始しません。',
      'browser.status.reload-required': '読取専用ブリッジを再読込し、{version} の ID 受領票を待ってください。',
      'browser.status.update': 'ブリッジの更新があります。ワンクリック設定後にブリッジを再読込してください。',
      'browser.status.synced': 'ブリッジ ID とローカルへの取り込みを検証しました。',
      'browser.status.empty': '3 つのソースの同期は完了しましたが、対象の会話はありませんでした。過去 14 日を優先し、日付がない場合はサイドバーの最新 30 件を読み取ります。',
      'browser.status.connected': '読取専用ブリッジの ID を確認しました。試読を開始できます。',
      'browser.status.incremental': '読取専用ブリッジの ID を確認しました。既存アーカイブには増分更新だけを追加します。',
      'browser.status.manual': '拡張機能を一度読み込み、ID 受領票を確認してください。',
      'browser.status.unconfigured': 'ブリッジの ID 受領票を検出できません。',
      'browser.toast.manual': '拡張機能管理ページと準備済みフォルダーを開きました。「展開して読み込み」を一度実行すると、Memorive が自動検証します。',
      'browser.toast.reload': '読取専用ブリッジを再読込中です。同期は開始していません。',
      'browser.toast.identity': '読取専用ブリッジ {version} の ID を確認しました。試読を開始できます。',
      'browser.toast.identity-required': '試読の前に現在の読取専用ブリッジ ID を確認してください。',
      'browser.toast.prepared': '拡張機能フォルダーを準備しました。Edge または Chrome の拡張機能管理ページから一度読み込んでください。',
      'browser.toast.current': '読取専用ブリッジは設定済みです。再設定は不要です。',
      'browser.toast.triggered': '有効なサイト試読を開始しました。各サイト最大 5 件です。',
      'browser.toast.incremental-triggered': '有効なサイト増分同期を開始しました。各サイト最大 10 件です。',
      'browser.toast.preflight-ready': '有効なサイトすべてが通過し、完全同期を手動で開始できます。',
      'browser.toast.full-triggered': '完全同期を開始しました。バックグラウンドの会話パッケージを待っています。',
      'browser.toast.full-blocked': '最新の試読で 有効なサイトすべてが通過するまで完全同期は利用できません。',
      'browser.toast.synced': 'Web 会話ライブラリを検証し、同期しました。',
      'browser.toast.connected': '拡張機能は接続済みです。会話パッケージを待っています。',
      'browser.toast.unconfigured': '拡張機能の接続を検出できません。初回の「展開して読み込み」を完了してください。',
      'assistant.title': 'デスクトップ助手',
      'assistant.help': '',
      'assistant.enabled.aria': 'デスクトップ助手を有効化',
      'assistant.show': '助手を表示',
      'assistant.hide': '助手を非表示',
      'assistant.status.enabled': '有効です。ナビゲーションロゴとここでの操作は同期します。',
      'assistant.status.disabled': '無効です。設定は保持されますが、助手は自動表示されません。',
      'assistant.saved.toast': 'デスクトップ助手の設定を保存し、同期しました。',
      'assistant.action.shown': 'デスクトップ助手を表示しました。',
      'assistant.action.hidden': 'デスクトップ助手を非表示にしました。',
      'shortcut.name.placeholder': '10 文字未満を推奨'
    })
  });
  const settingsLanguage = () => {
    const raw = q('#settings-language')?.value || state?.settings?.preferences?.language || root.lang || document.documentElement.lang || 'zh-CN';
    if (String(raw).toLowerCase().startsWith('ja')) return 'ja-JP';
    if (String(raw).toLowerCase().startsWith('en')) return 'en-US';
    return 'zh-CN';
  };
  const COPY_SETTINGS = Object.freeze({"browser.result.partial":["已读取 {count} 条 · {failed} 个网站需重试", "Read {count} chats · {failed} websites need retry", "{count} 件読み取り済み · {failed} サイトで再試行が必要"],"browser.result.done":["会话已同步 · 本次读取 {count} 条", "Chats synced · {count} read this time", "会話を同期済み · 今回 {count} 件を読み取り"],"browser.stage.read-failed":["本次读取未完成","Current read incomplete","今回の読み取りが未完了"],"browser.progress.no-receipt":["未收到本次读取结果 · 可重试","No result received · retry available","今回の結果未受信 · 再試行できます"],"browser.progress.manual-load":["待加载插件","Load extension","拡張の読み込み待ち"],"browser.progress.importing":["正在导入会话","Importing conversations","会話を取り込み中"],"browser.progress.import-failed":["会话导入失败 · 可重试","Import failed · retry available","取り込み失敗 · 再試行できます"],"browser.help":["连接浏览器以同步会话","Connect your browser to sync conversations","ブラウザーを接続して会話を同期"],"local.protocol.help":["发现本机模型服务","Discover local model services","ローカルモデルサービスを検出"],"local.endpoint.help":["仅接受 127.0.0.1 或 ::1","Only 127.0.0.1 or ::1","127.0.0.1 または ::1 のみ"],"local.detect":["发现模型","Discover models","モデルを検出"],"local.verify":["验证","Verify","検証"],"refinement.model.help":["请先添加可用模型","Add an available model first","利用可能なモデルを追加してください"],"refinement.content.help":["","",""],"refinement.tasks.help":["","",""],"refinement.risks.help":["","",""],"refinement.status.configured":["已配置","Configured","設定済み"],"refinement.status.saved":["已保存","Saved","保存済み"],"refinement.saved.toast":["已保存","Saved","保存済み"],"browser.setup":["配置浏览器扩展","Set up browser extension","ブラウザー拡張を設定"],"browser.setup.continue":["继续配置","Continue setup","設定を続ける"],"browser.setup.update":["更新浏览器扩展","Update browser extension","ブラウザー拡張を更新"],"browser.setup.current":["已配置","Configured","設定済み"],"browser.reload":["重新连接","Reconnect","再接続"],"browser.reload.starting":["正在重新连接","Reconnecting","再接続中"],"browser.reload.triggered":["正在重新连接","Reconnecting","再接続中"],"browser.sync":["试同步（每站 2 条）","Trial sync (2 per site)","試験同期（各サイト 2 件）"],"browser.sync.starting":["正在同步","Syncing","同期中"],"browser.sync.triggered":["同步已提交","Sync submitted","同期を開始しました"],"browser.full.triggered":["同步已提交","Sync submitted","同期を開始しました"],"browser.guidance":["","",""],"browser.stage.wait-connected":["等待浏览器连接","Waiting for browser connection","ブラウザーの接続待ち"],"browser.gate.ready":["可以完整同步","Ready for full sync","完全同期が可能"],"browser.gate.required":["请先完成试同步","Complete trial sync first","試験同期を完了してください"],"browser.gate.pending":["试同步未完成","Trial sync incomplete","試験同期が未完了"],"browser.gate.human":["请在浏览器完成人机验证。","Complete verification in your browser.","ブラウザーで人間確認を完了してください。"],"browser.provider.safe.pass":["已读取 {count} 条","Read {count} conversations","{count} 件読み取り済み"],"browser.provider.safe.human":["请在浏览器完成人机验证。","Complete verification in your browser.","ブラウザーで人間確認を完了してください。"],"browser.provider.safe.empty":["未读取到会话","No conversations read","会話を読み取れませんでした"],"browser.toast.identity":["已连接","Connected","接続済み"],"browser.toast.identity-required":["请先连接浏览器","Connect your browser first","ブラウザーを接続してください"],"browser.toast.preflight-ready":["可以完整同步","Ready for full sync","完全同期が可能"],"browser.toast.full-blocked":["请先完成试同步","Complete trial sync first","試験同期を完了してください"],"browser.toast.full-triggered":["同步已提交","Sync submitted","同期を開始しました"],"browser.toast.reload":["正在重新连接","Reconnecting","再接続中"],"browser.status.reloading":["正在重新连接","Reconnecting","再接続中"],"browser.status.reload-required":["请重新连接浏览器","Reconnect your browser","ブラウザーを再接続してください"],"browser.status.manual":["请先配置浏览器扩展","Set up the browser extension first","ブラウザー拡張を設定してください"],"browser.status.unconfigured":["等待浏览器连接","Waiting for browser connection","ブラウザーの接続待ち"],"assistant.status.enabled":["已启用","Enabled","有効"],"assistant.status.disabled":["已关闭","Disabled","無効"],"assistant.saved.toast":["已保存","Saved","保存済み"]});
  const uiText = (key, fallback, params = {}) => {
    const language = settingsLanguage();
    const compact = COPY_SETTINGS[key];
    let value = compact ? compact[language === 'en-US' ? 1 : language === 'ja-JP' ? 2 : 0] : (SETTINGS_TRANSLATIONS[language]?.[key] || fallback);
    for (const [name, replacement] of Object.entries(params)) {
      value = value.replaceAll(`{${name}}`, String(replacement));
    }
    return value;
  };
  const setText = (selector, key, fallback) => {
    const element = q(selector);
    if (element) element.textContent = uiText(key, fallback);
  };
  const applySettingsLocale = () => {
    const categoryFallbacks = {
      api: '保存服务、模型名称与凭据引用',
      'local-models': '发现本机服务并验证可用模型列表',
      workflow: '配置流程节点使用的模型与校核关系',
      refinement: '选择精炼模型与草稿保留规则',
      viewer: '设置工作区、产物目录与外部查看器',
      notice: '配置完成、异常与勿扰提醒',
      behavior: '设置启动、关闭确认与界面恢复行为',
      network: '配置代理并检查本地与出口网络',
      privacy: '调整日志保留、存储位置与诊断导出',
      appearance: '设置语言、字体、提示与桌宠行为',
      update: '检查版本、更新通道与安装状态'
    };
    for (const [category, fallback] of Object.entries(categoryFallbacks)) {
      const button = q(`[data-category="${category}"]`);
      const helper = button?.querySelector('.desktop-settings-category-copy small');
      const translated = uiText(`category.${category}.help`, fallback);
      if (helper) helper.textContent = translated;
      if (button) button.dataset.desktopTip = translated;
    }
    setText('#settings-local-model-custom-title', 'local.custom.title', '添加其他本机服务');
    const protocolHelp = q('#settings-local-model-protocol-help');
    if (protocolHelp) protocolHelp.setAttribute('aria-label', uiText('local.protocol.aria', '查看支持的本机模型服务协议'));
    setText('#settings-local-model-protocol-tip', 'local.protocol.help', '自动检测常见的本机模型服务。界面提供三个预设，但实际适配两类模型列表协议：Ollama 原生与 OpenAI 兼容；llama.cpp 是 OpenAI 兼容协议的独立预设，并不限于三个软件品牌。');
    setText('#settings-local-model-name-label', 'local.name.label', '显示名称');
    setText('#settings-local-model-kind-label', 'local.kind.label', '服务类型');
    setText('#settings-local-model-endpoint-label', 'local.endpoint.label', '本机服务地址');
    setText('#settings-local-model-endpoint-help', 'local.endpoint.help', 'Ollama 使用 /api/tags；其余两类使用 /v1/models。仅接受 127.0.0.1 或 ::1。');
    const localName = q('#settings-local-model-name');
    if (localName) localName.placeholder = uiText('local.name.placeholder', '例如 我的 LM Studio');
    const kind = q('#settings-local-model-kind');
    if (kind) {
      const labels = {
        ollama: uiText('local.kind.ollama', 'Ollama（原生 API）'),
        openai_compatible: uiText('local.kind.openai', 'OpenAI 兼容（LM Studio、vLLM、LocalAI、Jan 等）'),
        llama_cpp: uiText('local.kind.llama', 'llama.cpp Server（OpenAI 兼容预设）')
      };
      [...kind.options].forEach((option) => { if (labels[option.value]) option.textContent = labels[option.value]; });
    }
    setText('#settings-local-model-add', 'local.add', '添加');
    if (!localModelBusy) setText('[data-local-model-refresh-label]', 'local.detect', '发现模型');
    setText('#settings-refinement-model-title', 'refinement.model.title', '精炼模型');
    setText('#settings-refinement-model-title + small', 'refinement.model.help', '请先添加可用模型');
    setText('#settings-browser-companion-title', 'browser.title', '网页会话库');
    const browserHelp = q('#settings-browser-companion-help');
    if (browserHelp) browserHelp.setAttribute('aria-label', uiText('browser.help.aria', '查看网页会话库说明'));
    setText('#settings-browser-companion-tip', 'browser.help', '首次需要在 Edge 或 Chrome 中加载一次只读桥接；之后更新自动完成。同步会依次打开独立窗口读取网页会话，完成后自动关闭采集窗口。首次试读每站 2 条，后续只同步新增或变化。');
    q('.desktop-settings-browser-stage-grid')?.setAttribute('aria-label', uiText('browser.progress.aria', '网页会话库配置进度'));
    q('.desktop-settings-browser-provider-grid')?.setAttribute('aria-label', uiText('browser.providers.aria', '网页会话来源同步状态'));
    setText('#settings-browser-companion-setup', 'browser.setup', '配置浏览器扩展');
    const browserReloadCopy = browserCompanionReloadState === 'starting'
      ? ['browser.reload.starting', '正在重新连接']
      : browserCompanionReloadState === 'triggered'
        ? ['browser.reload.triggered', '只读桥接已触发重载']
        : ['browser.reload', '重新连接'];
    setText('#settings-browser-companion-reload', browserReloadCopy[0], browserReloadCopy[1]);
    q('#settings-browser-companion-sync').textContent = '增量同步';
    q('#settings-browser-companion-test').textContent = '测试读取';
    const browserFullCopy = browserCompanionFullSyncState === 'starting'
      ? ['browser.full.starting', '正在启动完整同步']
      : browserCompanionFullSyncState === 'triggered'
        ? ['browser.full.triggered', '完整同步已触发']
        : ['browser.full', '完整同步'];
    setText('#settings-browser-companion-full-sync', browserFullCopy[0], browserFullCopy[1]);
    setText('#settings-refinement-content-title', 'refinement.content.title', '精炼内容');
    setText('#settings-refinement-content-title + small', 'refinement.content.help', '');
    const taskRow = q('#settings-refinement-tasks')?.closest('.switch-row');
    if (taskRow) {
      taskRow.querySelector('.switch-copy strong').textContent = uiText('refinement.tasks.title', '提取待办');
      taskRow.querySelector('.switch-copy > span:not(.desktop-refinement-help):not(.desktop-refinement-label)').textContent = uiText('refinement.tasks.help', '');
    }
    const riskRow = q('#settings-refinement-risks')?.closest('.switch-row');
    if (riskRow) {
      riskRow.querySelector('.switch-copy strong').textContent = uiText('refinement.risks.title', '提取风险');
      riskRow.querySelector('.switch-copy > span:not(.desktop-refinement-help):not(.desktop-refinement-label)').textContent = uiText('refinement.risks.help', '');
    }
    // CONVERSATION-REFINEMENT detailed design sections 3-4: typed candidates with source context,
    // human decisions, no automatic execution/ingest and no invented conclusions.
    const helpCopy={
      tasks:['提取对话中尚未完成的行动与待办，保留来源和适用条件；仅生成候选，不自动创建或执行任务。','Extract unfinished actions with sources and conditions. Candidates only; no tasks are created or executed automatically.','未完了の行動・課題を出典と条件付きで抽出します。候補のみで、タスクの自動作成・実行はしません。'],
      risks:['提取对话中明确提及的风险、限制与失败情况，保留上下文；不把推测当成事实，仍需人工复核。','Extract stated risks, limitations and failed attempts with context. Speculation is not fact; human review is required.','明示されたリスク・制約・失敗を文脈とともに抽出します。推測は事実とせず、人による確認が必要です。']
    };
    for(const [name,row] of [['tasks',taskRow],['risks',riskRow]]){
      if(!row)continue;
      let help=row.querySelector('.desktop-refinement-help');
      if(!help){
        help=document.createElement('span');help.className='desktop-settings-help-wrap desktop-refinement-help';
        help.innerHTML=`<span class="desktop-settings-help-trigger" role="button" tabindex="0" aria-describedby="settings-${name}-tip">?</span><span class="desktop-settings-help-tooltip" role="tooltip" id="settings-${name}-tip"></span>`;
        const label=document.createElement('span');label.className='desktop-refinement-label';
        const strong=row.querySelector('.switch-copy strong');strong.before(label);label.append(strong,help);
      }
      const index={'en-US':1,'ja-JP':2}[settingsLanguage()]||0;
      const tip=help.querySelector('[role=tooltip]');if(tip.textContent!==helpCopy[name][index])tip.textContent=helpCopy[name][index];
      help.querySelector('[role=button]').setAttribute('aria-label',row.querySelector('strong').textContent+' ?');
    }
    setText('#settings-assistant-appearance-title', 'assistant.title', '桌面助手');
    setText('#settings-assistant-appearance-help', 'assistant.help', '');
    q('#settings-assistant-enabled-appearance')?.setAttribute('aria-label', uiText('assistant.enabled.aria', '启用桌面助手'));
    setText('#settings-assistant-show', 'assistant.show', '显示');
    setText('#settings-assistant-hide', 'assistant.hide', '隐藏');
    [1, 2, 3].forEach((index) => {
      const input = q(`#settings-assistant-shortcut-name-${index}`);
      if (input) input.placeholder = uiText('shortcut.name.placeholder', '建议少于 10 个字符');
    });
    window.dispatchEvent(new CustomEvent('desktop:settings-locale-change', { detail: { language: settingsLanguage() } }));
  };
  window.__Desktop_SETTINGS_I18N__ = Object.freeze({ text: uiText, language: settingsLanguage });
  const control = (id) => {
    const element = q(`#${id}`);
    if (!element) throw new Error(`SETTINGS_CONTROL_MISSING:${id}`);
    return element;
  };
  const nativeReady = () => Boolean(window.pywebview?.api?.call);

  const call = async (method, params = {}) => {
    if (!allowedMethods.has(method)) throw new Error(`SETTINGS_METHOD_NOT_ALLOWLISTED:${method}`);
    if (!nativeReady()) throw new Error('LOCAL_SETTINGS_SERVICE_NOT_CONNECTED');
    requestSequence += 1;
    lastReceipt = {
      method,
      request_sequence: requestSequence,
      schema_version: null,
      status: 'PENDING',
      action: null,
      revision: null
    };
    let receipt;
    try {
      receipt = await (window.DesktopDecorations?.trackCall(method, () => window.pywebview.api.call(method, params)) || window.pywebview.api.call(method, params));
    } catch (error) {
      lastReceipt = { ...lastReceipt, status: 'ERROR' };
      window.dispatchEvent(new CustomEvent('desktop:settings-receipt', { detail: lastReceipt }));
      throw new Error(`SETTINGS_CALL_FAILED:${method}:${error?.message || 'UNKNOWN'}`, { cause: error });
    }
    lastReceipt = {
      method,
      request_sequence: requestSequence,
      schema_version: receipt?.schema_version || null,
      status: receipt?.status || null,
      action: receipt?.action || null,
      revision: Number.isInteger(receipt?.revision) ? receipt.revision : null
    };
    if (lastReceipt.action) lastMutationReceipt = { ...lastReceipt };
    window.dispatchEvent(new CustomEvent('desktop:settings-receipt', { detail: lastReceipt }));
    return receipt;
  };

  const nativeAction = async (method, ...args) => {
    const target = window.pywebview?.api?.[method];
    if (typeof target !== 'function') throw new Error(`LOCAL_ACTION_NOT_AVAILABLE:${method}`);
    return target(...args);
  };

  const showToast = (message) => {
    const toast = q('#settings-toast');
    if (!toast) return;
    const copy = toast.querySelector('span');
    if (copy) copy.textContent = message;
    window.clearTimeout(toastTimer);
    toast.classList.remove('is-fading');
    toast.hidden = false;
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 5000);
  };

  const setCredentialDraftPending = (pending) => {
    const button = q('#settings-validate-draft');
    if (!button) return;
    button.disabled = Boolean(pending);
    button.textContent = pending ? '处理中…' : '保存';
    if (!pending) return;
    const dot = q('#settings-draft-dot');
    const status = q('#settings-draft-status');
    if (dot) dot.className = 'status-spinner';
    if (status) status.textContent = '保存中';
  };

  const resetCredentialDraftState = () => {
    setCredentialDraftPending(false);
    const dot = q('#settings-draft-dot');
    const status = q('#settings-draft-status');
    if (dot) dot.className = 'status-dot yellow';
    if (status) status.textContent = '未保存';
  };

  const settleCredentialDraftInput = () => {
    setCredentialDraftPending(false);
    const dot = q('#settings-draft-dot');
    const status = q('#settings-draft-status');
    if (dot?.classList.contains('status-spinner')) dot.className = 'status-dot yellow';
    if (status?.textContent === '保存中') {
      status.textContent = '请修正输入后重试';
    }
  };

  const credentialErrorCopy = (error) => {
    const message = String(error?.message || error || 'UNKNOWN');
    if (message.includes('CREDENTIAL_SECRET_SIZE_INVALID')) {
      return '保存失败：API Key 超过 512 个 UTF-8 字节，请检查后重试。';
    }
    if (message.includes('CREDENTIAL_INPUT_INVALID')) {
      return '保存失败：请输入有效的 API Key。';
    }
    if (message.includes('SETTINGS_REVISION_CONFLICT')) {
      return '保存失败：设置已在其他位置更新，请刷新页面后重试。';
    }
    if (message.includes('CREDENTIAL_REFERENCE_ALREADY_EXISTS')) {
      return '保存失败：检测到已有安全凭据引用，请刷新后重试。';
    }
    if (message.includes('PARAMETER_INVALID')) {
      return '保存失败：输入未通过校验，请检查服务商、API Key 与模型名称。';
    }
    return `凭据保存失败：${message}`;
  };

  const showCredentialFailure = (error) => {
    setCredentialDraftPending(false);
    const dot = q('#settings-draft-dot');
    const status = q('#settings-draft-status');
    const copy = credentialErrorCopy(error);
    if (dot) dot.className = 'status-dot red';
    if (status) status.textContent = copy;
    showToast(copy);
  };

  const setSwitch = (id, value) => {
    const control = q(`#${id}`);
    if (control) control.setAttribute('aria-checked', String(Boolean(value)));
  };
  const switchValue = (id) => q(`#${id}`)?.getAttribute('aria-checked') === 'true';

  const setSelect = (id, value) => {
    const control = q(`#${id}`);
    if (!control) return;
    control.value = String(value);
    control.dispatchEvent(new Event('change', { bubbles: true }));
  };

  let generalDraftDirty = false, aggregateSaveInFlight = false;
  const saveParticipants = new Map();
  const extraDirty = () => [...saveParticipants.values()].some(part => part.isDirty());
  const syncSaveState = () => {
    const dirty = generalDraftDirty || cliDraftDirty || extraDirty() ||
      Object.values(developerEditorMetrics.dirty).some(Boolean);
    if (dirty) window.__Desktop_SETTINGS_UI__?.setDirty?.();
    else window.__Desktop_SETTINGS_UI__?.setClean?.();
    if(q('#settings-revert'))q('#settings-revert').disabled=!dirty || aggregateSaveInFlight;
    if(aggregateSaveInFlight && q('#settings-save'))q('#settings-save').disabled=true;
    if(q('#settings-save') && [...saveParticipants.values()].some(part=>part.isDirty()&&part.validate?.()===false))q('#settings-save').disabled=true;
  };
  const registerSaveParticipant = (key, participant) => {
    saveParticipants.set(key, participant);
    return () => saveParticipants.delete(key);
  };
  const markClean = () => {
    generalDraftDirty = false;
    window.__Desktop_SETTINGS_UI__?.setClean?.();
    const revert = q('#settings-revert');
    if (revert) revert.disabled = !extraDirty();
    if(extraDirty())window.__Desktop_SETTINGS_UI__?.setDirty?.();
  };
  const markDirty = () => {
    if (applyingState || !state) return;
    generalDraftDirty = true;
    window.__Desktop_SETTINGS_UI__?.setDirty?.();
    const revert = q('#settings-revert');
    if (revert) revert.disabled = false;
  };

  const setCliDraftDirty = (dirty = true) => {
    cliDraftDirty = Boolean(dirty);
    if (cliDraftDirty) markDirty();
  };

  const riskIds = (settings) => {
    const ids = [];
    for (const node of settings?.workflow?.nodes || []) {
      if (node.node_id === 'card_admission' || node.node_id === 'human_judgment') continue;
      if (node.retry_count <= 1) ids.push(`RETRY_LOW:${node.node_id}:${node.retry_count}`);
      else if (node.retry_count > 5) ids.push(`RETRY_HIGH:${node.node_id}:${node.retry_count}`);
    }
    return ids.sort();
  };

  const create = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  const modelDescriptor = (service) => {
    const parts = [service?.model_name, service?.tier, service?.thinking_mode]
      .map((value) => typeof value === 'string' ? value.trim() : '')
      .filter(Boolean);
    return parts.length >= 2 ? parts.join('-') : (service?.provider || '未命名模型服务');
  };

  const providerIdentity = (value) => String(value || '')
    .normalize('NFKC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();

  const isKimiService = (service = {}) => {
    const labels = [service.provider, service.credential_ref]
      .map((value) => providerIdentity(value).replace(/[^\p{L}\p{N}]/gu, ''));
    return labels.some((label) => label.includes('kimi')
      || label.includes('moonshot') || label.includes('月之暗面'));
  };

  const applyKimiAutoDetection = (service) => {
    if (!isKimiService(service)) return;
    const endpoint = String(service.api_base_url || '').trim().replace(/\/+$/, '');
    // Keep custom compatible endpoints; official Kimi endpoints use the existing resolver.
    if (endpoint && !Object.values(KIMI_API_ENDPOINTS).includes(endpoint)) return;
    service.api_platform = 'AUTO';
  };

  const isKnownOfficialApiProvider = (provider) => {
    const identity = providerIdentity(provider).replace(/[^a-z0-9]/g, '');
    return [
      /^deepseek(?:apikey[a-z0-9]*)?$/,
      /^(?:anthropic|claude)(?:apikey[a-z0-9]*)?$/,
      /^openai(?:apikey[a-z0-9]*)?$/,
      /^(?:gemini|googleai)(?:apikey[a-z0-9]*)?$/,
      /^siliconflow(?:apikey[a-z0-9]*)?$/
    ].some((pattern) => pattern.test(identity)) || isKimiService({ provider });
  };

  const inferApiModelCapability = (provider, modelName) => {
    const model = String(modelName || '').normalize('NFKC').trim().toLowerCase().replace(/^\/+|\/+$/g, '');
    // Keep the known native families aligned with model_capabilities.py.
    if (model === 'qwen/qwen3-vl-embedding-8b') return 'EMBEDDING';
    if (model === 'bge-m3' || model.startsWith('bge-m3:')
        || model.endsWith('/bge-m3') || model.includes('/bge-m3:')) return 'EMBEDDING';
    if (model.includes('reranker')) return 'RERANKER';
    if (model.endsWith('deepseek-ai/deepseek-ocr') || model.endsWith('deepseek-ocr')) return 'VISION_OCR';
    return 'CHAT';
  };

  const providerReference = (provider) => {
    if (!state?.settings) return null;
    const matchingServices = state.settings.model_services.filter(
      (service) => providerIdentity(service.provider) === providerIdentity(provider)
    );
    const references = [...new Set(matchingServices.map((service) => service.credential_ref))];
    if (references.length !== 1) return null;
    return state.settings.credential_references.find(
      (reference) => reference.credential_ref === references[0]
    ) || null;
  };

  const apiConnectionState = (provider, protocolValue, baseUrlValue) => {
    const api_protocol = String(protocolValue || 'AUTO').trim().toUpperCase();
    const api_base_url = String(baseUrlValue || '').trim().replace(/\/+$/, '');
    if (!apiProtocolValues.has(api_protocol)) {
      return { valid: false, code: 'API_PROTOCOL_INVALID', api_protocol, api_base_url };
    }
    if (api_protocol === 'AUTO') {
      if (api_base_url) return { valid: false, code: 'API_CONNECTION_INCOMPLETE', api_protocol, api_base_url };
      if (!isKnownOfficialApiProvider(provider)) {
        return { valid: false, code: 'API_PROVIDER_PROTOCOL_REQUIRED', api_protocol, api_base_url };
      }
      return { valid: true, api_protocol, api_base_url };
    }
    if (!api_base_url) return { valid: false, code: 'API_BASE_URL_REQUIRED', api_protocol, api_base_url };
    try {
      const parsed = new URL(api_base_url);
      const hostname = parsed.hostname.replace(/^\[|\]$/g, '').toLowerCase();
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password
          || parsed.search || parsed.hash || !hostname
          || hostname === 'localhost' || hostname.endsWith('.localhost')) {
        throw new Error('API_BASE_URL_INVALID');
      }
    } catch (_) {
      return { valid: false, code: 'API_BASE_URL_INVALID', api_protocol, api_base_url };
    }
    return { valid: true, api_protocol, api_base_url };
  };

  const modelIdentity = (service) => [service?.model_name, service?.tier, service?.thinking_mode]
    .map((value) => typeof value === 'string' ? value.trim().normalize('NFKC').toLowerCase() : '')
    .join('|');

  const examProviderFamily = (service = {}) => {
    const protocol = String(service.api_protocol || 'AUTO').trim().toUpperCase();
    if (protocol !== 'AUTO') return protocol;
    const provider = String(service.provider || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
    if (/^(anthropic|claude)/.test(provider)) return 'ANTHROPIC';
    if (/^openai/.test(provider)) return 'OPENAI';
    if (/^(gemini|googleai)/.test(provider)) return 'GEMINI';
    if (/^(deepseek|siliconflow|硅基流动|硅基)/.test(provider)) return 'OPENAI_COMPATIBLE';
    return '';
  };

  const cachedWorkflowExamScore = (nodeId, identity) => {
    if (!nodeId || !identity) return null;
    const entries = state?.workflow_exam_score_catalog?.entries;
    if (!Array.isArray(entries)) return null;
    const row = entries.find((candidate) => candidate?.node_id === nodeId
      && Object.entries(identity).every(([key, value]) => candidate?.[key] === value));
    return Number.isInteger(row?.score) && row.score >= 0 && row.score <= 100
      ? row.score : null;
  };
  window.__Desktop_WORKFLOW_NODE_IDS__ = Object.fromEntries(nodeByStep);
  window.__Desktop_WORKFLOW_EXAM_SCORE__ = cachedWorkflowExamScore;

  const localModelDisplayName = (model = {}) => {
    const raw = String(model.display_name || model.model_name || model.profile_ref || '').trim();
    return raw.startsWith('[本地]') ? raw : `[本地] ${raw || '本地模型'}`;
  };

  const normalizeLocalCapabilities = (model = {}) => {
    const raw = Array.isArray(model.capabilities)
      ? model.capabilities
      : [model.capability || inferApiModelCapability(model.endpoint_name, model.model_name)];
    const normalized = raw
      .map((value) => String(value || '').trim().toUpperCase())
      .filter(Boolean);
    return normalized.length ? [...new Set(normalized)] : ['CHAT'];
  };

  const configuredModelOptions = (settings) => {
    const apiOptions = (settings.model_services || []).map((service) => {
    const reference = (settings.credential_references || []).find((row) => row.credential_ref === service.credential_ref);
    const stored = reference?.status === 'STORED_UNVERIFIED';
    const stateValue = !stored || service.connection_status === 'INVALID'
      ? 'red'
      : service.connection_status === 'AVAILABLE' ? 'green' : 'yellow';
    const capabilities = [inferApiModelCapability(service.provider, service.model_name)];
    return {
      profileRef: service.config_id,
      model: modelDescriptor(service),
      modelKey: modelIdentity(service),
      examIdentity: {
        profile_kind: 'API',
        provider_family: examProviderFamily(service),
        model_name: service.model_name,
        thinking: service.thinking === true,
        tier: service.tier
      },
      capabilities,
      embeddingEligible: capabilities.includes('EMBEDDING')
        && stored && service.connection_status === 'AVAILABLE',
      rerankerEligible: capabilities.some(value => ['RERANKER', 'CHAT'].includes(value))
        && stored && service.connection_status === 'AVAILABLE',
      state: stateValue,
      note: stored ? `API · ${service.provider}` : `API · ${service.provider} · 凭据引用缺失`
    };
    });
    const cliOptions = (settings.cli_services || [])
      .filter((service) => service.enabled)
      .flatMap((service) => (service.models || []).map((model) => ({
        profileRef: model.profile_ref,
        model: model.display_name || model.model_name,
        modelKey: [model.model_name, service.adapter_id, model.thinking_mode || '']
          .map((value) => String(value).trim().normalize('NFKC').toLowerCase())
          .join('|'),
        examIdentity: {
          profile_kind: 'CLI',
          adapter_id: service.adapter_id,
          model_name: model.model_name,
          thinking_mode: model.thinking_mode || ''
        },
        capabilities: ['CHAT'],
        embeddingEligible: false,
        rerankerEligible: service.adapter_id === 'codex_cli' && model.connection_status === 'AVAILABLE',
        state: model.connection_status === 'AVAILABLE'
          ? 'green' : model.connection_status === 'INVALID' ? 'red' : 'yellow',
        note: `CLI · ${service.display_name} · ${model.model_name}`
      })));
    const localOptions = (localModelsState?.recognized_models || []).map((model) => {
      const capabilities = normalizeLocalCapabilities(model);
      return {
        profileRef: model.profile_ref,
        model: localModelDisplayName(model),
        modelKey: `local|${model.endpoint_id}|${String(model.model_name).normalize('NFKC').toLowerCase()}`,
        examIdentity: {
          profile_kind: 'LOCAL',
          endpoint_kind: model.endpoint_kind,
          model_name: model.model_name,
          model_digest: String(model.model_digest || '').toUpperCase()
        },
        capabilities,
        embeddingEligible: capabilities.includes('EMBEDDING')
          && model.connection_status === 'AVAILABLE'
          && model.endpoint_kind === 'ollama'
          && model.exact_identity_available === true
          && model.execution_eligible === true,
        rerankerEligible: capabilities.some(value => ['RERANKER', 'CHAT'].includes(value))
          && model.connection_status === 'AVAILABLE'
          && ['loopback_reranker', 'ollama'].includes(model.endpoint_kind)
          && model.exact_identity_available === true
          && model.execution_eligible === true,
        state: model.connection_status === 'AVAILABLE' ? 'green' : 'yellow',
        note: `[本地] ${model.endpoint_name}`
      };
    });
    return [...localOptions, ...apiOptions, ...cliOptions];
  };

  const publishWorkflowConsumers = (settings, revision) => {
    const enabled = {};
    const retries = {};
    const models = {};
    const profileRefs = {};
    const fallbackProfileRefs = {};
    const profileSources = {};
    const scores = {};
    const serviceById = new Map(settings.model_services.map((service) => [service.config_id, service]));
    for (const cliService of settings.cli_services || []) {
      for (const model of cliService.models || []) {
        serviceById.set(model.profile_ref, { ...model, provider: cliService.display_name, cli: true });
      }
    }
    for (const localModel of localModelsState?.recognized_models || []) {
      serviceById.set(localModel.profile_ref, { ...localModel, local: true });
    }
    for (const node of settings.workflow.nodes) {
      const step = [...nodeByStep].find(([, nodeId]) => nodeId === node.node_id)?.[0];
      if (!step) continue;
      enabled[step] = node.enabled;
      retries[step] = node.retry_count;
      profileRefs[step] = node.profile_ref || '';
      fallbackProfileRefs[step] = node.fallback_profile_ref || '';
      profileSources[step] = node.profile_source_node_id || '';
      scores[step] = Number.isInteger(node.test_score) ? node.test_score : null;
      const service = serviceById.get(node.profile_ref);
      if (service) models[step] = service.cli || service.local ? (service.display_name || service.model_name) : modelDescriptor(service);
      else if (step === 'card' || step === 'analysis') models[step] = '尚未映射已配置模型';
    }
    const localModelProfiles = (localModelsState?.recognized_models || []).map((model) => ({
      ...model,
      display_name: model.display_name || `[本地] ${model.model_name}`
    }));
    const detail = {
      template: {
        revision: Math.max(1, revision + 1), enabled, retries, models,
        profileRefs, fallbackProfileRefs, profileSources, scores
      },
      configuredModelOptions: configuredModelOptions(settings),
      localModelProfiles
    };
    window.__Desktop_LOCAL_MODEL_PROFILES__ = localModelProfiles;
    window.__Desktop_WORKFLOW_SYNC__ = detail;
    window.dispatchEvent(new CustomEvent('desktop:workflow-config-updated', { detail }));
    return detail;
  };

  const apiReasoningControl = (service = {}) => {
    const entries = state?.api_reasoning_profiles?.[service.model_name] || [];
    const protocol = service.api_protocol || 'AUTO';
    const provider = String(service.provider || '').normalize('NFKC').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, '').replace(/apikey[a-z0-9]*$/, '');
    let host = '';
    try { host = new URL(service.api_base_url).hostname.toLowerCase(); } catch (_) { /* AUTO has no endpoint yet. */ }
    const matches = entries.filter(entry => protocol === 'AUTO'
      ? (entry.auto_provider_aliases || []).includes(provider)
      : entry.protocols.includes(protocol) && entry.origins.includes(host));
    return matches.length === 1 ? matches[0] : null;
  };

  const normalReasoningChoices = (control) => {
    if (!control || control.points.length < 2) return [];
    const names = {ECONOMY:'经济', STANDARD:'标准', HIGH_QUALITY:'高质量', MAXIMUM:'最高'};
    const representatives = new Map();
    for (const [plan,index] of Object.entries(control.plan_indices)) {
      if (!representatives.has(index) || plan === 'MAXIMUM') representatives.set(index,plan);
    }
    return [...representatives].map(([index,plan]) => ({index,plan,label:names[plan]}));
  };

  const normalPlanForService = (service) => {
    const control = apiReasoningControl(service);
    const tierPlans = {economy:'ECONOMY',standard:'STANDARD','high-quality':'HIGH_QUALITY',maximum:'MAXIMUM'};
    if (tierPlans[service.tier]) return tierPlans[service.tier];
    if (!control) return service.plan || 'STANDARD';
    const explicitEffort = control.effort_values.includes(service.tier) ? service.tier : null;
    const enabled = Boolean(explicitEffort) || control.thinking_mode === 'ALWAYS' || Boolean(service.thinking_mode || service.thinking);
    const effort = explicitEffort || (enabled ? control.default_effort : null);
    const index = control.points.findIndex(point => effort ? point.effort === effort : point.thinking === enabled);
    return normalReasoningChoices(control).find(choice => choice.index === index)?.plan
      || (control.thinking_mode === 'OPTIONAL' && !control.effort_values.length ? (enabled?'MAXIMUM':'ECONOMY') : service.plan || 'STANDARD');
  };

  const syncDraftReasoning = () => {
    const service = {provider:q('#settings-provider')?.value || '', model_name:q('#settings-model-name')?.value.trim() || '',
      api_protocol:q('#settings-api-protocol')?.value || 'AUTO', api_base_url:q('#settings-api-base-url')?.value || ''};
    const control = apiReasoningControl(service);
    const fieldset = q('#settings-model-plan');
    if (!fieldset) return control;
    fieldset.querySelector('legend').textContent = '处理偏好';
    let note = fieldset.querySelector('[data-reasoning-summary]');
    if (!note) {note=create('small');note.dataset.reasoningSummary='';fieldset.append(note);}
    let toggle = fieldset.querySelector('[data-normal-thinking]');
    if (!toggle) {
      const row=create('div','switch-row');row.dataset.normalThinkingRow='';
      row.append(create('span','switch-copy','深度思考'));
      toggle=create('button','switch');toggle.type='button';toggle.role='switch';toggle.dataset.normalThinking='';toggle.setAttribute('aria-label','深度思考');
      toggle.addEventListener('click',()=>{const enabled=toggle.getAttribute('aria-checked')!=='true';toggle.setAttribute('aria-checked',String(enabled));setSwitch('settings-thinking-enabled',enabled);setDraftPlan(enabled?'MAXIMUM':'ECONOMY');markDirty();});
      row.append(toggle);fieldset.append(row);
    }
    const toggleOnly=control?.thinking_mode==='OPTIONAL' && !control.effort_values.length && !control.recommended_budget_tokens?.length;
    const choices=toggleOnly?[]:normalReasoningChoices(control);
    const selected=selectedDraftPlan();const selectedIndex=control?.plan_indices?.[selected];
    const selectedChoice=choices.find(row=>row.index===selectedIndex) || choices[0];
    qa('#settings-model-plan [data-model-plan]').forEach(button=>{
      const choice=choices.find(row=>row.plan===button.dataset.modelPlan);
      button.hidden=!choice;button.disabled=!choice;
      if(choice) button.textContent=choice.label;
      if(choices.length) button.setAttribute('aria-checked',String(choice===selectedChoice));
    });
    fieldset.querySelector('.segmented').hidden=!choices.length;
    toggle.closest('[data-normal-thinking-row]').hidden=!toggleOnly;
    toggle.setAttribute('aria-checked',String(selectedIndex>0));
    note.textContent=!service.model_name?'选择模型后自动匹配可用选项。':!control?'使用接口默认配置；可调项尚未确认。':toggleOnly?'开启后会投入更多思考。':control.points.length<2?(control.thinking_mode==='ALWAYS'?'该模型始终思考，使用默认强度。':'该模型使用默认配置。'):'已按模型能力匹配可用选项。';
    const thinking=q('#settings-thinking-enabled');
    if (thinking && control?.thinking_mode==='ALWAYS') {setSwitch('settings-thinking-enabled',true);thinking.disabled=true;thinking.setAttribute('aria-disabled','true');}
    return control;
  };

  const createModelProfileEditor = (service, { includeExamples = false } = {}) => {
    const editor = create('div', 'mode-advanced desktop-settings-model-profile-editor');
    editor.hidden = true;
    const grid = create('div', 'desktop-settings-model-profile-grid');
    const fields = [
      ['model_name', '模型 ID', '以接口模型目录返回值为准', 128],
      ['tier', '推理档位 / 模型标记（可选）', '不填则使用模型默认', 80]
    ];
    for (const [field, labelText, placeholder, maximum] of fields) {
      const label = create('label', 'field-label', labelText);
      const reasoningControl = apiReasoningControl(service);
      const enumField = field === 'tier' && reasoningControl?.effort_values?.length;
      const input = create(enumField ? 'select' : 'input', enumField ? 'select' : 'field mono');
      if (enumField) {
        for (const optionValue of ['default', ...reasoningControl.effort_values]) {
          const option=create('option','',optionValue==='default'?'模型默认':optionValue);option.value=optionValue;input.append(option);
        }
        if (service?.tier && ![...input.options].some(o=>o.value===service.tier)) {const option=create('option','',service.tier+'（已有预设）');option.value=service.tier;input.append(option);}
      } else input.type = 'text';
      input.maxLength = maximum;
      input.autocomplete = 'off';
      input.placeholder = placeholder;
      input.value = typeof service?.[field] === 'string' ? service[field] : '';
      input.dataset.modelField = field;
      label.append(input);
      grid.append(label);
    }
    editor.append(grid);
    const connectionGrid = create('div', 'desktop-settings-model-profile-grid');
    const protocolLabel = create('label', 'field-label', '接口格式');
    const protocol = create('select', 'select');
    protocol.dataset.apiProtocol = '';
    for (const [value, label] of Object.entries(apiProtocolLabels)) {
      const option = create('option', '', label);
      option.value = value;
      protocol.append(option);
    }
    protocol.value = service?.api_protocol || 'AUTO';
    protocolLabel.append(protocol);
    const endpointLabel = create('label', 'field-label', 'API 地址');
    const endpoint = create('input', 'field mono');
    endpoint.type = 'url';
    endpoint.maxLength = 512;
    endpoint.autocomplete = 'url';
    endpoint.placeholder = 'https:' + '//relay.example.com/v1';
    endpoint.value = service?.api_base_url || '';
    endpoint.dataset.apiBaseUrl = '';
    const syncEndpoint = () => {
      endpoint.disabled = protocol.value === 'AUTO';
      if (endpoint.disabled) endpoint.value = '';
    };
    protocol.addEventListener('change', syncEndpoint);
    syncEndpoint();
    endpointLabel.append(endpoint);
    connectionGrid.append(protocolLabel, endpointLabel);
    editor.append(connectionGrid);
    const thinkingRow = create('div', 'switch-row desktop-settings-thinking-row');
    const thinkingCopy = create('span', 'switch-copy');
    thinkingCopy.append(
      create('strong', '', '深度思考'),
      create('span', '', '')
    );
    const thinking = create('button', 'switch');
    thinking.type = 'button';
    thinking.setAttribute('role', 'switch');
    thinking.setAttribute('aria-label', '深度思考');
    thinking.setAttribute('aria-checked', String(Boolean(service?.thinking_mode || service?.thinking)));
    thinking.dataset.modelThinking = 'true';
    thinking.addEventListener('click', () => {
      thinking.setAttribute('aria-checked', String(thinking.getAttribute('aria-checked') !== 'true'));
      markDirty();
    });
    const reasoningControl = apiReasoningControl(service);
    if (reasoningControl?.thinking_mode === 'ALWAYS') {
      thinking.setAttribute('aria-checked','true');thinking.disabled=true;
      thinkingCopy.querySelector('span').textContent='该模型始终开启思考';
    }
    thinkingRow.append(thinkingCopy, thinking);
    editor.append(thinkingRow);
    const reasoningNote=create('small','',reasoningControl
      ? (reasoningControl.effort_values.length ? '支持：'+reasoningControl.effort_values.join(' / ') : '无推理档位参数')
      : '未确认可调档位；模型标记不会作为推理参数发送。');
    editor.append(reasoningNote);
    if (includeExamples) {
      const examples = create('div', 'desktop-settings-model-examples');
      examples.append(
        create('small', '', '使用服务商提供的模型 ID')
      );
      editor.append(examples);
    }
    return editor;
  };

  const renderModelServices = (settings) => {
    const list = q('#settings-api-list');
    const draft = q('#settings-api-draft');
    const empty = q('#settings-api-empty');
    if (!list || !draft) return;
    list.querySelectorAll('[data-config-id]').forEach((card) => card.remove());
    const services = settings.model_services || [];
    if (empty) empty.hidden = services.length > 0;

    for (const service of services) {
      const reference = (settings.credential_references || []).find(
        (row) => row.credential_ref === service.credential_ref
      );
      const card = create('article', 'desktop-settings-api-card');
      card.dataset.configId = service.config_id;
      card.dataset.credentialRef = service.credential_ref;
      card.dataset.provider = service.provider;

      const summary = create('button', 'desktop-settings-api-summary');
      summary.type = 'button';
      summary.setAttribute('aria-expanded', 'false');
      const apiState = reference?.status !== 'STORED_UNVERIFIED' || service.connection_status === 'INVALID'
        ? 'red' : service.connection_status === 'AVAILABLE' ? 'green' : 'yellow';
      const dot = create('span', `status-dot ${apiState}`);
      dot.dataset.desktopTip = service.connection_status === 'AVAILABLE'
        ? '最小验证已通过'
        : reference?.status === 'STORED_UNVERIFIED' ? '已保存' : '凭据引用缺失';
      dot.dataset.desktopPlacement = 'top';
      const summaryCopy = create('span');
      const modelCapability = inferApiModelCapability(service.provider, service.model_name);
      const capabilityCopy = modelCapability === 'EMBEDDING'
        ? '嵌入模型'
        : modelCapability === 'RERANKER'
          ? '重排模型'
          : modelCapability === 'VISION_OCR' ? '视觉 / OCR' : '对话模型';
      summaryCopy.append(
        create('strong', '', service.model_name || service.provider),
        create('small', '', (service.model_name && service.tier)
          ? `${service.provider} · ${modelDescriptor(service)} · ${capabilityCopy}`
          : `${service.provider} · 模型描述待补充`)
      );
      summary.append(dot, summaryCopy, create('span', '', '详情⌄'));

      const detail = create('div', 'desktop-settings-api-detail');
      detail.setAttribute('aria-hidden', 'true');
      const inner = create('div', 'desktop-settings-api-detail-inner');
      const credentialRow = create('div', 'desktop-settings-control-row');
      const credentialCopy = create('span');
      credentialCopy.append(create('strong', '', 'API Key'), create('small', '', ''));
      const credentialActions = create('span', 'desktop-settings-inline');
      credentialActions.append(create('span', 'mono', reference?.status === 'STORED_UNVERIFIED' ? '已保存' : '引用缺失'));
      const replace = create('button', 'text-button', '编辑');
      replace.type = 'button';
      replace.dataset.replaceCredential = service.config_id;
      const remove = create('button', 'text-button', '删除');
      remove.type = 'button';
      remove.dataset.deleteModelService = service.config_id;
      credentialActions.append(replace, remove);
      credentialRow.append(credentialCopy, credentialActions);

      const statusCard = create('div', 'desktop-settings-status-card neutral');
      statusCard.append(
        create('strong', '', reference?.status === 'STORED_UNVERIFIED' ? '本地引用存在' : '本地引用缺失'),
        create('span', '', '检查凭据引用')
      );
      const localCheck = create('button', 'soft-button', reference?.status === 'STORED_UNVERIFIED' ? '再次检查' : '检查本地引用');
      localCheck.type = 'button';
      localCheck.dataset.credentialStatus = service.credential_ref;
      statusCard.append(localCheck);
      const validationCard = create('div', `desktop-settings-status-card ${apiState === 'red' ? 'red' : apiState === 'green' ? 'neutral' : 'yellow'}`);
      validationCard.dataset.apiValidation = service.config_id;
      validationCard.append(
        create('strong', '', service.connection_status === 'AVAILABLE' ? '验证通过' : service.connection_status === 'INVALID' ? '验证失败' : '待验证'),
        create('span', '', '读取模型目录并核对模型 ID')
      );
      const validateApi = create('button', 'soft-button', service.connection_status === 'UNVERIFIED' ? '验证 API' : '重新验证');
      validateApi.type = 'button';
      validateApi.dataset.validateApi = service.config_id;
      validationCard.append(validateApi);
      const profileEditor = createModelProfileEditor(service);

      inner.append(credentialRow, statusCard, validationCard, profileEditor);
      detail.append(inner);
      card.append(summary, detail);
      list.append(card);
    }
  };

  const connectionCopy = (status) => status === 'AVAILABLE'
    ? '可用' : status === 'INVALID' ? '验证失败' : '尚未验证';

  const cliValidationReasonCopy = Object.freeze({
    MODEL_VALIDATION_RUNNER_NOT_CONFIGURED: '当前无法验证模型。',
    EXPECTED_REVISION_CONFLICT: '设置已更新，请重新验证。',
    CLI_VALIDATION_IN_PROGRESS: '已有一个 CLI 模型正在验证，请等待完成。',
    CLI_ADAPTER_UNSUPPORTED: '当前 CLI 适配器暂不支持自动验证。',
    CLI_EXECUTABLE_NOT_FOUND: '未找到命令位置，请检查路径或系统 PATH。',
    CLI_MODEL_NOT_CONFIGURED: '请先填写模型标识。',
    CLI_MODEL_CONFIGURATION_INVALID: '模型标识或思考模式不符合该 CLI 的调用格式。',
    CLI_PROCESS_START_FAILED: 'CLI 未能启动，请检查命令位置与本机安装状态。',
    CLI_VALIDATION_TIMEOUT: '验证未在原等待时限内完成，不能据此判断登录失效。',
    CLI_VERIFICATION_CANCELLED: '已停止验证，模型仍待验证。',
    CLI_RESPONSE_INCOMPLETE: 'CLI 已退出，但没有返回完整的模型回复。',
    CLI_AUTH_REQUIRED: 'CLI 登录已失效，请在该 CLI 中重新登录后验证。',
    CLI_MODEL_UNAVAILABLE: '当前账号或 CLI 不支持这个模型，请核对模型标识。',
    CLI_RATE_LIMITED: '当前账号额度不足或请求受到限流，请稍后重试。',
    CLI_NETWORK_FAILED: 'CLI 网络连接失败，请检查网络或代理。',
    CLI_COMMAND_UNSUPPORTED: 'CLI 不支持当前参数，请检查安装版本与配置。',
    CLI_OUTPUT_LIMIT_EXCEEDED: 'CLI 返回内容超出服务限制。',
    CLI_AUTH_OR_MODEL_REQUEST_FAILED: '验证被拒绝，请检查登录状态与模型标识。',
    CLI_RESPONSE_EMPTY: 'CLI 未返回可识别结果。',
    CLI_RETURNED_MODEL_MISMATCH: 'CLI 返回的模型与当前模型标识不一致。',
    CLI_MINIMAL_PROBE_PASS: '连接成功。',
    CLI_MINIMAL_REQUEST_COMPLETED: '验证通过。',
    CLI_MINIMAL_REQUEST_COMPLETED_MODEL_NOT_EXPOSED: '连接成功，模型身份尚未核实。'
  });

  const cliValidationMessage = (receipt = {}) => (
    receipt.display_message
    || ({CLI_VERIFICATION_NOT_ASSESSED:'验证未完成，请检查 CLI 运行状态后重新验证。',
         CLI_VERIFICATION_WORKER_LOST:'验证进程已结束，未取得结果；没有自动重发请求。'}[receipt.reason])
    ||
    cliValidationReasonCopy[receipt.reason]
    || (receipt.reason ? String(receipt.reason).replaceAll('_', ' ') : '待验证。')
  );

  const renderCliValidationReceipt = (profileRef, receipt = {}) => {
    const row = q(`[data-cli-profile-ref="${CSS.escape(profileRef)}"]`);
    const card = row?.querySelector('[data-cli-validation]');
    if (!card) return;
    const available = receipt.status === 'AVAILABLE';
    const invalid = receipt.status === 'INVALID';
    card.className = `desktop-settings-status-card desktop-settings-cli-model-validation ${available ? 'neutral' : invalid ? 'red' : 'yellow'}`;
    const heading = card.querySelector('strong');
    const copy = card.querySelector('[data-cli-validation-copy]');
    const button = card.querySelector('[data-validate-cli]');
    if (heading) heading.textContent = available ? '验证通过' : invalid ? '验证失败' : receipt.status === 'RUNNING' ? '正在验证' : '待验证';
    if (copy) copy.textContent = ['CLI_MINIMAL_REQUEST_COMPLETED', 'CLI_MINIMAL_PROBE_PASS'].includes(receipt.reason) ? '' : cliValidationMessage(receipt);
    if (button) {
      button.textContent = available ? '再次验证' : receipt.status === 'RUNNING' ? '验证中…' : '保存并验证';
      button.disabled = cliValidationInFlight || receipt.status === 'RUNNING';
      button.hidden = receipt.status === 'RUNNING';
    }
    card.querySelector('[data-cancel-cli-verification]')?.remove();
    if (receipt.status === 'RUNNING' && receipt.job_id) {
      const stop=create('button','soft-button','停止验证');
      stop.type='button';stop.dataset.cancelCliVerification=receipt.job_id;
      stop.disabled=Boolean(receipt.cancel_requested);
      if(stop.disabled)stop.textContent='正在停止…';
      stop.onclick=async()=>{stop.disabled=true;try{await call('settings.cancel_cli_verification',{job_id:receipt.job_id});stop.textContent='正在停止…';}catch(_){stop.disabled=false;showToast('未能停止，请重试。');}};
      card.append(stop);
    }
  };

  const createCliField = (labelText, value, field, options = {}) => {
    const label = create('label', `field-label${options.className ? ` ${options.className}` : ''}`, labelText);
    const input = create('input', `field${options.mono === false ? '' : ' mono'}`);
    input.type = 'text';
    input.value = typeof value === 'string' ? value : '';
    input.maxLength = options.maxLength || 128;
    input.autocomplete = 'off';
    if (options.placeholder) input.placeholder = options.placeholder;
    input.dataset[field] = '';
    label.append(input);
    return label;
  };

  const createCliModelRow = (service, model) => {
    const row = create('section', 'desktop-settings-cli-model-row desktop-settings-cli-model-card desktop-settings-model-profile-editor');
    row.dataset.cliProfileRef = model.profile_ref;

    const head = create('header', 'desktop-settings-cli-model-head');
    const heading = create('span', 'desktop-settings-cli-model-heading');
    heading.append(
      create('strong', '', model.display_name || model.model_name || '未命名模型'),
      create('small', 'mono', model.model_name || '尚未填写模型标识')
    );
    const remove = create('button', 'text-button', '删除');
    remove.type = 'button';
    remove.dataset.removeCliModel = model.profile_ref;
    remove.dataset.cliConfigId = service.config_id;
    head.append(heading, remove);

    const grid = create('div', 'desktop-settings-model-profile-grid');
    grid.append(
      createCliField('名称', model.display_name, 'cliModelDisplay', {
        mono: false, maxLength: 80, placeholder: '例如 GPT 5.6'
      }),
      createCliField('模型标识', model.model_name, 'cliModelName', {
        maxLength: 128, placeholder: '以 CLI 实际接受的模型标识为准'
      }),
      createCliField('思考模式（可选）', model.thinking_mode || '', 'cliModelThinking', {
        className: 'desktop-settings-cli-thinking-field', maxLength: 80, placeholder: '例如 high 或 xhigh'
      })
    );

    const validationCard = create(
      'div',
      `desktop-settings-status-card desktop-settings-cli-model-validation ${model.connection_status === 'INVALID' ? 'red' : model.connection_status === 'AVAILABLE' ? 'neutral' : 'yellow'}`
    );
    validationCard.dataset.cliValidation = model.profile_ref;
    const validationCopy = create(
      'span',
      '',
      model.connection_status === 'AVAILABLE'
        ? '已完成最小连通验证。'
        : model.connection_status === 'INVALID'
          ? '上次最小验证未通过；保存当前字段后可重试。'
          : '保存后可验证。'
    );
    validationCopy.dataset.cliValidationCopy = '';
    const validate = create('button', 'soft-button', model.connection_status === 'AVAILABLE' ? '再次验证' : '保存并验证');
    validate.type = 'button';
    validate.disabled = cliValidationInFlight;
    validate.dataset.validateCli = model.profile_ref;
    validate.dataset.cliConfigId = service.config_id;
    validationCard.append(
      create('strong', '', model.connection_status === 'AVAILABLE' ? '验证通过' : model.connection_status === 'INVALID' ? '验证失败' : '待验证'),
      validationCopy,
      validate
    );
    row.append(head, grid, validationCard);
    return row;
  };

  const renderCliServices = (settings) => {
    const list = q('#settings-cli-list');
    if (!list) return;
    const openConfigIds = new Set(
      [...list.querySelectorAll('[data-cli-config-id]')]
        .filter((card) => card.querySelector('.desktop-settings-api-summary')?.getAttribute('aria-expanded') === 'true')
        .map((card) => card.dataset.cliConfigId)
    );
    list.replaceChildren();
    for (const service of settings.cli_services || []) {
      const card = create('article', 'desktop-settings-api-card desktop-settings-cli-service-card');
      card.dataset.cliConfigId = service.config_id;
      card.dataset.adapterId = service.adapter_id;
      const summary = create('button', 'desktop-settings-api-summary');
      summary.type = 'button';
      const wasOpen = openConfigIds.has(service.config_id);
      summary.setAttribute('aria-expanded', String(wasOpen));
      const stateValue = service.connection_status === 'AVAILABLE'
        ? 'green' : service.connection_status === 'INVALID' ? 'red' : 'yellow';
      const summaryCopy = create('span');
      summaryCopy.append(
        create('strong', '', service.display_name),
        create('small', '', `${service.models.length} 个模型 · ${service.enabled ? '已启用' : '未启用'} · ${connectionCopy(service.connection_status)}`)
      );
      summary.append(create('span', `status-dot ${stateValue}`), summaryCopy, create('span', '', '详情⌄'));
      const detail = create('div', 'desktop-settings-api-detail');
      detail.setAttribute('aria-hidden', String(!wasOpen));
      const inner = create('div', 'desktop-settings-api-detail-inner');
      const enableRow = create('div', 'switch-row');
      const enableCopy = create('span', 'switch-copy');
      enableCopy.append(create('strong', '', '启用此 CLI'), create('span', '', service.display_name));
      const enabled = create('button', 'switch');
      enabled.type = 'button';
      enabled.setAttribute('role', 'switch');
      enabled.setAttribute('aria-label', `启用 ${service.display_name}`);
      enabled.setAttribute('aria-checked', String(service.enabled));
      enabled.dataset.cliEnabled = service.config_id;
      enableRow.append(enableCopy, enabled);
      const commandCard = create('section', 'desktop-settings-cli-command-card desktop-settings-model-profile-editor');
      const executable = create('label', 'field-label', '命令位置');
      const executableInput = create('input', 'field mono');
      executableInput.value = service.executable;
      executableInput.maxLength = 260;
      executableInput.autocomplete = 'off';
      executableInput.placeholder = '可执行文件路径或系统 PATH 中的命令名';
      executableInput.dataset.cliExecutable = '';
      executable.append(executableInput);
      commandCard.append(executable, create('small', 'desktop-settings-cli-field-help', '配置命令位置与模型'));
      const models = create('div', 'desktop-settings-cli-models');
      models.dataset.cliModels = '';
      for (const model of service.models) models.append(createCliModelRow(service, model));
      if (!service.models.length) {
        const empty = create('div', 'desktop-settings-status-card neutral');
        empty.dataset.cliEmpty = '';
        empty.append(create('strong', '', '尚未添加模型'), create('span', '', ''));
        models.append(empty);
      }
      const add = create('section', 'desktop-settings-cli-add-model desktop-settings-cli-add-disclosure');
      const addSummary = create('button', 'desktop-settings-cli-add-summary');
      addSummary.type = 'button';
      addSummary.setAttribute('aria-expanded', 'false');
      const addHead = create('span', 'desktop-settings-cli-model-head');
      const addHeading = create('span', 'desktop-settings-cli-model-heading');
      addHeading.append(create('strong', '', '添加模型'), create('small', '', '一个 CLI 可保存多个模型'));
      addHead.append(addHeading);
      addSummary.append(addHead, create('span', 'desktop-settings-cli-add-chevron', '⌄'));
      const addDetail = create('div', 'desktop-settings-cli-add-detail');
      addDetail.setAttribute('aria-hidden', 'true');
      const addInner = create('div', 'desktop-settings-cli-add-inner desktop-settings-model-profile-editor');
      const addGrid = create('div', 'desktop-settings-model-profile-grid');
      addGrid.append(
        createCliField('名称', '', 'cliAddDisplay', {
          mono: false, maxLength: 80, placeholder: '例如 GPT 5.6'
        }),
        createCliField('模型标识', '', 'cliAddModel', {
          maxLength: 128, placeholder: '例如 gpt-5.6'
        }),
        createCliField('思考模式（可选）', '', 'cliAddThinking', {
          className: 'desktop-settings-cli-thinking-field', maxLength: 80, placeholder: '例如 high 或 xhigh'
        })
      );
      const addButton = create('button', 'soft-button', '＋ 添加模型');
      addButton.type = 'button';
      addButton.dataset.addCliModel = service.config_id;
      const addActions = create('div', 'desktop-settings-cli-add-actions');
      addActions.append(addButton);
      addInner.append(addGrid, addActions);
      addDetail.append(addInner);
      add.append(addSummary, addDetail);
      inner.append(enableRow, commandCard, models, add);
      detail.append(inner);
      card.append(summary, detail);
      list.append(card);
    }
    setCliDraftDirty(false);
  };

  const renderLocalModels = (projection) => {
    localModelsState = projection;
    const list = q('#settings-local-model-list');
    if (!list) return;
    list.replaceChildren();
    for (const endpoint of projection?.endpoints || []) {
      const card = create('article', 'desktop-settings-api-card desktop-settings-local-model-card');
      card.dataset.endpointId = endpoint.endpoint_id;
      const summary = create('div', 'desktop-settings-api-summary');
      const dot = create('span', `status-dot ${endpoint.last_verification?.service_reachable ? 'green' : 'yellow'}`);
      const copy = create('span', 'desktop-settings-api-summary-copy');
      const adapterLabel = endpoint.adapter_family === 'OLLAMA_NATIVE'
        ? uiText('local.adapter.ollama', 'Ollama 原生')
        : endpoint.kind === 'llama_cpp'
          ? uiText('local.adapter.llama', 'llama.cpp · OpenAI 兼容')
          : uiText('local.adapter.openai', 'OpenAI 兼容');
      copy.append(
        create('strong', '', endpoint.display_name),
        create('small', 'mono', `${adapterLabel} · GET ${endpoint.model_list_path || '/v1/models'} · ${endpoint.endpoint}`)
      );
      const actions = create('span', 'desktop-settings-inline');
      const verify = create('button', 'soft-button', endpoint.last_verification
        ? uiText('local.verify.again', '再次验证')
        : uiText('local.verify', '验证模型清单'));
      verify.type = 'button';
      verify.dataset.localModelVerify = endpoint.endpoint_id;
      actions.append(verify);
      if (endpoint.user_added) {
        const remove = create('button', 'text-button', uiText('local.remove', '移除'));
        remove.type = 'button';
        remove.dataset.localModelRemove = endpoint.endpoint_id;
        actions.append(remove);
      }
      summary.append(dot, copy, actions);
      const last = endpoint.last_verification;
      if (last) {
        const status = create('div', 'desktop-settings-status-card neutral');
        status.append(create('strong', '', last.service_reachable
          ? uiText('local.reachable', '本地服务可达')
          : uiText('local.unreachable', '本地服务未响应')));
        const endpointProfiles = (projection?.recognized_models || [])
          .filter((model) => model.endpoint_id === endpoint.endpoint_id);
        if (endpointProfiles.length) {
          const results = create('details', 'desktop-settings-local-model-results');
          const resultSummary = create('summary', 'desktop-settings-local-model-results-summary');
          resultSummary.append(
            create('span', '', uiText(
              'local.models.count',
              `已识别 ${endpointProfiles.length} 个模型`,
              { models: endpointProfiles.length }
            )),
            create('span', 'desktop-settings-disclosure-icon', '›')
          );
          const resultList = create('div', 'desktop-settings-local-model-options');
          for (const model of endpointProfiles) {
            const option = create(
              'div',
              'desktop-settings-local-model-option',
              model.display_name || `[本地] ${model.model_name}`
            );
            option.dataset.profileRef = model.profile_ref;
            resultList.append(option);
          }
          results.append(resultSummary, resultList);
          status.append(results);
        } else {
          status.append(create('span', '', uiText('local.models.none', '未读取到模型。')));
        }
        card.append(summary, status);
      } else {
        card.append(summary);
      }
      list.append(card);
    }
    if (state?.settings) {
      const workflowSync = publishWorkflowConsumers(state.settings, state.revision);
      window.__Desktop_SETTINGS_UI__?.setConfiguredModelOptions?.(workflowSync.configuredModelOptions);
      window.__Desktop_SETTINGS_UI__?.applyWorkflow?.(workflowSync.template);
    }
    const reachable = (projection?.endpoints || []).filter((row) => row.last_verification?.service_reachable);
    const models = reachable.flatMap((row) => row.last_verification?.models || []);
    q('#settings-local-model-dot').className = `status-dot ${reachable.length ? 'green' : 'yellow'}`;
    q('#settings-local-model-status').textContent = reachable.length
      ? uiText('local.summary', `已连接 ${reachable.length} 个服务，共 ${models.length} 个模型。`, { services: reachable.length, models: models.length })
      : uiText('local.summary.none', '未检测到可用服务。');
  };

  const renderRefinementSettings = (projection) => {
    refinementState = projection;
    window.__Desktop_SETTINGS_UI__?.setRefinementModelOptions?.(projection);
    const select = q('#settings-refinement-model');
    if (!select) return;
    const selected = projection?.settings?.profile_ref || '';
    select.replaceChildren(new Option(uiText('refinement.model.none', '未配置模型'), ''));
    for (const model of projection?.model_options || []) {
      select.append(new Option(model.display_name, model.profile_ref));
    }
    select.value = [...select.options].some((option) => option.value === selected) ? selected : '';
    setSwitch('settings-refinement-tasks', projection?.settings?.include_tasks !== false);
    setSwitch('settings-refinement-risks', projection?.settings?.include_risks !== false);
    q('#settings-refinement-dot').className = `status-dot ${projection?.configured ? 'green' : 'yellow'}`;
    q('#settings-refinement-status').textContent = projection?.configured
      ? uiText('refinement.status.configured', '已配置')
      : uiText('refinement.status.unconfigured', '未配置时不可用。');
  };

  const browserReadOutcome = (projection) => {
    const rows = (projection?.providers || []).map(id => projection?.provider_sync?.[id] || {});
    const failed = rows.filter(row => ['ERROR','FAILED','NOT_OPEN'].includes(row.status) || Number(row.failed_count || 0) > 0);
    return { rows, failed, count:rows.reduce((sum,row)=>sum+Number(row.captured_count || 0),0),
      complete:rows.length > 0 && rows.every(row=>row.status==='COMPLETE' && Number(row.failed_count || 0)===0) };
  };
  const browserReadSummary = (projection) => {
    const result=browserReadOutcome(projection);
    return result.failed.length ? uiText('browser.result.partial','已读取 {count} 条 · {failed} 个网站需重试',{count:result.count,failed:result.failed.length})
      : result.complete && projection?.import_verified ? uiText('browser.result.done','会话已同步 · 本次读取 {count} 条',{count:result.count}) : '';
  };
  const renderBrowserCompanionProgress = (projection) => {
    const label = q('#settings-browser-progress-label');
    const bar = q('#settings-browser-read-progress');
    if (!label || !bar) return;
    const prepared = projection?.prepared === true;
    const connected = projection?.bridge_identity_verified === true;
    const providerRows = Object.values(projection?.provider_sync || {});
    const synced = projection?.import_verified === true && browserReadOutcome(projection).complete;
    const completed = Number(prepared) + Number(connected) + Number(synced);
    const progress = browserCompanionProgressState;
    const stage = completed ? connected ? synced
      ? uiText('browser.stage.synced', '会话已同步')
      : uiText('browser.stage.connected-short', '浏览器已连接')
      : uiText('browser.stage.prepared', '组件已准备')
      : uiText('browser.progress.not-started', '未开始');
    let copy = uiText('browser.progress.ready', stage, { completed, stage });
    let remaining = null;
    if (prepared && !connected && !projection?.extension_id && ['idle','manual'].includes(progress.mode)) {
      copy = uiText('browser.progress.manual-load','待加载插件');
    } else if (progress.mode === 'identity' && !connected) {
      remaining = Math.max(0, progress.total - progress.attempt);
      copy = uiText('browser.progress.identity', '等待浏览器连接', { attempt:progress.attempt, total:progress.total, remaining });
    } else if (progress.mode === 'sync') {
      copy = uiText('browser.progress.sync-bar', '正在读取网页会话');
    } else if (progress.mode === 'import') {
      copy = uiText('browser.progress.importing', '正在导入会话');
    } else if (progress.mode === 'setup') {
      copy = uiText('browser.progress.setup', '正在准备浏览器组件');
    } else if (progress.mode === 'error') {
      copy = progress.error || uiText('browser.progress.error', '连接失败，请重试。');
    }
    if (['idle','complete'].includes(progress.mode)) copy = browserReadSummary(projection) || copy;
    label.textContent = copy;
    // Poll rounds are waiting progress, not seconds or completed conversations.
    const waiting = ['setup','identity','sync','import'].includes(progress.mode);
    const percent = progress.mode === 'complete' ? 100
      : Math.min(99, Math.max(0, progress.total ? progress.attempt / progress.total * 100 : 0));
    bar.hidden = !waiting;
    bar.dataset.mode = progress.mode;
    bar.setAttribute('aria-valuetext', progress.mode === 'complete' ? '读取结束' : `等待进度 ${Math.round(percent)}%`);
    if (['setup','import'].includes(progress.mode)) bar.removeAttribute('aria-valuenow');
    else bar.setAttribute('aria-valuenow', String(Math.round(percent)));
    bar.title = waiting ? '等待进度 · 不代表已读取的会话比例' : '读取结束';
    bar.firstElementChild.style.width = ['setup','import'].includes(progress.mode) ? '35%' : `${percent}%`;
  };

  const setBrowserCompanionProgressState = (nextState) => {
    browserCompanionProgressState = {
      mode:nextState?.mode || 'idle',
      attempt:Number(nextState?.attempt) || 0,
      total:Number(nextState?.total) || 0,
      error:String(nextState?.error || '')
    };
    const details = q('#settings-browser-companion-details');
    if (details && ['setup', 'identity', 'sync', 'import', 'error'].includes(browserCompanionProgressState.mode)) {
      details.open = true;
    } else if (details && browserCompanionProgressState.mode === 'complete') {
      details.open = false;
    }
    renderBrowserCompanionProgress(browserCompanionState);
    if (browserCompanionProgressState.mode === 'error') {
      q('#settings-browser-companion-badge').textContent = uiText('browser.badge.incomplete', '未完成');
    }
  };

  const renderBrowserCompanionStatus = (projection) => {
    browserCompanionState = projection;
    const picker = q('#settings-browser-companion-browser');
    const options = Array.isArray(projection?.browser_options) ? projection.browser_options : [];
    const selected = projection?.selected_browser || '';
    if (browserCompanionDraft === selected) browserCompanionDraft = null;
    const choice = browserCompanionDraft ?? (selected || options.find(row => row.installed)?.id || '');
    const selectionChanged = Boolean(choice && choice !== selected);
    const busy = ['setup','identity','sync','import'].includes(browserCompanionProgressState.mode)
      || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle'
      || browserCompanionReloadState !== 'idle';
    if (picker) {
      picker.replaceChildren(...options.map(row => {
        const option = document.createElement('option');
        option.value = row.id;
        option.textContent = row.name + (row.installed ? '' : ' · 未安装');
        option.disabled = !row.installed;
        return option;
      }));
      picker.value = choice;
      picker.disabled = busy;
    }
    if (selectionChanged) projection = {...projection, configured:false, bridge_identity_verified:false,
      preflight_eligible:false, full_sync_eligible:false, prepared:false, update_available:false,
      bridge_reload_required:false, provider_sync:{}, full_sync_gate:{}, preflight_status:'NOT_RUN'};
    const configured = projection?.configured === true;
    const identityVerified = projection?.bridge_identity_verified === true;
    // Cached bridge identity does not resolve a failed or unfinished read.
    // Keep the last attempt visible until the user starts another operation.
    const preflightEligible = projection?.preflight_eligible === true;
    const prepared = projection?.prepared === true;
    const reloadRequired = projection?.bridge_reload_required === true;
    const updateAvailable = projection?.update_available === true;
    const preflightStatus = ['NOT_RUN', 'RUNNING', 'PASS', 'FAIL'].includes(projection?.preflight_status)
      ? projection.preflight_status
      : 'NOT_RUN';
    const nextSyncMode = projection?.next_sync_mode === 'INCREMENTAL' ? 'INCREMENTAL' : 'PREFLIGHT';
    const providerSync = projection?.provider_sync || {};
    const fullSyncGate = projection?.full_sync_gate || {};
    const fullSyncProviders = fullSyncGate?.providers || {};
    const fullSyncEligible = projection?.full_sync_eligible === true;
    const providerRows = (projection.providers || ['deepseek', 'gemini', 'kimi']).map((provider) => providerSync[provider] || {});
    const capturedTotal = providerRows.reduce((total, row) => total + Number(row.captured_count || 0), 0);
    const failedTotal = providerRows.reduce((total, row) => total + Number(row.failed_count || 0), 0);
    const providersComplete = providerRows.length > 0 && providerRows.every((row) => row.status === 'COMPLETE') && failedTotal === 0;
    const syncRunning = ['sync','import'].includes(browserCompanionProgressState.mode)
      || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    const syncFailed = !syncRunning && (browserCompanionProgressState.mode === 'error' || browserReadOutcome(projection).failed.length > 0);
    const syncComplete = !syncRunning && !syncFailed && projection?.import_verified === true
      && providersComplete;
    q('#settings-browser-companion-badge').textContent = syncFailed
      ? uiText('browser.badge.incomplete', '未完成')
      : reloadRequired
      ? uiText('browser.badge.reload', '待重载桥接')
      : updateAvailable
        ? uiText('browser.badge.update', '可更新')
        : !prepared
          ? uiText('browser.badge.unconfigured', '未配置')
          : !configured
            ? (!projection?.extension_id ? uiText('browser.progress.manual-load','待加载插件') : uiText('browser.badge.prepared', '待连接'))
            : syncRunning ? uiText('browser.badge.syncing', '已连接 · 同步中')
            : providersComplete && capturedTotal === 0
              ? uiText('browser.badge.empty', '已连接 · 0 条会话')
              : syncComplete
                ? uiText('browser.badge.synced', '已同步')
                : syncRunning
                  ? uiText('browser.badge.syncing', '已连接 · 同步中')
                  : uiText('browser.badge.connected', '已连接');
    const setStage = (id, complete, copy) => {
      const stage = q(`#${id}`);
      if (!stage) return;
      const dot = stage.querySelector('.status-dot');
      if (dot) dot.className = `status-dot ${complete ? 'green' : 'yellow'}`;
      stage.dataset.state = complete ? 'complete' : 'pending';
      if (copy) stage.querySelector('small').textContent = copy;
    };
    setStage('settings-browser-companion-prepared', prepared, prepared
      ? uiText('browser.stage.prepared', '组件已准备')
      : uiText('browser.stage.wait-prepared', '等待准备组件'));
    setStage('settings-browser-companion-connected', identityVerified, identityVerified
      ? uiText('browser.stage.connected', `${projection?.connected_browser || '浏览器'} 身份已确认`, { browser: projection?.connected_browser || '浏览器' })
      : uiText('browser.stage.wait-connected', '等待桥接身份回执'));
    setStage('settings-browser-companion-synced', syncComplete, syncRunning
      ? (browserCompanionProgressState.mode === 'import' ? uiText('browser.progress.importing','正在导入会话') : uiText('browser.progress.sync-bar','正在读取网页会话'))
      : syncFailed ? uiText('browser.stage.read-failed','本次读取未完成')
      : syncComplete ? uiText('browser.stage.synced', '会话已同步')
      : uiText('browser.stage.wait-synced', '等待会话同步'));
    if(syncFailed)q('#settings-browser-companion-synced .status-dot').className='status-dot red';
    showBridgeSetupGuide(projection);
    window.__Desktop_BROWSER_SITES__?.render(projection, browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle');
    for (const provider of window.__Desktop_BROWSER_SITES__ && projection.site_settings ? [] : ['deepseek', 'gemini', 'kimi']) {
      const row = q(`#settings-browser-provider-${provider}`);
      if (!row) continue;
      const status = providerSync[provider] || {};
      const safety = fullSyncProviders[provider] || {};
      const captured = Number(status.captured_count || 0);
      const failed = Number(status.failed_count || 0);
      const complete = status.status === 'COMPLETE' && failed === 0;
      row.dataset.safetyState = safety.status || 'PREFLIGHT_REQUIRED';
      if (safety.status === 'HUMAN_VERIFICATION_BLOCKED') {
        row.dataset.state = 'error';
        row.querySelector('small').textContent = uiText('browser.provider.safe.human', '请在浏览器完成人机验证。');
      } else if (safety.status === 'PREFLIGHT_FAILED') {
        row.dataset.state = 'error';
        row.querySelector('small').textContent = uiText('browser.provider.safe.failed', `试读失败 ${Number(safety.failed_count || 0)} 条`, { count: Number(safety.failed_count || 0) });
      } else if (safety.status === 'PREFLIGHT_PASS') {
        row.dataset.state = 'complete';
        row.querySelector('small').textContent = uiText('browser.provider.safe.pass', `已读取 ${Number(safety.captured_count || 0)} 条`, { count: Number(safety.captured_count || 0) });
      } else if (safety.status === 'NO_CAPTURED_CONVERSATIONS') {
        row.dataset.state = 'pending';
        row.querySelector('small').textContent = uiText('browser.provider.safe.empty', '未读取到会话');
      } else {
        row.dataset.state = failed ? 'error' : complete ? 'complete' : 'pending';
        row.querySelector('small').textContent = identityVerified
          ? uiText('browser.provider.safe.required', '等待已启用网站试读')
          : uiText('browser.provider.wait-connect', '等待身份确认');
      }
    }
    const reloadButton = q('#settings-browser-companion-reload');
    if (reloadButton) {
      const reloadBusy = browserCompanionReloadState !== 'idle';
      reloadButton.disabled = !prepared || updateAvailable || busy || reloadBusy;
      reloadButton.setAttribute('aria-disabled', String(reloadButton.disabled));
      reloadButton.setAttribute('aria-busy', String(browserCompanionReloadState === 'starting'));
    }
    const setupButton = q('#settings-browser-companion-setup');
    if (setupButton) {
      const setupBusy = browserCompanionSetupInFlight || browserCompanionProgressState.mode === 'setup';
      const alreadyConfigured = configured && !reloadRequired && !updateAvailable;
      setupButton.disabled = (busy && browserCompanionProgressState.mode !== 'identity') || setupBusy || alreadyConfigured || !options.some(row => row.id === choice && row.installed);
      setupButton.setAttribute('aria-disabled', String(setupButton.disabled));
      setupButton.setAttribute('aria-busy', String(setupBusy));
      setupButton.textContent = alreadyConfigured
        ? uiText('browser.setup.current', '浏览器已连接')
        : updateAvailable
          ? uiText('browser.setup.update', '更新浏览器扩展')
          : prepared
            ? uiText('browser.setup.continue', '继续连接')
            : uiText('browser.setup', '连接浏览器');
    }
    const preflightButton = q('#settings-browser-companion-sync');
    if (preflightButton) {
      const preflightBusy = browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
      preflightButton.disabled = !preflightEligible || ((projection?.incremental_sync_eligible !== true || syncFailed) && root.querySelector('.desktop-settings-shell')?.dataset.settingsMode!=='normal') || preflightBusy;
      preflightButton.setAttribute('aria-disabled', String(preflightButton.disabled));
      if (!preflightBusy) {
        preflightButton.textContent = root.querySelector('.desktop-settings-shell')?.dataset.settingsMode==='normal'?'同步会话':'增量同步';
      }
    }
    const fullButton = q('#settings-browser-companion-full-sync');
    const testButton = q('#settings-browser-companion-test');
    if (testButton) testButton.disabled = !preflightEligible || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    if (fullButton) {
      const fullBusy = browserCompanionFullSyncState !== 'idle' || browserCompanionSyncState !== 'idle';
      fullButton.disabled = !fullSyncEligible || fullBusy || syncFailed;
      fullButton.setAttribute('aria-disabled', String(!fullSyncEligible || fullBusy || syncFailed));
    }
    const gateRows = Object.values(fullSyncProviders);
    const humanVerification = gateRows.some((row) => row?.human_verification_detected === true);
    const gateDot = q('#settings-browser-full-gate-dot');
    const gateStatus = q('#settings-browser-full-gate-status');
    if (gateDot) gateDot.className = `status-dot ${syncFailed ? 'red' : syncRunning ? 'yellow' : fullSyncEligible ? 'green' : humanVerification ? 'red' : 'yellow'}`;
    if (gateStatus) gateStatus.textContent = syncFailed ? uiText('browser.stage.read-failed','本次读取未完成')
      : syncRunning ? (browserCompanionProgressState.mode === 'import' ? uiText('browser.progress.importing','正在导入会话') : uiText('browser.progress.sync-bar','正在读取网页会话'))
      : fullSyncEligible
      ? uiText('browser.gate.ready', '可以完整同步')
      : humanVerification
        ? uiText('browser.gate.human', '请在浏览器完成人机验证。')
        : preflightStatus === 'FAIL'
          ? uiText('browser.gate.pending', '试同步未完成')
          : uiText('browser.gate.required', '请先完成试同步');
    q('.desktop-settings-browser-stage-grid').hidden = identityVerified;
    if (gateStatus) gateStatus.parentElement.hidden = identityVerified;
    renderBrowserCompanionProgress(projection);
    return projection;
  };

  const renderAssistantAppearance = (enabled) => {
    setSwitch('settings-assistant-enabled', enabled);
    setSwitch('settings-assistant-enabled-appearance', enabled);
    const dot = q('#settings-assistant-appearance-dot');
    const status = q('#settings-assistant-appearance-status');
    const show = q('#settings-assistant-show');
    if (dot) dot.className = `status-dot ${enabled ? 'green' : 'yellow'}`;
    if (status) status.textContent = enabled
      ? uiText('assistant.status.enabled', '已启用')
      : uiText('assistant.status.disabled', '已关闭');
    if (show) show.disabled = !enabled;
  };

  const renderAssistantPreferences = (projection) => {
    assistantPreferences = projection;
    const preferences = projection?.preferences || {};
    renderAssistantAppearance(preferences.enabled !== false);
    const entries = Array.isArray(preferences.shortcut_entries) ? preferences.shortcut_entries : [];
    for (let index = 0; index < 3; index += 1) {
      const entry = entries[index] || {};
      const name = q(`#settings-assistant-shortcut-name-${index + 1}`);
      const target = q(`#settings-assistant-shortcut-target-${index + 1}`);
      if (name) name.value = entry.name || '';
      if (target) target.value = entry.target || '';
    }
    if (q('#settings-assistant-reminder-scope')) {
      q('#settings-assistant-reminder-scope').value = preferences.reminder_scope || 'IMPORTANT_AND_COMPLETE';
    }
    setSwitch('settings-assistant-hide-names', preferences.hide_sensitive_names === true);
  };

  const refreshAuxiliarySettings = async ({ preserveDraft = false } = {}) => {
    const [localModels, refinement, assistant, browserCompanion] = await Promise.all([
      call('local_models.bootstrap', {}),
      call('sessions.refinement_settings_get', {}),
      call('assistant.preference_get', {}),
      nativeAction('browser_companion_status')
    ]);
    if (preserveDraft && hasPendingSettingsDraft()) return { localModels, refinement, assistant, browserCompanion };
    renderLocalModels(localModels);
    renderRefinementSettings(refinement);
    renderAssistantPreferences(assistant);
    renderBrowserCompanionStatus(browserCompanion);
    return { localModels, refinement, assistant, browserCompanion };
  };

  const setLocalModelBusy = (busy) => {
    localModelBusy = busy;
    const button = q('#settings-local-model-refresh');
    const label = button?.querySelector('[data-local-model-refresh-label]');
    if (!button) return;
    button.disabled = busy;
    button.setAttribute('aria-busy', String(busy));
    if (label) label.textContent = busy
      ? uiText('local.detecting', '正在检测')
      : uiText('local.detect', '发现模型');
    if (busy) {
      q('#settings-local-model-dot').className = 'status-dot blue running';
      q('#settings-local-model-status').textContent = uiText('local.detecting.status', '正在发现模型…');
    }
  };

  const discoverLocalModels = async () => {
    if (localModelBusy) return null;
    setLocalModelBusy(true);
    try {
      const receipt = await call('local_models.discover', {});
      renderLocalModels(await call('local_models.bootstrap', {}));
      renderRefinementSettings(await call('sessions.refinement_settings_get', {}));
      showToast(receipt.reachable_endpoint_count
        ? uiText('local.detect.complete', `检测完成：${receipt.model_count} 个模型。`, { models: receipt.model_count })
        : uiText('local.detect.empty', '检测完成：没有服务响应。'));
      return receipt;
    } catch (error) {
      q('#settings-local-model-dot').className = 'status-dot red';
      q('#settings-local-model-status').textContent = uiText('local.detect.failed', '检测失败，请重试。');
      throw error;
    } finally {
      setLocalModelBusy(false);
    }
  };

  q('#settings-browser-companion-browser')?.addEventListener('change', event => {
    browserCompanionDraft = event.target.value;
    if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
  });


  const showBridgeSetupGuide = (status) => {
    const tools=q('#memo-bridge-setup-guide');if(!tools)return;
    tools.hidden=!status?.prepared || status?.bridge_identity_verified===true;
    q('#memo-bridge-folder').value=status?.extension_path || '';
  };
  q('#memo-bridge-copy-folder')?.addEventListener('click', async () => {
    const value=q('#memo-bridge-folder')?.value;if(!value)return;
    try { await navigator.clipboard.writeText(value); }
    catch (_) {
      const focus=document.activeElement,input=document.createElement('textarea');
      input.value=value;input.style.cssText='position:fixed;opacity:0;pointer-events:none';
      document.body.append(input);input.select();
      let copied=false;try { copied=document.execCommand('copy'); } finally { input.remove();focus?.focus(); }
      if(!copied){showToast('复制失败，请打开文件夹');return;}
    }
    showToast('路径已复制');
  });
  q('#memo-bridge-open-folder')?.addEventListener('click',()=>void nativeAction('open_browser_companion_folder').catch(()=>showToast('文件夹无法打开，请复制路径手动打开')));
  q('#memo-bridge-check')?.addEventListener('click',async()=>{
    const button=q('#memo-bridge-check');button.disabled=true;
    try {
      const status=renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      showBridgeSetupGuide(status);
      showToast(status?.bridge_identity_verified ? '浏览器已连接' : '等待插件连接');
    } catch (_) { showToast('连接检查失败，请重试'); }
    finally { button.disabled=false; }
  });

  window.addEventListener('focus', () => {
    if(browserCompanionProgressState.mode !== 'manual')return;
    void nativeAction('browser_companion_status').then(status=>{
      if(status?.bridge_identity_verified)setBrowserCompanionProgressState({mode:'complete'});
      renderBrowserCompanionStatus(status);
    }).catch(()=>{});
  });

  const prepareBrowserCompanion = async () => {
    if(window.__Desktop_BROWSER_SITES__?.isDirty()){showToast('请先保存网页设置');return {status:'UNSAVED'};}
    if (browserCompanionSetupInFlight) return {status:'PREPARING'};
    // Waiting for the extension must not lock the user out of reopening setup.
    // Reuse the existing identity poll instead of starting another five-minute poll.
    if (browserCompanionProgressState.mode === 'identity') {
      browserCompanionSetupInFlight = true;
      if(browserCompanionState)renderBrowserCompanionStatus(browserCompanionState);
      try {
        const receipt=await nativeAction('install_browser_companion',q('#settings-browser-companion-browser')?.value || null);
        showToast(receipt.browser_manager_opened ? '已打开插件配置' : receipt.bridge_reload_requested ? '正在重新连接' : '等待插件连接');
        return receipt;
      } finally {
        browserCompanionSetupInFlight = false;
        if(browserCompanionState)renderBrowserCompanionStatus(browserCompanionState);
      }
    }
    setBrowserCompanionProgressState({ mode:'setup' });
    let receipt;
    let status;
    try {
      receipt = await nativeAction('install_browser_companion', q('#settings-browser-companion-browser')?.value || null);
      browserCompanionDraft = null;
      status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
    } catch (error) {
      const message = /SELECTED_BROWSER_NOT_FOUND|SUPPORTED_BROWSER_NOT_FOUND/.test(String(error)) ? '未找到所选浏览器，请检查安装位置或重新选择。' : uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。');
      setBrowserCompanionProgressState({ mode:'error', error:message });
      throw error;
    }
    if (receipt?.status === 'BROWSER_NO_VERIFIED_OFFICIAL_SITES') {
      const error = '请先启用已核实的模型官网';
      setBrowserCompanionProgressState({ mode:'error', error });
      showToast(error);
      return receipt;
    }
    if (receipt?.status === 'ALREADY_CONFIGURED') {
      setBrowserCompanionProgressState({ mode:'complete' });
      showToast(uiText('browser.toast.current', '已配置'));
      return receipt;
    }
    showBridgeSetupGuide(status);
    if (receipt.manual_load_required && !status?.bridge_identity_verified) {
      setBrowserCompanionProgressState({mode:'manual'});
      renderBrowserCompanionStatus(status);
      showToast(uiText('browser.toast.load-plugin','请在扩展页加载插件文件夹'));
      return receipt;
    }
    const reloadTriggered = ['BRIDGE_RELOAD_TRIGGERED', 'BRIDGE_RELOAD_READY'].includes(receipt.status);
    showToast(reloadTriggered
      ? uiText('browser.toast.reload', '正在重新连接')
      : receipt.manual_load_required
        ? uiText('browser.toast.manual', '已打开扩展管理页和组件文件夹；加载后将验证桥接身份。')
        : uiText('browser.toast.prepared', '已准备组件文件夹；请在所选浏览器的扩展页加载一次。'));
    if (reloadTriggered) setBrowserCompanionReloadState('triggered');
    setBrowserCompanionProgressState({ mode:'identity', attempt:0, total:300 });
    void pollBrowserCompanionIdentity({ attempts: 300 }).then((status) => {
      if (status?.bridge_identity_verified === true) {
        setBrowserCompanionProgressState({ mode:'complete' });
        showToast(uiText('browser.toast.identity', `只读桥接 ${status.extension_version || '3.6.1'} 已连接`));
      } else {
        const error = '等待插件连接；可继续配置或检查连接。';
        setBrowserCompanionProgressState({ mode:'error', error });
      }
    }).catch(() => {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
    }).finally(() => {
      setBrowserCompanionReloadState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    });
    return receipt;
  };

  const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const setBrowserCompanionReloadState = (nextState) => {
    browserCompanionReloadState = nextState;
    const button = q('#settings-browser-companion-reload');
    if (!button) return;
    const starting = nextState === 'starting';
    const triggered = nextState === 'triggered';
    button.disabled = starting || triggered || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    button.setAttribute('aria-busy', String(starting));
    button.setAttribute('aria-disabled', String(button.disabled));
    button.textContent = starting
      ? uiText('browser.reload.starting', '正在重新连接')
      : triggered
        ? uiText('browser.reload.triggered', '只读桥接已触发重载')
        : uiText('browser.reload', '重新连接');
  };
  const setBrowserCompanionSyncState = (nextState, syncMode = null) => {
    browserCompanionSyncState = nextState;
    browserCompanionActiveSyncMode = nextState === 'idle'
      ? null
      : (syncMode || browserCompanionActiveSyncMode || browserCompanionState?.next_sync_mode || 'PREFLIGHT');
    const busy = nextState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    for (const [id, mode, label] of [
      ['settings-browser-companion-test','PREFLIGHT','测试读取'],
      ['settings-browser-companion-sync','INCREMENTAL','增量同步']
    ]) {
      const button=q('#'+id);
      if (!button) continue;
      const eligible=browserCompanionState?.preflight_eligible === true &&
        (mode==='PREFLIGHT' || browserCompanionState?.incremental_sync_eligible===true || root.querySelector('.desktop-settings-shell')?.dataset.settingsMode==='normal');
      button.disabled=busy || !eligible;
      button.setAttribute('aria-disabled',String(button.disabled));
      const active=nextState!=='idle' && browserCompanionActiveSyncMode===mode;
      button.setAttribute('aria-busy',String(active));
      button.textContent=active ? (nextState==='starting' ? '启动中…' : '读取中…') : label;
    }
  };
  const setBrowserCompanionFullSyncState = (nextState) => {
    browserCompanionFullSyncState = nextState;
    const button = q('#settings-browser-companion-full-sync');
    if (!button) return;
    const starting = nextState === 'starting';
    const triggered = nextState === 'triggered';
    button.disabled = starting || triggered || browserCompanionState?.full_sync_eligible !== true;
    button.setAttribute('aria-busy', String(starting));
    button.setAttribute('aria-disabled', String(button.disabled));
    button.textContent = starting
      ? uiText('browser.full.starting', '正在启动完整同步')
      : triggered
        ? uiText('browser.full.triggered', '完整同步已触发')
        : uiText('browser.full', '完整同步');
  };
  const pollBrowserCompanion = async ({ attempts = 1, requestSync = false, expectedMode = null, previousReportSha = null } = {}) => {
    if (requestSync) await nativeAction('run_browser_companion_sync');
    let latest = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      setBrowserCompanionProgressState({ mode:'sync', attempt:attempt + 1, total:attempts });
      latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      const latestReportSha = latest?.latest_sync_report_sha256 || null;
      const expectedReportArrived = Boolean(
        expectedMode
        && latest?.latest_sync_mode === expectedMode
        && latestReportSha
        && latestReportSha !== previousReportSha
      );
      if (expectedMode ? expectedReportArrived : (latest?.configured && latest?.import_verified)) {
        setBrowserCompanionProgressState({mode:'import'});
        let imported;
        try { imported = await nativeAction('sync_browser_sessions_from_downloads'); }
        catch (error) {
          setBrowserCompanionProgressState({mode:'error',error:uiText('browser.progress.import-failed','会话导入失败 · 可重试')});
          return latest;
        }
        latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
        if (Number(imported?.failure_count || 0) > 0 || !latest?.import_verified) {
          setBrowserCompanionProgressState({mode:'error',error:uiText('browser.progress.import-failed','会话导入失败 · 可重试')});
          return latest;
        }
        const failed = browserReadOutcome(latest).failed.length > 0;
        setBrowserCompanionProgressState(failed
          ? { mode:'error', error:browserReadSummary(latest) }
          : { mode:'complete' });
        return latest;
      }
      if (attempt + 1 < attempts) await wait(1500);
    }
    if (browserCompanionProgressState.mode !== 'complete') {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.progress.no-receipt','未收到本次读取结果 · 可重试') });
    }
    return latest;
  };

  const pollBrowserCompanionIdentity = async ({ attempts = 1 } = {}) => {
    let latest = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      setBrowserCompanionProgressState({ mode:'identity', attempt:attempt + 1, total:attempts });
      latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      if (latest?.bridge_identity_verified === true) {
        setBrowserCompanionProgressState({ mode:'complete' });
        break;
      }
      if (attempt + 1 < attempts) await wait(1000);
    }
    return latest;
  };

  const reloadBrowserCompanion = async () => {
    if (browserCompanionReloadState !== 'idle') return browserCompanionState;
    setBrowserCompanionReloadState('starting');
    try {
      const launch = await nativeAction('run_browser_companion_reload');
      if (!['BRIDGE_RELOAD_TRIGGERED', 'BRIDGE_RELOAD_READY'].includes(launch?.status)) {
        return renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      }
      setBrowserCompanionReloadState('triggered');
      showToast(uiText('browser.toast.reload', '正在重新连接'));
      const status = await pollBrowserCompanionIdentity({ attempts: 300 });
      if (status?.bridge_identity_verified === true) {
        const version = status.extension_version || '3.6.1';
        showToast(uiText('browser.toast.identity', `只读桥接 ${version} 身份已确认，可以开始网站试读。`, { version }));
      } else {
        const error = '等待插件连接；可继续配置或检查连接。';
        setBrowserCompanionProgressState({ mode:'error', error });
      }
      return status;
    } catch (error) {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
      throw error;
    } finally {
      setBrowserCompanionReloadState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    }
  };

  const syncBrowserCompanion = async (explicitMode = 'INCREMENTAL') => {
    if (browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle') return browserCompanionState;
    if (browserCompanionState?.preflight_eligible !== true) {
      showToast(uiText('browser.toast.identity-required', '请先确认当前只读桥接身份，再开始网站试读。'));
      return browserCompanionState;
    }
    if(window.__Desktop_BROWSER_SITES__?.isDirty()){showToast('请先保存网页设置');return browserCompanionState;}
    const requestedMode = explicitMode==='AUTO' ? (browserCompanionState?.incremental_sync_eligible && !browserReadOutcome(browserCompanionState).failed.length ? 'INCREMENTAL' : 'PREFLIGHT') : explicitMode;
    if (requestedMode === 'INCREMENTAL' && browserCompanionState?.incremental_sync_eligible !== true) { showToast('请先测试读取'); return browserCompanionState; }
    setBrowserCompanionSyncState('starting', requestedMode);
    renderBrowserCompanionStatus(browserCompanionState);
    try {
      const previousReportSha = (requestedMode === 'PREFLIGHT' ? browserCompanionState?.preflight_report_sha256 : browserCompanionState?.latest_sync_report_sha256) || null;
      const launch = requestedMode === 'PREFLIGHT' ? await nativeAction('run_browser_companion_test_read') : await nativeAction('run_browser_companion_sync','INCREMENTAL');
      if (!['SYNC_TRIGGERED', 'SYNC_READY', 'INCREMENTAL_SYNC_TRIGGERED', 'INCREMENTAL_SYNC_READY'].includes(launch?.status)) {
        const status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
        showToast(uiText('browser.toast.identity-required', '请先确认当前只读桥接身份，再开始网站试读。'));
        return status;
      }
      const launchedMode = launch?.sync_mode === 'INCREMENTAL' ? 'INCREMENTAL' : 'PREFLIGHT';
      setBrowserCompanionSyncState('triggered', launchedMode);
      showToast(launchedMode === 'INCREMENTAL'
        ? uiText('browser.toast.incremental-triggered', '网站增量同步已触发，每站最多读取 10 条正文。')
        : uiText('browser.toast.triggered', '已启用网站试读已触发，每站最多 2 条。'));
      const status = await pollBrowserCompanion({ attempts: ({PREFLIGHT:820, INCREMENTAL:1220, FULL:3620}[launchedMode] || 820), requestSync: false, expectedMode: launchedMode, previousReportSha });
      if (browserCompanionProgressState.mode === 'error') return status;
      if (launchedMode === 'PREFLIGHT' && status?.full_sync_eligible) {
        showToast(uiText('browser.toast.preflight-ready', '已启用网站安全通过，现可手动启动完整同步。'));
      } else if (status?.import_verified) {
        showToast(uiText('browser.toast.synced', '浏览器会话库已验证并同步。'));
      }
      return status;
    } catch (error) {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
      throw error;
    } finally {
      setBrowserCompanionSyncState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    }
  };

  const syncBrowserCompanionFull = async () => {
    if (browserCompanionFullSyncState !== 'idle' || browserCompanionSyncState !== 'idle') return browserCompanionState;
    if (browserCompanionState?.full_sync_eligible !== true) {
      showToast(uiText('browser.toast.full-blocked', '最近一次已启用网站试读尚未全部通过，完整同步保持禁用。'));
      return browserCompanionState;
    }
    setBrowserCompanionFullSyncState('starting');
    renderBrowserCompanionStatus(browserCompanionState);
    try {
      const previousReportSha = browserCompanionState?.latest_sync_report_sha256 || null;
      const launch = await nativeAction('run_browser_companion_full_sync');
      if (!['FULL_SYNC_TRIGGERED', 'FULL_SYNC_READY'].includes(launch?.status)) {
        const status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
        showToast(uiText('browser.toast.full-blocked', '最近一次已启用网站试读尚未全部通过，完整同步保持禁用。'));
        return status;
      }
      setBrowserCompanionFullSyncState('triggered');
      showToast(uiText('browser.toast.full-triggered', '完整同步已触发，正在等待后台会话包。'));
      return await pollBrowserCompanion({ attempts: 3620, requestSync: false, expectedMode: 'FULL', previousReportSha });
    } catch (error) {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
      throw error;
    } finally {
      setBrowserCompanionFullSyncState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    }
  };

  const checkApiCallReadiness = async () => {
    const target = q('#settings-api-readiness-copy');
    target.textContent = '正在检查配置';
    const projection = await nativeAction('api_call_readiness');
    target.textContent = projection.configuration_ready_count
      ? `配置检查通过 · ${projection.configuration_ready_count} 个模型`
      : '请先完善模型配置';
    return projection;
  };

  const localEndpointDraft = () => ({
      display_name: q('#settings-local-model-name').value.trim(),
      kind: q('#settings-local-model-kind').value,
      endpoint: q('#settings-local-model-endpoint').value.trim()
  });
  let localEndpointBaseline = localEndpointDraft();
  const saveLocalModelEndpoint = async () => {
    const draft = localEndpointDraft();
    if (!draft.display_name || !draft.endpoint) throw new Error('请填写本机服务名称和地址。');
    const receipt = await call('local_models.endpoint_save', draft);
    renderLocalModels(await call('local_models.bootstrap', {}));
    q('#settings-local-model-name').value = '';
    localEndpointBaseline = localEndpointDraft();
    syncSaveState();
    showToast(uiText('local.added', '本机服务已添加。'));
    return receipt;
  };

  registerSaveParticipant('local-model-endpoint', {
    isDirty: () => JSON.stringify(localEndpointDraft()) !== JSON.stringify(localEndpointBaseline),
    save: saveLocalModelEndpoint,
    discard: () => {
      q('#settings-local-model-name').value = localEndpointBaseline.display_name;
      q('#settings-local-model-kind').value = localEndpointBaseline.kind;
      q('#settings-local-model-endpoint').value = localEndpointBaseline.endpoint;
      syncSaveState();
    }
  });

  const verifyLocalModel = async (endpointId) => {
    const button = qa('[data-local-model-verify]').find((node) => node.dataset.localModelVerify === endpointId);
    if (button) { button.disabled = true; button.textContent = uiText('local.verifying', '验证中…'); }
    try {
      const receipt = await call('local_models.verify', { endpoint_id: endpointId });
      renderLocalModels(await call('local_models.bootstrap', {}));
      renderRefinementSettings(await call('sessions.refinement_settings_get', {}));
      showToast(receipt.service_reachable
        ? uiText('local.verify.reachable', `服务可达，共 ${receipt.models.length} 个模型。`, { models: receipt.models.length })
        : uiText('local.verify.unreachable', `本地服务未响应：${receipt.error_code || receipt.status}`, { error: receipt.error_code || receipt.status }));
      return receipt;
    } finally {
      if (button && document.contains(button)) { button.disabled = false; button.textContent = uiText('local.verify.again', '再次验证'); }
    }
  };

  const removeLocalModel = async (endpointId) => {
    const receipt = await call('local_models.endpoint_remove', { endpoint_id: endpointId });
    renderLocalModels(await call('local_models.bootstrap', {}));
    renderRefinementSettings(await call('sessions.refinement_settings_get', {}));
    showToast(uiText('local.removed', '自定义本地模型端点已移除。'));
    return receipt;
  };

  const refinementDraft = () => ({
      profile_ref: q('#settings-refinement-model').value,
      include_tasks: switchValue('settings-refinement-tasks'),
      include_risks: switchValue('settings-refinement-risks')
  });

  const saveRefinementSettings = async (draft = refinementDraft(), options = {}) => {
    const receipt = await call('sessions.refinement_settings_save', draft);
    if (options.refreshAfter !== false) {
      renderRefinementSettings(await call('sessions.refinement_settings_get', {}));
    }
    q('#settings-refinement-dot').className = 'status-dot green';
    q('#settings-refinement-status').textContent = uiText('refinement.status.saved', '已保存');
    if (options.announce !== false) showToast(uiText('refinement.saved.toast', '已保存'));
    return receipt;
  };

  const assistantDraft = () => ({
    enabled: switchValue('settings-assistant-enabled'),
    shortcut_entries: [1, 2, 3].map((index) => ({
      name: q(`#settings-assistant-shortcut-name-${index}`).value.trim(),
      target: q(`#settings-assistant-shortcut-target-${index}`).value.trim()
    })),
    reminder_scope: q('#settings-assistant-reminder-scope').value,
    hide_sensitive_names: switchValue('settings-assistant-hide-names')
  });

  const runAssistantAction = async (actionId, successMessage) => {
    const receipt = await nativeAction('assistant_shortcut', { action_id: actionId });
    if (receipt?.presentation_refresh_status === 'DEGRADED') {
      showToast('桌面助手已显示；任务状态将在服务恢复后自动刷新。');
    } else if (successMessage) showToast(successMessage);
    return receipt;
  };

  const saveAssistantPreferences = async (draft = assistantDraft(), options = {}) => {
    const receipt = await call('assistant.preference_save', draft);
    if (options.refreshAfter !== false) renderAssistantPreferences(receipt);
    if (options.announce !== false) showToast(uiText('assistant.saved.toast', '桌面助手设置已保存并联动。'));
    return receipt;
  };

  const applySettings = (nextState) => {
    if (!nextState?.settings) return;
    applyingState = true;
    const settings = nextState.settings;
    const preferences = settings.preferences;
    try {
      const shell = q('.desktop-settings-shell');
      const persistedMode = settings.mode === 'DEVELOPER' ? 'developer' : settings.mode === 'ADVANCED' ? 'advanced' : 'normal';
      const activeMode = settingsAppliedOnce ? activeSettingsMode() : persistedMode;
      const mode = shell?.dataset.modeScope === 'workflow' && activeMode === 'advanced'
        ? 'normal' : activeMode;
      if (window.__Desktop_SETTINGS_UI__?.applySettingsMode) {
        window.__Desktop_SETTINGS_UI__.applySettingsMode(mode);
      } else {
        qa('#settings-mode button[data-mode]').forEach((button) => {
          button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
        });
        if (shell) shell.dataset.settingsMode = mode;
      }

      q('#settings-workspace-root').value = settings.directories.workspace_root;
      q('#settings-artifact-root').value = settings.directories.artifact_root;
      setSelect('settings-auto-open-preview', String(preferences.auto_open_preview));
      setSelect('settings-document-viewer', preferences.document_viewer);

      const library = settings.directories.external_library;
      setSelect('settings-library-provider', library.enabled ? library.provider_kind : 'NONE');
      q('#settings-library-name').value = library.display_name || '';
      q('#settings-library-root').value = library.root || '';
      q('#settings-library-status').textContent = library.enabled ? '已保存 · 待本地检测' : '未配置';
      q('#settings-library-copy').textContent = library.enabled
        ? '目录已保存；点击检测核对当前可访问状态'
        : '尚未配置';
      q('#settings-library-dot').className = 'status-dot yellow';
      setSwitch('settings-external-refresh', preferences.external_refresh);

      setSwitch('settings-notify-complete', preferences.task_complete_notification);
      setSwitch('settings-notify-error', preferences.task_error_notification);
      setSwitch('settings-notify-approval', preferences.approval_notification);
      setSwitch('settings-notify-weekly', preferences.weekly_report_notification);
      setSwitch('settings-notify-sound', preferences.notification_sound);
      q('#settings-dnd-start').value = preferences.do_not_disturb_start;
      q('#settings-dnd-end').value = preferences.do_not_disturb_end;
      setSwitch('settings-notify-open-task', preferences.notification_open_task);

      setSwitch('settings-restore-view', preferences.restore_last_view);
      setSelect('settings-close-behavior', preferences.close_behavior);
      setSwitch('settings-warn-close', preferences.warn_on_close_running);
      setSwitch('settings-launch-startup', preferences.launch_at_startup);
      setSwitch('settings-background-tasks', preferences.keep_tasks_in_background);
      setSwitch('settings-remember-panels', preferences.remember_panel_state);

      setSelect('settings-proxy-mode', preferences.proxy_mode.toLowerCase());
      q('#settings-proxy-address').value = preferences.proxy_address;
      q('#settings-request-timeout').value = String(preferences.request_timeout_seconds);
      q('#settings-network-dot').className = 'status-dot yellow';
      q('#settings-network-status').textContent = '外部连接未检测';
      q('#settings-network-copy').textContent = '检查本地与出口网络';
      q('#settings-local-ip').textContent = '—';
      q('#settings-egress-ip').textContent = '—';
      q('#settings-egress-location').textContent = '—';

      setSelect('settings-log-level', preferences.log_level);
      setSelect('settings-retention-days', String(preferences.retention_days));
      setSelect('settings-cache-limit', String(preferences.cache_limit_mb));
      setSwitch('settings-telemetry', preferences.telemetry_enabled);
      setSelect('settings-language', preferences.language);
      control('settings-font-size').textContent = `${preferences.font_scale_percent}%`;
      setSwitch('settings-full-tooltips', preferences.show_full_tooltips);
      window.__Desktop_INTEGRATED_APP__?.applyPreferences?.(preferences);
      applySettingsLocale();

      const workflowSync = publishWorkflowConsumers(settings, nextState.revision);
      window.__Desktop_SETTINGS_UI__?.setConfiguredModelOptions?.(workflowSync.configuredModelOptions);
      window.__Desktop_SETTINGS_UI__?.applyWorkflow?.(workflowSync.template);
      const routeBlocked = (capabilityState?.capabilities || []).some(
        (row) => row.capability_id.startsWith('model.') && row.blocked
      );
      const routeStatus = control('settings-route-status');
      const routeHeading = routeStatus.querySelector('strong');
      if (!routeHeading) throw new Error('SETTINGS_CONTROL_MISSING:settings-route-status-heading');
      routeHeading.textContent = routeBlocked ? '映射已保存 · 正式路由禁用' : '映射已保存';
      const routeCopy = control('settings-route-copy');
      if (routeBlocked && !routeCopy.textContent.includes('当前 authority 不执行模型 route')) {
        routeCopy.textContent += ' · 当前 authority 不执行模型 route';
      }
      renderModelServices(settings);
      renderCliServices(settings);
      refreshDeveloperEditors(settings, !aggregateSaveInFlight);
    } finally {
      applyingState = false;
      settingsAppliedOnce = true;
      window.requestAnimationFrame(markClean);
    }
  };

  const hasPendingSettingsDraft = () => generalDraftDirty || cliDraftDirty || extraDirty()
    || Object.values(developerEditorMetrics.dirty).some(Boolean) || hasApiDraft();

  const refresh = async ({ apply = true, preserveDraft = true } = {}) => {
    state = await call('settings.get_state', {});
    capabilityState = state.capabilities || capabilityState;
    // Verification and other background reads may finish after editing starts.
    // Only a completed save or an explicit discard may replace that draft.
    if (preserveDraft && hasPendingSettingsDraft()) return state;
    if (apply) applySettings(state);
    await refreshAuxiliarySettings({ preserveDraft });
    return state;
  };

  const bootstrap = () => {
    if (bootstrapPromise || !nativeReady()) return bootstrapPromise;
    bootstrapPromise = (async () => {
      contract = await call('settings.get_contract', {});
      state = await call('settings.get_state', {});
      capabilityState = await call('settings.capability_state', {});
      effects = await call('settings.effect_metrics', {});
      riskIds(state.settings).forEach((id) => confirmedRiskIds.add(id));
      applySettings(state);
      await refreshAuxiliarySettings();
      return { contract, state, capabilityState, effects };
    })().catch((error) => {
      bootstrapPromise = null;
      throw error;
    });
    return bootstrapPromise;
  };

  const pendingCliModel = (card, service, used) => {
    const value = field => card.querySelector(`[data-cli-add-${field}]`)?.value.trim() || '';
    const displayName = value('display'), modelName = value('model'), thinkingMode = value('thinking');
    if (!displayName && !modelName && !thinkingMode) return null;
    if (!displayName || !modelName) throw new Error('请填写新增 CLI 模型的名称和模型标识。');
    if (!cliModelPattern.test(modelName)) throw new Error('模型标识只能使用字母、数字、点、下划线、冒号、斜杠或连字符。');
    if (thinkingMode && !modelSegmentPattern.test(thinkingMode)) throw new Error('思考模式只能使用字母、数字、点、下划线或连字符。');
    if ([...card.querySelectorAll('[data-cli-model-name]')].some(input => providerIdentity(input.value) === providerIdentity(modelName))) {
      throw new Error('此 CLI 已存在相同模型标识。');
    }
    const stem = modelName.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'model';
    let profileRef = `cli:${service.config_id}:${stem}`, suffix = 2;
    while (used.has(profileRef)) profileRef = `cli:${service.config_id}:${stem}-${suffix++}`;
    used.add(profileRef);
    return {profile_ref:profileRef, display_name:displayName, model_name:modelName,
      thinking_mode:thinkingMode, connection_status:'UNVERIFIED'};
  };

  const hasApiDraft = () => !q('#settings-api-draft')?.hidden && (
    Boolean(replacingCredentialRef) || ['settings-provider','settings-model-name','settings-api-key','settings-api-base-url']
      .some(id => Boolean(q(`#${id}`)?.value.trim()))
  );

  const collectCliServices = (baseline = state?.settings?.cli_services || []) => (
    structuredClone(baseline).map((service) => {
      const card = q(`[data-cli-config-id="${CSS.escape(service.config_id)}"]`);
      if (!card) return service;
      service.enabled = card.querySelector('[data-cli-enabled]')?.getAttribute('aria-checked') === 'true';
      const executable = card.querySelector('[data-cli-executable]')?.value.trim() ?? '';
      const executableUnchanged = executable === service.executable;
      service.executable = executable;
      const previousByRef = new Map(service.models.map((model) => [model.profile_ref, model]));
      service.models = [...card.querySelectorAll('[data-cli-profile-ref]')].map((row) => {
        const profileRef = row.dataset.cliProfileRef;
        const previous = previousByRef.get(profileRef);
        const modelName = row.querySelector('[data-cli-model-name]')?.value.trim() || '';
        const thinkingMode = row.querySelector('[data-cli-model-thinking]')?.value.trim() || '';
        const unchanged = executableUnchanged
          && previous?.model_name === modelName
          && previous?.thinking_mode === thinkingMode;
        return {
          profile_ref: profileRef,
          display_name: row.querySelector('[data-cli-model-display]')?.value.trim() || '',
          model_name: modelName,
          thinking_mode: thinkingMode,
          connection_status: unchanged ? previous.connection_status : 'UNVERIFIED'
        };
      });
      const pending = pendingCliModel(card, service,
        new Set(qa('[data-cli-profile-ref]').map(row => row.dataset.cliProfileRef)));
      if (pending) service.models.push(pending);
      service.connection_status = service.models.some((model) => model.connection_status === 'AVAILABLE')
        ? 'AVAILABLE' : service.models.some((model) => model.connection_status === 'INVALID') ? 'INVALID' : 'UNVERIFIED';
      return service;
    })
  );

  const collectSettings = () => {
    if (!state?.settings) throw new Error('SETTINGS_STATE_NOT_READY');
    [
      'settings-workspace-root', 'settings-artifact-root', 'settings-library-provider',
      'settings-library-name', 'settings-library-root', 'settings-auto-open-preview',
      'settings-document-viewer', 'settings-dnd-start', 'settings-dnd-end',
      'settings-close-behavior', 'settings-proxy-mode', 'settings-proxy-address',
      'settings-request-timeout', 'settings-log-level', 'settings-retention-days',
      'settings-cache-limit', 'settings-language', 'settings-font-size'
    ].forEach(control);
    const next = structuredClone(state.settings);
    const mode = q('#settings-mode button[aria-pressed="true"]')?.dataset.mode || 'normal';
    next.mode = mode === 'developer' ? 'DEVELOPER' : mode === 'advanced' ? 'ADVANCED' : 'NORMAL';

    next.directories.workspace_root = q('#settings-workspace-root').value.trim();
    next.directories.artifact_root = q('#settings-artifact-root').value.trim();
    const libraryEnabled = q('#settings-library-provider').value !== 'NONE';
    next.directories.external_library = {
      enabled: libraryEnabled,
      provider_kind: libraryEnabled ? q('#settings-library-provider').value : 'NONE',
      display_name: libraryEnabled ? q('#settings-library-name').value.trim() : '',
      root: libraryEnabled ? (q('#settings-library-root').value.trim() || null) : null
    };

    const preferences = next.preferences;
    preferences.auto_open_preview = q('#settings-auto-open-preview').value === 'true';
    preferences.document_viewer = q('#settings-document-viewer').value;
    preferences.external_refresh = switchValue('settings-external-refresh');
    preferences.task_complete_notification = switchValue('settings-notify-complete');
    preferences.task_error_notification = switchValue('settings-notify-error');
    preferences.approval_notification = switchValue('settings-notify-approval');
    preferences.weekly_report_notification = switchValue('settings-notify-weekly');
    preferences.notifications_enabled = [
      preferences.task_complete_notification, preferences.task_error_notification,
      preferences.approval_notification, preferences.weekly_report_notification
    ].some(Boolean);
    preferences.notification_sound = switchValue('settings-notify-sound');
    preferences.do_not_disturb_start = q('#settings-dnd-start').value;
    preferences.do_not_disturb_end = q('#settings-dnd-end').value;
    preferences.notification_open_task = switchValue('settings-notify-open-task');
    preferences.restore_last_view = switchValue('settings-restore-view');
    preferences.restore_last_document = false;
    preferences.close_behavior = q('#settings-close-behavior').value;
    preferences.warn_on_close_running = switchValue('settings-warn-close');
    preferences.launch_at_startup = switchValue('settings-launch-startup');
    preferences.keep_tasks_in_background = switchValue('settings-background-tasks');
    preferences.remember_panel_state = switchValue('settings-remember-panels');
    preferences.proxy_mode = q('#settings-proxy-mode').value.toUpperCase();
    preferences.proxy_address = q('#settings-proxy-address').value.trim();
    preferences.request_timeout_seconds = Number.parseInt(q('#settings-request-timeout').value, 10);
    preferences.log_level = q('#settings-log-level').value;
    preferences.retention_days = Number.parseInt(q('#settings-retention-days').value, 10);
    preferences.cache_limit_mb = Number.parseInt(q('#settings-cache-limit').value, 10);
    preferences.telemetry_enabled = switchValue('settings-telemetry');
    preferences.language = q('#settings-language').value;
    preferences.font_scale_percent = Number.parseInt(control('settings-font-size').textContent, 10);
    preferences.show_full_tooltips = switchValue('settings-full-tooltips');

    qa('button.desktop-settings-node[data-step]').forEach((button) => {
      const nodeId = nodeByStep.get(button.dataset.step);
      const target = next.workflow.nodes.find((row) => row.node_id === nodeId);
      if (!target) return;
      if (button.dataset.enabled) target.enabled = button.dataset.enabled !== 'false';
      const retry = Number(button.dataset.retries);
      if (Number.isInteger(retry)) target.retry_count = retry;
      if (button.dataset.modelLocked !== 'true') {
        target.profile_ref = button.dataset.profileRef || null;
        if (nodeId === 'ingest') target.fallback_profile_ref = button.dataset.fallbackProfileRef || null;
      }
      target.test_score = /^\d+$/.test(button.dataset.testScore || '')
        ? Number(button.dataset.testScore) : null;
    });

    for (const service of next.model_services) {
      const card = q(`[data-config-id="${CSS.escape(service.config_id)}"]`);
      if (!card) continue;
      const previousDescriptor = [service.model_name || '', service.tier || '', service.thinking_mode || ''].join('|');
      const previousConnection = [service.api_protocol || 'AUTO', service.api_base_url || ''].join('|');
      for (const field of ['model_name', 'tier']) {
        service[field] = card.querySelector(`[data-model-field="${field}"]`)?.value.trim() || '';
      }
      const apiProtocol = card.querySelector('[data-api-protocol]')?.value || service.api_protocol || 'AUTO';
      const apiBaseUrl = card.querySelector('[data-api-base-url]')?.value.trim().replace(/\/+$/, '') || '';
      if (apiProtocol === 'AUTO' && !apiBaseUrl) {
        delete service.api_protocol;
        delete service.api_base_url;
      } else {
        service.api_protocol = apiProtocol;
        service.api_base_url = apiBaseUrl;
      }
      const thinkingControl = card.querySelector('[data-model-thinking]');
      service.thinking = thinkingControl?.getAttribute('aria-checked') === 'true';
      service.tier ||= 'default';
      service.thinking_mode = service.thinking ? 'thinking' : '';
      const nextConnection = [service.api_protocol || 'AUTO', service.api_base_url || ''].join('|');
      if (previousDescriptor !== [service.model_name, service.tier, service.thinking_mode].join('|')
          || previousConnection !== nextConnection) {
        service.connection_status = 'UNVERIFIED';
      }
    }
    (next.model_services || []).forEach(applyKimiAutoDetection);
    next.cli_services = collectCliServices(next.cli_services || []);
    return next;
  };

  const saveSettings = async (
    settings,
    successMessage = '已保存',
    options = {}
  ) => {
    const confirmed = [...confirmedRiskIds].sort();
    const preview = await call('settings.preview', { settings, confirmed_risk_ids: confirmed });
    if (preview.status !== 'PASS') {
      const reasons = [...(preview.errors || []), ...(preview.pending_risks || [])];
      showToast(`设置未保存：${reasons.join(' · ') || '本地预检未通过'}`);
      return preview;
    }
    const receipt = await call('settings.save', {
      settings,
      expected_revision: state.revision,
      confirmed_risk_ids: confirmed
    });
    if (options.refreshAfter !== false) await refresh({ preserveDraft:false });
    if (options.announce !== false) showToast(successMessage);
    return receipt;
  };

  const cliSaveErrorCopy = Object.freeze({
    CLI_MODE_NORMAL_FORBIDDEN: 'CLI 仅提供高级配置与开发者模式。',
    EXPECTED_REVISION_INVALID: '保存版本无效，请刷新设置后重试。',
    EXPECTED_REVISION_CONFLICT: '设置已在其他位置更新，请刷新后重试。',
    CLI_SERVICES_INVALID: 'CLI 适配器集合不完整，请恢复已保存配置后重试。',
    CLI_ADAPTER_SET_MISMATCH: 'CLI 适配器集合与当前版本不一致。',
    CLI_ADAPTER_IDENTITY_DRIFT: 'CLI 适配器标识不能修改。',
    CLI_EXECUTABLE_INVALID: '请填写有效的命令位置或系统 PATH 命令名。',
    CLI_MODELS_INVALID: '每个 CLI 最多可保存 32 个模型。',
    CLI_MODEL_PROFILE_REF_INVALID: '模型内部标识冲突，请删除后重新添加。',
    CLI_MODEL_DISPLAY_NAME_INVALID: '请填写 80 个字符以内的模型名称。',
    CLI_MODEL_NAME_INVALID: '模型标识只能使用字母、数字、点、下划线、冒号、斜杠或连字符。',
    CLI_MODEL_NAME_DUPLICATE: '同一 CLI 不能保存重复的模型标识。',
    CLI_MODEL_THINKING_MODE_INVALID: '思考模式只能使用字母、数字、点、下划线或连字符。',
    WORKFLOW_PROFILE_REFERENCE_NOT_CONFIGURED: '该 CLI 模型仍被流程节点引用，请先调整当前流程模型映射。'
  });

  const cliSaveMessage = (receipt = {}) => (
    cliSaveErrorCopy[receipt.error_code]
    || `CLI 配置未保存：${receipt.error_code || receipt.status || '本地校验未通过'}`
  );

  const saveCliServices = async (options = {}) => {
    await bootstrap();
    if (cliSaveInFlight) {
      return {
        schema_version: 'SettingsCliServicesSaveReceipt-v1',
        status: 'PENDING',
        error_code: 'CLI_SAVE_IN_PROGRESS',
        persistent_mutation: false
      };
    }
    if (options.skipIfClean && !cliDraftDirty && !options.cliServices) {
      return {
        schema_version: 'SettingsCliServicesSaveReceipt-v1',
        status: 'PASS',
        action: 'NO_CHANGES',
        persistent_mutation: false,
        settings_persisted: true
      };
    }
    const mode = options.mode || activeSettingsMode();
    const persistedMode = mode === 'developer' || mode === 'DEVELOPER' ? 'DEVELOPER' : 'ADVANCED';
    const cliServices = options.cliServices
      ? structuredClone(options.cliServices)
      : collectCliServices();
    const button = q('#settings-save');
    const originalCopy = button?.textContent || '保存';
    cliSaveInFlight = true;
    if (button) {
      button.disabled = true;
      button.textContent = '正在保存…';
      button.setAttribute('aria-busy', 'true');
    }
    try {
      let receipt = await call('settings.save_cli_services', {
        cli_services: cliServices,
        mode: persistedMode,
        expected_revision: state.revision
      });
      if (receipt?.status === 'BLOCKED' && receipt?.error_code === 'EXPECTED_REVISION_CONFLICT') {
        const freshState = await call('settings.get_state', {});
        const retry_revision = freshState?.revision;
        if (Number.isInteger(retry_revision)) {
          state = freshState;
          receipt = await call('settings.save_cli_services', {
            cli_services: cliServices,
            mode: persistedMode,
            expected_revision: retry_revision
          });
        }
      }
      if (receipt?.status !== 'PASS') {
        if (options.announce !== false) showToast(cliSaveMessage(receipt));
        return receipt;
      }
      let finalReceipt = receipt;
      try {
        await refresh({ apply: options.refreshAfter !== false, preserveDraft:false });
      } catch (_) {
        finalReceipt = {
          ...receipt,
          settings_persisted: true,
          ui_refresh_status: 'WARNING',
          ui_refresh_error_code: 'CLI_UI_REFRESH_DEFERRED'
        };
      }
      setCliDraftDirty(false);
      markClean();
      if (options.announce !== false) {
        showToast(finalReceipt.ui_refresh_status === 'WARNING'
          ? '已保存，页面刷新失败'
          : finalReceipt.runtime_effects_status === 'WARNING'
            ? '已保存，映射刷新失败'
          : '已保存');
      }
      return finalReceipt;
    } finally {
      cliSaveInFlight = false;
      if (button?.isConnected) {
        button.textContent = originalCopy;
        button.removeAttribute('aria-busy');
        button.disabled = !cliDraftDirty;
      }
      const liveButton = q('#settings-save');
      if (liveButton) {
        liveButton.textContent = '保存';
        liveButton.removeAttribute('aria-busy');
        liveButton.disabled = !cliDraftDirty;
      }
    }
  };

  const saveErrorCode = (error, fallback) => String(
    error?.message || fallback || 'UNKNOWN'
  ).toUpperCase().replace(/[^A-Z0-9_:-]+/g, '_').slice(0, 120);

  const savePrimary = async () => {
    cancelScopedReset();
    await bootstrap();
    // The header saves the collected draft, independent of the visible category.
    // Explicit CLI verification/editor actions still use saveCliServices().
    if (settingsSaveInFlight) {
      return {
        schema_version:'DesktopSettingsSaveAggregateReceipt-v1',
        status:'PENDING',
        error_code:'SETTINGS_SAVE_IN_PROGRESS',
        persistent_mutation:false
      };
    }
    const button = q('#settings-save');
    const originalCopy = button?.textContent || '保存';
    const stages = {
      settings:{ status:'SKIPPED', error_code:null, revision:null },
      refinement:{ status:'SKIPPED', error_code:null, revision:null },
      assistant:{ status:'SKIPPED', error_code:null, revision:null },
      refresh:{ status:'SKIPPED', error_code:null, revision:null }
    };
    settingsSaveInFlight = true;
    if (button) {
      button.disabled = true;
      button.textContent = '正在保存…';
      button.setAttribute('aria-busy', 'true');
    }
    try {
      const settings = collectSettings();
      const savedRefinementDraft = refinementDraft();
      const savedAssistantDraft = assistantDraft();
      if (settings.directories.external_library.enabled) await testLibrary();
      let receipt;
      try {
        receipt = hasApiDraft()
          ? await bindCredential(credentialDraftGeneration, {
            settingsDraft:settings, refreshAfter:false, announce:false, returnReceipt:true
          })
          : await saveSettings(settings, '', { refreshAfter:false, announce:false });
        if (!receipt) receipt = {status:'BLOCKED', error_code:'API_DRAFT_INVALID'};
      } catch (error) {
        stages.settings = {
          status:'FAILED', error_code:saveErrorCode(error, 'SETTINGS_SAVE_FAILED'), revision:null
        };
        try {
          await nativeAction('record_settings_save_receipt', {
            overall_status:'FAILED', stages
          });
        } catch (_) {}
        throw error;
      }
      if (receipt?.status !== 'PASS' && receipt?.action !== 'SAVE') {
        stages.settings = {
          status:'FAILED',
          error_code:saveErrorCode(null, receipt?.error_code || receipt?.status || 'SETTINGS_SAVE_REJECTED'),
          revision:Number.isInteger(receipt?.revision) ? receipt.revision : null
        };
        try {
          await nativeAction('record_settings_save_receipt', {
            overall_status:'FAILED', stages
          });
        } catch (_) {}
        return receipt;
      }
      stages.settings = {
        status:'PASS', error_code:null,
        revision:Number.isInteger(receipt?.revision) ? receipt.revision : null
      };
      markClean();

      const auxiliary = [
        ['refinement', () => saveRefinementSettings(savedRefinementDraft, {
          refreshAfter:false, announce:false
        })],
        ['assistant', () => saveAssistantPreferences(savedAssistantDraft, {
          refreshAfter:false, announce:false
        })],
        ['refresh', () => refresh({ preserveDraft:false })]
      ];
      for (const [name, operation] of auxiliary) {
        try {
          const auxiliaryReceipt = await operation();
          stages[name] = {
            status:'PASS', error_code:null,
            revision:Number.isInteger(auxiliaryReceipt?.revision) ? auxiliaryReceipt.revision : null
          };
        } catch (error) {
          stages[name] = {
            status:'WARNING', error_code:saveErrorCode(error, `${name}_REFRESH_DEFERRED`), revision:null
          };
        }
      }
      const warning = Object.values(stages).some((stage) => stage.status === 'WARNING');
      let receiptPersisted = true;
      try {
        await nativeAction('record_settings_save_receipt', {
          overall_status:warning ? 'WARNING' : 'PASS', stages
        });
      } catch (_) {
        receiptPersisted = false;
      }
      const hasWarning = warning || !receiptPersisted;
      showToast(hasWarning
        ? '已保存，页面刷新失败'
        : '设置已真实保存；运行中任务继续使用既有冻结快照。');
      return {
        ...receipt,
        settings_persisted:true,
        aggregate_status:hasWarning ? 'WARNING' : 'PASS',
        stages,
        diagnostic_receipt_persisted:receiptPersisted
      };
    } finally {
      settingsSaveInFlight = false;
      if (button?.isConnected) {
        button.textContent = originalCopy;
        button.removeAttribute('aria-busy');
      }
    }
  };

  const save = async () => {
    if(aggregateSaveInFlight)return {status:'PENDING'};
    const pending=[...saveParticipants.entries()].filter(([,part])=>part.isDirty());
    if(pending.some(([,part])=>part.validate?.()===false))return {status:'BLOCKED',reason:'INVALID_SETTINGS_INPUT'};
    const developer=Object.keys(developerEditorMetrics.dirty)
      .filter(key=>developerEditorMetrics.dirty[key])
      .map(scope=>[scope,parseDeveloperInput(scope)]);
    aggregateSaveInFlight=true;
    const controls=qa('input,select,textarea,button').map(node=>[node,node.disabled]);
    controls.forEach(([node])=>node.disabled=true);
    q('#settings-save')?.setAttribute('aria-busy','true');
    const completed=[];
    try {
      if(generalDraftDirty || cliDraftDirty || hasApiDraft() || (!pending.length && !developer.length)){
        const result=await savePrimary();
        if(result?.status==='BLOCKED'||result?.status==='FAILED'||result?.aggregate_status==='WARNING')
          return result;
        completed.push('settings');
      }
      for(const [scope,documentValue] of developer){
        const result=await saveDeveloper(scope,documentValue);
        if(result?.status==='BLOCKED'||result?.status==='FAILED')throw new Error('开发者配置未保存');
        completed.push(scope);
      }
      for(const [key,part] of pending){await part.save();completed.push(key);}
      syncSaveState();
      showToast('设置已保存');
      return {status:'PASS',completed_scopes:completed};
    } catch(error){
      showToast((completed.length?'部分设置已保存；':'')+'其余修改尚未保存，请检查后重试');
      throw error;
    } finally {
      aggregateSaveInFlight=false;
      controls.forEach(([node,disabled])=>{if(node.isConnected)node.disabled=disabled;});
      // Dependencies may have changed while the form was locked (for
      // example enabling literature receiving enables its source refresh).
      // Recompute those controls after releasing the temporary save lock.
      for(const part of saveParticipants.values())part.afterUnlock?.();
      q('#settings-save')?.removeAttribute('aria-busy');
      syncSaveState();
      window.__Desktop_SETTINGS_ICONS__?.applyActions();
    }
  };

  const developerSchemas = Object.freeze({
    api: 'DesktopDeveloperApi-v2',
    cli: 'DesktopDeveloperCli-v1',
    workflow: 'DesktopDeveloperWorkflow-v1'
  });
  const developerScopeIds = Object.freeze({
    api: {
      input: 'settings-api-code', lines: 'settings-api-code-lines', dot: 'settings-api-code-dot',
      status: 'settings-api-code-status', save: 'settings-api-code-save',
      cancel: 'settings-api-code-cancel', format: 'settings-api-code-format',
      highlight: 'settings-api-code-highlight', diff: 'settings-api-code-diff',
      diffRows: 'settings-api-code-diff-rows', diffToggle: 'settings-api-code-diff-toggle'
    },
    cli: {
      input: 'settings-cli-code', lines: 'settings-cli-code-lines', dot: 'settings-cli-code-dot',
      status: 'settings-cli-code-status', save: 'settings-cli-code-save',
      cancel: 'settings-cli-code-cancel', format: 'settings-cli-code-format',
      highlight: 'settings-cli-code-highlight', diff: 'settings-cli-code-diff',
      diffRows: 'settings-cli-code-diff-rows', diffToggle: 'settings-cli-code-diff-toggle'
    },
    workflow: {
      input: 'settings-workflow-code', lines: 'settings-workflow-code-lines', dot: 'settings-workflow-code-dot',
      status: 'settings-workflow-code-status', save: 'settings-workflow-code-save',
      cancel: 'settings-workflow-code-cancel', format: 'settings-workflow-code-format',
      highlight: 'settings-workflow-code-highlight', diff: 'settings-workflow-code-diff',
      diffRows: 'settings-workflow-code-diff-rows', diffToggle: 'settings-workflow-code-diff-toggle'
    }
  });
  const developerForbiddenKeys = new Set([
    'apikey', 'secret', 'secret_input', 'credential_value',
    'password', 'token', 'access_token', 'refresh_token', 'private_key'
  ]);
  const developerPlanValues = new Set(['ECONOMY', 'STANDARD', 'HIGH_QUALITY', 'MAXIMUM']);

  const developerError = (code, detail = '') => {
    const error = new Error(`${code}${detail ? `:${detail}` : ''}`);
    error.code = code;
    return error;
  };

  const assertNoDeveloperSecretFields = (value, path = '$', allowApiKey = false) => {
    if (Array.isArray(value)) {
      value.forEach((entry, index) => assertNoDeveloperSecretFields(entry, `${path}[${index}]`, allowApiKey));
      return;
    }
    if (!value || typeof value !== 'object') return;
    Object.entries(value).forEach(([key, entry]) => {
      const normalized = key.trim().toLowerCase();
      const supportedWriteOnlyKey = normalized === 'api_key'
        && allowApiKey && /^\$\.model_services\[\d+\]$/.test(path);
      if (!supportedWriteOnlyKey && (normalized === 'api_key' || developerForbiddenKeys.has(normalized))) {
        throw developerError('DEVELOPER_SECRET_FIELD_FORBIDDEN', `${path}.${key}`);
      }
      assertNoDeveloperSecretFields(entry, `${path}.${key}`, allowApiKey);
    });
  };

  const assertDeveloperObjectKeys = (value, expected, path) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw developerError('DEVELOPER_OBJECT_REQUIRED', path);
    }
    const expectedSet = new Set(expected);
    const unknown = Object.keys(value).find((key) => !expectedSet.has(key));
    if (unknown) throw developerError('DEVELOPER_UNKNOWN_FIELD', `${path}.${unknown}`);
    const missing = expected.find((key) => !Object.prototype.hasOwnProperty.call(value, key));
    if (missing) throw developerError('DEVELOPER_REQUIRED_FIELD_MISSING', `${path}.${missing}`);
  };

  const developerDocument = (scope, settings = state?.settings) => {
    if (!settings || !developerSchemas[scope]) throw developerError('DEVELOPER_SCOPE_INVALID', scope);
    if (scope === 'api') {
      return {
        schema_version: developerSchemas.api,
        model_services: settings.model_services.map((service) => ({
          config_id: service.config_id,
          provider: service.provider,
          credential_ref: service.credential_ref,
          model_name: service.model_name || '',
          api_protocol: service.api_protocol || 'AUTO',
          api_base_url: service.api_base_url || '',
          tier: service.tier || '',
          thinking_mode: service.thinking_mode || (service.thinking ? 'thinking' : ''),
          plan: service.plan || 'STANDARD',
          api_key: ''
        }))
      };
    }
    if (scope === 'cli') {
      return {
        schema_version: developerSchemas.cli,
        cli_services: settings.cli_services.map((service) => ({
          config_id: service.config_id,
          adapter_id: service.adapter_id,
          display_name: service.display_name,
          executable: service.executable,
          enabled: service.enabled,
          models: service.models.map((model) => ({
            profile_ref: model.profile_ref,
            display_name: model.display_name,
            model_name: model.model_name,
            thinking_mode: model.thinking_mode
          }))
        }))
      };
    }
    return {
      schema_version: developerSchemas.workflow,
      nodes: settings.workflow.nodes.map((node) => ({
        node_id: node.node_id,
        enabled: node.enabled,
        retry_count: node.retry_count,
        profile_ref: node.profile_ref,
        ...(node.node_id === 'ingest' ? {fallback_profile_ref: node.fallback_profile_ref || null} : {})
      }))
    };
  };

  const validateDeveloperDocument = (scope, value) => {
    if (!state?.settings) throw developerError('SETTINGS_STATE_NOT_READY');
    assertNoDeveloperSecretFields(value, '$', scope === 'api');
    if (scope === 'api') {
      assertDeveloperObjectKeys(value, ['schema_version', 'model_services'], '$');
      if (value.schema_version !== developerSchemas.api) throw developerError('DEVELOPER_SCHEMA_VERSION_INVALID');
      if (!Array.isArray(value.model_services)) throw developerError('DEVELOPER_MODEL_SERVICES_ARRAY_REQUIRED');
      const currentById = new Map(state.settings.model_services.map((service) => [service.config_id, service]));
      if (value.model_services.length < currentById.size) throw developerError('DEVELOPER_MODEL_SERVICE_SET_MISMATCH');
      const seenIds = new Set();
      const seenProfiles = new Set();
      value.model_services.forEach((service, index) => {
        const path = `$.model_services[${index}]`;
        assertDeveloperObjectKeys(service, [
          'config_id', 'provider', 'credential_ref', 'model_name', 'api_protocol', 'api_base_url',
          'tier', 'thinking_mode', 'plan', 'api_key'
        ], path);
        const current = currentById.get(service.config_id);
        if (typeof service.config_id !== 'string' || service.config_id !== service.config_id.trim()
            || !modelSegmentPattern.test(service.config_id)) {
          throw developerError('DEVELOPER_CONFIG_ID_INVALID', path);
        }
        if (seenIds.has(service.config_id)) throw developerError('DEVELOPER_CONFIG_ID_DUPLICATE', path);
        seenIds.add(service.config_id);
        if (current && service.credential_ref !== current.credential_ref) {
          throw developerError('DEVELOPER_CREDENTIAL_REFERENCE_IMMUTABLE', path);
        }
        if (!current && service.credential_ref !== '') {
          throw developerError('DEVELOPER_NEW_CREDENTIAL_REFERENCE_MUST_BE_EMPTY', path);
        }
        if (typeof service.provider !== 'string' || !service.provider.trim() || service.provider.trim().length > 64) {
          throw developerError('DEVELOPER_PROVIDER_INVALID', path);
        }
        if (typeof service.model_name !== 'string' || !apiModelPattern.test(service.model_name.trim())) {
          throw developerError('DEVELOPER_MODEL_NAME_INVALID', path);
        }
        const connection = apiConnectionState(service.provider, service.api_protocol, service.api_base_url);
        if (!connection.valid) throw developerError(`DEVELOPER_${connection.code}`, path);
        if (typeof service.tier !== 'string' || !modelSegmentPattern.test(service.tier.trim())) {
          throw developerError('DEVELOPER_MODEL_TIER_INVALID', path);
        }
        if (!['', 'thinking'].includes(service.thinking_mode)) throw developerError('DEVELOPER_THINKING_MODE_INVALID', path);
        if (inferApiModelCapability(service.provider, service.model_name) !== 'CHAT' && service.thinking_mode) {
          throw developerError('DEVELOPER_THINKING_MODE_INVALID', path);
        }
        const profileKey = `${providerIdentity(service.provider)}|${modelIdentity(service)}`;
        if (seenProfiles.has(profileKey)) throw developerError('DEVELOPER_MODEL_PROFILE_DUPLICATE', path);
        seenProfiles.add(profileKey);
        if (!developerPlanValues.has(service.plan)) throw developerError('DEVELOPER_PLAN_INVALID', path);
        if (typeof service.api_key !== 'string' || new TextEncoder().encode(service.api_key).length > 512) {
          throw developerError('DEVELOPER_API_KEY_INVALID', path);
        }
        if (!current && !service.api_key && !providerReference(service.provider)) {
          throw developerError('DEVELOPER_API_KEY_REQUIRED', path);
        }
      });
      if ([...currentById.keys()].some((configId) => !seenIds.has(configId))) {
        throw developerError('DEVELOPER_MODEL_SERVICE_SET_MISMATCH');
      }
      return value;
    }
    if (scope === 'cli') {
      assertDeveloperObjectKeys(value, ['schema_version', 'cli_services'], '$');
      if (value.schema_version !== developerSchemas.cli) throw developerError('DEVELOPER_SCHEMA_VERSION_INVALID');
      if (!Array.isArray(value.cli_services)) throw developerError('DEVELOPER_CLI_SERVICES_ARRAY_REQUIRED');
      const currentById = new Map(state.settings.cli_services.map((service) => [service.config_id, service]));
      if (value.cli_services.length !== currentById.size) throw developerError('DEVELOPER_CLI_SERVICE_SET_MISMATCH');
      const seenProfiles = new Set();
      value.cli_services.forEach((service, index) => {
        const path = `$.cli_services[${index}]`;
        assertDeveloperObjectKeys(service, [
          'config_id', 'adapter_id', 'display_name', 'executable', 'enabled', 'models'
        ], path);
        const current = currentById.get(service.config_id);
        if (!current || current.adapter_id !== service.adapter_id || current.display_name !== service.display_name) {
          throw developerError('DEVELOPER_CLI_IDENTITY_IMMUTABLE', path);
        }
        if (typeof service.executable !== 'string' || !service.executable.trim()
            || service.executable.length > 260 || /[\u0000-\u001f\u007f]/.test(service.executable)) {
          throw developerError('DEVELOPER_CLI_EXECUTABLE_INVALID', path);
        }
        if (typeof service.enabled !== 'boolean') throw developerError('DEVELOPER_CLI_ENABLED_INVALID', path);
        if (!Array.isArray(service.models) || service.models.length > 32) {
          throw developerError('DEVELOPER_CLI_MODELS_INVALID', path);
        }
        const seenModelNames = new Set();
        service.models.forEach((model, modelIndex) => {
          const modelPath = `${path}.models[${modelIndex}]`;
          assertDeveloperObjectKeys(model, [
            'profile_ref', 'display_name', 'model_name', 'thinking_mode'
          ], modelPath);
          if (typeof model.profile_ref !== 'string'
              || !model.profile_ref.startsWith(`cli:${service.config_id}:`)
              || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(model.profile_ref)
              || seenProfiles.has(model.profile_ref)) {
            throw developerError('DEVELOPER_CLI_PROFILE_REF_INVALID', modelPath);
          }
          seenProfiles.add(model.profile_ref);
          if (typeof model.display_name !== 'string' || !model.display_name.trim() || model.display_name.trim().length > 80) {
            throw developerError('DEVELOPER_CLI_MODEL_DISPLAY_INVALID', modelPath);
          }
          if (typeof model.model_name !== 'string' || !cliModelPattern.test(model.model_name.trim())) {
            throw developerError('DEVELOPER_CLI_MODEL_NAME_INVALID', modelPath);
          }
          const modelKey = providerIdentity(model.model_name);
          if (seenModelNames.has(modelKey)) throw developerError('DEVELOPER_CLI_MODEL_DUPLICATE', modelPath);
          seenModelNames.add(modelKey);
          if (typeof model.thinking_mode !== 'string' || (model.thinking_mode && !modelSegmentPattern.test(model.thinking_mode))) {
            throw developerError('DEVELOPER_CLI_THINKING_MODE_INVALID', modelPath);
          }
        });
      });
      if ([...currentById.keys()].some((configId) => !value.cli_services.some((row) => row.config_id === configId))) {
        throw developerError('DEVELOPER_CLI_SERVICE_SET_MISMATCH');
      }
      return value;
    }
    if (scope !== 'workflow') throw developerError('DEVELOPER_SCOPE_INVALID', scope);
    assertDeveloperObjectKeys(value, ['schema_version', 'nodes'], '$');
    if (value.schema_version !== developerSchemas.workflow) throw developerError('DEVELOPER_SCHEMA_VERSION_INVALID');
    if (!Array.isArray(value.nodes)) throw developerError('DEVELOPER_WORKFLOW_NODES_ARRAY_REQUIRED');
    const currentById = new Map(state.settings.workflow.nodes.map((node) => [node.node_id, node]));
    if (value.nodes.length !== currentById.size) throw developerError('DEVELOPER_WORKFLOW_NODE_SET_MISMATCH');
    const configuredRefs = new Set(state.settings.model_services.map((service) => service.config_id));
    state.settings.cli_services.forEach((service) => service.models.forEach((model) => configuredRefs.add(model.profile_ref)));
    state.settings.workflow.nodes.forEach((node) => {
      if (typeof node.profile_ref === 'string') configuredRefs.add(node.profile_ref);
    });
    for (const model of localModelsState?.recognized_models || []) configuredRefs.add(model.profile_ref);
    configuredRefs.add('');
    const seenIds = new Set();
    value.nodes.forEach((node, index) => {
      const path = `$.nodes[${index}]`;
      assertDeveloperObjectKeys(node, ['node_id', 'enabled', 'retry_count', 'profile_ref', ...(node.node_id === 'ingest' ? ['fallback_profile_ref'] : [])], path);
      const current = currentById.get(node.node_id);
      if (!current || seenIds.has(node.node_id)) throw developerError('DEVELOPER_WORKFLOW_NODE_SET_MISMATCH', path);
      seenIds.add(node.node_id);
      if (typeof node.enabled !== 'boolean') throw developerError('DEVELOPER_NODE_ENABLED_INVALID', path);
      if (!Number.isInteger(node.retry_count) || node.retry_count < 0 || node.retry_count > 20) {
        throw developerError('DEVELOPER_RETRY_COUNT_INVALID', path);
      }
      if (!(node.profile_ref === null || typeof node.profile_ref === 'string')) {
        throw developerError('DEVELOPER_PROFILE_REFERENCE_INVALID', path);
      }
      if (node.node_id === 'ingest' && node.fallback_profile_ref != null && (!configuredRefs.has(node.fallback_profile_ref) || node.fallback_profile_ref === node.profile_ref)) throw developerError('DEVELOPER_PROFILE_REFERENCE_UNKNOWN', path);
      const reference = node.profile_ref === null ? '' : node.profile_ref;
      if (!configuredRefs.has(reference)) throw developerError('DEVELOPER_PROFILE_REFERENCE_UNKNOWN', path);
      const optional = node.node_id === 'transport_review' || node.node_id === 'judgment_review';
      if (!optional && node.enabled !== current.enabled) throw developerError('DEVELOPER_NODE_ENABLEMENT_LOCKED', path);
      const profileLocked = node.node_id === 'card_admission'
        || node.node_id === 'human_judgment';
      if (profileLocked && node.profile_ref !== current.profile_ref) {
        throw developerError('DEVELOPER_PROFILE_REFERENCE_LOCKED', path);
      }
    });
    return value;
  };

  const developerStatusCopy = (error) => {
    const code = error?.code || String(error?.message || error || 'DEVELOPER_VALIDATION_FAILED').split(':')[0];
    const messages = {
      DEVELOPER_SECRET_FIELD_FORBIDDEN: '保存失败：仅 model_services 内的 api_key 支持只写；Token、密码及其他密钥字段仍被禁止。',
      DEVELOPER_UNKNOWN_FIELD: '保存失败：存在不受支持的字段，请使用当前生成的结构。',
      DEVELOPER_MODEL_SERVICE_SET_MISMATCH: '保存失败：不能删除已保存的模型服务；可保留现有条目或新增完整条目。',
      DEVELOPER_CREDENTIAL_REFERENCE_IMMUTABLE: '保存失败：安全凭据引用不可在代码编辑器中改写。',
      DEVELOPER_NEW_CREDENTIAL_REFERENCE_MUST_BE_EMPTY: '保存失败：新增服务的 credential_ref 必须留空，由 Windows 凭据管理器生成。',
      DEVELOPER_API_KEY_REQUIRED: '保存失败：该服务商尚无可复用凭据，请在 api_key 中写入凭据。',
      DEVELOPER_API_KEY_INVALID: '保存失败：api_key 必须是 512 个 UTF-8 字节以内的字符串。',
      DEVELOPER_CONFIG_ID_DUPLICATE: '保存失败：config_id 重复。',
      DEVELOPER_PROVIDER_DUPLICATE: '保存失败：服务商名称重复。',
      DEVELOPER_MODEL_PROFILE_DUPLICATE: '保存失败：同一服务商下已存在完全相同的模型配置。',
      DEVELOPER_API_PROTOCOL_INVALID: '保存失败：请选择受支持的接口格式。',
      DEVELOPER_API_PROVIDER_PROTOCOL_REQUIRED: '保存失败：陌生厂商或中转站需要选择兼容格式并填写 API 地址。',
      DEVELOPER_API_BASE_URL_REQUIRED: '保存失败：兼容接口需要填写 HTTPS API 地址。',
      DEVELOPER_API_BASE_URL_INVALID: '保存失败：API 地址必须是无凭据、查询参数和片段的公开 HTTPS 地址。',
      DEVELOPER_API_CONNECTION_INCOMPLETE: '保存失败：官方自动接口不能同时填写自定义 API 地址。',
      DEVELOPER_PROFILE_REFERENCE_UNKNOWN: '保存失败：节点引用了尚未保存的模型配置。',
      DEVELOPER_NODE_ENABLEMENT_LOCKED: '保存失败：此节点的启用状态由流程合同锁定。',
      DEVELOPER_PROFILE_REFERENCE_LOCKED: '保存失败：确定性节点或继承型节点不能直接改写模型。'
    };
    if (messages[code]) return messages[code];
    if (cliSaveErrorCopy[code]) return `保存失败：${cliSaveErrorCopy[code]}`;
    if (String(error?.message || '').includes('CREDENTIAL_') || String(error?.message || '').includes('SETTINGS_CALL_FAILED')) {
      return credentialErrorCopy(error);
    }
    return `保存失败：${String(error?.message || error || code)}`;
  };

  const setDeveloperStatus = (scope, status, message) => {
    const ids = developerScopeIds[scope];
    if (!ids) return;
    q(`#${ids.dot}`).className = `status-dot ${status === 'ERROR' ? 'red' : status === 'DIRTY' ? 'yellow' : 'green'}`;
    q(`#${ids.status}`).textContent = message;
    developerEditorMetrics.last_scope = scope;
    developerEditorMetrics.last_status = status;
    developerEditorMetrics.last_error = status === 'ERROR' ? message : null;
  };

  const renderDeveloperSyntax = (scope, elements = null) => {
    const ids = developerScopeIds[scope];
    const input = elements?.input || q(`#${ids.input}`);
    const highlight = elements?.highlight || q(`#${ids.highlight}`);
    if (!input || !highlight) return;
    const source = input.value || ' ';
    const pattern = /"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false)\b|\bnull\b|[{}\[\],:]/g;
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    let tokenCount = 0;
    for (const match of source.matchAll(pattern)) {
      if (match.index > cursor) fragment.appendChild(document.createTextNode(source.slice(cursor, match.index)));
      const token = match[0];
      const span = document.createElement('span');
      let kind = 'punctuation';
      if (token.startsWith('"')) {
        kind = /^\s*:/.test(source.slice(match.index + token.length)) ? 'key' : 'string';
      } else if (/^-?\d/.test(token)) kind = 'number';
      else if (token === 'true' || token === 'false') kind = 'boolean';
      else if (token === 'null') kind = 'null';
      span.className = `desktop-settings-json-${kind}`;
      span.textContent = token;
      fragment.appendChild(span);
      cursor = match.index + token.length;
      tokenCount += 1;
    }
    if (cursor < source.length) fragment.appendChild(document.createTextNode(source.slice(cursor)));
    highlight.replaceChildren(fragment);
    highlight.dataset.tokenCount = String(tokenCount);
    if (scope) developerEditorMetrics.syntax_token_count[scope] = tokenCount;
  };

  const developerDiff = (beforeText, afterText) => {
    const before = beforeText.split('\n');
    const after = afterText.split('\n');
    const matrix = Array.from({ length: before.length + 1 }, () => new Uint16Array(after.length + 1));
    for (let i = before.length - 1; i >= 0; i -= 1) {
      for (let j = after.length - 1; j >= 0; j -= 1) {
        matrix[i][j] = before[i] === after[j]
          ? matrix[i + 1][j + 1] + 1
          : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
      }
    }
    const rows = [];
    let i = 0;
    let j = 0;
    while (i < before.length || j < after.length) {
      if (i < before.length && j < after.length && before[i] === after[j]) {
        rows.push({ kind: 'context', marker: ' ', text: before[i] });
        i += 1;
        j += 1;
      } else if (j < after.length && (i === before.length || matrix[i][j + 1] >= matrix[i + 1][j])) {
        rows.push({ kind: 'added', marker: '+', text: after[j] });
        j += 1;
      } else {
        rows.push({ kind: 'removed', marker: '−', text: before[i] });
        i += 1;
      }
    }
    return rows;
  };

  const renderDeveloperDiff = (scope) => {
    const ids = developerScopeIds[scope];
    const input = q(`#${ids.input}`);
    const container = q(`#${ids.diffRows}`);
    if (!input || !container) return;
    const rows = developerDiff(developerEditorMetrics.baseline[scope] || '', input.value);
    const fragment = document.createDocumentFragment();
    let added = 0;
    let removed = 0;
    rows.forEach((entry) => {
      const row = document.createElement('div');
      row.className = `desktop-settings-diff-row desktop-settings-diff-${entry.kind}`;
      const marker = document.createElement('span');
      marker.textContent = entry.marker;
      const code = document.createElement('code');
      code.textContent = entry.text || ' ';
      row.append(marker, code);
      fragment.appendChild(row);
      if (entry.kind === 'added') added += 1;
      if (entry.kind === 'removed') removed += 1;
    });
    container.replaceChildren(fragment);
    container.dataset.addedCount = String(added);
    container.dataset.removedCount = String(removed);
    developerEditorMetrics.diff_added_count[scope] = added;
    developerEditorMetrics.diff_removed_count[scope] = removed;
  };

  const updateDeveloperLineNumbers = (scope, elements = null) => {
    const ids = developerScopeIds[scope];
    const input = elements?.input || q(`#${ids.input}`);
    const lines = elements?.lines || q(`#${ids.lines}`);
    const highlight = elements?.highlight || q(`#${ids.highlight}`);
    const count = Math.max(1, input.value.split('\n').length);
    lines.textContent = Array.from({ length: count }, (_, index) => String(index + 1)).join('\n');
    lines.scrollTop = input.scrollTop;
    if (highlight) highlight.style.transform = `translate(${-input.scrollLeft}px, ${-input.scrollTop}px)`;
  };

  const updateDeveloperVisuals = (scope) => {
    renderDeveloperSyntax(scope);
    updateDeveloperLineNumbers(scope);
    if (developerEditorMetrics.diff_open[scope]) renderDeveloperDiff(scope);
  };

  const refreshDeveloperEditor = (scope, settings = state?.settings, force = false) => {
    if (!settings || (!force && developerEditorMetrics.dirty[scope])) return;
    const ids = developerScopeIds[scope];
    const input = q(`#${ids.input}`);
    if (!input) return;
    input.value = JSON.stringify(developerDocument(scope, settings), null, 2) + '\n';
    developerEditorMetrics.baseline[scope] = input.value;
    developerEditorMetrics.dirty[scope] = false;
    input.dataset.dirty = 'false';
    updateDeveloperVisuals(scope);
    setDeveloperStatus(scope, 'READY', scope === 'api'
      ? '已生成脱敏代码；api_key 只写，空字符串保留现有凭据。'
      : scope === 'cli'
        ? '已生成'
        : '已生成');
  };

  const refreshDeveloperEditors = (settings = state?.settings, force = false) => {
    refreshDeveloperEditor('api', settings, force);
    refreshDeveloperEditor('cli', settings, force);
    refreshDeveloperEditor('workflow', settings, force);
  };

  const parseDeveloperInput = (scope) => {
    const ids = developerScopeIds[scope];
    const input = q(`#${ids.input}`);
    try {
      return validateDeveloperDocument(scope, JSON.parse(input.value));
    } catch (error) {
      developerEditorMetrics.validation_failure_count += 1;
      if (String(error?.message || '').includes('DEVELOPER_SECRET_FIELD_FORBIDDEN')) {
        developerEditorMetrics.secret_field_block_count += 1;
      }
      const message = developerStatusCopy(error);
      setDeveloperStatus(scope, 'ERROR', message);
      throw error;
    }
  };

  const scrubDeveloperApiKeys = (documentValue, input = q('#settings-api-code')) => {
    let scrubbed = 0;
    for (const entry of documentValue?.model_services || []) {
      if (typeof entry.api_key === 'string' && entry.api_key) scrubbed += 1;
      if (entry && typeof entry === 'object') entry.api_key = '';
    }
    if (input && documentValue) input.value = JSON.stringify(documentValue, null, 2) + '\n';
    developerEditorMetrics.api_key_scrub_count += scrubbed;
    updateDeveloperVisuals('api');
    return scrubbed;
  };

  const writeDeveloperApiCredentials = async (documentValue, createdCredentialRefs) => {
    const originalById = new Map(state.settings.model_services.map((service) => [service.config_id, service]));
    const newCredentialRefs = new Map();
    for (const entry of documentValue.model_services) {
      const existingService = originalById.get(entry.config_id);
      let credentialRef = existingService?.credential_ref
        || providerReference(entry.provider)?.credential_ref
        || null;
      let created = false;
      if (!credentialRef) {
        const orphan = state.settings.credential_references.find((reference) => (
          providerIdentity(reference.provider) === providerIdentity(entry.provider)
          && !state.settings.model_services.some((service) => service.credential_ref === reference.credential_ref)
        ));
        credentialRef = orphan?.credential_ref || null;
      }
      if (!entry.api_key) {
        if (!existingService && credentialRef) newCredentialRefs.set(entry.config_id, credentialRef);
        continue;
      }
      if (credentialRef) {
        await call('settings.credential_replace', {
          credential_ref: credentialRef,
          secret_input: entry.api_key,
          expected_revision: state.revision
        });
      } else {
        const receipt = await call('settings.credential_create', {
          provider: entry.provider.trim(),
          logical_key: 'primary',
          secret_input: entry.api_key,
          expected_revision: state.revision
        });
        credentialRef = receipt.credential_ref;
        created = true;
      }
      developerEditorMetrics.credential_write_count += 1;
      await refresh({ apply: false });
      if (!existingService) newCredentialRefs.set(entry.config_id, credentialRef);
      if (created) createdCredentialRefs.push(credentialRef);
    }
    return { newCredentialRefs, createdCredentialRefs };
  };

  const rollbackDeveloperCreatedCredentials = async (credentialRefs) => {
    for (const credentialRef of [...credentialRefs].reverse()) {
      try {
        await call('settings.credential_delete', {
          credential_ref: credentialRef,
          expected_revision: state.revision
        });
        await refresh({ apply: false });
      } catch (_) { /* preserve the original developer save failure */ }
    }
  };

  const saveDeveloperApi = async (documentValue) => {
    const input = q('#settings-api-code');
    let createdCredentialRefs = [];
    let receipt = null;
    let completed = false;
    try {
      const credentialWrites = await writeDeveloperApiCredentials(documentValue, createdCredentialRefs);
      const currentById = new Map(state.settings.model_services.map((service) => [service.config_id, service]));
      const next = structuredClone(state.settings);
      // Saving from the code workbench must not silently return the page to
      // normal mode. Keep the developer surface active after persistence.
      next.mode = 'DEVELOPER';
      next.model_services = documentValue.model_services.map((entry) => {
        const current = currentById.get(entry.config_id);
        const credentialRef = current?.credential_ref || credentialWrites.newCredentialRefs.get(entry.config_id);
        if (!credentialRef) throw developerError('DEVELOPER_CREDENTIAL_REFERENCE_NOT_CREATED', entry.config_id);
        return {
          ...(current || {}),
          config_id: entry.config_id.trim(),
          provider: entry.provider.trim(),
          credential_ref: credentialRef,
          model_name: entry.model_name.trim(),
          api_protocol: entry.api_protocol,
          api_base_url: entry.api_base_url.trim().replace(/\/+$/, ''),
          tier: entry.tier.trim(),
          thinking_mode: entry.thinking_mode,
          thinking: entry.thinking_mode === 'thinking',
          plan: entry.plan,
          connection_status: 'UNVERIFIED'
        };
      });
      receipt = await saveSettings(
        next,
        '已保存'
      );
      if (receipt?.status !== 'PASS' && receipt?.action !== 'SAVE') {
        throw developerError('DEVELOPER_SAVE_BLOCKED', receipt?.status || 'UNKNOWN');
      }
      effects = await call('settings.effect_metrics', {});
      completed = true;
    } catch (error) {
      if (createdCredentialRefs.length) await rollbackDeveloperCreatedCredentials(createdCredentialRefs);
      const message = developerStatusCopy(error);
      setDeveloperStatus('api', 'ERROR', message);
      throw error;
    } finally {
      scrubDeveloperApiKeys(documentValue, input);
    }
    if (completed) {
      developerEditorMetrics.save_count += 1;
      developerEditorMetrics.dirty.api = false;
      refreshDeveloperEditor('api', state.settings, true);
      setDeveloperStatus('api', 'SAVED', '配置与只写凭据已持久化；API Key 已从编辑器清空。');
    }
    return receipt;
  };

  const saveDeveloper = async (scope, preparedDocument = null) => {
    await bootstrap();
    const documentValue = preparedDocument || parseDeveloperInput(scope);
    if (scope === 'api') return saveDeveloperApi(documentValue);
    if (scope === 'cli') {
      const currentById = new Map(state.settings.cli_services.map((service) => [service.config_id, service]));
      const cliServices = documentValue.cli_services.map((entry) => {
        const current = currentById.get(entry.config_id);
        const currentModels = new Map(current.models.map((model) => [model.profile_ref, model]));
        const models = entry.models.map((model) => {
          const prior = currentModels.get(model.profile_ref);
          const unchanged = prior?.model_name === model.model_name.trim()
            && prior?.thinking_mode === model.thinking_mode.trim();
          return {
            profile_ref: model.profile_ref,
            display_name: model.display_name.trim(),
            model_name: model.model_name.trim(),
            thinking_mode: model.thinking_mode.trim(),
            connection_status: unchanged ? prior.connection_status : 'UNVERIFIED'
          };
        });
        return {
          ...current,
          executable: entry.executable.trim(),
          enabled: entry.enabled,
          models,
          connection_status: models.some((model) => model.connection_status === 'AVAILABLE')
            ? 'AVAILABLE' : models.some((model) => model.connection_status === 'INVALID') ? 'INVALID' : 'UNVERIFIED'
        };
      });
      const receipt = await saveCliServices({
        cliServices,
        mode: 'developer',
        announce: false
      });
      if (receipt?.status !== 'PASS') {
        throw developerError(receipt?.error_code || 'DEVELOPER_SAVE_BLOCKED', receipt?.status || 'UNKNOWN');
      }
      effects = await call('settings.effect_metrics', {});
      developerEditorMetrics.save_count += 1;
      developerEditorMetrics.dirty.cli = false;
      refreshDeveloperEditor('cli', state.settings, true);
      setDeveloperStatus('cli', 'SAVED', 'CLI 配置已持久化；连通状态保持显式验证。');
      return receipt;
    }
    const next = collectSettings();
    const editedById = new Map(documentValue.nodes.map((node) => [node.node_id, node]));
    next.workflow.nodes = next.workflow.nodes.map((node) => ({ ...node, ...editedById.get(node.node_id) }));
    const receipt = await saveSettings(
      next,
      '流程映射代码已校验并保存；仅之后新建任务使用。'
    );
    if (receipt?.status !== 'PASS' && receipt?.action !== 'SAVE') {
      throw developerError('DEVELOPER_SAVE_BLOCKED', receipt?.status || 'UNKNOWN');
    }
    effects = await call('settings.effect_metrics', {});
    developerEditorMetrics.save_count += 1;
    developerEditorMetrics.dirty[scope] = false;
    refreshDeveloperEditor(scope, state.settings, true);
    setDeveloperStatus(scope, 'SAVED', '本地校验与持久化均已完成。');
    return receipt;
  };

  const formatDeveloper = (scope) => {
    const value = parseDeveloperInput(scope);
    const input = q(`#${developerScopeIds[scope].input}`);
    input.value = JSON.stringify(value, null, 2) + '\n';
    updateDeveloperVisuals(scope);
    setDeveloperStatus(scope, 'DIRTY', '已格式化，未保存');
  };

  const toggleDeveloperDiff = (scope) => {
    const ids = developerScopeIds[scope];
    const panel = q(`#${ids.diff}`);
    const button = q(`#${ids.diffToggle}`);
    const open = panel.hidden;
    panel.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    button.textContent = open ? '隐藏差异' : '查看差异';
    developerEditorMetrics.diff_open[scope] = open;
    if (open) renderDeveloperDiff(scope);
  };

  Object.keys(developerScopeIds).forEach((scope) => {
    const ids = developerScopeIds[scope];
    const input = q(`#${ids.input}`);
    input.addEventListener('input', () => {
      developerEditorMetrics.dirty[scope] = true;
      syncSaveState();
      input.dataset.dirty = 'true';
      updateDeveloperVisuals(scope);
      setDeveloperStatus(scope, 'DIRTY', '代码已修改，尚未保存。');
    });
    input.addEventListener('scroll', () => updateDeveloperLineNumbers(scope));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Tab') {
        event.preventDefault();
        const start = input.selectionStart;
        input.setRangeText('  ', start, input.selectionEnd, 'end');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        return;
      }
      if (event.ctrlKey && event.key.toLowerCase() === 's') {
        event.preventDefault();
        void save().catch((error) => showToast(developerStatusCopy(error)));
      }
    });
    q(`#${ids.cancel}`).addEventListener('click', () => refreshDeveloperEditor(scope, state?.settings, true));
    q(`#${ids.format}`).addEventListener('click', () => {
      try { formatDeveloper(scope); } catch (error) { showToast(developerStatusCopy(error)); }
    });
    q(`#${ids.diffToggle}`).addEventListener('click', () => toggleDeveloperDiff(scope));
    q(`#${ids.save}`).addEventListener('click', () => {
      void save().catch((error) => showToast(developerStatusCopy(error)));
    });
  });
  window.addEventListener('desktop:settings-mode-changed', (event) => {
    const scope = event.detail?.scope;
    if (event.detail?.mode === 'developer' && developerScopeIds[scope]) {
      refreshDeveloperEditor(scope, state?.settings, false);
    }
  });
  window.addEventListener('desktop:settings-mode-scope-changed', (event) => {
    const scope = event.detail?.scope;
    if (event.detail?.mode === 'developer' && developerScopeIds[scope]) {
      refreshDeveloperEditor(scope, state?.settings, false);
    }
  });

  const normalTierByPlan = Object.freeze({
    ECONOMY: 'economy',
    STANDARD: 'standard',
    HIGH_QUALITY: 'high-quality',
    MAXIMUM: 'maximum'
  });

  const selectedDraftPlan = () => (
    q('#settings-model-plan [data-model-plan][aria-checked="true"]')?.dataset.modelPlan || 'STANDARD'
  );

  const setDraftPlan = (plan) => {
    qa('#settings-model-plan [data-model-plan]').forEach((button) => {
      button.setAttribute('aria-checked', String(button.dataset.modelPlan === plan));
    });
  };

  const syncDraftApiConnectionControls = () => {
    const protocol = q('#settings-api-protocol');
    const endpoint = q('#settings-api-base-url');
    if (!protocol || !endpoint) return;
    endpoint.disabled = protocol.value === 'AUTO';
    if (endpoint.disabled) endpoint.value = '';
  };

  const syncDraftModelCapability = () => {
    const hint = q('#settings-model-capability-hint');
    if (!hint) return 'CHAT';
    const provider = q('#settings-provider')?.value || '';
    const modelName = q('#settings-model-name')?.value || '';
    const capability = inferApiModelCapability(provider, modelName);
    const copy = hint.querySelector('span:last-child');
    const dot = hint.querySelector('.status-dot');
    const descriptions = {
      CHAT: modelName ? '对话模型 · 可用于生成、分析与校核节点。' : '填写模型 ID 后显示可用的流程节点。',
      EMBEDDING: '嵌入模型 · 仅用于 向量化 节点。',
      RERANKER: '重排模型 · 用于 整理分析材料 的重排，不用于向量嵌入。',
      VISION_OCR: '视觉 / OCR 模型 · 仅用于文档摄取节点。'
    };
    if (copy) copy.textContent = descriptions[capability];
    if (dot) dot.className = `status-dot ${modelName ? 'green' : 'yellow'}`;
    const thinking = q('#settings-thinking-enabled');
    if (thinking) {
      const compatible = capability === 'CHAT';
      thinking.disabled = !compatible;
      thinking.setAttribute('aria-disabled', String(!compatible));
      if (!compatible) setSwitch('settings-thinking-enabled', false);
    }
    hint.dataset.modelCapability = capability;
    syncDraftReasoning();
    return capability;
  };

  const draftModelProfile = () => {
    const capability = syncDraftModelCapability();
    const advanced = ['advanced', 'developer'].includes(q('.desktop-settings-shell')?.dataset.settingsMode || 'normal');
    const plan = selectedDraftPlan();
    const profile = {
      model_name: control('settings-model-name').value.trim(),
      api_protocol: control('settings-api-protocol').value,
      api_base_url: control('settings-api-base-url').value.trim().replace(/\/+$/, ''),
      tier: advanced ? (control('settings-model-tier').value.trim() || 'default') : (apiReasoningControl({provider:control('settings-provider').value,model_name:control('settings-model-name').value.trim(),api_protocol:control('settings-api-protocol').value,api_base_url:control('settings-api-base-url').value}) ? normalTierByPlan[plan] : 'default'),
      thinking_mode: capability === 'CHAT' && advanced && switchValue('settings-thinking-enabled') ? 'thinking' : '',
      plan: selectedDraftPlan()
    };
    // Editing a saved Key in ordinary mode must not silently convert an
    // existing advanced profile. A different preference intentionally replaces it.
    const previous = state?.settings?.model_services.find(service=>service.config_id===replacingConfigId);
    if (!advanced && previous && previous.model_name===profile.model_name
        && (previous.api_protocol || 'AUTO')===profile.api_protocol
        && (profile.api_protocol==='AUTO' || (previous.api_base_url || '')===profile.api_base_url)
        && normalPlanForService(previous)===plan) {
      profile.tier=previous.tier || 'default';
      profile.thinking_mode=previous.thinking_mode || (previous.thinking?'thinking':'');
    }
    return profile;
  };

  const validateDraftModelProfile = (profile, provider) => {
    const advanced = ['advanced', 'developer'].includes(q('.desktop-settings-shell')?.dataset.settingsMode || 'normal');
    if (!profile.model_name) {
      showToast('请输入接口模型目录中的精确模型 ID。');
      return false;
    }
    if (advanced && (!profile.model_name || !profile.tier)) {
      showToast('请填写模型名称；未指定档位时使用模型默认。');
      return false;
    }
    if (!profile.tier || (profile.thinking_mode && !profile.model_name)) {
      showToast('模型名称与档位需要同时填写；深度思考可按需开启。');
      return false;
    }
    if (!apiModelPattern.test(profile.model_name)) {
      showToast('模型 ID 只能使用字母、数字、点、下划线、斜杠、冒号或连字符。');
      return false;
    }
    const invalidDescriptor = ['tier', 'thinking_mode']
      .map((field) => profile[field])
      .find((value) => value && !modelSegmentPattern.test(value));
    if (invalidDescriptor) {
      showToast('档位和思考模式只能使用字母、数字、点、下划线或连字符。');
      return false;
    }
    const connection = apiConnectionState(provider, profile.api_protocol, profile.api_base_url);
    if (!connection.valid) {
      const messages = {
        API_PROTOCOL_INVALID: '请选择受支持的接口格式。',
        API_PROVIDER_PROTOCOL_REQUIRED: '陌生厂商或中转站需要选择兼容格式并填写 API 地址。',
        API_BASE_URL_REQUIRED: '请输入该服务的 HTTPS API 地址。',
        API_BASE_URL_INVALID: 'API 地址必须是无凭据、查询参数和片段的公开 HTTPS 地址。',
        API_CONNECTION_INCOMPLETE: '官方自动接口不能同时填写自定义 API 地址。'
      };
      showToast(messages[connection.code] || 'API 接口配置不完整。');
      return false;
    }
    profile.api_protocol = connection.api_protocol;
    profile.api_base_url = connection.api_base_url;
    return true;
  };

  const clearProviderError = () => {
    const provider = q('#settings-provider');
    if (!provider) return;
    provider.classList.remove('is-invalid');
    provider.removeAttribute('aria-invalid');
  };

  const showProviderDuplicateError = () => {
    const provider = control('settings-provider');
    provider.classList.add('is-invalid');
    provider.setAttribute('aria-invalid', 'true');
    control('settings-draft-dot').className = 'status-dot red';
    control('settings-draft-status').textContent = '保存失败-该模型配置已存在';
    provider.focus();
    showToast('同一服务商下已经存在完全相同的模型配置。');
  };

  const resetApiDraft = async ({ close = true } = {}) => {
    for (const id of ['settings-provider', 'settings-api-key', 'settings-model-name', 'settings-model-tier', 'settings-api-base-url']) {
      control(id).value = '';
    }
    control('settings-api-protocol').value = 'AUTO';
    syncDraftApiConnectionControls();
    setSwitch('settings-thinking-enabled', false);
    setDraftPlan('STANDARD');
    resetCredentialDraftState();
    replacingCredentialRef = null;
    replacingConfigId = null;
    clearProviderError();
    syncDraftModelCapability();
    if (close) await window.__Desktop_SETTINGS_UI__?.setApiDraftOpen?.(false);
  };

  const prepareCredentialDraft = async () => {
    credentialDraftGeneration += 1;
    await resetApiDraft({ close: false });
  };

  const cancelCredentialDraftOperation = async () => {
    credentialDraftGeneration += 1;
    await resetApiDraft();
    showToast('未保存的 API 草稿已取消。');
  };

  const bindCredential = async (operationGeneration = credentialDraftGeneration, options = {}) => {
    await bootstrap();
    if (operationGeneration !== credentialDraftGeneration) return null;
    const input = control('settings-api-key');
    const provider = control('settings-provider').value.trim();
    const credentialRefAtStart = replacingCredentialRef;
    const configIdAtStart = replacingConfigId;
    if (!provider || provider.length > 64) {
      showToast('请输入 1–64 个字符的服务商名称。');
      return null;
    }
    clearProviderError();
    const secretInput = input.value || '';
    const profile = draftModelProfile();
    if (!validateDraftModelProfile(profile, provider)) return null;
    const duplicate = state.settings.model_services.find(
      (service) => service.config_id !== configIdAtStart
        && providerIdentity(service.provider) === providerIdentity(provider)
        && modelIdentity(service) === modelIdentity(profile)
    );
    if (duplicate) {
      showProviderDuplicateError();
      return null;
    }
    const reusableReference = !credentialRefAtStart ? providerReference(provider) : null;
    const orphanReference = !credentialRefAtStart
      ? state.settings.credential_references.find((reference) => (
          providerIdentity(reference.provider) === providerIdentity(provider)
          && !state.settings.model_services.some(
            (service) => service.credential_ref === reference.credential_ref
          )
        ))
      : null;
    const credentialRefToWrite = credentialRefAtStart
      || reusableReference?.credential_ref
      || orphanReference?.credential_ref
      || null;
    const existing = credentialRefToWrite
      ? state.settings.credential_references.find((row) => row.credential_ref === credentialRefToWrite)
      : null;
    if (credentialRefToWrite && !existing) throw new Error('CREDENTIAL_REFERENCE_TO_REPLACE_NOT_FOUND');
    if (!secretInput && !existing) {
      showToast('首次添加该服务商时需要输入 API Key；后续模型可复用已保存凭据。');
      return null;
    }
    let createdReference = null;
    input.value = '';
    if (credentialRefToWrite && secretInput) {
      await call('settings.credential_replace', {
        credential_ref: existing.credential_ref,
        secret_input: secretInput,
        expected_revision: state.revision
      });
    } else if (!credentialRefToWrite) {
      const receipt = await call('settings.credential_create', {
        provider,
        logical_key: 'primary',
        secret_input: secretInput,
        expected_revision: state.revision
      });
      createdReference = receipt.credential_ref;
    }
    await refresh({ apply: false });
    const reference = state.settings.credential_references.find(
      (row) => row.credential_ref === (createdReference || credentialRefToWrite)
    );
    if (!reference) throw new Error('CREDENTIAL_REFERENCE_NOT_PERSISTED');

    const next = structuredClone(options.settingsDraft || state.settings);
    next.credential_references = structuredClone(state.settings.credential_references);
    next.mode = persistedSettingsMode(activeSettingsMode());
    let service = configIdAtStart
      ? next.model_services.find((row) => row.config_id === configIdAtStart)
      : null;
    if (configIdAtStart && !service) throw new Error('MODEL_SERVICE_NOT_FOUND');
    if (!service) {
      const referenceSegment = reference.credential_ref.split('/').at(-2) || 'custom-v1-provider';
      let configId = `${referenceSegment}-primary`;
      const stem = referenceSegment.replace(/[^A-Za-z0-9._:-]+/g, '-');
      let suffix = 2;
      while (next.model_services.some((row) => row.config_id === configId)) configId = `${stem}-primary-${suffix++}`;
      service = {
        config_id: configId,
        provider,
        credential_ref: reference.credential_ref,
        plan: 'STANDARD',
        thinking: Boolean(profile.thinking_mode),
        ...profile,
        connection_status: 'UNVERIFIED'
      };
      next.model_services.push(service);
    } else {
      service.provider = provider;
      service.credential_ref = reference.credential_ref;
      service.thinking = Boolean(profile.thinking_mode);
      Object.assign(service, profile);
      service.connection_status = 'UNVERIFIED';
    }
    if (isKimiService(service)) {
      applyKimiAutoDetection(service);
    } else {
      delete service.api_platform;
    }
    let saveReceipt;
    try {
      saveReceipt = await saveSettings(next, '已保存', options);
      if (saveReceipt?.status === 'BLOCKED' || saveReceipt?.schema_version === 'SettingsPreview-v1') {
        throw new Error('MODEL_CONFIGURATION_SAVE_BLOCKED');
      }
      effects = await call('settings.effect_metrics', {});
      if (options.refreshAfter === false) await refresh({ apply:false });
    } catch (error) {
      if (createdReference && state) {
        try {
          await call('settings.credential_delete', {
            credential_ref: createdReference,
            expected_revision: state.revision
          });
          await refresh();
        } catch (_) { /* preserve the original failure */ }
      }
      throw error;
    }
    if (operationGeneration === credentialDraftGeneration) await resetApiDraft();
    return options.returnReceipt ? saveReceipt : service;
  };

  const countryFlagElement = (countryCode) => {
    const normalized = String(countryCode || '').trim().toUpperCase();
    if (!/^[A-Z]{2}$/.test(normalized)) return null;
    const namespace = ['http:', '//www.w3.org/2000/svg'].join('');
    const span = create('span', 'desktop-settings-country-flag');
    span.dataset.countryCode = normalized;
    span.setAttribute('role', 'img');
    span.setAttribute('aria-label', `${normalized} 国旗`);
    const svg = document.createElementNS(namespace, 'svg');
    svg.setAttribute('viewBox', '0 0 30 20');
    svg.setAttribute('aria-hidden', 'true');
    const shape = (tag, attributes) => {
      const node = document.createElementNS(namespace, tag);
      Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, String(value)));
      svg.appendChild(node);
      return node;
    };
    const banded = (colours, vertical = false) => colours.forEach((colour, index) => {
      const size = (vertical ? 30 : 20) / colours.length;
      shape('rect', vertical
        ? { x: index * size, y: 0, width: size + 0.1, height: 20, fill: colour }
        : { x: 0, y: index * size, width: 30, height: size + 0.1, fill: colour });
    });
    if (normalized === 'JP') {
      shape('rect', { width: 30, height: 20, fill: '#FFFFFF' });
      shape('circle', { cx: 15, cy: 10, r: 5.8, fill: '#BC002D' });
    } else if (normalized === 'CN') {
      shape('rect', { width: 30, height: 20, fill: '#DE2910' });
      shape('polygon', { points: '5,3 6.1,6.2 9.5,6.2 6.8,8.2 7.8,11.4 5,9.4 2.2,11.4 3.2,8.2 .5,6.2 3.9,6.2', fill: '#FFDE00' });
    } else if (normalized === 'US') {
      banded(['#B22234','#FFFFFF','#B22234','#FFFFFF','#B22234','#FFFFFF','#B22234','#FFFFFF','#B22234','#FFFFFF','#B22234','#FFFFFF','#B22234']);
      shape('rect', { x: 0, y: 0, width: 13, height: 10.8, fill: '#3C3B6E' });
      for (let row = 0; row < 4; row += 1) for (let column = 0; column < 5; column += 1) {
        shape('circle', { cx: 1.4 + column * 2.4, cy: 1.4 + row * 2.5, r: .45, fill: '#FFFFFF' });
      }
    } else if (normalized === 'SG') {
      banded(['#EF3340', '#FFFFFF']);
      shape('circle', { cx: 6.4, cy: 5.4, r: 3.5, fill: '#FFFFFF' });
      shape('circle', { cx: 7.8, cy: 5.4, r: 3.1, fill: '#EF3340' });
      shape('circle', { cx: 10.8, cy: 3.4, r: .55, fill: '#FFFFFF' });
      shape('circle', { cx: 11.8, cy: 5.4, r: .55, fill: '#FFFFFF' });
      shape('circle', { cx: 10.8, cy: 7.4, r: .55, fill: '#FFFFFF' });
    } else if (normalized === 'HK') {
      shape('rect', { width: 30, height: 20, fill: '#DE2910' });
      for (let index = 0; index < 5; index += 1) {
        const petal = shape('ellipse', { cx: 15, cy: 6.6, rx: 1.35, ry: 3.7, fill: '#FFFFFF' });
        petal.setAttribute('transform', `rotate(${index * 72} 15 10)`);
      }
    } else if (normalized === 'TW') {
      shape('rect', { width: 30, height: 20, fill: '#FE0000' });
      shape('rect', { width: 15, height: 10, fill: '#000095' });
      shape('circle', { cx: 7.5, cy: 5, r: 2.3, fill: '#FFFFFF' });
    } else if (normalized === 'KR') {
      shape('rect', { width: 30, height: 20, fill: '#FFFFFF' });
      shape('path', { d: 'M15 5a5 5 0 0 1 0 10 2.5 2.5 0 0 1 0-5 2.5 2.5 0 0 0 0-5z', fill: '#CD2E3A' });
      shape('path', { d: 'M15 15a5 5 0 0 1 0-10 2.5 2.5 0 0 1 0 5 2.5 2.5 0 0 0 0 5z', fill: '#0047A0' });
    } else if (normalized === 'DE') {
      banded(['#000000', '#DD0000', '#FFCE00']);
    } else if (normalized === 'FR') {
      banded(['#0055A4', '#FFFFFF', '#EF4135'], true);
    } else if (normalized === 'GB') {
      shape('rect', { width: 30, height: 20, fill: '#012169' });
      shape('path', { d: 'M0 0l30 20M30 0L0 20', stroke: '#FFFFFF', 'stroke-width': 4 });
      shape('path', { d: 'M0 0l30 20M30 0L0 20', stroke: '#C8102E', 'stroke-width': 1.8 });
      shape('path', { d: 'M15 0v20M0 10h30', stroke: '#FFFFFF', 'stroke-width': 6 });
      shape('path', { d: 'M15 0v20M0 10h30', stroke: '#C8102E', 'stroke-width': 3.2 });
    } else {
      banded(['#3A6EA5', '#FFFFFF', '#D04A4A']);
    }
    span.appendChild(svg);
    return span;
  };

  const networkTest = async () => {
    await bootstrap();
    q('#settings-network-dot').className = 'status-dot yellow';
    q('#settings-network-status').textContent = '正在检查';
    q('#settings-network-copy').textContent = '正在核对本地地址与当前 HTTPS 出口…';
    const receipt = await nativeAction('test_network_connection', {
      proxy_mode: q('#settings-proxy-mode').value.toUpperCase(),
      proxy_address: q('#settings-proxy-address').value.trim(),
      timeout_seconds: Number.parseInt(q('#settings-request-timeout').value, 10)
    });
    const passed = receipt.status === 'PASS';
    q('#settings-network-dot').className = passed ? 'status-dot green' : 'status-dot red';
    q('#settings-network-status').textContent = passed ? '网络检查通过' : '网络检查失败';
    q('#settings-local-ip').textContent = receipt.local_ip || '未识别';
    q('#settings-egress-ip').textContent = receipt.egress_ip || '未取得';
    renderNetworkLocation(receipt);
    q('#settings-network-copy').textContent = passed
      ? `已通过 ${receipt.proxy_mode} 路径取得出口；耗时 ${receipt.elapsed_ms} ms`
      : `检查失败：${receipt.reason_code || 'NETWORK_TEST_FAILED'}`;
    showToast(passed
      ? `网络检查完成：本地 IP ${receipt.local_ip || '未识别'}；出口 IP ${receipt.egress_ip}。`
      : `网络检查失败：${receipt.reason_code || '未知错误'}`);
    return receipt;
  };

  const renderNetworkLocation = (receipt = {}) => {
    const egressLocation = q('#settings-egress-location');
    const locationParts = [receipt.country_code, receipt.colo].filter(Boolean);
    const flag = countryFlagElement(receipt.country_code);
    egressLocation.replaceChildren(document.createTextNode(locationParts.join(' · ') || '—'));
    if (flag) egressLocation.append(document.createTextNode(' · '), flag);
    return {
      country_code: String(receipt.country_code || '').toUpperCase(),
      colo: receipt.colo || null,
      flag_present: Boolean(flag),
      svg_present: Boolean(flag?.querySelector('svg'))
    };
  };

  const previewAppearance = () => {
    if (!state?.settings) return;
    window.__Desktop_INTEGRATED_APP__?.applyPreferences?.({
      ...state.settings.preferences,
      language: q('#settings-language')?.value || state.settings.preferences.language,
      font_scale_percent: Number.parseInt(q('#settings-font-size')?.textContent || '100', 10),
      show_full_tooltips: switchValue('settings-full-tooltips')
    });
    applySettingsLocale();
    if (localModelsState) renderLocalModels(localModelsState);
    if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    if (refinementState) {
      const empty = q('#settings-refinement-model option[value=""]');
      if (empty) empty.textContent = uiText('refinement.model.none', '未配置模型');
      q('#settings-refinement-status').textContent = refinementState?.configured
        ? uiText('refinement.status.configured', '已配置')
        : uiText('refinement.status.unconfigured', '未配置时不可用。');
    }
    renderAssistantAppearance(switchValue('settings-assistant-enabled-appearance'));
  };

  const pickDirectory = async (role, inputId) => {
    const receipt = await nativeAction('pick_settings_directory', role);
    if (receipt.status !== 'SELECTED') return receipt;
    q(`#${inputId}`).value = receipt.path;
    if (role === 'external_library' && !q('#settings-library-name').value.trim()) {
      q('#settings-library-name').value = receipt.path.split(/[\\/]/).filter(Boolean).at(-1) || '外部资料库';
    }
    markDirty();
    showToast('目录已选择并通过本地访问检查；点击保存后持久化。');
    return receipt;
  };

  const testLibrary = async () => {
    const providerKind = q('#settings-library-provider').value;
    const displayName = q('#settings-library-name').value.trim();
    const path = q('#settings-library-root').value.trim();
    if (providerKind === 'NONE') throw new Error('请先选择外部资料库类型。');
    if (!displayName) throw new Error('请填写“显示名称”；它只是在 Memorive 中显示的自定义标签。');
    if (!path) throw new Error(providerKind === 'OBSIDIAN'
      ? '请选择包含 .obsidian 文件夹的 Obsidian Vault 根目录。'
      : '请选择资料库根目录。');
    const button = q('#settings-library-test');
    button.disabled = true;
    button.textContent = '检测中…';
    q('#settings-library-dot').className = 'status-dot yellow';
    q('#settings-library-status').textContent = '正在检测';
    try {
      const receipt = await nativeAction('test_external_library', {
        provider_kind: providerKind,
        display_name: displayName,
        root: path
      });
      q('#settings-library-dot').className = 'status-dot green';
      q('#settings-library-status').textContent = '本地可用';
      q('#settings-library-copy').textContent = providerKind === 'OBSIDIAN'
        ? '目录可用，待保存'
        : '目录可用，待保存';
      showToast('目录可用');
      return receipt;
    } finally {
      button.disabled = false;
      button.textContent = '检测';
    }
  };

  const testWindowsNotification = async () => {
    await bootstrap();
    const button = q('#settings-notification-test');
    button.disabled = true;
    button.textContent = '发送中…';
    try {
      const saved = await save();
      if (saved?.status === 'BLOCKED') return saved;
      const receipt = await nativeAction('test_windows_notification', {
        kind: q('#settings-notification-test-kind').value
      });
      const messages = {
        DELIVERED: 'Windows 测试通知已发送。',
        BLOCKED_DISABLED: '测试通知被当前类别开关拦截；请开启对应通知后重试。',
        BLOCKED_QUIET: '当前处于勿扰时段',
        BLOCKED_UNAVAILABLE: 'Windows 通知展示器当前不可用。'
      };
      q('#settings-notification-test-copy').textContent = messages[receipt.status]
        || `通知测试结果：${receipt.status}`;
      showToast(q('#settings-notification-test-copy').textContent);
      return receipt;
    } finally {
      button.disabled = false;
      button.textContent = '发送测试通知';
    }
  };

  const checkCredential = async (credentialRef) => {
    const receipt = await call('settings.credential_status', { credential_ref: credentialRef });
    const button = qa('[data-credential-status]').find((candidate) => candidate.dataset.credentialStatus === credentialRef);
    if (button) {
      button.textContent = receipt.status === 'STORED_UNVERIFIED' ? '再次检查' : '检查本地引用';
      const statusCard = button.closest('.desktop-settings-status-card');
      const heading = statusCard?.querySelector('strong');
      if (heading) heading.textContent = receipt.status === 'STORED_UNVERIFIED' ? '本地引用存在' : '本地引用缺失';
      statusCard?.classList.remove('yellow', 'red');
      statusCard?.classList.add('neutral');
    }
    showToast(receipt.status === 'STORED_UNVERIFIED'
      ? 'Windows 凭据引用存在；未读取凭据值，也未发送外部请求。'
      : `本地凭据状态：${receipt.status}`);
    return receipt;
  };

  const deleteModelService = async (configId) => {
    await bootstrap();
    const receipt = await call('settings.model_service_remove', {
      config_id: configId,
      expected_revision: state.revision
    });
    await refresh();
    showToast(receipt.credential_deleted
      ? '模型配置及其未再使用的凭据引用已删除；其他设置保持不变。'
      : '仅删除了当前模型；同一服务商的其他模型与凭据保持不变。');
    return receipt;
  };

  const replaceCredential = (configId) => {
    const service = state?.settings?.model_services?.find((row) => row.config_id === configId);
    const provider = control('settings-provider');
    if (service) {
      credentialDraftGeneration += 1;
      resetCredentialDraftState();
      replacingCredentialRef = service.credential_ref;
      replacingConfigId = service.config_id;
      clearProviderError();
      provider.value = service.provider;
      provider.dispatchEvent(new Event('input', { bubbles: true }));
      control('settings-model-name').value = service.model_name || '';
      control('settings-model-tier').value = service.tier || '';
      control('settings-api-protocol').value = service.api_protocol || 'AUTO';
      control('settings-api-base-url').value = service.api_base_url || '';
      syncDraftApiConnectionControls();
      setSwitch('settings-thinking-enabled', Boolean(service.thinking_mode || service.thinking));
      setDraftPlan(normalPlanForService(service));
      syncDraftModelCapability();
    }
    void window.__Desktop_SETTINGS_UI__?.setApiDraftOpen?.(true);
    control('settings-api-key').focus();
    showToast('可直接保存模型字段；仅在需要更换 Key 时输入新凭据。');
  };

  const requireSavedValidationTarget = () => {
    if (!q('#settings-save')?.disabled) {
      showToast('请先保存当前修改，再验证已持久化的模型配置。');
      return false;
    }
    return true;
  };

  const apiValidationReasonCopy = Object.freeze({
    API_VERIFICATION_NOT_ASSESSED: '验证未完成，尚未取得接口结果。',
    API_VERIFICATION_INTERNAL_ERROR: '本地验证发生异常，尚不能判断 Key 是否有效。请查看诊断记录。',
    API_VERIFICATION_TEST_POLICY_BLOCKED: '当前运行副本带有测试限制，请使用完整应用版本后验证。',
    API_REASONING_EFFORT_UNSUPPORTED: '该接口不支持所选推理档位，请选择可用档位。',
    API_VERIFICATION_TARGET_CHANGED: '配置已变更，请重新验证。',
    API_VERIFICATION_WORKER_LOST: '验证已中断，请重新验证。',
    API_MODEL_NOT_LISTED: '当前实时模型目录中没有这个模型 ID，请核对是否已改名或下架。',
    API_PROVIDER_UNSUPPORTED: '该服务尚未选择兼容接口格式和 API 地址。',
    API_ENDPOINT_INVALID: 'API 地址无效；请使用公开 HTTPS 地址。',
    API_MODEL_NOT_CONFIGURED: '尚未填写模型 ID。',
    API_CREDENTIAL_UNAVAILABLE: '无法读取已保存的 Windows 凭据引用。',
    API_AUTHENTICATION_REJECTED: '接口拒绝了当前凭据。',
    API_RATE_LIMITED: '接口暂时限流，请稍后重试。',
    API_NETWORK_OR_RESPONSE_FAILURE: '接口连接失败。',
    API_TLS_CERTIFICATE_FAILED: '接口证书验证失败。',
    API_TLS_CONNECTION_FAILED: '接口加密连接失败。',
    API_DNS_FAILED: '接口域名解析失败。',
    API_CONNECTION_TIMEOUT: '接口连接超时。',
    API_CONNECTION_REFUSED: '接口或代理拒绝连接。',
    API_SYSTEM_PROXY_UNAVAILABLE: '系统代理不可用。',
    API_SYSTEM_PROXY_RESOLUTION_FAILED: '系统代理解析失败。',
    API_CATALOG_REQUEST_FAILED: '模型目录请求失败。',
    API_CATALOG_RESPONSE_INVALID: '接口未返回受支持的模型目录格式。',
    API_CATALOG_PAGINATION_LIMIT: '模型目录分页过多，已安全停止。'
  });

  const receiptCatalogModels = (receipt) => {
    if (!Array.isArray(receipt?.available_models)) return [];
    return receipt.available_models.filter(
      (modelId, index, values) => apiModelPattern.test(modelId) && values.indexOf(modelId) === index
    ).slice(0, 50);
  };

  const renderApiValidationReceipt = (configId, receipt) => {
    const validationCard = q(`[data-api-validation="${CSS.escape(configId)}"]`);
    if (!validationCard) return;
    const heading = validationCard.querySelector('strong');
    const copy = validationCard.querySelector('span');
    const action = validationCard.querySelector('button');
    if (!heading || !copy || !action) return;
    const models = receiptCatalogModels(receipt);
    const suggested = models.includes(receipt?.suggested_model) ? receipt.suggested_model : null;
    delete action.dataset.useCatalogModel;
    delete action.dataset.apiConfigId;
    action.dataset.validateApi = configId;
    action.textContent = receipt.status === 'AVAILABLE' ? '重新验证' : '再次验证';
    if (receipt.status === 'NOT_RUN') {
      validationCard.classList.remove('red','neutral');validationCard.classList.add('yellow');
      heading.textContent = '验证未完成';
      copy.textContent = apiValidationReasonCopy[receipt.reason] || '验证结果尚未确认，请重新查看验证状态。';
      return;
    }
    if (receipt.status === 'AVAILABLE') {
      validationCard.classList.remove('red', 'yellow');
      validationCard.classList.add('neutral');
      heading.textContent = '验证通过';
      copy.textContent = `实时目录已确认模型 ID：${receipt.returned_model || receipt.requested_model || '—'}`;
      return;
    }
    validationCard.classList.remove('neutral', 'yellow');
    validationCard.classList.add('red');
    heading.textContent = receipt.reason === 'API_MODEL_NOT_LISTED' ? '模型 ID 不可用' : '验证失败';
    const reason = apiValidationReasonCopy[receipt.reason] || receipt.reason || '未知错误';
    const listed = models.length ? ` 实时目录：${models.slice(0, 6).join('、')}${models.length > 6 ? '…' : ''}` : '';
    copy.textContent = `${reason}${listed}`;
    copy.title = models.join('\n');
    if (suggested) {
      delete action.dataset.validateApi;
      action.dataset.useCatalogModel = suggested;
      action.dataset.apiConfigId = configId;
      action.textContent = `改用 ${suggested}`;
    }
  };

  const applyCatalogModel = async (configId, modelId, button) => {
    await bootstrap();
    if (!apiModelPattern.test(modelId)) throw new Error('API_CATALOG_MODEL_INVALID');
    const next = structuredClone(state.settings);
    const service = next.model_services.find((row) => row.config_id === configId);
    if (!service) throw new Error('MODEL_SERVICE_NOT_FOUND');
    const oldCopy = button.textContent;
    button.disabled = true;
    button.textContent = '保存中…';
    try {
      service.model_name = modelId;
      service.connection_status = 'UNVERIFIED';
      next.mode = persistedSettingsMode(activeSettingsMode());
      const receipt = await saveSettings(
        next,
        `模型 ID 已更新为“${modelId}”；请再次验证确认。`
      );
      if (receipt?.status !== 'PASS' && receipt?.action !== 'SAVE') return receipt;
      return receipt;
    } finally {
      button.disabled = false;
      button.textContent = oldCopy;
    }
  };

  const runApiValidation = async (configId, button) => {
    await bootstrap();
    if (!requireSavedValidationTarget()) return null;
    if (apiValidationInFlight) return null;
    apiValidationInFlight = true;
    const oldCopy = button.textContent;
    button.disabled = true;
    button.textContent = '验证中…';
    button.setAttribute('aria-busy', 'true');
    try {
      let job = await call('settings.api_verification_status', {config_id:configId});
      if (job.state !== 'RUNNING') {
        const params = {config_id:configId,idempotency_key:crypto.randomUUID()};
        try { job = await call('settings.start_api_verification', params); }
        catch (error) {
          // Uncertain IPC delivery is not permission to repeat provider metadata requests.
          job = await call('settings.api_verification_status', {idempotency_key:params.idempotency_key});
          if (job.state === 'IDLE') throw error;
        }
      }
      await refresh();
      const progress = (lost=false) => {
        const card = q(`[data-api-validation="${CSS.escape(configId)}"]`);
        if (!card) return;
        card.classList.remove('red','neutral');card.classList.add('yellow');
        card.querySelector('strong').textContent = '正在验证';
        card.querySelector('span').textContent = lost ? '暂时无法读取验证进度，正在重新连接。' : '正在读取模型目录…';
        card.querySelector('button').disabled = true;
      };
      if (job.state === 'RUNNING') progress();
      while (job.state === 'RUNNING') {
        await new Promise(resolve=>setTimeout(resolve,1500));
        try {
          job = await call('settings.api_verification_status', {job_id:job.job_id});
          if (job.state === 'RUNNING') progress();
        } catch (_) { progress(true); }
      }
      const receipt = job.receipt || {status:'NOT_RUN',reason:job.reason || 'API_VERIFICATION_NOT_ASSESSED'};
      await refresh();
      renderApiValidationReceipt(configId, receipt);
      const modelId = receipt.returned_model || receipt.requested_model || '';
      const suggested = receiptCatalogModels(receipt).includes(receipt.suggested_model)
        ? receipt.suggested_model : null;
      showToast(receipt.status === 'AVAILABLE'
        ? `已在当前实时模型目录中确认${modelId ? `“${modelId}”` : '该模型'}可用。`
        : suggested
          ? `当前填写的是版本/档位描述；可直接改用实时目录 ID“${suggested}”。`
          : `API 验证未通过：${apiValidationReasonCopy[receipt.reason] || receipt.reason || '未知错误'}`);
      return receipt;
    } catch (error) {
      try { await refresh(); } catch (_) { /* Do not replace missing state with a success. */ }
      renderApiValidationReceipt(configId,{status:'NOT_RUN',reason:'API_VERIFICATION_NOT_ASSESSED'});
      showToast('API 验证结果尚未确认，请重新查看验证状态。');
      return {status:'NOT_RUN',reason:'API_VERIFICATION_NOT_ASSESSED'};
    } finally {
      apiValidationInFlight = false;
      button.disabled = false;
      button.textContent = oldCopy;
      button.removeAttribute('aria-busy');
      for (const action of document.querySelectorAll('[data-validate-api]')) action.disabled = false;
    }
  };

  const runCliValidation = async (configId, profileRef, button) => {
    await bootstrap();
    if (cliValidationInFlight) {
      const pending = {
        status: 'NOT_RUN',
        reason: 'CLI_VALIDATION_IN_PROGRESS',
        persistent_mutation: false
      };
      renderCliValidationReceipt(profileRef, pending);
      showToast(cliValidationMessage(pending));
      return pending;
    }
    cliValidationInFlight = true;
    const oldCopy = button.textContent;
    for (const target of document.querySelectorAll('[data-validate-cli]')) target.disabled = true;
    button.textContent = '正在保存…';
    button.setAttribute('aria-busy', 'true');
    try {
      const saveReceipt = hasPendingSettingsDraft()
        ? await save()
        : {status:'PASS', action:'NO_CHANGES'};
      if (saveReceipt?.status !== 'PASS') {
        renderCliValidationReceipt(profileRef, {
          status: 'NOT_RUN',
          reason: saveReceipt?.error_code || 'CLI_SETTINGS_INVALID'
        });
        showToast(cliSaveMessage(saveReceipt));
        return saveReceipt;
      }
      const liveButton = q(`[data-validate-cli="${CSS.escape(profileRef)}"]`);
      if (liveButton) {
        liveButton.disabled = true;
        liveButton.textContent = '验证中…';
        liveButton.setAttribute('aria-busy', 'true');
      }
      renderCliValidationReceipt(profileRef, {status:'RUNNING', display_message:'CLI 验证正在等待结果。'});
      // Reattach to an existing worker, including after an interrupted UI connection.
      let job = await call('settings.cli_verification_status', {profile_ref:profileRef});
      if (job.state !== 'RUNNING') {
        const params={config_id:configId,profile_ref:profileRef,idempotency_key:crypto.randomUUID()};
        try { job=await call('settings.start_cli_verification',params); }
        catch (error) {
          // Resolve this exact request; an old success from the same profile is not this attempt.
          job=await call('settings.cli_verification_status',{idempotency_key:params.idempotency_key});
          if(job.state==='IDLE')throw error;
        }
      }
      await refresh();
      const progressCopy = value => ({STARTING:'正在启动 CLI…',STOPPING:'正在停止验证…',RECONNECTING:'CLI 正在重新连接…',RECEIVING:'正在接收模型回复…'}[value.stage]
        || (value.slow_response ? '尚未收到完整回复，仍在等待；可停止验证。' : '等待模型回复…'));
      const showProgress=value=>renderCliValidationReceipt(profileRef,{status:'RUNNING',job_id:value.job_id,cancel_requested:value.cancel_requested,display_message:progressCopy(value)});
      if(job.state==='RUNNING')showProgress(job);
      while (job.state==='RUNNING') {
        await new Promise(resolve=>setTimeout(resolve,1500));
        try {
          job=await call('settings.cli_verification_status',{job_id:job.job_id});
          if(job.state==='RUNNING')showProgress(job);
        } catch (_) {
          renderCliValidationReceipt(profileRef,{status:'RUNNING',job_id:job.job_id,display_message:'暂时无法读取验证进度；正在重新连接，没有重发模型请求。'});
        }
      }
      const receipt=job.receipt||{status:'NOT_RUN',reason:job.reason||'CLI_VERIFICATION_NOT_ASSESSED'};
      await refresh();
      renderCliValidationReceipt(profileRef, receipt);
      showToast(receipt.status === 'AVAILABLE'
        ? 'CLI 模型最小验证通过。'
        : receipt.status === 'INVALID'
          ? `CLI 模型验证失败：${cliValidationMessage(receipt)}`
          : cliValidationMessage(receipt));
      return receipt;
    } catch (error) {
      renderCliValidationReceipt(profileRef,{status:'NOT_RUN',display_message:'验证结果未取得，请检查 CLI 或连接状态；上一次通过不代表本次通过。'});
      showToast('CLI 验证未完成，请查看验证状态。');
      return {status:'NOT_RUN',reason:'CLI_VERIFICATION_NOT_ASSESSED'};
    } finally {
      cliValidationInFlight = false;
      for (const target of document.querySelectorAll('[data-validate-cli]')) {
        target.disabled = false;
        target.textContent = target.dataset.validateCli === profileRef
          ? (target.closest('[data-cli-profile-ref]')?.querySelector('.desktop-settings-cli-model-validation.neutral') ? '再次验证' : oldCopy)
          : (target.closest('[data-cli-profile-ref]')?.querySelector('.desktop-settings-cli-model-validation.neutral') ? '再次验证' : '保存并验证');
        target.removeAttribute('aria-busy');
      }
    }
  };

  let workflowExamInFlight = false;
  let workflowExamJob = null;
  const examTerminal = job => ['COMPLETE','ERROR','CANCELLED','INTERRUPTED'].includes(job.state);
  const watchWorkflowExam = async (job, step) => {
    workflowExamJob = job;
    let connectionLost = false;
    const stop = q('#settings-exam-stop');
    stop.onclick = async () => {
      stop.disabled = true;
      try { workflowExamJob = await call('settings.cancel_workflow_exam', {job_id:job.job_id}); }
      catch (_) { showToast('停止请求未送达 · 可重试'); stop.disabled = false; }
    };
    while (true) {
      const terminal = examTerminal(workflowExamJob);
      window.__Desktop_EXAM_PANEL__?.progress(terminal ? (workflowExamJob.state === 'COMPLETE' ? 'COMPLETE' : 'ERROR') : 'RUNNING', {job:workflowExamJob,step,connectionLost});
      stop.hidden = terminal; stop.disabled = !!workflowExamJob.cancel_requested;
      if (terminal) return workflowExamJob.receipt || {status:'NOT_RUN',reason:workflowExamJob.reason || workflowExamJob.state};
      await new Promise(resolve => setTimeout(resolve,1500));
      try { workflowExamJob = await call('settings.workflow_exam_status', {job_id:job.job_id}); connectionLost = false; }
      catch (_) { connectionLost = true; }
    }
  };
  const runWorkflowNodeTest = async (step, button) => {
    if (workflowExamInFlight) return null;
    await bootstrap();
    if (!requireSavedValidationTarget()) return null;
    const nodeId = nodeByStep.get(step);
    if (!nodeId) throw new Error('WORKFLOW_NODE_NOT_FOUND');
    const selectedExamTier = q('#settings-exam-tier')?.value || 'FULL';
    workflowExamInFlight = true;
    window.__Desktop_EXAM_PANEL__?.progress('PLANNING', {label:q('#settings-node-title')?.textContent || nodeId, nodeId,step});
    if (q('#settings-exam-tier')) q('#settings-exam-tier').disabled = true;
    const oldCopy = button.textContent;
    let examStarted = false;
    button.disabled = true;
    button.textContent = '生成计划…';
    button.setAttribute('aria-busy', 'true');
    try {
      const plan = await call('settings.test_workflow_node', {
        node_id: nodeId,
        exam_tier: selectedExamTier,
        force_exam: true,
        plan_only: true
      });
      if (plan.status !== 'READY') {
        window.__Desktop_EXAM_PANEL__?.progress('ERROR');
        showToast(`当前映射没有可执行考试：${plan.reason || 'NOT_AVAILABLE'}`);
        return plan;
      }
      let authorization = null;
      if (!(await window.__Desktop_EXAM_PANEL__.confirmRun(plan))) return null;
      if (plan.authorization_required || plan.authorization_schema_version) {
        authorization = {schema_version:plan.authorization_schema_version, authorized:true,
          plan_sha256:plan.plan_sha256, cost_cap_cny:plan.budget_cap_cny,
          acknowledged_subscription_no_per_call_price:plan.profile_kind === 'CLI'};
        if (plan.mode === 'LIVE_Quality_CARD_DISTILLER_REFERENCE_REGRESSION') {
          delete authorization.acknowledged_subscription_no_per_call_price;
          authorization.acknowledged_non_reusable_ratio_exception = true;
        }
      }
      examStarted = true;
      button.textContent = '考试中…';
      window.__Desktop_EXAM_PANEL__?.progress('RUNNING');
      const params = { node_id: nodeId, exam_tier: selectedExamTier, force_exam: true,
        idempotency_key:crypto.randomUUID(), expected_plan_sha256:plan.plan_sha256 };
      if (authorization) params.authorization = authorization;
      let job;
      try { job = await call('settings.start_workflow_exam', params); }
      catch (error) {
        // An uncertain start response is not permission to issue another exam.
        job = await call('settings.workflow_exam_status', {node_id:nodeId});
        if (!job.job_id || job.plan_sha256 !== plan.plan_sha256) throw error;
      }
      const receipt = await watchWorkflowExam(job, step);
      window.__Desktop_EXAM_PANEL__?.progress(receipt.status === 'NOT_RUN' && !receipt.execution_completed ? 'ERROR' : 'COMPLETE');
      if (receipt.persistent_mutation) await refresh();
      if (!receipt.saved_mapping_changed && !receipt.tier_result_is_provisional && receipt.status !== 'NOT_RUN' && Number.isInteger(receipt.score)
          && q('button.desktop-settings-node[aria-current="true"]')?.dataset.step === step) q('#settings-node-test-score').textContent = String(receipt.score);
      const tierCopy = q('#settings-tier-result');
      const panelCopy = (text) => (window.__Desktop_INTEGRATED_APP__?.translate?.(text) || text)
        .replace('{score}', String(receipt.diagnostic_score)).replace('{count}', String(receipt.cohort_panel_count));
      if (tierCopy && q('button.desktop-settings-node[aria-current="true"]')?.dataset.step === step) tierCopy.textContent = receipt.exam_tier
        ? `${receipt.exam_tier.label} · 实际覆盖 ${Math.round(receipt.exam_tier.actual_fraction * 1000) / 10}%` +
          (receipt.assessment_status==='COHORT_PENDING' ? ' · '+panelCopy('本组 {score} 分 · 已完成 {count}/3 组') : receipt.status === 'NOT_RUN' ? ' · 未取得有效评分' :
            receipt.tier_result_is_provisional ? ` · 子集成绩 ${receipt.score ?? '—'}` : '') +
          (Number.isFinite(receipt.repair_adjusted_exam_score) ? ` · 含修复扣分 ${receipt.repair_adjusted_exam_score}` : '')
        : receipt.reason === 'FROZEN_MODEL_EXAM_SCORE_REUSED'
          ? '已复用成绩' : '—';
      showToast(receipt.assessment_status==='COHORT_PENDING' ? panelCopy('本组已完成：{score} 分，完整成绩待其余组完成') : receipt.status === 'NOT_RUN'
        ? `未取得有效评分：${receipt.reason || 'NOT_ASSESSED'}`
        : receipt.tier_result_is_provisional && Number.isInteger(receipt.score)
        ? `子集成绩：${receipt.score}`
        : receipt.status === 'PASS'
        ? `测试完成：${receipt.score}`
        : receipt.status === 'FAIL'
          ? `测试未通过：${receipt.score}`
          : `未取得有效评分：${receipt.reason || 'NOT_ASSESSED'}`);
      return receipt;
    } catch (error) {
      window.__Desktop_EXAM_PANEL__?.progress('ERROR');
      throw error;
    } finally {
      workflowExamInFlight = false;
      if (!examStarted) window.__Desktop_EXAM_PANEL__?.progress('IDLE');
      button.disabled = false;
      button.textContent = oldCopy;
      button.removeAttribute('aria-busy');
      if (q('#settings-exam-tier')) q('#settings-exam-tier').disabled = false;
    }
  };

  const addCliModel = (configId) => {
    const card = q(`[data-cli-config-id="${CSS.escape(configId)}"]`);
    const displayInput = card?.querySelector('[data-cli-add-display]');
    const modelInput = card?.querySelector('[data-cli-add-model]');
    const thinkingInput = card?.querySelector('[data-cli-add-thinking]');
    const used = new Set(qa('[data-cli-profile-ref]').map((row) => row.dataset.cliProfileRef));
    const service = state.settings.cli_services.find((row) => row.config_id === configId);
    let model;
    try { model = pendingCliModel(card, service, used); }
    catch (error) { showToast(error.message); return; }
    if (!model) { showToast('请填写模型名称和模型标识。'); return; }
    card.querySelector('[data-cli-empty]')?.remove();
    card.querySelector('[data-cli-models]').append(createCliModelRow(service, model));
    displayInput.value = '';
    modelInput.value = '';
    if (thinkingInput) thinkingInput.value = '';
    const addSummary = card.querySelector('.desktop-settings-cli-add-summary');
    if (addSummary) {
      addSummary.setAttribute('aria-expanded', 'false');
      addSummary.nextElementSibling?.setAttribute('aria-hidden', 'true');
    }
    setCliDraftDirty(true);
    showToast(`${model.display_name} 已加入草稿；保存后可用于流程映射。`);
  };

  const removeCliModel = (profileRef) => {
    const inUse = qa('button.desktop-settings-node[data-step]')
      .some((node) => node.dataset.profileRef === profileRef);
    if (inUse) {
      showToast('该模型正在被流程节点引用；请先调整流程映射。');
      return;
    }
    const row = qa('[data-cli-profile-ref]').find((candidate) => candidate.dataset.cliProfileRef === profileRef);
    if (!row) return;
    const models = row.closest('[data-cli-models]');
    row.remove();
    if (!models.querySelector('[data-cli-profile-ref]')) {
      const empty = create('div', 'desktop-settings-status-card neutral');
      empty.dataset.cliEmpty = '';
      empty.append(create('strong', '', '尚未添加模型'), create('span', '', '展开后可为此 CLI 添加多个模型。'));
      models.append(empty);
    }
    setCliDraftDirty(true);
  };

  const revert = async () => {
    cancelScopedReset();
    await bootstrap();
    const receipt = await call('settings.revert', {});
    state = await call('settings.get_state', {});
    applySettings(state);
    Object.keys(developerScopeIds).forEach(scope=>refreshDeveloperEditor(scope,state.settings,true));
    for(const part of [...saveParticipants.values()])if(part.isDirty())await part.discard();
    generalDraftDirty=false;cliDraftDirty=false;syncSaveState();
    showToast('已恢复已保存设置');
    return receipt;
  };

  const clearResetCountdownTimer = () => {
    if (resetCountdownTimer) window.clearInterval(resetCountdownTimer);
    resetCountdownTimer = 0;
  };

  const renderScopedResetIdle = () => {
    const button = q('#settings-reset');
    if (!button) return;
    button.textContent = '恢复当前项默认';
    button.removeAttribute('aria-pressed');
    button.removeAttribute('data-reset-armed');
    button.disabled = false;
  };

  const cancelScopedReset = ({ announce = false } = {}) => {
    const wasArmed = Boolean(resetArmedScope);
    clearResetCountdownTimer();
    resetArmedScope = null;
    resetArmedRevision = null;
    resetDeadline = 0;
    renderScopedResetIdle();
    if (announce && wasArmed) showToast('已取消恢复默认，当前设置未改变。');
    return wasArmed;
  };

  const updateScopedResetAvailability = (category) => {
    const next = String(category || 'none');
    if (next !== activeResetScope) cancelScopedReset();
    activeResetScope = next;
    const button = q('#settings-reset');
    if (!button) return;
    const isSensitive = SENSITIVE_RESET_SCOPES.has(next);
    button.hidden = isSensitive || !RESETTABLE_SCOPES.has(next);
    button.dataset.resetScope = button.hidden ? '' : next;
    if (!button.hidden && !resetArmedScope) renderScopedResetIdle();
  };

  const executeScopedReset = async (scope, expectedRevision) => {
    clearResetCountdownTimer();
    resetArmedScope = null;
    resetArmedRevision = null;
    resetDeadline = 0;
    if (scope !== activeResetScope || !RESETTABLE_SCOPES.has(scope)) {
      updateScopedResetAvailability(activeResetScope);
      return null;
    }
    const button = q('#settings-reset');
    if (button) {
      button.textContent = '正在恢复当前项…';
      button.disabled = true;
      button.removeAttribute('aria-pressed');
    }
    try {
      const receipt = await call('settings.reset_scope', {
        scope,
        expected_revision: expectedRevision
      });
      if(scope==='notice')await window.__Desktop_REPORT_UI__?.reset();
      await refresh();
      showToast('当前项已恢复默认');
      return receipt;
    } finally {
      updateScopedResetAvailability(activeResetScope);
    }
  };

  const renderScopedResetCountdown = () => {
    const button = q('#settings-reset');
    if (!button || !resetArmedScope) return;
    const remaining = Math.max(0, Math.ceil((resetDeadline - Date.now()) / 1000));
    if (remaining <= 0) {
      const scope = resetArmedScope;
      const expectedRevision = resetArmedRevision;
      void executeScopedReset(scope, expectedRevision)
        .catch((error) => {
          updateScopedResetAvailability(activeResetScope);
          showToast(`恢复默认失败：${error.message}`);
        });
      return;
    }
    button.textContent = `再次点击取消 · ${remaining}s`;
    button.setAttribute('aria-pressed', 'true');
    button.dataset.resetArmed = 'true';
  };

  const armScopedReset = async () => {
    await bootstrap();
    const scope = activeResetScope;
    if (!RESETTABLE_SCOPES.has(scope) || SENSITIVE_RESET_SCOPES.has(scope)) return null;
    if (resetArmedScope === scope) {
      cancelScopedReset({ announce: true });
      return null;
    }
    cancelScopedReset();
    resetArmedScope = scope;
    resetArmedRevision = state.revision;
    resetDeadline = Date.now() + RESET_COUNTDOWN_SECONDS * 1000;
    renderScopedResetCountdown();
    resetCountdownTimer = window.setInterval(renderScopedResetCountdown, 250);
    return {
      status: 'ARMED',
      scope,
      countdown_seconds: RESET_COUNTDOWN_SECONDS,
      persistent_mutation: false
    };
  };

  const importSettings = async () => {
    await bootstrap();
    const selected = await nativeAction('import_redacted_settings_file');
    if (selected.status !== 'SELECTED') return selected;
    const receipt = await call('settings.import_redacted', {
      payload: selected.payload,
      expected_revision: state.revision,
      confirmed_risk_ids: [...confirmedRiskIds].sort()
    });
    await refresh();
    showToast('配置已导入，凭据请在本机配置。');
    return receipt;
  };

  const stopOriginal = (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };

  document.addEventListener('click', (event) => {
    const target = event.target.closest('button');
    if (!target || !root.contains(target)) return;
    const fail = (prefix) => (error) => showToast(`${prefix}：${error.message}`);

    if (target.id === 'settings-save') {
      stopOriginal(event);
      void save().catch(fail('设置保存失败'));
    } else if (target.id === 'settings-revert') {
      stopOriginal(event);
      void revert().catch(fail('恢复失败'));
    } else if (target.id === 'settings-reset') {
      stopOriginal(event);
      void armScopedReset().catch(fail('恢复默认失败'));
    } else if (target.id === 'settings-validate-draft') {
      stopOriginal(event);
      const operationGeneration = ++credentialDraftGeneration;
      setCredentialDraftPending(true);
      void save()
        .then((result) => {
          if (operationGeneration === credentialDraftGeneration && !result) {
            settleCredentialDraftInput();
          }
        })
        .catch((error) => {
          if (operationGeneration === credentialDraftGeneration) showCredentialFailure(error);
        })
        .finally(() => {
          if (operationGeneration === credentialDraftGeneration && !q('#settings-api-draft')?.hidden) {
            setCredentialDraftPending(false);
          }
        });
    } else if (target.id === 'settings-add-api') {
      void prepareCredentialDraft().catch(fail('API 草稿打开失败'));
    } else if (target.id === 'settings-cancel-api') {
      stopOriginal(event);
      void cancelCredentialDraftOperation().catch(fail('API 草稿关闭失败'));
    } else if (target.id === 'settings-api-readiness-check') {
      stopOriginal(event);
      void checkApiCallReadiness().catch(fail('调用准备检查失败'));
    } else if (target.dataset.validateApi) {
      stopOriginal(event);
      void runApiValidation(target.dataset.validateApi, target).catch(fail('验证失败'));
    } else if (target.dataset.useCatalogModel) {
      stopOriginal(event);
      void applyCatalogModel(
        target.dataset.apiConfigId,
        target.dataset.useCatalogModel,
        target
      ).catch(fail('模型 ID 更新失败'));
    } else if (target.dataset.validateCli) {
      stopOriginal(event);
      void runCliValidation(target.dataset.cliConfigId, target.dataset.validateCli, target).catch(fail('验证失败'));
    } else if (target.dataset.cliEnabled) {
      stopOriginal(event);
      target.setAttribute('aria-checked', String(target.getAttribute('aria-checked') !== 'true'));
      setCliDraftDirty(true);
    } else if (target.dataset.addCliModel) {
      stopOriginal(event);
      addCliModel(target.dataset.addCliModel);
    } else if (target.dataset.removeCliModel) {
      stopOriginal(event);
      removeCliModel(target.dataset.removeCliModel);
    } else if (target.id === 'settings-node-test') {
      stopOriginal(event);
      void runWorkflowNodeTest(target.dataset.step, target).catch(fail('节点模型测试失败'));
    } else if (target.id === 'settings-local-model-refresh') {
      stopOriginal(event);
      void discoverLocalModels().catch(fail('本地模型检测失败'));
    } else if (target.id === 'settings-local-model-add') {
      stopOriginal(event);
      void saveLocalModelEndpoint().catch(fail('本地模型端点保存失败'));
    } else if (target.dataset.localModelVerify) {
      stopOriginal(event);
      void verifyLocalModel(target.dataset.localModelVerify).catch(fail('本地模型验证失败'));
    } else if (target.dataset.localModelRemove) {
      stopOriginal(event);
      void removeLocalModel(target.dataset.localModelRemove).catch(fail('本地模型端点移除失败'));
    } else if (target.id === 'settings-browser-companion-setup') {
      stopOriginal(event);
      void prepareBrowserCompanion().catch(fail('网页会话读取组件准备失败'));
    } else if (target.id === 'settings-browser-companion-reload') {
      stopOriginal(event);
      void reloadBrowserCompanion().catch(fail('只读桥接重新加载失败'));
    } else if (target.id === 'settings-browser-companion-test') {
      void syncBrowserCompanion('PREFLIGHT').catch(error => showToast(error.message));
    } else if (target.id === 'settings-browser-companion-sync') {
      stopOriginal(event);
      void syncBrowserCompanion(root.querySelector('.desktop-settings-shell')?.dataset.settingsMode==='normal'?'AUTO':'INCREMENTAL').catch(fail('网页会话同步失败'));
    } else if (target.id === 'settings-browser-companion-full-sync') {
      stopOriginal(event);
      void syncBrowserCompanionFull().catch(fail('网页会话完整同步失败'));
    } else if (target.id === 'settings-network-test') {
      stopOriginal(event);
      void networkTest().catch(fail('网络检查失败'));
    } else if (target.dataset.credentialStatus) {
      stopOriginal(event);
      void checkCredential(target.dataset.credentialStatus).catch(fail('凭据状态检查失败'));
    } else if (target.dataset.deleteModelService) {
      stopOriginal(event);
      void deleteModelService(target.dataset.deleteModelService).catch(fail('模型删除失败'));
    } else if (target.dataset.replaceCredential) {
      stopOriginal(event);
      replaceCredential(target.dataset.replaceCredential);
    } else if (target.id === 'settings-workspace-browse') {
      stopOriginal(event);
      void pickDirectory('workspace_root', 'settings-workspace-root').catch(fail('工作区选择失败'));
    } else if (target.id === 'settings-artifact-browse') {
      stopOriginal(event);
      void pickDirectory('artifact_root', 'settings-artifact-root').catch(fail('产物目录选择失败'));
    } else if (target.id === 'settings-library-browse') {
      stopOriginal(event);
      void pickDirectory('external_library', 'settings-library-root').catch(fail('资料库目录选择失败'));
    } else if (target.id === 'settings-library-test') {
      stopOriginal(event);
      void testLibrary().catch(fail('资料库检测失败'));
    } else if (target.id === 'settings-notification-test') {
      stopOriginal(event);
      void testWindowsNotification().catch(fail('Windows 通知测试失败'));
    } else if (target.id === 'settings-clear-confirm') {
      stopOriginal(event);
      q('#settings-cache-confirm').hidden = true;
      void nativeAction('clear_temporary_cache').then((receipt) => {
        showToast(`已清理 ${receipt.removed_stage_directories} 项临时缓存`);
      }).catch(fail('临时缓存清理失败'));
    } else if (target.id === 'settings-open-log') {
      stopOriginal(event);
      void nativeAction('open_settings_log_directory').catch(fail('日志目录打开失败'));
    } else if (target.id === 'settings-export-support') {
      stopOriginal(event);
      void nativeAction('export_support_bundle_file').then((receipt) => {
        if (receipt.status === 'EXPORTED') showToast('诊断文件已导出');
      }).catch(fail('诊断包导出失败'));
    } else if (target.id === 'settings-export-settings') {
      stopOriginal(event);
      void nativeAction('export_redacted_settings_file').then((receipt) => {
        if (receipt.status === 'EXPORTED') showToast('配置已导出');
      }).catch(fail('配置导出失败'));
    } else if (target.id === 'settings-import-settings') {
      stopOriginal(event);
      void importSettings().catch(fail('配置导入失败'));
    } else if (target.id === 'settings-reset-layout') {
      stopOriginal(event);
      void nativeAction('reset_panel_layout').then(() => {
        q('.desktop-settings-workarea')?.style.removeProperty('--desktop-settings-primary-size');
        q('.desktop-workspace')?.style.removeProperty('--desktop-right-size');
        q('.desktop-workspace')?.style.removeProperty('--desktop-bottom-size');
        window.__Desktop_SETTINGS_UI__?.resetPaneLayout();
        showToast('面板布局已恢复设计默认值。');
      }).catch(fail('布局恢复失败'));
    } else if (target.id === 'settings-assistant-show') {
      stopOriginal(event);
      void runAssistantAction('SHOW_DESKTOP_ASSISTANT', uiText('assistant.action.shown', '桌面助手已显示。')).catch(fail('桌面助手显示失败'));
    } else if (target.id === 'settings-assistant-hide') {
      stopOriginal(event);
      void runAssistantAction('HIDE_DESKTOP_ASSISTANT', uiText('assistant.action.hidden', '桌面助手已隐藏。')).catch(fail('桌面助手隐藏失败'));
    } else if (target.id === 'settings-project-page') {
      stopOriginal(event);
      void nativeAction('open_project_page').catch(fail('项目页打开失败'));
    } else if (target.dataset.localHelp) {
      stopOriginal(event);
      void nativeAction('open_local_help', target.dataset.localHelp, settingsLanguage()).catch(() => {
        showToast(window.__Desktop_INTEGRATED_APP__?.translate?.('文档打开失败，请检查默认打开程序') || '文档打开失败，请检查默认打开程序');
      });
    } else if (target.id === 'settings-confirm-retry' && !target.disabled) {
      const selected = q('button.desktop-settings-node[aria-current="true"]');
      const nodeId = selected ? nodeByStep.get(selected.dataset.step) : null;
      const retry = Number.parseInt(q('#settings-retries').value, 10);
      if (nodeId && Number.isInteger(retry)) {
        confirmedRiskIds.add(`${retry <= 1 ? 'RETRY_LOW' : 'RETRY_HIGH'}:${nodeId}:${retry}`);
      }
    }
  }, true);

  root.addEventListener('change', (event) => {
    markDirty();
    if (event.target.closest('[data-panel="cli"]')) setCliDraftDirty(true);
    if (event.target.matches('#settings-language')) previewAppearance();
    if (event.target.matches('#settings-api-protocol')) syncDraftApiConnectionControls();
    if (event.target.matches('#settings-model-name, #settings-provider, #settings-api-protocol, #settings-api-base-url')) syncDraftModelCapability();
  });
  root.addEventListener('input', (event) => {
    if (event.target.matches('#settings-provider')) clearProviderError();
    if (event.target.matches('#settings-model-name, #settings-provider, #settings-api-protocol, #settings-api-base-url')) syncDraftModelCapability();
    if (event.target.matches('input')) markDirty();
    if (event.target.closest('[data-panel="cli"]')) {
      setCliDraftDirty(true);
      const modelCard = event.target.closest('[data-cli-profile-ref]');
      if (modelCard) {
        const heading = modelCard.querySelector('.desktop-settings-cli-model-heading');
        const displayName = modelCard.querySelector('[data-cli-model-display]')?.value.trim();
        const modelName = modelCard.querySelector('[data-cli-model-name]')?.value.trim();
        if (heading?.querySelector('strong')) heading.querySelector('strong').textContent = displayName || modelName || '未命名模型';
        if (heading?.querySelector('small')) heading.querySelector('small').textContent = modelName || '尚未填写模型标识';
      }
    }
  });
  root.addEventListener('click', (event) => {
    if((event.target.closest('#settings-mode') && root.querySelector('.desktop-settings-shell')?.dataset.modeScope==='refinement') || event.target.closest('#memo-weights-page, #memo-settings-page') ||
      (event.target.closest('#settings-mode') && root.querySelector('.desktop-settings-shell')?.dataset.modeScope==='research-weights')){
      syncSaveState();return;
    }
    if (event.target.closest('[role="switch"], #settings-mode button, [data-font-step], #settings-confirm-retry, #settings-confirm-model')) {
      window.setTimeout(markDirty, 0);
      if (event.target.closest('#settings-full-tooltips, [data-font-step]')) {
        window.setTimeout(previewAppearance, 0);
      }
      const appearanceSwitch = event.target.closest('#settings-assistant-enabled-appearance');
      const behaviorSwitch = event.target.closest('#settings-assistant-enabled');
      if (appearanceSwitch || behaviorSwitch) {
        window.setTimeout(() => {
          const enabled = (appearanceSwitch || behaviorSwitch).getAttribute('aria-checked') === 'true';
          renderAssistantAppearance(enabled);
        }, 0);
      }
    }
  });

  const closeHelpCards = (except = null) => {
    for (const trigger of document.querySelectorAll('.desktop-settings-help-trigger')) {
      if (trigger === except) continue;
      trigger.dataset.open = 'false';
      trigger.setAttribute('aria-expanded', 'false');
    }
  };
  // Locale-dependent help is created after bootstrap. Delegation includes those
  // controls without rebinding handlers every time settings are refreshed.
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('.desktop-settings-help-trigger');
    if (trigger) {
      event.preventDefault();
      event.stopPropagation();
      const open = trigger.dataset.open !== 'true';
      closeHelpCards(trigger);
      trigger.dataset.open = String(open);
      trigger.setAttribute('aria-expanded', String(open));
      if (!open) trigger.blur();
      return;
    }
    if (!event.target.closest('.desktop-settings-help-wrap')) closeHelpCards();
  });
  document.addEventListener('keydown', (event) => {
    const trigger = event.target.closest('.desktop-settings-help-trigger');
    if (trigger && ['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      trigger.click();
      return;
    }
    if (event.key !== 'Escape') return;
    closeHelpCards();
    if (document.activeElement?.classList?.contains('desktop-settings-help-trigger')) {
      document.activeElement.blur();
    }
  });

  window.addEventListener('desktop:settings-mode-changed',()=>{if(browserCompanionState)renderBrowserCompanionStatus(browserCompanionState);});
  window.addEventListener('desktop:browser-companion-updated', (event) => {
    if (event.detail && typeof event.detail === 'object') {
      renderBrowserCompanionStatus(event.detail);
    }
  });

  window.addEventListener('desktop:settings-category-changed', (event) => {
    updateScopedResetAvailability(event.detail?.category || 'none');
  });

  window.__Desktop_SETTINGS_BRIDGE__ = Object.freeze({
    renderCodeEditor: (elements) => { renderDeveloperSyntax(null,elements); updateDeveloperLineNumbers(null,elements); },
    codeDiff: developerDiff,
    notify: showToast,
    methods: SETTINGS_METHODS,
    call,
    bootstrap,
    refresh,
    collectSettings,
    collectCliServices,
    save,
    registerSaveParticipant,
    syncSaveState,
    getEditState: () => ({
      ready: Boolean(state && contract && capabilityState && effects),
      dirty: Boolean(generalDraftDirty || cliDraftDirty || extraDirty() ||
        window.__Desktop_SETTINGS_UI__?.getEditState?.().dirty ||
        Object.values(developerEditorMetrics.dirty).some(Boolean) ||
        (q('#settings-api-draft') && !q('#settings-api-draft').hidden)),
      saving: Boolean(aggregateSaveInFlight || settingsSaveInFlight || cliSaveInFlight ||
        window.__Desktop_LOCAL_USER__?.getState?.().decoding)
    }),
    saveCliServices,
    renderCliValidationReceipt,
    bindCredential,
    diagnostics: networkTest,
    networkTest,
    renderNetworkLocation,
    developerDocument,
    validateDeveloperDocument,
    saveDeveloper,
    refreshDeveloperEditors,
    revert,
    armScopedReset,
    cancelScopedReset,
    inspect: () => ({
      method_count: SETTINGS_METHODS.length,
      native_transport: nativeReady(),
      bootstrapped: Boolean(state && contract && capabilityState && effects),
      revision: state?.revision ?? null,
      last_receipt: lastReceipt,
      last_mutation_receipt: lastMutationReceipt,
      external_network_calls: effects?.external_network_calls ?? 0,
      provider_calls: effects?.provider_calls ?? 0,
      credential_value_reads: effects?.credential_value_reads ?? 0,
      developer_editor: {
        ...developerEditorMetrics,
        dirty: { ...developerEditorMetrics.dirty },
        schemas: { ...developerSchemas }
      },
      sample_data_used_for_settings: false
    })
  });

  const resumeWorkflowExam = async () => {
    if (workflowExamInFlight) return;
    const job = await call('settings.workflow_exam_status', {});
    if (!job.job_id || examTerminal(job) || workflowExamInFlight) return;
    workflowExamInFlight = true;
    const step = [...nodeByStep.entries()].find(([,node]) => node === job.node_id)?.[0];
    try { await watchWorkflowExam(job,step); await refresh(); }
    finally { workflowExamInFlight = false; }
  };
  const startSettings = async () => {
    await bootstrap();
    void refreshConsoleConnection();
    void resumeWorkflowExam().catch(() => showToast('考试状态待查询 · 重新打开设置可恢复'));
  };
  const refreshConsoleConnection=async()=>{
    const status=q('#settings-console-status');if(!status)return;
    try{
      const value=await window.pywebview.api.get_console_connection();
      const connected=value.status==='CONNECTED';
      const lang=settingsLanguage();
      status.textContent=value.status==='PAUSED'?({'en-US':'Paused','ja-JP':'一時停止'}[lang]||'已暂停'):connected?({'en-US':'Connected','ja-JP':'接続済み'}[lang]||'已连接'):({'en-US':'Not connected','ja-JP':'未接続'}[lang]||'未连接');
      status.dataset.connectionStatus=value.status;
      const toggle=q('#settings-console-access');
      toggle.disabled=!value.can_toggle;toggle.setAttribute('aria-checked',String(value.access_enabled===true));
    }catch(_){status.textContent='连接状态暂不可用';status.dataset.connectionStatus='UNAVAILABLE';}
  };
  q('#settings-console-access')?.addEventListener('click',async event=>{
    event.preventDefault();event.stopPropagation();
    const button=event.currentTarget;button.disabled=true;
    try{await window.pywebview.api.set_console_connection({enabled:button.getAttribute('aria-checked')!=='true'});}
    catch(_){showToast(({'en-US':'Console connection could not be changed','ja-JP':'コンソール接続を変更できません'}[settingsLanguage()]||'无法更改控制台接入状态'));}
    finally{await refreshConsoleConnection();}
  });
  q('[data-category="privacy"]')?.addEventListener('click',refreshConsoleConnection);
  window.addEventListener('desktop:settings-locale-change',()=>void refreshConsoleConnection());
  window.addEventListener('pywebviewready', () => {
    void startSettings().catch((error) => showToast(`本地设置服务启动失败：${error.message}`));
  }, { once: true });
  if (nativeReady()) void startSettings().catch((error) => showToast(`本地设置服务启动失败：${error.message}`));
})();
