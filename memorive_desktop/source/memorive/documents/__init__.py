"""Importing and processing papers.

  inbox/          files the user adds: path checks, preflight, the import queue
  processing/     the document pipeline (RawMD, CleanMD, chunks, vectors) and the paper id registry
  library/        the library page: processed papers and the files each one has
  registry/       the artifact registry: records of every product file and how they relate
  workflow/       one paper's run through the pipeline: worker, executor, the model bridge
  card_distillation/  a paper's cards drawn from its chunks by a model
  card_review/    the review of each card against its sources, repairs, judgments
  card_states/    the review state of each card (pending, active, quarantined) and its log
  data_review/    the data review of a paper and the chosen cloud logic review
  front_matter.py the "---" header of RawMD, CleanMD and card files
"""
