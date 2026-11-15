# v1.03 validation scope

Inherited source integration: 12 checks, 3 targeted successor checks, two real agent returns. Console baseline has 30 packaged checks and 29 reference protocol checks. These are historical source-specific evidence, not new binary acceptance.

New package checks are recorded in the release candidate evidence and delivery report. Full business regression, scientific acceptance, physical clean-machine tests and online upgrade acceptance remain unassessed.

## Checks to run before a release

Run these from the repository root. Each one prints what is wrong and how to fix it.

```powershell
python .\memorive_desktop\tools\build_pages.py --check
python .\memorive_desktop\tools\sync_manifests.py --check
python .\memorive_desktop\tools\check_internal_codes.py
```

The first two confirm that the UI pages are what their sources under `ui/` generate, script hashes included, and that the file hashes in the manifests match the files. The build stops when they do not.

The third enforces a rule: a release carries no internal development codes. Phase, task and module numbers, build, candidate and run ids, feature codenames and the working name of the project mean nothing to a reader and get in the way of anyone who continues the work. The check reads the name and the text of every published file.

Some names of this kind are saved in users' data or checked by copies that are already installed. They cannot change without a data upgrade, so they are listed in `memorive_desktop/tools/historical_names.json`, each with the reason it stays. The check accepts only what that list covers. A new hit is either renamed or added to the list with its reason; the list should get shorter with each data upgrade.

Codenames that no general pattern can recognise go in a word list of your own, passed with `--words`. Keep that list outside the repository.
