"""Local model servers (Ollama and the like) on this computer: found and checked without running a model.

The window reaches it through local_models.* methods (LocalModelProductController).
"""

from memorive.models.local.product_controller import (
    LOCAL_MODEL_METHODS,
    LocalModelProductController,
    LocalModelProductError,
)

__all__ = ["LOCAL_MODEL_METHODS", "LocalModelProductController", "LocalModelProductError"]
