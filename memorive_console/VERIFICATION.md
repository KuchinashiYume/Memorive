# Verification — Console 1.02, 2026-09-30

Current console delivery, not a complete Memorive product acceptance.

- Final packaged EXE: **30/30 checks PASS**, including real WebView window load, native drag handler attachment, three language resources/preferences, reference-only evidence labels, report export, normal window-close cleanup and UI-profile removal.
- Reference safe suite: **29/29 PASS**, zero model calls. This is the built-in protocol reference peer, not Memorive business/UI acceptance.
- Source suite: 118 passed, 2 failed, 1 platform skip at the multilingual integration checkpoint. Both failures were extracted JavaScript test fixtures missing the newly introduced translation helper; assertions were preserved and both tests passed with the real helper (12 pass, 1 skip in the affected modules). A subsequent cleanup-failure report test and the other five review tests passed. Current unique coverage: **121 pass, 1 skip**, aggregated from the recorded run and targeted successors; not a single fresh all-green full run.
- Earlier inherited long-path fixture failure was reproduced on unchanged 1.01 source: the fixture sized the temporary name to 225 characters but asserted the final name was at least 225, although the final name was 5 shorter. The successor sizes the final name, preserving all MAX_PATH assertions; 5/5 import-staging tests passed. Runtime import logic was unchanged.
- Browser UI checks: globe menu, Chinese/English/Japanese switching, preservation of an edited title and current event page, unchanged session ID, English review export, reference-mode manual-check prohibition and no captured JavaScript errors.
- Current local Memorive v1.02 EXE: handshake recognized **15 operations**, reached WAITING_FOR_ACCESS, retained disabled native permission and automatic execution, then cleaned successfully. Transport and permission boundaries only; no product business commands or model calls requested.

Not run: new clean-machine installer/upgrade acceptance, laptop/DPI acceptance, actual model-backed research/CLI/discovery, application updater signing/install/recovery and full product regression. Installer compiled successfully; a new installation was not performed over the author's installed console. The standard Inno installer wizard is separate from the three-language console UI.

Local evidence is retained outside public distributions. The handoff binds exact EXE, installer and source archive hashes. No publishing action performed. Existing artwork rights and third-party notices remain separate from code licensing and test results.
