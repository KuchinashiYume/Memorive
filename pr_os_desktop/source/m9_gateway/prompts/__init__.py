"""Versioned model prompts reused by the desktop product.

The desktop package also owns prompt asset directories below this package,
while Phase 1 historically exposes its registry from the sibling
``m9_gateway/prompts.py`` module.  Python gives the package precedence over the
module when both exist, so explicitly re-export the frozen Phase 1 registry
without copying or changing any prompt body.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "prompts.py"
_MODULE_NAME = "m9_gateway._phase1_prompt_registry"
_SPEC = spec_from_file_location(_MODULE_NAME, _REGISTRY_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load Phase 1 prompt registry: {_REGISTRY_PATH}")
_MODULE = module_from_spec(_SPEC)
sys.modules[_MODULE_NAME] = _MODULE
_SPEC.loader.exec_module(_MODULE)

PromptRegistry = _MODULE.PromptRegistry
PromptTemplate = _MODULE.PromptTemplate

__all__ = ["PromptRegistry", "PromptTemplate"]
