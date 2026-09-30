# Public release status

The maintainer has authorized Memorive v1.02 for public release. This source update includes the reviewed desktop implementation, three-language documentation and production update public key. The independently versioned Test Console remains v1.01; its existing source, notices and downloads are preserved.

The GitHub Release and SHA256SUMS.txt identify the distribution files. The named desktop-source ZIP contains the exact desktop build source and its source manifest; the tagged repository additionally retains the unchanged console and legacy companion sources. The package locator SOURCE_ARCHIVE_v1.02_release001 is an archive identity, not a Git commit hash.

The normal update helper embeds the production RSA public key and verifies signed update metadata and artifact hashes. Private signing material is not distributed. Windows Authenticode signing is separate: the application and installer still have no Authenticode certificate. For the first upgrade from v1.01, use the v1.02 installer and back up existing data.

Some embedded build manifests retain candidate/unsigned status as packaging-time provenance. The maintainer's later authorization is recorded here and in the Release; it does not turn historical tests into fresh results or expand third-party rights. Final release preparation included code, exact source/package identity and signature review. No new full application regression, model experiment, VM acceptance or end-to-end production online update application is claimed.

Original code is AGPL-3.0-only. Character artwork and third-party components retain their separate notices. See [licensing scope](LICENSING.md), [application artwork](memorive_desktop/ASSET_NOTICE.md), [console artwork and privacy](memorive_console/NOTICE.txt), and component notices. No official affiliation or independent artwork redistribution authorization is claimed. OCR, model answers and citation support require human review.
