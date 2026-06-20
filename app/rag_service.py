from __future__ import annotations

import csv
import re
from pathlib import Path

from .config import DATA_DIR

KB_FILE = DATA_DIR / "knowledge_base.md"
TRAIN_CSV = DATA_DIR / "train.csv"
SAMPLE_KB_CSV = DATA_DIR / "sample_knowledge_base.csv"


def normalize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9À-ỹ]+", text.lower())


def load_chunks() -> list[str]:
    chunks: list[str] = []
    for path in [KB_FILE]:
        if path.exists():
            chunks.extend([chunk.strip() for chunk in path.read_text(encoding="utf-8").split("\n\n") if chunk.strip()])
    for path in [TRAIN_CSV, SAMPLE_KB_CSV]:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    chunks.append(" | ".join(str(value) for value in row.values() if value))
    return chunks


def search(query: str, limit: int = 3) -> list[str]:
    q = set(normalize(query))
    scored: list[tuple[int, str]] = []
    for chunk in load_chunks():
        score = len(q.intersection(normalize(chunk)))
        if score:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]

