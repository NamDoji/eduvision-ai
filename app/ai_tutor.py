from __future__ import annotations

from typing import Any

from .accessibility import accessible_suggestions
from .english_tutor import correct_english
from .geometry_tutor import explain_geometry


def tutor_answer(subject: str, question: str, profile: dict[str, Any] | None, context: list[str], language: str = "en") -> tuple[str, list[str]]:
    if subject == "geometry":
        return explain_geometry(question, profile, context, language), accessible_suggestions(language)
    if subject == "english":
        suggestions = ["Xin thêm ví dụ", "Luyện hội thoại ngắn", "Tạo 5 câu luyện tập"] if language == "vi" else [
            "Ask for more examples",
            "Ask for a short dialogue",
            "Ask for 5 practice sentences",
        ]
        return correct_english(question, language), suggestions
    if language == "vi":
        return "Cô có thể hỗ trợ Toán hình, tiếng Anh, đọc ảnh/PDF bằng OCR và lập kế hoạch học tập. Em hãy nói môn học và phần em thấy khó.", [
            "Thử /geometry",
            "Thử /english",
            "Thử /plan",
        ]
    return "I can help with geometry, English, OCR reading, and study planning. Please tell me the subject and what you find difficult.", [
        "Try /geometry",
        "Try /english",
        "Try /plan",
    ]

