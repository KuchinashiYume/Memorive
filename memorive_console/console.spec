# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

webview_datas, webview_binaries, webview_hidden = collect_all('webview')
pythonnet_datas, pythonnet_binaries, pythonnet_hidden = collect_all('pythonnet')
clr_datas, clr_binaries, clr_hidden = collect_all('clr_loader')

datas = [
    ('source/index.html', '.'),
    ('source/app.js', '.'),
    ('source/review_ui.js', '.'),
    ('source/i18n.js', '.'),
    ('source/locales.json', '.'),
    ('source/app.css', '.'),
    ('source/bridge.schema.json', '.'),
    ('assets/memorive_test_console_icon.png', '.'),
    ('LICENSE', '.'),
    ('NOTICE.txt', '.'),
    ('THIRD_PARTY_NOTICES.md', '.'),
    ('licenses', 'licenses'),
] + webview_datas + pythonnet_datas + clr_datas

a = Analysis(
    ['source/main.py'],
    pathex=['source'],
    binaries=webview_binaries + pythonnet_binaries + clr_binaries,
    datas=datas,
    hiddenimports=webview_hidden + pythonnet_hidden + clr_hidden + [
        'clr',
        'reference_bridge',
        'webview.dom',
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Memorive-Test-Console',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/memorive_test_console.ico'],
    version='console_version_info.txt',
)
