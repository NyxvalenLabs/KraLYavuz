import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPEC).resolve().parent
icon_path = project_root / "assets" / (
    "kralyavuz_icon.icns" if sys.platform == "darwin" else "kralyavuz_icon.ico"
)
datas = [(str(project_root / "assets"), "assets")]
datas += collect_data_files("playwright")
hidden_imports = collect_submodules("playwright")

a = Analysis(
    [str(project_root / "run_kralyavuz.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
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
    name="KraLYavuz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon_path),
)

distribution = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="KraLYavuz",
)

if sys.platform == "darwin":
    app = BUNDLE(
        distribution,
        name="KraLYavuz.app",
        icon=str(icon_path),
        bundle_identifier="com.kralyavuz.desktop",
        info_plist={
            "CFBundleDisplayName": "KraLYavuz",
            "NSHighResolutionCapable": True,
        },
    )
