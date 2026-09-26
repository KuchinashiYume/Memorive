# Build Memorive from source

This repository contains the declared Windows application source, runtime contracts and build tools. Builds are unsigned and remain private review candidates until the release decision and checks are complete.

## Application

Use Windows x64 and CPython **3.13.14** in a separate virtual environment. Install the exact versions from `requirements-build-agpl-candidate.lock`; the build refuses unexpected dependency versions or extra packages other than pip.

Run from the repository root, selecting an output directory outside this checkout that does not already exist:

```powershell
py -3.13 -m venv G:\Memorive-build-env
G:\Memorive-build-env\Scripts\python.exe -m pip install -r .\memorive_desktop\requirements-build-agpl-candidate.lock
G:\Memorive-build-env\Scripts\python.exe -m pip check
G:\Memorive-build-env\Scripts\python.exe -X utf8 .\memorive_desktop\tools\build_memorive.py --output G:\Memorive-build-output
```

`py -3.13` must resolve to the required patch version. `SOURCE_MANIFEST.public-candidate.json` binds source and resource bytes; `BUILD_RESOURCES.public-candidate.json` maps repository-relative inputs into the package. Keep the `contracts/` directory. `--stage-only` validates and stages inputs without compiling an EXE.

The output is `dist/Memorive/Memorive.exe` together with its required `_internal/` directory. The receipt records input hashes, dependency versions and the EXE hash. The build reads no user profile and includes no private test capsule. It uses only the selected Python and Windows directories for native dependency lookup. Windows Visual C++ runtime libraries are prerequisites; unrelated DLLs from developer tools are not build inputs.

## Inventory and installer

Run `memorive_desktop/tools/collect_build_inventory.py --build <build directory> --wheelhouse <wheel directory> --output <new inventory directory>` with the same interpreter. It records dependency metadata, licenses, installed artifacts and hashes. Third-party source archives and license notices must accompany the selected distribution as required by their licenses.

The installer is built from `memorive_desktop/installer/public_candidate/` using the Windows .NET Framework compiler and WebView2 SDK **1.0.3856.49**. Toolchain member sources and hashes are in `release_materials/webview2-toolchain-source.json`. Supply the declared SDK DLLs, loader and `toolchain-source.json` explicitly:

```text
python memorive_desktop/installer/public_candidate/build_installer.py
  --app <build output>/dist/Memorive
  --artifact-manifest <inventory>/artifact-manifest.json
  --toolchain <verified SDK directory>
  --notices memorive_desktop/release_materials
  --output <new installer output directory>
```

The installer checks missing Microsoft prerequisites in System Check, including Visual C++ **x64**. It does not bundle a WebView2 Runtime installer. SDK and Runtime terms are documented separately. Installing the application and deleting user data are separate operations; uninstall retains data by default.

## Revision and verification

This naming revision retains **v1.01**. Changed imports, identifiers, resources and namespace bindings require a corresponding recompilation and installation check. Earlier binaries do not represent these source changes. Build receipts identify the exact bytes under review; they do not assert acceptance on every Windows machine or byte-for-byte reproducibility.

Public names use functional responsibilities, such as `document_processing`, `model_gateway`, `retrieval`, `evidence_review` and `memorive_research_workspace`. Original third-party implementation bytes and licenses are preserved. Configurations store opaque credential references; new bindings use the Memorive namespace.

`tools/import_local_settings.py --source-profile <existing profile> --new-profile <new empty profile>` copies only the documented settings files. It validates configuration checksums, refuses an existing destination, and does not read the credential vault or copy documents and conversations. Credential references remain usable only where the original local credential store is available. This is not an automatic migration of a research library.

## Review and export

`tools/export_source_candidate.py --output <new directory outside the repository>` produces a source review candidate from the declared inputs. Review the exact file set before upload. `SOURCE_REVIEW_MANIFEST.json` describes the current reviewed checkout; a Git commit and source archive identify a particular source version.

The repository and release remain private/draft during this revision. The installation review must complete at least five rounds. Rounds one through three are mandatory; from round four, two consecutive clean rounds are required. A missed product name or internal identifier restarts the clean-round count after repair. Test installations are removed after review; the user's existing portable installation and personal data are retained.
