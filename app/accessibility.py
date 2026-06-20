from __future__ import annotations

VISUAL_ONLY_PHRASES = [
    "look at the figure",
    "as shown in the image",
    "you can see",
    "nhìn vào hình",
    "như hình vẽ",
]

ACCESSIBLE_GEOMETRY_PHRASES = [
    "imagine",
    "touch",
    "feel",
    "use sticks",
    "use string",
    "use cardboard",
    "trace the edge",
    "raised-line drawing",
]


def contains_visual_only_language(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in VISUAL_ONLY_PHRASES)


def accessible_suggestions(language: str = "en") -> list[str]:
    if language == "vi":
        return ["Yêu cầu giải thích chậm hơn", "Xin bài tương tự", "Nghe phiên bản giọng nói"]
    return ["Ask for a slower explanation", "Ask for similar exercises", "Ask for an audio version"]

