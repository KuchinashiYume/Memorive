"""Characterization tests written for the code cleanup (not ported from the dev repo).

They pin what the code returns and writes, with the in-process hash envelope
(content_hash and values equal to it) left out, so removing envelopes does not
fail them but any other change does. Golden files live next to each test;
regenerate with MEMORIVE_TESTS_WRITE_GOLDEN=1 only on purpose.
"""
