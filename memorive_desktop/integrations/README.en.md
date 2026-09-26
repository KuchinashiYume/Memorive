# Memorive companion plugins

[中文](README.md) · [日本語](README.ja.md)

Connect selected Obsidian notes or Zotero items and annotations to Memorive. Preview before import, search cited evidence, submit questions, and write research results back to managed notes. Windows desktop only; plugins call your local Memorive.exe and do not store model API keys. Questions use the desktop model configuration and may incur costs.

1. Open Memorive and create the appropriate connection in Connections and Sync. Copy the executable path, initialized research workspace path, project ID and connection ID from the desktop connection configuration.
2. Obsidian: extract `memorive-companion` into the vault's `.obsidian/plugins/` directory, reload the plugin list and enable it. Declared minimum: 1.5.0; historical host verification used 1.13.7, not every supported version.
3. Zotero: install `zotero/memorive-companion.xpi` from the plugin manager. The declared compatibility is Zotero 10.0.x, personal libraries only. Download PDF attachments before importing them.
4. Enter the copied paths and IDs in Memorive Companion settings. The research workspace is neither the app installation directory nor the Obsidian vault. Validate the connection; in Zotero, fetch the available connections and save your selection.
5. Select notes or items, preview changes and confirm import. Search the research sidebar for evidence and its page or line references. Questions and Agent requests wait in the desktop queue; keep Memorive open.
6. Enable writeback in the desktop connection before saving results. Managed note sections or dedicated Zotero child notes preserve personal content; conflicting edits require saving separately. Try an empty library first.

Plugin version 1.0.0 targets Memorive 1.01. Install updates manually; no automatic update service is promised. Private repository downloads require access. Host settings, private libraries and test data are excluded from distribution. Model data handling follows the desktop configuration and provider terms. The plugins do not embed WebView2.

See [license and AI development notice](NOTICE.md). Developer SDKs and Agent skills are included. Package audited sources with `python memorive_desktop/tools/package_companions.py --output <new-directory>`. Current host compatibility still requires verification in the host you use.
