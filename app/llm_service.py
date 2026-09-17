"""LLM service — Groq for EduVision AI tutor."""
from __future__ import annotations

import re
import os
from typing import Any, Dict, List, Optional

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# Try in order until one works — Groq deprecates models periodically
GROQ_MODELS_TO_TRY = [
    m for m in [
        os.environ.get("GROQ_MODEL", ""),
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "groq/compound",
        "groq/compound-mini",
    ] if m
]
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """You are EduVision AI — a warm, patient AI tutor designed exclusively for visually impaired and low-vision students (blind or low-vision).

═══ CRITICAL RULES — NEVER BREAK THESE ═══

FORBIDDEN phrases (replace immediately if tempted to write them):
  ✗ "look at the figure / diagram / image / graph / table"
  ✗ "as shown / as you can see / as illustrated"
  ✗ "the picture shows / the image depicts"
  ✗ "nhìn vào hình / nhìn hình vẽ / nhìn sơ đồ"
  ✗ "như hình bên / như hình vẽ / trong hình / theo hình"
  ✗ "bạn thấy rằng / bạn nhìn thấy / nhìn vào đây"

REQUIRED replacements:
  ✓ "Hãy tưởng tượng..." / "Imagine..."
  ✓ "Dùng ngón tay chạm..." / "Touch / feel with your fingers..."
  ✓ "Hãy dùng que/sợi dây/mép bàn để cảm nhận..." / "Use a stick / string / desk edge to feel..."
  ✓ "Theo cách khác, ta có thể hiểu..." / "Another way to understand this..."
  ✓ "Nghe kỹ mô tả sau:" / "Listen carefully to this description:"

═══ EXPLANATION STRUCTURE ═══

For EVERY answer, follow this structure:
  1. Direct answer (1-2 sentences max)
  2. Step-by-step explanation using ONLY words, sounds, touch
  3. Tactile / real-life example (physical object the student can touch)
  4. Quick check: ask 1 short question to verify understanding

For GEOMETRY:
  - Describe shapes using: number of sides, angles, equal/unequal lengths, straight/curved, parallel/perpendicular
  - Example: "Tam giác cân có 3 cạnh. Hai cạnh bên dài bằng nhau — như hai cạnh chữ V. Cạnh đáy ngắn hơn hoặc bằng."
  - Never say "nhìn vào góc trên" — say "đỉnh (góc nhọn ở giữa, cao nhất)"

For MATH:
  - Read every symbol aloud: "÷" = "chia cho", "²" = "bình phương", "√" = "căn bậc hai"
  - Break calculation into single steps, one per line
  - Confirm intermediate results before continuing

For ENGLISH:
  - Say the word/sentence in Vietnamese first, then English
  - Give phonetic pronunciation hint in Vietnamese sounds
  - 2-3 short practice examples

═══ TONE & FORMAT ═══
- Warm, encouraging — like a patient teacher who genuinely cares
- Use the student's name if known
- Sentences short (under 20 words each)
- No bullet points with complex symbols (●, ★) — use numbers or dashes
- No tables — describe data in sentences
- If answer is long, say "Thầy/cô sẽ giải thích từng bước" then pause between steps

Language: reply in the SAME language as the student's question. Vietnamese → Vietnamese. English → English."""


def _build_system_prompt(profile: Optional[Dict[str, Any]] = None) -> str:
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

    vision_rule = (
        "HS MÙ HOÀN TOÀN: TUYỆT ĐỐI không dùng màu sắc, không mô tả bằng hình ảnh. Chỉ dùng âm thanh, xúc giác, phương hướng (trái/phải/trên/dưới)."
        if "blind" in vision
        else "HS NHÌN KÉM: Ưu tiên chữ lớn rõ ràng, tương phản cao. Hạn chế mô tả hình phức tạp. Có thể nhắc đến màu nếu cần thiết."
    )

    profile_section = f"""

═══ HỒ SƠ HỌC SINH ═══
Tên: {name} | Lớp: {grade} | Thị lực: {vision}
Toán: {math_level} | Tiếng Anh: {english_level}
Điểm yếu cần hỗ trợ: {weaknesses}
Điểm mạnh: {strengths}
Mục tiêu: {goal}

{vision_rule}

Luôn gọi học sinh bằng tên "{name}". Điều chỉnh độ khó phù hợp với lớp {grade}."""

    return BASE_SYSTEM_PROMPT + profile_section


