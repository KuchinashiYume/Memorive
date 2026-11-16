"""The library page: processed papers, their product files, and a person's read confirmation.

The window reaches it through library.* methods (LibraryProductController in product_controller.py).
Other packages read library files only through its public methods, listed in DEVELOPING.md.
"""

from .product_controller import LIBRARY_METHODS, SAFE_SUFFIXES, LibraryProductController, LibraryProductError

__all__ = ['LIBRARY_METHODS', 'SAFE_SUFFIXES', 'LibraryProductController', 'LibraryProductError']
