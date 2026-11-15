"""Earlier name of build_pages.py.

The two UI pages are generated from memorive_desktop/ui/ now, and build_pages.py
writes their script hashes as part of each page. This name is kept so that older
instructions still work: it runs build_pages.py with the same options.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("build_pages.py")), run_name="__main__")
