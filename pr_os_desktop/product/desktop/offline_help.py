"""Only bundled, local help assets can be opened by the desktop bridge."""
import os,sys
from pathlib import Path

FORMATS={'web':'index.html','pdf':'manual.pdf','markdown':'manual.md',
         'design-pdf':'design.pdf','design-markdown':'design.md',
         'license-html':'license-privacy.html','license-markdown':'license-privacy.md'}
LANGUAGES={'zh-CN','en-US','ja-JP'}

def resolve_help_asset(kind,language,*,root=None):
    if not isinstance(kind,str) or kind not in FORMATS:raise ValueError('HELP_FORMAT_INVALID')
    if not isinstance(language,str) or language not in LANGUAGES:raise ValueError('HELP_LANGUAGE_INVALID')
    base=(Path(root) if root is not None else Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'help').resolve(strict=True)
    asset=(base/language/FORMATS[kind]).resolve(strict=True)
    if not asset.is_relative_to(base) or not asset.is_file():raise ValueError('HELP_ASSET_OUTSIDE_BUNDLE')
    return asset

def open_help_asset(kind,language):
    asset=resolve_help_asset(kind,language)
    os.startfile(str(asset),'open')
    return {'schema_version':'P08LocalHelpOpenReceipt-v1','status':'OPENED','kind':kind,'language':language,'bundled':True}