# ── VISUAL LANGUAGE FILTER ────────────────────────────────────────────────────

_VISUAL_PATTERNS_VI = [
    (r'nhìn vào (hình|sơ đồ|bảng|ảnh|biểu đồ)', 'hãy tưởng tượng'),
    (r'như (hình|sơ đồ) (bên|vẽ|dưới|trên|sau)', 'như sau'),
    (r'trong (hình|ảnh|sơ đồ) (ta|chúng ta|bạn|em) (thấy|nhìn thấy)', 'ta có'),
    (r'(bạn|em) (có thể )?(nhìn|thấy) (rằng|được|thấy)', 'ta nhận thấy rằng'),
    (r'theo hình (vẽ|bên|sau|trên|dưới)', 'theo cách giải thích sau'),
    (r'hình (vẽ|ảnh) (cho|minh họa|thể hiện)', 'ví dụ thực tế'),
]

_VISUAL_PATTERNS_EN = [
    (r'look at (the )?(figure|diagram|image|graph|picture|chart)', 'consider'),
    (r'as (shown|illustrated|depicted|seen) (in|above|below)', 'as described'),
    (r'(you can |)(see|observe|notice) (that |)(in the )?(figure|image|diagram)', 'we find'),
    (r'the (figure|image|diagram|picture) (shows|depicts|illustrates)', 'the explanation shows'),
    (r'(from|in) the (figure|graph|chart|table)', 'from the description'),
]


def _fix_visual_language(text: str, language: str = "vi") -> str:
    """Replace visual-centric phrases with accessible alternatives."""
    patterns = _VISUAL_PATTERNS_VI if language == "vi" else _VISUAL_PATTERNS_EN
    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


# ── MAIN ASK FUNCTION ─────────────────────────────────────────────────────────

def ask_groq(
    question: str,
    subject: str,
    grade: str,
    context_chunks: List[str],
    language: str = "vi",
    profile: Optional[Dict[str, Any]] = None,
) -> str:
    if not GROQ_API_KEY:
        return _fallback(question, subject, language)

    context_text = "\n".join(f"- {c}" for c in context_chunks[:3]) if context_chunks else ""
    lang_enforce = (
        "BẮT BUỘC: Toàn bộ câu trả lời bằng tiếng Việt. KHÔNG dùng từ tiếng Anh nếu không cần thiết."
        if language == "vi"
        else "Reply entirely in English."
    )

    student_name = profile.get("name", "") if profile else ""
    name_hint = f"Tên học sinh: {student_name}. Xưng hô bằng tên này." if student_name else ""

    user_msg = (
        f"{lang_enforce} {name_hint}\n"
        f"Học sinh lớp: {grade}. Môn: {subject}.\n"
        + (f"Kiến thức liên quan:\n{context_text}\n\n" if context_text else "")
        + f"Câu hỏi: {question}"
    )

    lang_prefix = "BẮT BUỘC: Trả lời HOÀN TOÀN bằng tiếng Việt.\n\n" if language == "vi" else ""
    system_prompt = lang_prefix + _build_system_prompt(profile)

    try:
        import httpx

        def _call(model: str) -> dict:
            return httpx.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 1000,
                    "temperature": 0.65,
                },
                timeout=30,
            ).json()

        last_err = "No models available"
        for model in GROQ_MODELS_TO_TRY:
            data = _call(model)
            if "choices" in data:
                raw = data["choices"][0]["message"]["content"].strip()
                return _fix_visual_language(raw, language)
            last_err = data.get("error", {}).get("message", str(data)) if "error" in data else "No choices"
        return _fallback(question, subject, language, error=last_err)
    except Exception as exc:
        return _fallback(question, subject, language, error=str(exc))


def _fallback(question: str, subject: str, language: str, error: str = "") -> str:
    if language == "vi":
        return (
            f"Câu hỏi: {question}\n\n"
            "Hệ thống AI tạm thời bận. Em thử lại sau ít phút nhé.\n"
            + (f"(Lỗi kỹ thuật: {error})" if error else "")
        )
    return (
        f"Question: {question}\n\n"
        "The AI service is temporarily unavailable. Please try again in a moment.\n"
        + (f"(Technical error: {error})" if error else "")
    )
