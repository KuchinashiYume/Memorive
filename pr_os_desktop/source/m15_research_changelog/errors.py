"""Fail-closed exception types for the M15 shadow candidate."""


class M15Error(Exception):
    """Base class for controlled M15 failures."""


class ManifestIntegrityError(M15Error):
    """The frozen source manifest or one of its members is not intact."""


class SourceRecordError(M15Error):
    """A source record cannot be converted without guessing."""

    def __init__(self, error_code: str, message: str, *, field_names=()):
        super().__init__(message)
        self.error_code = error_code
        self.field_names = tuple(sorted(set(field_names)))


class OutputBoundaryError(M15Error):
    """The requested output path overlaps a protected or non-empty root."""


class WindowContractError(M15Error):
    """A requested report window does not satisfy the frozen calendar policy."""

