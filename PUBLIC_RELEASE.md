# Public release status

Memorive v1.02.01 is the first maintenance release of v1.02, authorized for synchronization by the maintainer. The three-part version policy is major.feature.fix; Windows resources use 1.2.1.0. The v1.01 and v1.02 tags and downloads remain immutable.

This release fixes stale directory configuration blocking unrelated model saves, independent settings being skipped after a save error, and shortcut-name validation. It improves the explicit settings-import utility and adds three-part version parsing to the application, native installer and updater. New shortcut names are limited to 20 Unicode characters; unchanged legacy longer names are preserved.

For the first upgrade from v1.01 or v1.02, back up your data and use this installer or the complete portable package. The old updater cannot parse this release's three-part version. Signed delta assets are prepared against the exact v1.02 inventory; their existence is not a claim that old clients can apply them. Never manually overlay update archives. Subsequent updates can use the new client's three-part parser.

The named desktop source archive and source manifest bind the shipped program; SOURCE_ARCHIVE_v1.02.01_hotfix001 is an archive locator, not a Git commit. The same production update public key is retained. Private signing material, user settings and research data are not distributed. Windows Authenticode remains unsigned.

17 targeted settings/import checks from the byte-identical repair were inherited. Version-parser and packaging checks are recorded separately. No new full regression, model experiment, native settings-save UI acceptance, clean-machine installation or end-to-end online update is claimed. Existing OCR and research quality limitations remain.

The independently versioned Test Console and companions remain unchanged. Original code is AGPL-3.0-only. Character artwork, third-party and privacy notices are unchanged. See LICENSING.md and memorive_desktop/ASSET_NOTICE.md; no affiliation or additional artwork redistribution rights are claimed.
