"""The workflow model exams, one module per exam family.

  common.py          plan, authorization and result schemas, the workload profile of a primary run,
                     and the file and JSON helpers every family uses
  card_distiller.py  card distiller exams
  card_reviewer.py   card reviewer exams: successor samples, scoring and repair prompts, executors
  analysis.py        the analysis exam; its executor subclasses the card reviewer successor executor
  embedding.py       embedding exams

Each executor's EXECUTOR_REF is the name saved scores are matched by; keep it unchanged.
"""
