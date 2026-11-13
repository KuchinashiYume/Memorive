"""Change digests of the desktop research runtime: the shared builder under this product's identity."""

from functools import partial

from research_reports.digest import DigestIdentity, build_digest as _build_digest

IDENTITY = DigestIdentity(
    generator_version="desktop-desktop-research_reports/1.0",
    producer="Desktop_DESKTOP_RESEARCH_REPORTS_SUCCESSOR",
    change_scope=("Desktop_APPLICATION_EVENTS", "Desktop_WORK_LOG_PROJECTION"),
)
GENERATOR_VERSION = IDENTITY.generator_version
build_digest = partial(_build_digest, builder=IDENTITY)
