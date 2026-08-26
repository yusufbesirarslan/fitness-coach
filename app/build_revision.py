"""Immutable serving-revision loader.

The Docker image bakes a lowercase 40-hex SHA into /app/BUILD_REVISION.
Runtime environment, including APP_REVISION, cannot replace that value.
Outside a built image the file is absent and the loader returns "unknown".
"""
from __future__ import annotations

import re
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}")
BUILD_REVISION_PATH = Path("/app/BUILD_REVISION")


def load_build_revision(path: Path | None = None) -> str:
    target = BUILD_REVISION_PATH if path is None else path
    try:
        value = target.read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"
    return value if SHA_RE.fullmatch(value) else "unknown"
