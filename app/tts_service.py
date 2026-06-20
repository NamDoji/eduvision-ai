from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .config import AUDIO_OUTPUT_DIR


def default_voice(language: str) -> str:
    return "Linh" if language == "vi" else "Samantha"


def voices() -> dict[str, str]:
    if not shutil.which("say"):
        return {"en": "", "vi": ""}
    return {"en": "Samantha", "vi": "Linh"}


def synthesize_with_macos_say(text: str, language: str = "en", voice: str | None = None) -> Path | None:
    if not shutil.which("say"):
        return None
    AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = AUDIO_OUTPUT_DIR / f"eduvision-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.aiff"
    subprocess.run(["say", "-v", voice or default_voice(language), "-o", str(output), text[:2500]], check=True)
    return output

