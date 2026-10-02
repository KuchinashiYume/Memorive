# Memorive

From reading materials to research records you can verify and reuse.

Memorive, or memo, is a Windows desktop workspace for personal learning and research. It connects papers, web pages, and research conversations in one workflow: retain the originals, organize information with its sources, ask questions and compare evidence, then keep the conclusions you have reviewed for future work.

[English](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md)

### [⬇ Download Memorive for Windows](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Setup-1.03-x64.exe)

**v1.03 · Windows 10 22H2 / Windows 11 · x64 · about 205 MB**

**First time here? Choose the main app installer above.** It installs Memorive; plugins and the Test Console are optional extras.

[Which file should I download?](#download-guide) · [Downloads](https://github.com/KuchinashiYume/Memorive/releases) · [User guide](memorive_desktop/product/desktop/help/en-US/manual-text.md) · [Design](memorive_desktop/product/desktop/help/en-US/design.md) · [Issues](https://github.com/KuchinashiYume/Memorive/issues)

## What changes in v1.03

### Feature update: v1.02.01 → v1.03

Research Q&A can reuse originals and converted text prepared by the memo-research Skill, read and compare sources for the current question, and return source-checked answers to the same conversation. The Skill also works independently for one paper or agent-led comparisons. The Test Console adds three languages, a review-and-results view, and shareable summaries.

This version retains the settings-save and three-part version fixes from 1.02.01. The research Skill complements question-led reading and source reuse; no low-latency or full-workflow replacement claim is made. Source integration checks and two real agent returns are recorded separately from package checks; they do not establish scientific quality, full business regression, clean-machine or online-upgrade acceptance.

Back up data and settings before upgrading with the installer or complete portable package; never manually overlay a delta ZIP. Install or fully extract the console separately. First access still requires permission in Memo; changing the console language neither changes Memo language nor restores model-call permission.

## Home at a glance

![Memorive v1.02 Home — English interface](docs/images/memorive-home-v1.02-en.png)

Memorive v1.02, shown in English with default settings.

## What can you do?

### Read: bring documents, pages, and conversations into your research

Import materials through the Inbox, check their previews, and choose when to process them. Research Q&A also accepts PDF, images, Word, Markdown, and TXT directly; a document does not need a generated card before you can discuss it. Session Management brings together local Codex and Claude Code records, browser captures, and manual imports. Refinement turns a conversation into reviewable notes about ideas, decisions, assumptions, and next steps.

### Organize: turn materials into evidence you can revisit

Document processing preserves the original and produces a literature card (Card) and an analysis report (Analysis). Cards organize the research subject, methods, important numbers, findings, and limitations; analysis reports propose interpretations based on those materials. Read them beside the original in the Library. Current Tasks shows the model, inputs, outputs, and errors for each step, so you can retry from the relevant point.

### Verify: ask questions and check each citation

Select several papers in one conversation to compare methods, sample conditions, results, and limitations. Retrieval starts with the conversation's attachments; you can extend it to project materials and adjust retrieval weights for your purpose. Citations point back to sources and their saved versions. Open the passage, page, or line and check whether it supports a number, comparison, or causal claim. A fluent answer or a long reference list is not a substitute for this check.

### Retain: reuse knowledge you have reviewed

Before adding a suggested conclusion to your knowledge, review its claim, scope, limitations, and citations. Reviewing a document's outputs and confirming a derived conclusion are separate actions. Research records, previous versions, refined conversations, and daily, weekly, or monthly reports help you resume work. You choose whether confirmed research context is reused in later questions.

### Choose your models and working style

| Capability | How it helps |
| --- | --- |
| APIs, CLIs, and local models | Connect official APIs, compatible endpoints, or relay services; use a locally authenticated CLI or a local service such as Ollama. |
| Workflow model mapping | Assign models separately to embeddings, card generation, analysis, review, and periodic reports. New tasks record the configuration they use. |
| Web reading and browser companion | Save the current page or capture conversations within a source's supported scope; inspect completeness before refinement. |
| Model rankings | Compare public capability indicators, prices, sources, and update dates, then test candidates on your own materials. |
| Billing and usage | Filter requests, input/output tokens, and actual or estimated costs by time and model. Missing cost information remains unknown. |

## Install and get started

[Complete portable application](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Portable-1.03-x64.zip) · [Three-language PDF, Markdown and local-page documentation](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-1.03-Documentation.zip) · [Corresponding v1.03 desktop source](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-1.03-desktop-source.zip). Extract the entire portable directory. The current source includes Test Console 1.03 and the research Skill; v1.01 remains available for historical reference.

### Download guide

| What you need | Download | When to choose it |
| --- | --- | --- |
| **Memorive — recommended** | [Memorive-Setup-1.03-x64.exe](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Setup-1.03-x64.exe) | The main desktop app. Start here for reading papers, research Q&A, and conversation management. |
| Verify your download | [SHA256SUMS.txt](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/SHA256SUMS.txt) | A small text file of checksums; use it to check that your downloaded file is intact. |
| Obsidian companion — optional | [Memorive-Obsidian-Companion.zip](https://github.com/KuchinashiYume/Memorive/releases/download/v1.01/Memorive-Obsidian-Companion.zip) | Connect notes from your Obsidian vault. Requires Obsidian and Memorive. |
| Zotero companion — optional | [Memorive-Zotero-Companion.xpi](https://github.com/KuchinashiYume/Memorive/releases/download/v1.01/Memorive-Zotero-Companion.xpi) | Connect Zotero items and annotations. Requires Zotero and Memorive. |
| Test Console — optional installer | [Memorive-Test-Console-Setup-1.03-x64.exe](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Test-Console-Setup-1.03-x64.exe) | For users who want to modify, test, or diagnose Memorive. Choose this for an installed console. |
| Test Console — portable alternative | [Memorive-Test-Console-Portable-1.03-x64.zip](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Test-Console-Portable-1.03-x64.zip) | For the same testing tool without installation. Extract the whole ZIP; guides and notices are included. Choose this OR the console installer. |
| Test Console — executable only | [Memorive-Test-Console.exe](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Test-Console.exe) | For advanced use when you already have the guide and notices. The installer or portable ZIP is easier to start with. |
| Current desktop source — v1.03 | [Memorive-1.03-desktop-source.zip](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-1.03-desktop-source.zip) | Corresponding source for the current desktop application; for reading or building, not installation. |
| Historical project source — v1.01 | [Memorive-1.01-source.zip](https://github.com/KuchinashiYume/Memorive/releases/download/v1.01/Memorive-1.01-source.zip) | Historical application, companion and Test Console sources. For the current desktop source, choose v1.03 above. |
| Third-party source — developers / redistribution | [Memorive-1.02-third-party-sources.zip](https://github.com/KuchinashiYume/Memorive/releases/download/v1.02/Memorive-1.02-third-party-sources.zip) | Corresponding source and index for bundled dependencies (about 592 MB). Not needed to install or use Memorive. |

GitHub also adds **Source code (zip)** and **Source code (tar.gz)** automatically. These are source snapshots, not Windows installers. For normal use, choose **Memorive-Setup-1.03-x64.exe**.

### Installation

1. Use **Windows 10 22H2 or later, or Windows 11, x64**. Obtain the installer and its matching `SHA256SUMS.txt` from this project's Releases.
2. To check the download, run `Get-FileHash -Algorithm SHA256 .\Memorive-Setup-1.03-x64.exe` in PowerShell and compare the result with the checksum file.
3. In the installer's System Check, prepare any missing components: .NET Framework 4.8, WebView2 Runtime, and Visual C++ v14 **x64** (14.51.36247 or later). Install only the components requested, then check again. Python is bundled.
4. Choose application and data directories and finish installation. The first launch has blank settings. In Settings → Documents and external viewing, check your workspace and generated-output locations.

### Configure one model connection

- **API:** in Settings → Model services and API, enter the provider's protocol, endpoint, and model ID, and save the key through the credential field. Verify the connection before assigning work.
- **CLI:** install and sign in through the tool's own terminal, confirm the model works, then select and verify the tool in Settings → CLI integration.
- **Local model:** start the local server and download the model first. In Settings → Local models, use detection or enter the local address, select the matching protocol, and load the model list.

Start with the connection you need. Chat, image reading, embeddings, and review have different requirements; appearing in a list does not establish every capability. Supply your own cloud access or CLI subscription. Memo does not include API credits.

### Complete your first research task

1. **Map the workflow:** choose models for the required steps in Workflow model mapping. Configure embeddings, generation, and review according to their roles.
2. **Try one familiar document:** add it to the Inbox, check its preview and automatic-execution setting, then follow it in Current Tasks.
3. **Review the outputs:** open the original, Card, and Analysis in the Library. Check methods, key numbers, and conclusions against the same source and version.
4. **Start a research conversation:** select a project, create a conversation, attach files or choose project materials, and check the model and retrieval scope.
5. **Ask a focused question:** for example, “Compare the methods, sample conditions, and limitations in these two papers. Cite each source and explain where direct comparison is inappropriate.”
6. **Check citations before retaining a conclusion:** open the references and surrounding text, then review the scope and limitations before confirming an addition to knowledge.

For ongoing work, sync new conversation records, refine notes, adjust project retrieval presets, and review reports and usage. Inspect the failed step before retrying a task. Changing embedding models may require rebuilding indexes; back up files before changing storage locations.

Illustrated guides in Chinese, English, and Japanese are available in Settings → About and version → User documentation. See also [installation and first setup](memorive_desktop/INSTALL.md) and the [text guide](memorive_desktop/product/desktop/help/en-US/manual-text.md).

## How it fits together

```mermaid
flowchart LR
  sources[Documents, pages, conversations] --> processing[Process and organize]
  processing --> library[Originals and literature cards]
  library --> retrieval[Retrieve for the project and question]
  retrieval --> answer[Research Q&A and comparison]
  answer --> check[Check citations and make a judgment]
  check --> knowledge[Confirmed research knowledge]
  knowledge --> retrieval
```

| Part | Responsibility |
| --- | --- |
| Originals and versions | Preserve files, conversation snapshots, and source locations so you can revisit the material used. |
| Processing and library | Convert and organize documents, extract information, and connect originals to cards and analyses. |
| Retrieval and research | Assemble evidence within the selected project scope for questions, comparisons, and citation checks. |
| Knowledge and records | Retain confirmed conclusions, refined conversations, and periodic reports for future research. |
| Shared services | Provide model connections, task state, usage records, and local storage. |

The desktop interface calls a Python application layer through an application service. Model connections are managed centrally, while originals and derived results remain separate. Search indexes help locate content; the original and its version remain the basis for verification. See the [design document](memorive_desktop/product/desktop/help/en-US/design.md) for details and [build instructions](memorive_desktop/BUILDING.md) for source builds.

## Data, costs, and current status

Materials and settings are stored locally. Cloud models, web reading, and external retrieval send relevant requests to the selected services; choose connections that fit your materials. Store keys through the credential interface and keep them out of issues, screenshots, and research exports.

Cost records distinguish actual, estimated, and unknown amounts. Public list prices and subscriptions are not the same as the amount charged for one task. Check model answers and citations yourself.

The current application version is v1.03. The update index is signed with the production RSA key; the application and installer do not have a Windows Authenticode certificate. Verify downloads against the release checksums. Final release preparation included source, package-identity and signature checks, not a new full regression round. OCR, model answers and citation support still require human review.

## Improve Memorive and verify your changes

You can modify and extend Memorive for your own learning and research. The repository includes **Memorive Test Console**, an optional tool that starts a selected Memorive executable in a temporary data space, helps reproduce issues and check behavior, and records events, results, and cleanup receipts.

1. Edit and build the application using your preferred development tools.
2. Run the console's reference self-check, then connect your own executable and grant console access inside that temporary application instance. Real model calls require separate permission and may incur charges.
3. Review results and diagnostics, then end the session to clear temporary business data. Some local settings can be copied into the session; the original data directory is not overwritten.

Results apply only to the selected executable, configuration, and scenarios. Reference self-checks do not establish that the actual application works, and a passing test is not complete quality or security certification.

[Console source and guide](memorive_console/README.en.md) · [中文](memorive_console/README.md) · [日本語](memorive_console/README.ja.md) · [Installer and portable package](https://github.com/KuchinashiYume/Memorive/releases)

The console is optional for everyday use. Its code uses AGPL-3.0-only; its illustrated icon has separate artist attribution and rights limitations in the [console notice](memorive_console/NOTICE.txt).

## How this project is developed

Memorive is an AI-developed project. Its project-specific code was generated, modified, and iterated by **OpenAI Codex** and **Anthropic Claude Code**. The project initiator defined requirements and product design and carried out testing and acceptance; they did not write the code themselves. Third-party components retain their authors' attribution and licenses.

## Design reference and acknowledgements

The document-structure work draws on [Marker](https://github.com/datalab-to/marker) by Datalab, particularly document blocks and preservation of table and layout structure. Thank you to its authors and contributors. Memorive also offers optional local Marker parsing; its runtime and model weights must be configured separately, under their own licenses.

## License and character artwork

Project-specific code uses [AGPL-3.0-only](LICENSE); see the [licensing scope](memorive_desktop/LICENSING.md). The maintainer encourages personal learning, research, and non-commercial use. This preference adds no restrictions to the code license.

Some character images are AI-assisted fan creations inspired by Blue Archive. Memorive is an unofficial personal project, unaffiliated with Nexon, Nexon Games, or Yostar. Underlying characters, names, and marks belong to their respective rights holders. Desktop-pet images, expressions, and character icons are covered separately by the [asset notice](memorive_desktop/ASSET_NOTICE.md). Dependencies are listed in the [third-party notices](memorive_desktop/THIRD_PARTY_NOTICES.md).

## Feedback and contact

For usage questions, bugs and feature ideas, please use [GitHub Issues](https://github.com/KuchinashiYume/Memorive/issues). For private feedback, security concerns or artwork-rights questions, contact the project maintainer at [kuchinashiyume01@Gmail.com](mailto:kuchinashiyume01@Gmail.com).

Include your Memorive version and steps to reproduce when reporting a problem. Please remove API keys and private research content from screenshots and logs.

## Companion plugins

[memo-research Skill v0.2.0](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Research-Skill-0.2.0.zip) · [Test Console 1.03 source](https://github.com/KuchinashiYume/Memorive/releases/download/v1.03/Memorive-Test-Console-Source-1.03.zip)

Connect Obsidian notes and Zotero items to Memorive for previewed imports, evidence search, questions and managed note writeback. [Installation / 安装 / インストール](memorive_desktop/integrations/README.en.md).

## Version history

**[v1.02.01](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.02.01)** — Settings-save and version-parsing fixes.

**[v1.02](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.02)** — Added literature discovery, AI Updates, custom CLI connections, multilingual output, conversation archiving, and signed update support.

**[v1.01](https://github.com/KuchinashiYume/Memorive/releases/tag/v1.01)** — v1.01 established the Windows research workspace: document processing, research Q&A, source-linked records, model connections and optional companions and Test Console. The original release remains available for historical reference. Choose v1.03 above for the current application.
