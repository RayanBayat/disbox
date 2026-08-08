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

# The schema migrations are read at runtime through importlib.resources, which
# finds nothing in a bundle unless the files are shipped. Without them the
# packaged application raises on the first vault it opens -- and a smoke test
# that only checks the program starts will never notice, because opening a
# vault is the step after starting.
SCHEMA = [(str(path), "disbox/core/schema") for path in (ROOT / "src" / "disbox" / "core" / "schema").glob("*.sql")]

# Qt ships translations, 3D, WebEngine and multimedia this application never
# touches. Excluding them is most of the difference between a ~90 MB bundle
# and a ~250 MB one. Shared by both programs so they cannot drift apart.
EXCLUDES = [
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
]


gui_analysis = Analysis(
    [str(ROOT / "src" / "disbox" / "gui" / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=SCHEMA,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)



# The CLI is a separate program, not a flag on the GUI: a windowed binary
# cannot write to a console, so `disbox ls` from a terminal would print
# nothing at all if it shared the GUI's executable.
cli_analysis = Analysis(
    [str(ROOT / "src" / "disbox" / "cli" / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=SCHEMA,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# Sharing dependencies between the two, so Qt is present once rather than twice.
MERGE((gui_analysis, "Disbox", "Disbox"), (cli_analysis, "disbox-cli", "disbox-cli"))

gui_pyz = PYZ(gui_analysis.pure)
cli_pyz = PYZ(cli_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
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

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="disbox-cli",  # not "disbox": Windows would treat it as Disbox.exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # a command-line tool must be able to write to one
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Disbox",
)
