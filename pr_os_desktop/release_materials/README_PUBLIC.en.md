# Memorive

[Project](https://github.com/KuchinashiYume/Memorive) · [Releases](https://github.com/KuchinashiYume/Memorive/releases) · [Issues](https://github.com/KuchinashiYume/Memorive/issues)

[中文](README_PUBLIC.md) · [日本語](README_PUBLIC.ja.md)

Memorive is a desktop literature workspace for personal learning and research. It brings document organization, research questions, and source checking into one application.

## Features

- Import literature and web content, follow processing progress, and organize a library.
- Ask questions about your materials and inspect citations and source locations.
- Organize conversations and retain research records for later work.
- Configure API services, supported CLI integrations, or local models using the manual.
- View model rankings, usage, and cost records. External rankings and prices can change.

Check model answers and citations yourself. Cost records depend on information returned by providers and execution channels and do not replace provider invoices.

## Getting started

Requirements: Windows 10 22H2 or later, or Windows 11, x64; .NET Framework 4.8, Microsoft WebView2 Runtime, and Visual C++ v14 x64 (14.51.36247 or later). Python is bundled with the application.

The installer checks these prerequisites and provides official Microsoft download links when a component is missing. Install the component, then return and check again. Preparing missing components may require internet access; application installation is offline once the prerequisites are present. See [third-party notices](THIRD_PARTY_NOTICES.md) for WebView2 updates, SmartScreen, and privacy information.

1. Once published, obtain the installer and SHA256 checksums from this repository's Releases page.
2. Choose separate application and data directories. The public build starts with blank settings and does not include the author's documents, conversations, or model credentials.
3. Open Settings and configure a model service. Supply your own cloud service access, CLI subscription, or local model; this software does not provide free API credits.
4. Import a document, ask a research question, and check its citations before expanding your library.

The application includes Chinese, English, and Japanese manuals, design documents, and local documentation pages. See [BUILDING.md](BUILDING.md) for source builds.

## Data and licensing

Memorive is an unofficial personal project and is not affiliated with Nexon, Nexon Games, or Yostar. Some character images are AI-assisted fan creations inspired by Blue Archive. Rights in the underlying characters, names, and marks belong to their respective holders.

Locally stored data does not mean every operation runs offline. Cloud models, web reading, and external retrieval require network requests. Choose which material to send according to your provider's terms. Do not post credentials, private documents, or conversations in issues or screenshots.

Original software code is licensed under [AGPL-3.0-only](LICENSE); see [LICENSING.md](LICENSING.md) for scope. Character artwork is separately covered by [ASSET_NOTICE.md](ASSET_NOTICE.md) and is excluded from the code license. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependencies.

The author encourages personal learning, research, and non-commercial use. This is a preference, not an additional restriction on a standard open-source license. The project is provided as is, without commercial support or service guarantees.

The source version is v1.01 (build139). See [INSTALL.md](INSTALL.md) for setup. Release availability is shown on the project Releases page. See [RELEASE_NOTES.md](RELEASE_NOTES.md) and [RELEASE_PREPARATION.md](RELEASE_PREPARATION.md) for current limitations and preparation status.
