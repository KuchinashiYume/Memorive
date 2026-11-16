"""Shared basics used by every area. Nothing here imports a business area.

  clock.py          the current UTC time as the desktop writes it into receipts
  naming.py         titles, paper IDs and the names of document folders and files
  json_files.py     canonical JSON, its SHA-256, and JSON files written whole (compact or indented)
  jcs.py            RFC 8785 (JCS) JSON and the payload hashes built on it
  sensitive.py      the check that keeps secrets out of what the app writes
  tls.py            the bundled public root certificates and the TLS context built from them
  windows_paths.py  file paths past the Windows MAX_PATH limit
  data_folders.py   where data goes when nothing has bound the folders to a profile
  folders/          the folders of a user profile, their migration, and the roles of product folders
  logs/             the runtime log
  language/         which language new content is written in
"""
