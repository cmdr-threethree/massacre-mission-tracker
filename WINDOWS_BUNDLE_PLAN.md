# Windows Bundle Plan

Status: proposed.

Goal: ship a portable Windows bundle that runs the tracker without requiring a
system Python installation, using the official CPython embeddable distribution
plus only the required runtime dependency packages.

---

## 1. Short answer

Yes. This project is a good fit for a bundled `python.exe`:

- Outside the standard library, the application imports only Textual and Rich.
- Every runtime dependency is pure Python.
- No PyInstaller, Nuitka, pip, setuptools, C compiler, or `pywin32` is needed in
  the shipped bundle.

The result is a self-contained folder of roughly 32 MB uncompressed, or about
13 MB as a zip.

---

## 2. Dependency audit

From `pyproject.toml`:

```toml
dependencies = ["textual>=8.0", "rich>=14.2"]
```

The resolved runtime closure in the development environment is:

| Package | Version | Approx. source size |
|---|---:|---:|
| textual | 8.2.8 | 3.3 MB |
| rich | 15.0.0 | 1.4 MB |
| pygments | 2.21.0 | 5.1 MB |
| markdown-it-py | 4.2.0 | 416 KB |
| mdit-py-plugins | 0.6.1 | 400 KB |
| platformdirs | 4.13.5 | 176 KB |
| typing-extensions | 4.16.0 | 164 KB |
| linkify-it-py | 2.2.0 | 68 KB |
| mdurl | 0.1.2 | 40 KB |

All nine are pure Python; none contains a native extension. The application
package itself is about 0.5 MB.

The official Python 3.13.7 embeddable amd64 archive is 10.9 MB compressed and
about 21 MB extracted. A simulated bundle of Python + dependencies + the
application measured about 32 MB uncompressed and 13 MB zipped.

Relevant implementation facts:

- Textual's Windows driver is pure Python plus `ctypes`; it does not require
  `pywin32`.
- Clipboard copy uses OSC 52, with the application's in-memory clipboard as a
  fallback; it does not require `pyperclip`.
- `src/massacre_tracker/__main__.py` exists, so
  `python.exe -m massacre_tracker` is a valid entry point.
- `pyproject.toml` includes `ui/*.tcss` as package data. `ui/app.tcss` must be
  present in the bundle.
- The application uses only standard-library modules besides Textual and Rich.

---

## 3. Bundle layout

```text
massacre-tracker-0.1.0-win64/
├── massacre-tracker.cmd
├── README.txt
├── LICENSE
└── python/
    ├── python.exe
    ├── python313.dll
    ├── python313.zip
    ├── python313._pth
    ├── vcruntime140.dll
    ├── vcruntime140_1.dll
    └── Lib/
        └── site-packages/
            ├── textual/
            ├── rich/
            ├── pygments/
            ├── massacre_tracker/
            └── *.dist-info/
```

Keeping `site-packages` under `python/Lib` keeps the `._pth` entries relative to
the executable and avoids path-resolution surprises.

Do not zip `site-packages`. Textual resolves its packaged CSS relative to
`__file__`, so the package directories should remain on disk.

---

## 4. Build plan

Build and smoke-test on Windows, or on a `windows-latest` CI runner. The
assembly could be done on Linux because every wheel is `py3-none-any`, but the
result cannot be executed there.

1. Download the official embeddable Python archive for an exact 3.13.x patch
   from python.org and verify its SHA-256.

   For Python 3.13.7 amd64:

   ```text
   f6cca216a359be84797cabb54149ce5e062afb16cc7567eb7fc51cacb2d86b65
   ```

2. Extract the archive into `python/`.

3. Install only the pinned runtime closure into `python/Lib/site-packages` with
   `--no-compile`, `--no-deps`, `--only-binary=:all:`, and the lock file.

   Keep the `.dist-info` directories. Textual reads its own version through
   `importlib.metadata`.

4. Build and install the application wheel, or copy `src/massacre_tracker`.
   Using the wheel verifies that `app.tcss` is packaged. Remove any generated
   `bin/` or `Scripts/` launcher produced by `pip --target`.

5. Write `python313._pth`:

   ```text
   python313.zip
   .
   Lib\site-packages
   ```

   Leave `import site` commented out unless a future dependency requires `.pth`
   processing. None of the current dependencies do.

6. Add the launcher, documentation, and license files.

7. Remove `__pycache__`, assert that `site-packages` contains no `.pyd` or
   `.dll` files, and zip the folder.

PowerShell outline:

```powershell
$tag   = "313"
$stage = "dist/massacre-tracker-0.1.0-win64"
$site  = "$stage/python/Lib/site-packages"

New-Item -ItemType Directory -Force $site | Out-Null
Expand-Archive dist/python-embed.zip -DestinationPath "$stage/python"

python -m pip install --no-compile --no-deps --only-binary=:all: `
  --target $site -r packaging/windows/requirements.lock

