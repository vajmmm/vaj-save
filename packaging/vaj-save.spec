# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec that produces dist/vaj-save.app (macOS windowed bundle)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent
entry = spec_dir / "entrypoint.py"
hiddenimports = collect_submodules("vajsave")
qtawesome_datas = collect_data_files("qtawesome")

a = Analysis(
    [str(entry)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[
        (str(project_root / "assets"), "assets"),
        (str(project_root / "src" / "vajsave" / "data"), "vajsave/data"),
    ] + qtawesome_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vaj-save",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="vaj-save",
)
app = BUNDLE(
    coll,
    name="vaj-save.app",
    icon=None,
    bundle_identifier="local.vajsave.app",
    info_plist={
        "CFBundleName": "vaj-save",
        "CFBundleDisplayName": "vaj-save",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
    },
)
