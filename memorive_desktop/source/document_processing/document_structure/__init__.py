"""E10 candidate structure sidecars; no model/runtime imports at module load."""

from .service import validate_options, verify_bundle, write_structure

__all__ = ["validate_options", "verify_bundle", "write_structure"]