python -m pip wheel . --no-deps -w dist/wheels
python -m pip install --no-compile --no-deps --target $site `
  dist/wheels/massacre_mission_tracker-*.whl

Remove-Item "$site/bin", "$site/Scripts" -Recurse -Force `
  -ErrorAction SilentlyContinue

@"
python$tag.zip
.
Lib\site-packages
"@ | Set-Content -Encoding ASCII "$stage/python/python$tag._pth"

Copy-Item packaging/windows/massacre-tracker.cmd $stage
Copy-Item LICENSE, README.md $stage
Get-ChildItem $stage -Recurse -Directory -Filter __pycache__ |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Compress-Archive -Path $stage `
  -DestinationPath "dist/massacre-tracker-0.1.0-win64.zip"
```

The lock file should contain the nine runtime packages at exact versions. For
the versions audited above:

```text
textual==8.2.8
rich==15.0.0
markdown-it-py==4.2.0
mdit-py-plugins==0.6.1
linkify-it-py==2.2.0
mdurl==0.1.2
platformdirs==4.13.5
pygments==2.21.0
typing-extensions==4.16.0
```

Regenerate and test the lock before a release rather than assuming these
versions remain appropriate.

---

## 5. Launcher

```bat
@echo off
setlocal

rem Optional convenience: usual Windows journal location.
rem Remove this if the documented explicit-path behavior should be preserved.
if not defined ED_JOURNAL_DIR set "ED_JOURNAL_DIR=%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"

"%~dp0python\python.exe" -B -X utf8 -m massacre_tracker %*
if errorlevel 1 pause
```

Important details:

- Use `python.exe`, not `pythonw.exe`. Textual needs a real console.
- `-B` prevents Python from writing `__pycache__` into the bundle, preserving
  the application's runtime-writes-nothing behavior.
- Use `-X utf8` rather than relying on `PYTHONUTF8`. A `._pth` file enables
  isolated mode, which ignores Python environment variables.
- `%*` preserves argument quoting, so journal paths with spaces work.
- `if errorlevel 1 pause` keeps the window open after a startup failure when
  the launcher is double-clicked.
- Keeping the default `ED_JOURNAL_DIR` is optional. If retained, document that
  `--journal-dir` and an explicit `ED_JOURNAL_DIR` still take precedence.

---

## 6. Windows-specific validation

Before publishing:

- Run the existing pytest suite on Windows.
- Test the bundle on a clean Windows 10/11 VM with no system Python installed.
- Test `massacre-tracker.cmd --help` and the missing-journal-directory error
  screen.
- Test a journal path containing spaces and non-ASCII characters.
- Test journal appends while the tracker is running, plus truncation and file
  replacement.
- Test both Windows Terminal and legacy conhost. Windows Terminal is the better
  experience; conhost works through Textual's Win32 driver.
- Test clipboard copy in Windows Terminal using OSC 52, and confirm the
  in-memory Clipboard tab still works as a fallback.
- Confirm no `__pycache__` or other files appear after running.
- Test at 100×30 and 80×24 terminal sizes.
- Check SmartScreen behavior for a downloaded zip and document that the archive
  may need to be unblocked before extraction.

The main Windows-specific risk is concurrent journal reading. CPython opens
files read-only with sharing enabled by default, but this should be verified
against the real game while it is writing a journal.

---

## 7. Alternatives

- **PyInstaller `--onedir`**: works and produces a real `.exe`, but adds a
  bootloader and roughly 10–15 MB, with more antivirus friction. There is no
  functional benefit for this application.
- **PyInstaller `--onefile`**: extracts to `%TEMP%` on every launch; slower and
  more likely to be flagged. Not recommended for a TUI.
- **Nuitka**: requires a Windows compiler toolchain and produces a larger
  build; unnecessary.
- **python-build-standalone**: larger than the official embeddable
  distribution; useful only if a fuller standard library is needed.

If a native `massacre-tracker.exe` name is required, the options are
PyInstaller or a small compiled launcher. The `.cmd` launcher is the minimal
approach and is recommended for this bundle.

---

## 8. Planned repository changes

- `packaging/windows/build.ps1` — build and validation script.
- `packaging/windows/requirements.lock` — exact runtime pins.
- `packaging/windows/massacre-tracker.cmd` — launcher template.
- `packaging/windows/README.txt` — Windows usage, journal path, terminal
  recommendation, and unblock instructions.
- Windows bundle section in `README.md`.
- Optional `.github/workflows/windows-bundle.yml` to run tests on Windows and
  upload the zip artifact.
- No application-code changes are required for Windows compatibility.

---

## 9. Decisions to confirm

1. **Default journal path:** set
   `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous` in the
   launcher when `ED_JOURNAL_DIR` is unset, or preserve the current
   explicit-path-only behavior? Recommended: set it in the launcher.
2. **Launcher form:** portable `.cmd` (recommended) or PyInstaller-built `.exe`.
3. **Bytecode:** ship without `.pyc` and launch with `-B` (smaller, no runtime
   writes), or precompile during the build for slightly faster startup.
4. **Release build:** local Windows build script or CI artifact from
   `windows-latest`?
