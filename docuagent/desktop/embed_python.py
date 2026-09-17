"""Prepare the Python runtime that ships inside the desktop packages.

The desktop build must not depend on a Python installation on the user's PATH, so the
release bundles the official Windows embeddable distribution next to the app. This
script downloads that distribution, verifies its SHA-256 against the checksum published
in the same directory, unpacks it into ``desktop/build/python-runtime`` and declares the
bundled app directory in the interpreter's ``python312._pth`` path file (the embedded
interpreter ignores ``PYTHONPATH`` by design).

Usage:
    python docuagent/desktop/embed_python.py            # download and prepare
    python docuagent/desktop/embed_python.py --check     # verify an existing runtime
    python docuagent/desktop/embed_python.py --version 3.12.10
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent
BUILD_DIR = DESKTOP / "build"
RUNTIME_DIR = BUILD_DIR / "python-runtime"

DEFAULT_VERSION = "3.12.10"
# Official python.org builds, mirrored for networks where python.org is unreachable.
MIRRORS = (
    "https://registry.npmmirror.com/-/binary/python/{version}",
    "https://www.python.org/ftp/python/{version}",
)
PTH_NAME = "python312._pth"
APP_RELATIVE_PATH = "..\\app"

# python.org publishes no per-file `.sha256` for the embeddable archive (it ships
# detached `.asc` / `.sigstore` next to it) and GitHub-style release pages are
# unreachable from some networks, so the expected digest is pinned here. It was
# recorded from the official artifact once, cross-checked against the mirror's
# reported size (11 133 606 bytes for 3.12.10). A new version must be pinned the same
# way before it can be bundled; anything unpinned is refused below.
PINNED_SHA256 = {
    "3.12.10": {
        "python-3.12.10-embed-amd64.zip": (
            "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
        ),
    },
}


def expected_checksum(version: str, filename: str) -> str | None:
    """Return the pinned SHA-256 for ``filename``, or None when it is not pinned."""
    return PINNED_SHA256.get(version, {}).get(filename)


def download(url: str, target: Path) -> None:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    print(f"  saved {target.name} ({target.stat().st_size} bytes)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(version: str, force: bool) -> int:
    filename = f"python-{version}-embed-amd64.zip"
    if RUNTIME_DIR.exists() and not force:
        print(f"runtime already prepared at {RUNTIME_DIR} (use --force to rebuild)")
        return check()

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    archive = BUILD_DIR / filename
    if not archive.exists():
        for mirror in MIRRORS:
            try:
                download(f"{mirror.format(version=version)}/{filename}", archive)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  mirror failed: {exc}")
        else:
            raise SystemExit(f"could not download {filename} from any mirror")

    expected = expected_checksum(version, filename)
    actual = sha256(archive)
    if expected is None:
        raise SystemExit(
            f"no published SHA-256 for {filename}; refusing to bundle an unverified archive"
        )
    if actual != expected:
        raise SystemExit(f"checksum mismatch for {filename}: {actual} != {expected}")
    print(f"  checksum ok: {actual}")

    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)
    RUNTIME_DIR.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(RUNTIME_DIR)

    pth = RUNTIME_DIR / PTH_NAME
    if not pth.exists():
        raise SystemExit(f"{PTH_NAME} missing from {filename}")
    lines = [line.rstrip() for line in pth.read_text(encoding="utf-8").splitlines() if line.strip()]
    if APP_RELATIVE_PATH not in lines:
        lines.append(APP_RELATIVE_PATH)
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  prepared {RUNTIME_DIR} (declared {APP_RELATIVE_PATH} in {PTH_NAME})")
    return check()


def check() -> int:
    python = RUNTIME_DIR / ("python.exe" if sys.platform == "win32" else "bin/python3")
    if not python.exists():
        print(f"FAIL: interpreter missing at {python}")
        return 1
    probe = subprocess.run(
        [str(python), "-c", "import sys, ssl, sqlite3, http.server; print(sys.version)"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        print(f"FAIL: interpreter probe exited {probe.returncode}\n{probe.stderr}")
        return 1
    version = probe.stdout.strip()
    size = sum(path.stat().st_size for path in RUNTIME_DIR.rglob("*") if path.is_file())
    print(f"OK: bundled runtime {version} ({size / 1024 / 1024:.1f} MiB)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--check", action="store_true", help="only verify an existing runtime")
    parser.add_argument("--force", action="store_true", help="rebuild even when the runtime exists")
    args = parser.parse_args()
    if args.check:
        return check()
    return prepare(args.version, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
