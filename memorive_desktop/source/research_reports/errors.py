"""Fail-closed exception types for the RESEARCH_REPORTS shadow candidate."""


class ResearchReportsError(Exception):
    """Base class for controlled RESEARCH_REPORTS failures."""


class SourceRecordError(ResearchReportsError):
    """A source record cannot be converted without guessing."""

    def __init__(self, error_code: str, message: str, *, field_names=()):
        super().__init__(message)
        self.error_code = error_code
        self.field_names = tuple(sorted(set(field_names)))


class WindowContractError(ResearchReportsError):
    """A requested report window does not satisfy the frozen calendar policy."""
