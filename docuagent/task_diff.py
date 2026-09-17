"""Pure diff primitives shared by task generation and patch review."""
from __future__ import annotations

import difflib
from typing import Any


def diff_hunks(before: str, after: str) -> list[dict[str, Any]]:
    """Split a before/after pair into independently selectable change hunks.

    Each hunk carries a stable `id` (the opcode index) plus removed/added line
    spans, so the frontend renders per-hunk accept/reject and the apply path
    reconstructs a partial result from the same ids.
    """
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    hunks: list[dict[str, Any]] = []
    for index, (tag, i1, i2, j1, j2) in enumerate(matcher.get_opcodes()):
        if tag == "equal":
            continue
        hunks.append({
            "id": index,
            "tag": tag,
            "before_start": i1 + 1,
            "before_count": i2 - i1,
            "after_start": j1 + 1,
            "after_count": j2 - j1,
            "before": "".join(before_lines[i1:i2]),
            "after": "".join(after_lines[j1:j2]),
        })
    return hunks


def apply_hunks(before: str, after: str, accepted_ids: set[int]) -> str:
    """Reconstruct file content by applying only the accepted change hunks."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    out: list[str] = []
    for index, (tag, i1, i2, j1, j2) in enumerate(matcher.get_opcodes()):
        if tag == "equal":
            out.extend(before_lines[i1:i2])
        elif index in accepted_ids:
            if tag in ("replace", "insert"):
                out.extend(after_lines[j1:j2])
        else:
            if tag in ("replace", "delete"):
                out.extend(before_lines[i1:i2])
    return "".join(out)
