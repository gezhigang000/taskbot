# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['websockets', 'websockets.legacy', 'websockets.legacy.client', 'asyncio', 'tkinter', 'tkinter.ttk', 'tkinter.scrolledtext', 'tkinter.filedialog', 'tkinter.messagebox', 'json', 'urllib.request', 'urllib.parse', 'threading', 'dataclasses', 'pathlib']
hiddenimports += collect_submodules('websockets')


a = Analysis(
    ['agent/gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClaudeCodeRemote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClaudeCodeRemote',
)
app = BUNDLE(
    coll,
    name='ClaudeCodeRemote.app',
    icon=None,
    bundle_identifier='com.claudecode.remote',
)
