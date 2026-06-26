"""LLM service — Groq (Llama 3.3 70B) for EduVision AI tutor."""
from __future__ import annotations

import os
from typing import List

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """You are EduVision AI — a warm, patient tutor for visually impaired and low-vision students.

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


def ask_groq(
    question: str,
    subject: str,
    grade: str,
    context_chunks: List[str],
    language: str = "vi",
) -> str:
    """Call Groq Llama 3.3 70B and return the answer text."""
    if not GROQ_API_KEY:
        return _fallback(question, subject, language)

    context_text = "\n".join(f"- {c}" for c in context_chunks[:3]) if context_chunks else ""
    lang_hint = "Trả lời bằng tiếng Việt." if language == "vi" else "Reply in English."

    user_msg = (
        f"{lang_hint}\n"
        f"Học sinh lớp: {grade}. Môn: {subject}.\n"
        + (f"Kiến thức liên quan:\n{context_text}\n\n" if context_text else "")
        + f"Câu hỏi: {question}"
    )

    try:
        import httpx
        resp = httpx.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 800,
                "temperature": 0.7,
            },
            timeout=30,
        )
        data = resp.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
        return _fallback(question, subject, language)
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
