"""The library page: processed papers, their product files, and a person's read confirmation.

The window reaches it through library.* methods (LibraryProductController in product_controller.py).
The rows of finished document jobs come from core_products.py (CoreProducts, built by ProductApi);
paper_review.py keeps the read confirmation of a paper's card and analysis.
Other packages read library files only through its public methods, listed in DEVELOPING.md.
"""

from .product_controller import LIBRARY_METHODS, SAFE_SUFFIXES, LibraryProductController, LibraryProductError

__all__ = ['LIBRARY_METHODS', 'SAFE_SUFFIXES', 'LibraryProductController', 'LibraryProductError']
