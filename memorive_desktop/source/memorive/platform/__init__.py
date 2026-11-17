"""Shared basics used by every area. Nothing here imports a business area.

  clock.py          the current UTC time as the desktop writes it into receipts
  naming.py         titles, paper IDs and the names of document folders and files
  json_files.py     canonical JSON, its SHA-256, and JSON files written whole (compact or indented)
  jcs.py            RFC 8785 (JCS) JSON and the payload hashes built on it
  hashed_payloads.py payloads that carry their own content hash, and the checks and errors they use
  sensitive.py      the check that keeps secrets out of what the app writes
  tls.py            the bundled public root certificates and the TLS context built from them
  public_http.py    public metadata over HTTPS: the response record, error codes, curl.exe and its proxy
  windows_paths.py  file paths past the Windows MAX_PATH limit
  data_folders.py   where data goes when nothing has bound the folders to a profile
  pickle_compat.py  reading job pickles of earlier versions after their classes moved
  projection_reads.py one read of a source per window query
  progress.py       progress events of long steps (document nodes, reviews, research jobs)
  errors.py         the application's error types
  error_envelope.py the payload that reports an application error
  credentials.py    secrets in Windows Credential Manager, by reference
  folders/          the folders of a user profile, their migration, and the roles of product folders
  logs/             the runtime log
  language/         which language new content is written in
  jobs/             job contracts, the job stores, state transitions, the checks before a job starts
  ledger/           run ledgers and the file locks that keep one writer per file
  decisions/        design decision records
"""
