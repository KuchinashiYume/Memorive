# Memorive Test Console 1.03

This directory is the console companion source. Merge it as `memorive_console/` alongside the reviewed application source. It does not replace `memorive_desktop/` and does not grant publication approval for application or console binaries.

## Release assets

- `Memorive-Test-Console.exe`: self-contained Windows x64 console executable; WebView2 Runtime remains a prerequisite.
- `Memorive-Test-Console-Setup-1.03-x64.exe`: per-user Inno Setup installer, same AppId and installation directory as 1.01.
- `Memorive-Test-Console-Portable-1.03-x64.zip`: executable and license/user documentation.
- `Memorive-Test-Console-Source-1.03.zip`: corresponding source, assets, build configuration and licenses.
- `SHA256SUMS.txt`: hashes for the release assets.

Both executable and installer remain unsigned by Windows Authenticode. The application updater's production key is unrelated to console signing. Do not include private signing keys or local testing evidence in public assets.

## Build

Use Windows x64, Python 3.13.14, `build_requirements.lock`, and Inno Setup 6.7.3 under its own terms. Run:

```powershell
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build console.spec
New-Item -ItemType Directory -Path output -Force
Copy-Item -LiteralPath dist\Memorive-Test-Console.exe -Destination output\Memorive-Test-Console.exe
ISCC.exe installer.iss
```

`source/locales.json` is a runtime resource and must stay in the PyInstaller datas. Runtime languages are zh-CN, en-US and ja-JP; missing translations fall back to the source key. Initial language uses the OS locale and later selections are stored only in the console's own preferences file. The console installer currently retains the standard Inno wizard; the console's language control follows Memorive's globe-button interaction.

## Verification and boundaries

Run `python -m pytest source -q` (pytest 8.3.5) and `python verify_packaged_console.py --exe dist\Memorive-Test-Console.exe --runtime <new-short-test-directory> --output <evidence.json>`. Never use a live user profile as a test directory. See `VERIFICATION.md` for the actual candidate results, including inherited and untested boundaries.

Use only actual handshake operations. Existing safe suites exercise the bridge; they do not validate every application feature, real model quality, external providers, multilingual application UI, CLI execution, update signing, update installation or laptop behavior. `research.qa.simulate` remains optional. Manual observations never change `release_verdict: NOT_ASSESSED` automatically.

Before upload, incorporate the source manifest into the combined release's exact source inventory, refresh combined archive hashes and download links, and retain artwork/third-party notices. A language selector or interface receipt is not authorization to enable application access or model calls. Publishing remains a maintainer action.
