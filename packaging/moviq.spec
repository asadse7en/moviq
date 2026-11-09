from pathlib import Path
import sys

spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent
sys.path.insert(0, str(project_root))
from moviq import __version__

asset_root = project_root / "assets"
version = __version__
ui_asset_root = project_root / "moviq" / "ui" / "assets"

datas = [
    (str(asset_root / "moviq-icon.png"), "assets"),
    *[
        (
            str(path),
            str(Path("moviq") / "ui" / "assets" / path.relative_to(ui_asset_root).parent),
        )
        for path in ui_asset_root.rglob("*")
        if path.is_file()
    ],
]

entry_script = spec_dir / "run_moviq.py"

a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["debug_trace"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    win_exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=f"Moviq-Portable-{version}",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(asset_root / "moviq-icon.ico"),
    )
    win_coll = COLLECT(
        win_exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=f"Moviq-Portable-{version}",
    )

if sys.platform == "darwin":
    mac_exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Moviq",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(asset_root / "moviq-icon.icns"),
    )
    mac_coll = COLLECT(
        mac_exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Moviq",
    )
    mac_bundle = BUNDLE(
        mac_coll,
        name="Moviq.app",
        icon=str(asset_root / "moviq-icon.icns"),
        bundle_identifier="io.github.asadse7en.moviq",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )

if sys.platform == "linux":
    linux_exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="moviq",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    linux_coll = COLLECT(
        linux_exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Moviq",
    )
