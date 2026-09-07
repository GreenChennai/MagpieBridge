# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import importlib.util

block_cipher = None

# Locate rapidocr_onnxruntime package dir dynamically (model/config files needed at runtime)
_rapidocr_spec = importlib.util.find_spec('rapidocr_onnxruntime')
_rapidocr_dir = os.path.dirname(_rapidocr_spec.origin) if _rapidocr_spec and _rapidocr_spec.origin else ''
print(f"[spec] rapidocr_onnxruntime dir: {_rapidocr_dir}")

_datas = [('magpie/web/dist', 'magpie/web/dist')]
if _rapidocr_dir:
    _datas.append((_rapidocr_dir, 'rapidocr_onnxruntime'))

# better PP-OCRv4-det + PP-OCRv3-rec models (drop-in from the OCR.exe project).
# bundles as `models/` inside the frozen app; ocr._models_dir() picks them up.
if os.path.isdir('models') and os.path.isfile(os.path.join('models', 'det.onnx')):
    _datas.append(('models', 'models'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'aiohttp',
        'websockets',
        'fastapi',
        'pydantic',
        'PIL',
        'PIL.Image',
        'rapidocr_onnxruntime',
        'onnxruntime',
        'pyclipper',
        'shapely',
        'yaml',
        'cv2',
        'six',
        'comtypes',
        'pycaw',
        'magpie.core.hotkey',
        # Plugin ecosystem deps (bundled so Plugins/ can import them on
        # machines without a system Python): openpyxl, xlsxwriter, requests
        'openpyxl',
        'xlsxwriter',
        'requests',
        'magpie.admin',
        'magpie.admin.command_handler',
        'magpie.tui',
        'magpie.tui.app',
        'magpie.tui.bus',
        'magpie.tui.log_handler',
        'textual',
        'textual.app',
        'textual.widgets',
        'textual.widgets._data_table',
        'textual.containers',
        'textual.css',
        'textual.reactive',
        'textual.binding',
        'rich',
        'rich.markup',
        'rich.console',
        'rich.text',
        'rich.table',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MagpieBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
