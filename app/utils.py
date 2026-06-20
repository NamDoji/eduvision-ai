from __future__ import annotations

import re


def safe_filename(filename: str | None) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", filename or "upload.bin")

