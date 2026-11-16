"""The analysis reviewer exam: its executor (executor.py) and its scorer code.

The executor imports core_v2.py when an exam is run from the settings page; core_v1_frozen.py, the
earlier scorer, has no caller outside the package. The SOURCE_CORE_*_SHA256 values below are
written into results as a record. The exam's question pack is kept locally, not in this package.
"""

from memorive.exams.analysis_reviewer import core_v1_frozen, core_v2


SOURCE_CORE_V1_SHA256 = (
    "F62BDF48908933B06CDD14AE668CFF7434F71D4CE14560095526163F5489B031"
)
SOURCE_CORE_V2_SHA256 = (
    "A254B09450AB3485B1BD220BD4096F1CAF7A9E2D1C3205F35BCE0F42228B3940"
)
BUNDLED_CORE_V2_IMPORT_ADAPTER_REVISION = (
    "Desktop_ANALYSIS_REVIEWER_A4_RELATIVE_IMPORT_ADAPTER_V1"
)

__all__ = [
    "BUNDLED_CORE_V2_IMPORT_ADAPTER_REVISION",
    "SOURCE_CORE_V1_SHA256",
    "SOURCE_CORE_V2_SHA256",
    "core_v1_frozen",
    "core_v2",
]
