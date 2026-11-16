"""The public root certificates shipped with Memorive, and the TLS context built from them.

public_roots.pem is Mozilla's root store as packaged by certifi (version and licence in
public_roots_manifest.json and public_roots_LICENSE); PUBLIC_ROOTS_SHA256 pins it, and the build
(tools/build_memorive.py, check_public_roots) checks the file against that hash.
public_metadata_context() adds these roots to the system's; it never installs them system-wide.
External metadata and price downloads (memorive_settings/metadata_transport.py) and model API calls
(memorive_settings/network_compat.py, tls_context) use it.
"""
import hashlib
from pathlib import Path
import ssl
import sys

PUBLIC_ROOTS_PATH = ((Path(sys._MEIPASS) / 'source/memorive/platform/public_roots.pem')
    if getattr(sys, 'frozen', False) else Path(__file__).with_name('public_roots.pem'))
PUBLIC_ROOTS_SHA256 = 'bbc7e9c01d7551bb8a159b5dedd989b8ee3ce105aff522b68eb1b01bf854cab0'


def public_metadata_context():
    # Byte-exact Mozilla public roots, with distribution/license provenance.
    # Preserve OS roots and strict defaults; never install roots system-wide.
    try:
        bundle = PUBLIC_ROOTS_PATH.read_bytes()
    except OSError as error:
        raise ValueError('EXTERNAL_METADATA_PUBLIC_ROOTS_UNAVAILABLE') from error
    if hashlib.sha256(bundle).hexdigest() != PUBLIC_ROOTS_SHA256:
        raise ValueError('EXTERNAL_METADATA_PUBLIC_ROOTS_INTEGRITY')
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=bundle.decode('ascii'))
    return context
