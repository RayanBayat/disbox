# Packaging

## Build the application

```bash
uv run pyinstaller packaging/disbox.spec --noconfirm --distpath build/dist --workpath build/work
```

Produces `build/dist/Disbox/Disbox.exe` and its dependencies. Around **149 MB**,
most of it Qt.

**Verified:** builds under Python 3.14 with PyInstaller 6.21, and the resulting
executable launches, initialises Qt and reaches application code.

## Build the installer

```bash
ISCC packaging/disbox.iss
```

Produces `build/installer/disbox-0.1.0-setup.exe`.

**Not verified.** Inno Setup was not available when this was written, so the
script has never been compiled. Expect to fix something the first time it runs.

## Decisions

**One directory, not one file.** A `--onefile` build unpacks itself to a
temporary directory on every launch. For a Qt application that is several
seconds of startup and a fresh antivirus scan each time. The installer hides the
directory from the user anyway.

**No UPX.** Compressed executables are one of the most reliable ways to be
flagged by antivirus heuristics. The saving is not worth the support burden.

**Qt modules are excluded aggressively.** PySide6 ships 3D, WebEngine,
multimedia, QML and more that this application never imports. The exclusion list
in the spec is most of the difference between roughly 90 MB and roughly 250 MB.
If a feature stops working after a packaging change, check that list first --
a missing Qt plugin fails at runtime, not at build time.

**Per-user install.** Disbox writes only to the user's own directories, so the
installer does not demand administrator rights. The user can still choose a
machine-wide install from the privileges dialog.

**The uninstaller removes only the program.** A vault is the user's data and
lives wherever they put it. Deleting vaults on uninstall would destroy the index
for everything they have stored and leave the blobs on Discord unreachable.

## Still to do

- **Code signing.** Unsigned, Windows SmartScreen will warn on first run. Needs
  a certificate, which is a purchasing decision rather than a technical one.
- **An application icon.** The executable currently uses PyInstaller's default.
- **CI.** The build is run by hand; it should produce an artefact per tag.
