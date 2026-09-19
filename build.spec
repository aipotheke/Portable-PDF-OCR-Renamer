# PyInstaller build spec (M5).
# Build:  pyinstaller build.spec
# Output: dist/PDF-OCR-Renamer.exe (onefile, windowed)

from pathlib import Path

REPO = Path(SPECPATH)

datas = [
    (str(REPO / "app" / "webui" / "index.html"), "webui"),
    (str(REPO / "assets" / "tray.png"), "assets"),
]

a = Analysis(
    ["run.py"],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="PDF-OCR-Renamer",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # --windowed: no console window
    icon=str(REPO / "assets" / "tray.ico") if (REPO / "assets" / "tray.ico").exists() else None,
)
