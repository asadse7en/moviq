from pathlib import Path
import sys

spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent
sys.path.insert(0, str(project_root))
from moviq import __version__

asset_root = project_root / "assets"
asset_files = [
    (str(path), str(path.parent.relative_to(project_root)))
    for path in asset_root.rglob("*")
    if path.is_file()
]
ui_asset_root = project_root / "moviq" / "ui" / "assets"
asset_files.extend(
    (str(path), str(path.parent.relative_to(project_root)))
    for path in ui_asset_root.rglob("*")
    if path.is_file()
)
entry_script = spec_dir / "run_moviq.py"

a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root)],
    binaries=[],
    datas=asset_files,
    hiddenimports=[],
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
    name=f"Moviq-Portable-{__version__}",
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
    icon=str(asset_root / "moviq-icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=f"Moviq-Portable-{__version__}",
)
