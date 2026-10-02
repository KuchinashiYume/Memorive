# Memorive Test Console

[中文](README.md) · [日本語](README.ja.md) · [Memorive](../README.en.md)

Modify, extend and improve Memorive for your own research, then use this companion to reproduce issues, inspect behavior and check changes. Results apply to the selected version, configuration and scenarios. The console provides tests and diagnostics; editing and rebuilding the application requires your development tools.

## 1.03: language and review

Use the globe button at the top right for Simplified Chinese, English or Japanese, following the Memorive installer interaction. Switching preserves the page, edited forms and backend session, and remembers the selection. It never changes the application language or restores model permission after reload.

Review and results separates bridge evidence from nine groups of manual checks. Record observations only after connecting the application and enabling access. Export Markdown and JSON summaries with version, EXE hash, command states, observations and cleanup status. Bodies, settings, paths and tokens are excluded from this summary; raw diagnostics are separate. Reference results are labeled and never imply release acceptance.


## Get started

Download the portable `Memorive-Test-Console.exe` or its Windows x64 installer from this repository's Releases. Microsoft WebView2 Runtime is required. Packages are unsigned.

Start with the built-in protocol self-test. To test the application, drag its `Memorive.exe` into the connection page, retaining its complete application directory. In the temporary Memorive instance launched by the console, enable console access in privacy settings. Run a smoke test, inspect receipts, then select relevant scenarios. End and clean the session when finished.

The reference peer is a simulator. Current Memorive supports 15 declared operations; `research.qa.simulate` is unavailable in the application and remains reference-only. Simulation does not validate real papers, model answers or performance.

## Your own builds

Use the [application build instructions](../memorive_desktop/BUILDING.md). A regular `Memorive.exe` with a valid `DesktopReleaseIdentityBinding-v1` record and `Memorive-ConsoleBuildCapabilities-v1` capabilities can connect; there is no official EXE hash allowlist or signature requirement. Manifest validation is not proof of official origin.

Keep protocol 1.0, ENV_V1, EPHEMERAL_ONLY, the `/memorive/test-bridge/v1` routes, `MEMORIVE_TEST_CONSOLE_*` environment and `X-Memorive-Session` header. The handshake binds the actual EXE hash, session and temporary data directory. Missing operations must remain unavailable. See [protocol schema](source/bridge.schema.json).

## Data and permissions

Six allowlisted settings files may be copied into the temporary session; originals remain read-only. Personal libraries and conversations are not automatically imported. Safe suites and simulations do not call models. Model-backed single operations require explicit permission each time and may incur charges.

Normal session cleanup removes temporary business data and settings copies. Receipts remain for diagnosis; abnormal exits may require recovery on the next launch. Review paths, errors and screenshots before sharing. Never upload credentials or complete personal workspaces. Uninstall retains diagnostics.

Project code uses [AGPL-3.0-only](LICENSE) and was developed with **OpenAI Codex** and **Anthropic Claude Code**. Personal learning and noncommercial use are encouraged without restricting license rights. The icon derives from artwork labeled GRADIUS / @Riza2201 and has a separate [notice](NOTICE.txt); attribution does not establish redistribution permission. [Third-party notices](THIRD_PARTY_NOTICES.md).

Build on Windows x64 with Python 3.13.14 and `build_requirements.lock`; run PyInstaller with `console.spec`, copy its EXE to `output/`, then compile `installer.iss` with Inno Setup 6.7.3 under its own terms. Run pytest 8.3.5 in `source/` for source tests.
