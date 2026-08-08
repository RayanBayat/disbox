# PyInstaller build for the Disbox desktop client.
#
# Run from the repository root:
#     uv run pyinstaller packaging/disbox.spec --noconfirm
#
# One directory rather than one file. A --onefile build unpacks itself to a
# temporary directory on every launch, which for a Qt application means several
# seconds of startup and an antivirus scan each time; the installer hides the
# directory anyway.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

# Typer builds its command table by importing the module, so the CLI's
# subcommands are not reachable by static analysis.
hidden = collect_submodules("disbox.cli")

analysis = Analysis(
    [str(ROOT / "src" / "disbox" / "gui" / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # Qt ships translations, 3D, WebEngine and multimedia that this application
    # never touches. Excluding them is most of the difference between a ~90 MB
    # bundle and a ~250 MB one.
    excludes=[
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetworkAuth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQml",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        "tkinter",
        "unittest",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Disbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries are a reliable way to be flagged as malware
    console=False,  # a GUI application should not open a terminal behind itself
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Disbox",
)
