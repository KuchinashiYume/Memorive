"""Importing and processing papers.

  inbox/          files the user adds: path checks, preflight, the import queue
  processing/     the document pipeline (RawMD, CleanMD, chunks, vectors) and the paper id registry
  library/        the library page: processed papers and the files each one has
  registry/       the artifact registry: records of every product file and how they relate
  front_matter.py the "---" header of RawMD, CleanMD and card files

The workflow and the card packages are still top-level packages; DEVELOPING.md lists where each
package belongs.
"""
