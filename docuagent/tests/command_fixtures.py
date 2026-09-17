"""Helpers for building verification commands inside tests.

The verification path refuses Python inline code (`-c`) on every platform, so tests
must not use `sys.executable -c ...` as a fixture: the same fixture then behaves
differently on Windows (where the interpreter path bypasses the argument check) and
on POSIX (where it is refused). Writing a small script file into the project root
and invoking it relative to the working directory works on both platforms and
matches the documented policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_SCRIPT_NAME = "verify_check.py"


def verification_command(
    root: Path,
    body: str = "print('ok')\n",
    *,
    name: str = DEFAULT_SCRIPT_NAME,
) -> str:
    """Write ``body`` as ``root/name`` and return a verifiable command for it.

    The returned command uses the running interpreter by absolute path (allowed by
    the policy) and a project-relative script path (required by the policy).
    """
    script = Path(root) / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    return f"{sys.executable} {name}"
