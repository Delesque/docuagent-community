# DocuAgent Desktop

Electron shell for the local DocuAgent workbench. It starts the Python backend,
waits for `/api/health`, and opens the built frontend in a desktop window.

## One-click launch

Windows: double-click `DocuAgent-Desktop.bat` at the repository root.

Linux/macOS: run `./DocuAgent-Desktop.sh`.

The launcher builds `web-next/` when it is missing and installs Electron on first
run. The Python backend is killed automatically when the desktop window closes.
The desktop shell uses Electron's native folder picker instead of the backend
tkinter dialog, so "项目地址" works reliably inside the desktop window.

Source runs use the bundled runtime when `embed_python.py` has prepared it, and
otherwise fall back to `python` / `python3` on `PATH`.

## Bundled Python runtime

The desktop packages ship their own interpreter, so an installed app does not need
Python on the user's machine. `embed_python.py` downloads the official Windows
embeddable distribution, verifies its SHA-256 against the digest pinned in the
script, unpacks it into `build/python-runtime`, and declares the bundled app
directory in the interpreter's `python312._pth` (the embedded interpreter ignores
`PYTHONPATH` by design).

```powershell
python embed_python.py           # download, verify and prepare
python embed_python.py --check   # verify an existing runtime
```

The application runtime is standard-library only, so the embeddable distribution is
sufficient. A different Python version must have its digest pinned in
`embed_python.py` before it can be bundled; unpinned versions are refused.

## Build a Windows package

Install the desktop dependencies, then build the unpacked app or the installer and
portable executable:

```powershell
npm install
npm run pack
npm run dist
```

Both commands build the frontend and prepare the runtime first. Build output is
written to `.release/desktop/` and contains `resources/app` (the backend and the
built frontend) plus `resources/python-runtime` (the bundled interpreter).

Smoke-test a build with `DOCUAGENT_DESKTOP_SMOKE=1`; the shell then loads the UI,
asserts the preload bridge answers, and exits with code 0.

Anonymous telemetry is stored locally by default. A release build can configure
the opt-in upload collector with `DOCUAGENT_TELEMETRY_UPLOAD_URL`; it must be an
HTTPS endpoint (loopback HTTP is accepted for development). Leaving it unset
keeps upload disabled while local metrics remain available.
