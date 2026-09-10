# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec that produces dist/vaj-save/vaj-save.exe (Windows windowed)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent
entry = spec_dir / "entrypoint.py"
hiddenimports = collect_submodules("vajsave")

a = Analysis(
    [str(entry)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[(str(project_root / "assets"), "assets")],
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="vaj-save",
)
