"""Shared basics used by every area. Nothing here imports a business area.

  naming.py       titles, paper IDs and the names of document folders and files
  json_files.py   canonical JSON, its SHA-256, and JSON files written whole
  sensitive.py    the check that keeps secrets out of what the app writes
  tls.py          the bundled public root certificates and the TLS context built from them

Folders, logs, language and file helpers move here during v2.00.
"""
