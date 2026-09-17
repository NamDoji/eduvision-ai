"""LLM service — Groq (Llama 3.3 70B) for EduVision AI tutor."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# Try in order until one works — Groq deprecates models periodically
# As of 2026: old llama3/gemma/mixtral IDs decommissioned; use openai/gpt-oss-* or groq/compound
GROQ_MODELS_TO_TRY = [
    m for m in [
        os.environ.get("GROQ_MODEL", ""),
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "groq/compound",
        "groq/compound-mini",
    ] if m
]
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

BASE_SYSTEM_PROMPT = """You are EduVision AI — a warm, patient tutor for visually impaired and low-vision students.

Rules (always follow):
- NEVER say "look at the figure", "as shown in the image", "you can see". Replace with tactile/verbal descriptions.
- Use: "imagine", "touch", "feel", "use sticks/string/cardboard/desk edge".
- Explain step by step. Keep each step short and clear.
- For geometry: describe points, sides, angles, equal lengths, parallel/perpendicular relationships verbally.
- For English: give corrected sentence → grammar explanation → 2-3 practice examples.
- For any subject: give direct answer → explanation → tactile/real-life example → quick check question.
- Response length: concise but complete. No unnecessary filler phrases.
- Never start with "Great question!" or similar sycophantic openers.

Language: reply in the same language as the student's question (Vietnamese if asked in Vietnamese, English if in English)."""


def _build_system_prompt(profile: Optional[Dict[str, Any]] = None) -> str:
    """Xây system prompt cá nhân hoá theo hồ sơ học sinh."""
    if not profile or not profile.get("name"):
        return BASE_SYSTEM_PROMPT

    vision = profile.get("vision_status", "")
    name = profile.get("name", "học sinh")
    grade = profile.get("grade", "")
    weaknesses = ", ".join(profile.get("weaknesses", [])) or "chưa xác định"
    strengths = ", ".join(profile.get("strengths", [])) or "chưa xác định"
    math_level = profile.get("math_level", "")
    english_level = profile.get("english_level", "")
    goal = profile.get("learning_goal", "")

    profile_section = f"""
--- THÔNG TIN HỌC SINH ---
Tên: {name} | Lớp: {grade} | Thị lực: {vision}
Toán: {math_level} | Tiếng Anh trình độ: {english_level}
Điểm yếu: {weaknesses}
Điểm mạnh: {strengths}
Mục tiêu học: {goal}
--- KẾT THÚC HỒ SƠ ---

Điều chỉnh giải thích phù hợp với thị lực "{vision}" của {name}:
- Nếu mù hoàn toàn (blind): chỉ dùng mô tả xúc giác và âm thanh, không dùng màu sắc.
- Nếu thị lực kém (low vision): có thể dùng tương phản cao, chữ lớn, hạn chế mô tả hình ảnh phức tạp.
- Điều chỉnh độ khó theo lớp và điểm yếu đã biết của học sinh."""

    return BASE_SYSTEM_PROMPT + profile_section


def ask_groq(
    question: str,
    subject: str,
    grade: str,
    context_chunks: List[str],
    language: str = "vi",
    profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Call Groq Llama 3.3 70B and return the answer text."""
    if not GROQ_API_KEY:
        return _fallback(question, subject, language)

    context_text = "\n".join(f"- {c}" for c in context_chunks[:3]) if context_chunks else ""
    lang_hint = "QUAN TRỌNG: Toàn bộ câu trả lời phải bằng tiếng Việt, kể cả thuật ngữ kỹ thuật (dịch hoặc giữ nguyên kèm giải thích)." if language == "vi" else "Reply entirely in English."

    student_name = profile.get("name", "") if profile else ""
    name_hint = f"Xưng hô với học sinh bằng tên: {student_name}. " if student_name else ""

    user_msg = (
        f"{lang_hint} {name_hint}\n"
        f"Học sinh lớp: {grade}. Môn: {subject}.\n"
        + (f"Kiến thức liên quan:\n{context_text}\n\n" if context_text else "")
        + f"Câu hỏi: {question}"
    )

    system_prompt = _build_system_prompt(profile)

    try:
        import httpx

        def _call(model: str) -> dict:
            return httpx.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 900,
                    "temperature": 0.7,
                },
                timeout=30,
            ).json()

        last_err = "No models available"
        for model in GROQ_MODELS_TO_TRY:
            data = _call(model)
            if "choices" in data:
                return data["choices"][0]["message"]["content"].strip()
            last_err = data.get("error", {}).get("message", str(data)) if "error" in data else "No choices"
        return _fallback(question, subject, language, error=last_err)
    except Exception as exc:
        return _fallback(question, subject, language, error=str(exc))


def _fallback(question: str, subject: str, language: str, error: str = "") -> str:
    """Simple fallback when Groq is unavailable."""
    if language == "vi":
        return (
            f"Câu hỏi của em: {question}\n\n"
            "Hiện tại hệ thống AI đang bận. Em thử lại sau ít phút nhé.\n"
            + (f"(Lỗi: {error})" if error else "")
        )
    return (
        f"Your question: {question}\n\n"
        "The AI service is temporarily busy. Please try again in a moment.\n"
        + (f"(Error: {error})" if error else "")
    )
