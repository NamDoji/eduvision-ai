from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
AUDIO_OUTPUT_DIR = BASE_DIR / "audio_outputs"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'eduvision.db'}")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

DEFAULT_HOST = os.getenv("EDUVISION_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("EDUVISION_PORT", "8010"))
DEFAULT_LANGUAGE = os.getenv("EDUVISION_DEFAULT_LANGUAGE", "en")

