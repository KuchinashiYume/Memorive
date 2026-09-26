# Third-party components — Memorive Test Console

This inventory describes the console's pinned packaging environment. The desktop application's dependency inventory is separate. Original component licenses are preserved under `licenses/`; these files are not relicensed under AGPL. CPython and its bundled notices are included as `licenses/CPython-LICENSE.txt`.

| Component | Version | License / notice |
| --- | --- | --- |
| altgraph | 0.17.5 | [MIT](licenses/altgraph/altgraph-0.17.5.dist-info/LICENSE) |
| bottle | 0.13.4 | [MIT](licenses/bottle/bottle-0.13.4.dist-info/licenses/LICENSE) |
| cffi | 2.1.1 | [MIT-0](licenses/cffi/cffi-2.1.1.dist-info/licenses/LICENSE) |
| clr_loader | 0.2.10 | [MIT](licenses/clr_loader/000-LICENSE) |
| packaging | 26.3 | [Apache-2.0 OR BSD-2-Clause](licenses/packaging/packaging-26.3.dist-info/licenses/LICENSE) |
| pefile | 2023.2.7 | [MIT](licenses/pefile/pefile-2023.2.7.dist-info/LICENSE) |
| pillow | 11.3.0 | [MIT-CMU](licenses/pillow/pillow-11.3.0.dist-info/licenses/LICENSE) |
| proxy_tools | 0.1.0 | [MIT](licenses/proxy_tools/LICENSE.txt) |
| pycparser | 3.0 | [BSD-3-Clause](licenses/pycparser/pycparser-3.0.dist-info/licenses/LICENSE) |
| pyinstaller | 6.16.0 | [See included license (including exceptions)](licenses/pyinstaller/pyinstaller-6.16.0.dist-info/licenses/COPYING.txt) |
| pyinstaller-hooks-contrib | 2026.7 | [See included license](licenses/pyinstaller-hooks-contrib/pyinstaller_hooks_contrib-2026.7.dist-info/licenses/LICENSE) |
| pythonnet | 3.0.5 | [MIT](licenses/pythonnet/000-LICENSE) |
| pywebview | 6.2.1 | [See included license (including exceptions)](licenses/pywebview/pywebview-6.2.1.dist-info/licenses/LICENSE) |
| pywin32-ctypes | 0.2.3 | [BSD-3-Clause](licenses/pywin32-ctypes/pywin32_ctypes-0.2.3.dist-info/LICENSE.txt) |
| setuptools | 84.0.0 | [MIT](licenses/setuptools/setuptools-84.0.0.dist-info/licenses/LICENSE) |
| typing_extensions | 4.16.0 | [PSF-2.0](licenses/typing_extensions/typing_extensions-4.16.0.dist-info/licenses/LICENSE) |

PyInstaller's included license contains its bootloader distribution exception. The inventory includes build tools; it does not imply their complete Python packages are shipped at runtime. The frozen executable also carries Python standard-library extension modules and the native libraries collected by these packages.

The Windows interface uses Microsoft .NET Framework and WebView2. WebView2 Runtime is installed separately and follows Microsoft's terms, privacy statement, and update policy. The packaged WebView2 SDK loader and managed assemblies follow the Microsoft notice in `licenses/WebView2-LICENSE.txt`. Inno Setup is a separate build tool; its license is included as `licenses/Inno-Setup-LICENSE.txt`.

The character icon is separate artwork, not a software dependency. Its attribution and rights limitations are in [NOTICE.txt](NOTICE.txt).
