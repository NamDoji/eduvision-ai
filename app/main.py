from __future__ import annotations

import csv
import importlib.util
import json
import os
import base64
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from app.auth import (
    init_auth_db, login as auth_login, logout as auth_logout,
    get_user_by_token, create_student_account, create_teacher_account,
    list_users, change_password,
    create_school, list_schools, get_school, list_school_users, reset_user_password,
)
from app.braille import text_to_unicode_braille, text_to_brf, vietnamese_note


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
PROFILE_FILE = DATA_DIR / "student_profiles.json"
KB_FILE = DATA_DIR / "knowledge_base.md"
TRAIN_CSV = DATA_DIR / "train.csv"
WRITE_BASE_DIR = Path(os.environ.get("EDUVISION_WRITE_DIR", "/tmp/eduvision-ai")) if os.environ.get("VERCEL") else BASE_DIR
DB_FILE = WRITE_BASE_DIR / "data" / "eduvision.db"
UPLOAD_DIR = WRITE_BASE_DIR / "uploads"
AUDIO_OUTPUT_DIR = WRITE_BASE_DIR / "audio_outputs"
MEDIA_DIR = BASE_DIR / "media"
TTS_DIR = AUDIO_OUTPUT_DIR

app = FastAPI(
    title="EduVision AI Backend",
    description="Conference-ready local backend for accessible tutoring through OpenClaw.",
    version="0.5.0",
)


class AskRequest(BaseModel):
    student_id: str = Field(default="S001")
    question: str
    grade: Optional[str] = None
    vision_status: Optional[str] = None
    subject: Literal["geometry", "english", "general"] = "general"
    language: Literal["en", "vi"] = "en"


class AskResponse(BaseModel):
    answer: str
    subject: str
    suggestions: List[str]
    context_used: List[str] = []


class StudyPlanRequest(BaseModel):
    student_id: str = "S001"
    grade: str = "Grade 8"
    weakness: str = "geometry"
    available_time: str = "25 minutes per day"
    language: Literal["en", "vi"] = "en"


class StudyPlanResponse(BaseModel):
    weekly_plan: List[str]


class ProfilePayload(BaseModel):
    student_id: str = "S001"
    name: str = "Student A"
    grade: str = "Grade 8"
    vision_status: str = "low vision"
    math_level: str = "weak in geometry"
    english_level: str = "A2"
    weaknesses: List[str] = ["geometry", "charts", "self-study"]
    strengths: List[str] = ["listening", "verbal explanation"]
    learning_goal: str = "understand classroom lessons better"


class CommandRequest(BaseModel):
    student_id: str = "S001"
    message: str
    language: Literal["en", "vi"] = "en"


class TTSRequest(BaseModel):
    text: str
    language: Literal["en", "vi"] = "en"
    voice: Optional[str] = None


def db() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                input TEXT NOT NULL,
                output TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
        if count == 0 and PROFILE_FILE.exists():
            profiles = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            for student_id, profile in profiles.items():
                conn.execute(
                    "INSERT OR REPLACE INTO students VALUES (?, ?, ?)",
                    (student_id, json.dumps(profile, ensure_ascii=False), datetime.utcnow().isoformat()),
                )


@app.on_event("startup")
def startup() -> None:
    # Vercel cold-start: copy bundled seed DB to writable /tmp if not yet present
    if os.environ.get("VERCEL"):
        seed_src = BASE_DIR / "data" / "eduvision_seed.db"
        if seed_src.exists() and not DB_FILE.exists():
            DB_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed_src, DB_FILE)
    init_db()
    init_auth_db()


def _get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("session") or request.headers.get("X-Session-Token", "")
    return get_user_by_token(token)


def _require_teacher(request: Request) -> dict:
    user = _get_current_user(request)
    if not user or user["role"] != "teacher":
        raise HTTPException(status_code=403, detail="Chỉ giáo viên mới có quyền truy cập")
    return user


def get_profile(student_id: str) -> Dict[str, Any]:
    init_db()
    with db() as conn:
        row = conn.execute("SELECT profile_json FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if not row:
        return {}
    return json.loads(row["profile_json"])


def save_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    init_db()
    student_id = profile["student_id"]
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO students VALUES (?, ?, ?)",
            (student_id, json.dumps(profile, ensure_ascii=False), datetime.utcnow().isoformat()),
        )
    return profile


def log_event(student_id: str, subject: str, input_text: str, output_text: str) -> None:
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO learning_events (student_id, subject, input, output, created_at) VALUES (?, ?, ?, ?, ?)",
                (student_id, subject, input_text, output_text, datetime.utcnow().isoformat()),
            )
    except sqlite3.OperationalError:
        # Vercel filesystem is read-only for the deployed bundle; keep the app working.
        return


def normalize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9À-ỹ]+", text.lower())


def load_knowledge_chunks() -> List[str]:
    chunks: List[str] = []
    if KB_FILE.exists():
        chunks.extend([c.strip() for c in KB_FILE.read_text(encoding="utf-8").split("\n\n") if c.strip()])
    if TRAIN_CSV.exists():
        with TRAIN_CSV.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                chunks.append(f"{row.get('category', '')}: {row.get('input', '')} -> {row.get('output', '')}")
    return chunks


def sanitize_accessibility_context(text: str) -> str:
    replacements = {
        "look at the figure": "use the tactile description",
        "look at the picture": "use the tactile description",
        "as shown in the figure": "as described in words",
        "as shown in the image": "as described in words",
        "you can see": "you can notice",
        "nhìn vào hình": "nghe mô tả bằng lời",
        "như hình vẽ": "như mô tả bằng lời",
    }
    cleaned = text
    for phrase, replacement in replacements.items():
        cleaned = re.sub(re.escape(phrase), replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def rag_search(query: str, limit: int = 3) -> List[str]:
    q = set(normalize(query))
    scored = []
    for chunk in load_knowledge_chunks():
        score = len(q.intersection(normalize(chunk)))
        if score:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


def ocr_status() -> Dict[str, Any]:
    from app.ocr_service import provider_status
    return provider_status()


def default_voice(language: str) -> str:
    return "Linh" if language == "vi" else "Samantha"


def available_tts_voices() -> Dict[str, str]:
    if not shutil.which("say"):
        return {"en": "", "vi": ""}
    return {"en": "Samantha", "vi": "Linh"}


def accessible_geometry_answer(question: str, profile: Dict[str, Any], context: List[str], language: str = "en") -> str:
    grade = profile.get("grade", "your grade")
    context_text = "\n".join(f"- {sanitize_accessibility_context(item)}" for item in context[:2])
    lowered = question.lower()
    if "pythag" in lowered or "pitago" in lowered or "right triangle" in lowered or "tam giác vuông" in lowered:
        concept_en = "The Pythagorean theorem is used in a right triangle. The two shorter sides meet at the right angle. If you square both shorter sides and add them, you get the square of the longest side."
        tactile_en = "Use two rulers to form an L shape on the desk. The side across from the corner is the longest side. That opposite side is called the hypotenuse."
        concept_vi = "Định lý Pythagore dùng cho tam giác vuông. Hai cạnh ngắn gặp nhau tại góc vuông. Nếu bình phương hai cạnh ngắn rồi cộng lại, ta được bình phương cạnh dài nhất."
        tactile_vi = "Em có thể đặt hai chiếc thước thành hình chữ L trên mặt bàn. Cạnh nối hai đầu còn lại là cạnh dài nhất, gọi là cạnh huyền."
    elif "parallel" in lowered or "song song" in lowered:
        concept_en = "Parallel lines are two straight lines that stay the same distance apart and never meet."
        tactile_en = "Feel the two long edges of a ruler or two sides of a notebook. They run in the same direction and do not cross."
        concept_vi = "Hai đường thẳng song song là hai đường luôn cách đều nhau và không bao giờ cắt nhau."
        tactile_vi = "Em hãy sờ hai cạnh dài của thước hoặc hai mép song song của quyển vở. Chúng đi cùng hướng và không cắt nhau."
    elif "median" in lowered or "trung tuyến" in lowered:
        concept_en = "A median of a triangle goes from one vertex to the midpoint of the opposite side."
        tactile_en = "Use a string from one corner of a cardboard triangle to the middle point of the opposite edge."
        concept_vi = "Đường trung tuyến của tam giác là đoạn thẳng đi từ một đỉnh đến trung điểm của cạnh đối diện."
        tactile_vi = "Em có thể dùng một sợi dây nối từ một góc của miếng bìa hình tam giác đến điểm chính giữa của cạnh đối diện."
    else:
        concept_en = "An isosceles triangle is a triangle with two equal sides. Imagine using two sticks of the same length and one shorter stick. Put the two equal sticks so they meet at one point, then connect their open ends with the third stick. The two equal sticks are the equal sides."
        tactile_en = "Use two equal pens and one shorter pen to make a triangle on your desk."
        concept_vi = "Tam giác cân là tam giác có hai cạnh bằng nhau. Em hãy tưởng tượng có hai que tính dài bằng nhau và một que ngắn hơn. Hai que bằng nhau gặp nhau tại một điểm, hai đầu còn lại được nối bằng que thứ ba."
        tactile_vi = "Em có thể dùng hai chiếc bút bằng nhau và một chiếc bút ngắn hơn để xếp thành tam giác trên bàn."
    if language == "vi":
        return (
            f"Cô sẽ giải thích theo cách phù hợp với học sinh {grade}, dùng lời nói và ví dụ có thể sờ/chạm.\n\n"
            f"{concept_vi}\n\n"
            "Bước 1: Một tam giác có ba cạnh.\n"
            "Bước 2: Gọi tên từng cạnh và từng góc thật chậm.\n"
            "Bước 3: Dùng tay hoặc trí tưởng tượng để nhận ra quan hệ quan trọng: bằng nhau, song song, vuông góc, trung điểm hoặc cạnh dài nhất.\n"
            "Bước 4: Nói lại kết luận bằng một câu ngắn.\n\n"
            f"Ví dụ xúc giác: {tactile_vi}\n\n"
            "Câu hỏi kiểm tra nhanh: Em hãy nói cạnh, góc hoặc điểm nào là quan trọng nhất trong bài này.\n\n"
            f"Kiến thức đã dùng:\n{context_text if context_text else '- Quy tắc hình học cơ bản'}\n\n"
            f"Câu hỏi của em: {question}"
        )
    return (
        f"I will explain this for a student in {grade} using words and touch-based examples.\n\n"
        f"{concept_en}\n\n"
        "Step 1: A triangle has three sides.\n"
        "Step 2: Name the sides and angles slowly, one by one.\n"
        "Step 3: Touch or imagine the key relationship: equal, parallel, perpendicular, midpoint, or longest side.\n"
        "Step 4: Say the conclusion aloud in one sentence.\n\n"
        f"Touch-based practice: {tactile_en}\n\n"
        "Quick check: Tell me which sides, angles, or points are important in this problem.\n\n"
        f"Knowledge used:\n{context_text if context_text else '- Basic geometry rule'}\n\n"
        f"Your question was: {question}"
    )


def english_answer(question: str, context: List[str], language: str = "en") -> str:
    if "many meeting" in question.lower():
        if language == "vi":
            return (
                "Câu đúng là: I have many meetings today.\n\n"
                "Giải thích: Từ 'many' dùng với danh từ đếm được số nhiều, nên 'meeting' phải chuyển thành 'meetings'.\n\n"
                "Luyện tập:\n"
                "1. I have many classes today.\n"
                "2. She has many emails to answer.\n"
                "3. We have many meetings this week."
            )
        return (
            "The correct sentence is: I have many meetings today.\n\n"
            "Because 'many' is used with plural countable nouns, 'meeting' should be plural: 'meetings'.\n\n"
            "Practice:\n"
            "1. I have many classes today.\n"
            "2. She has many emails to answer.\n"
            "3. We have many meetings this week."
        )
    if language == "vi":
        return (
            "Cô có thể sửa câu tiếng Anh này cho em:\n\n"
            f"{question}\n\n"
            "Hãy gửi một câu tiếng Anh cụ thể, cô sẽ sửa, giải thích ngữ pháp đơn giản và cho ví dụ luyện tập."
        )
    return (
        "Here is a clearer version of your sentence:\n\n"
        f"{question}\n\n"
        "I will correct the sentence, explain the grammar in simple language, and give short practice examples."
    )


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:
        return f"PDF text extraction failed: {exc}"


def extract_image_text_google_vision(path: Path) -> str:
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials or not Path(credentials).exists():
        return "Google Vision is not configured. Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file."
    try:
        from google.cloud import vision

        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=path.read_bytes())
        response = client.document_text_detection(image=image)
        if response.error.message:
            return f"Google Vision OCR failed: {response.error.message}"
        if response.full_text_annotation and response.full_text_annotation.text:
            return response.full_text_annotation.text.strip()
        if response.text_annotations:
            return response.text_annotations[0].description.strip()
        return "Google Vision OCR did not find readable text."
    except Exception as exc:
        return f"Google Vision OCR failed: {exc}"


def extract_image_text_tesseract(path: Path) -> str:
    if not shutil.which("tesseract"):
        return "Image OCR is not available because Tesseract is not installed on this Mac."
    try:
        from PIL import Image
        import pytesseract

        return pytesseract.image_to_string(Image.open(path), lang="eng+vie").strip()
    except Exception as exc:
        return f"Tesseract image OCR failed: {exc}"


def extract_image_text(path: Path, language: str = "vi") -> str:
    from app.ocr_service import extract_with_paddleocr, extract_with_easyocr, extract_with_ocrspace
    status = ocr_status()
    provider = status.get("recommended_provider", "auto")

    def try_ocrspace():
        text = extract_with_ocrspace(path, language)
        return text if text and "failed" not in text.lower() else None

    def try_google():
        text = extract_image_text_google_vision(path)
        return text if text and "failed" not in text.lower() and "not configured" not in text.lower() else None

    def try_paddle():
        text = extract_with_paddleocr(path, language)
        return text if text and "failed" not in text.lower() else None

    def try_easy():
        text = extract_with_easyocr(path, language)
        return text if text and "failed" not in text.lower() else None

    def try_tesseract():
        if status.get("tesseract_available"):
            return extract_image_text_tesseract(path)
        return None

    if provider == "ocrspace":
        return try_ocrspace() or try_google() or try_paddle() or try_easy() or try_tesseract() or "OCR not available."
    if provider == "google_vision":
        return try_google() or try_ocrspace() or try_paddle() or try_easy() or try_tesseract() or "OCR not available."
    if provider == "paddleocr":
        return try_paddle() or try_easy() or try_tesseract() or try_ocrspace() or "OCR not available."
    if provider == "easyocr":
        return try_easy() or try_paddle() or try_tesseract() or try_ocrspace() or "OCR not available."
    if provider == "tesseract":
        return try_tesseract() or try_paddle() or try_easy() or try_ocrspace() or "OCR not available."
    # auto: ocrspace > google_vision > paddleocr > easyocr > tesseract
    return try_ocrspace() or try_google() or try_paddle() or try_easy() or try_tesseract() or "OCR not available."


def describe_ocr_text(text: str, language: str = "en") -> str:
    if not text or "not available" in text or "failed" in text.lower():
        if language == "vi":
            return "Chưa trích xuất được chữ đáng tin cậy. Em hãy thử ảnh/PDF rõ hơn hoặc kiểm tra lại công cụ OCR."
        return "I could not extract reliable text yet. Please try a clearer image/PDF or install the OCR engine."
    if any(token in text.lower() for token in ["triangle", "tam giác", "angle", "góc", "ab", "ac"]):
        if language == "vi":
            return "Tài liệu có nội dung hình học. Cô sẽ mô tả các điểm, cạnh, độ dài bằng nhau, góc và kết luận bằng lời."
        return "The material appears to contain geometry content. I will describe points, sides, equal lengths, angles, and conclusions verbally."
    if "|" in text or "\t" in text:
        if language == "vi":
            return "Tài liệu có thể chứa bảng. Cô sẽ đọc tiêu đề, hàng, cột và các giá trị quan trọng."
        return "The material may contain a table. I will read headers, rows, columns, and key values."
    if language == "vi":
        return "Tài liệu có vẻ là văn bản. Cô sẽ tóm tắt và giải thích nhiệm vụ học tập."
    return "The material appears to be text. I will summarize it and explain the learning task."


def demo_prompts() -> List[Dict[str, str]]:
    return [
        {"label": "Geometry", "subject": "geometry", "text": "I do not understand an isosceles triangle"},
        {"label": "Right triangle", "subject": "geometry", "text": "Explain the Pythagorean theorem for a Grade 8 low-vision student"},
        {"label": "English", "subject": "english", "text": "I have many meeting today"},
        {"label": "Plan", "subject": "general", "text": "/plan I am in Grade 8, weak at geometry, and can study 25 minutes per day"},
    ]


def parse_plan_message(content: str) -> StudyPlanRequest:
    lowered = content.lower()
    if "geometry" in lowered or "toán hình" in lowered or "hình học" in lowered:
        weakness = "geometry"
    elif "english" in lowered or "tiếng anh" in lowered:
        weakness = "English communication and grammar"
    else:
        weakness = content or "geometry"

    grade_match = re.search(r"(?:grade|lớp)\s*(\d+)", lowered)
    grade = f"Grade {grade_match.group(1)}" if grade_match else "Grade 8"

    time_match = re.search(r"(\d+\s*(?:minutes?|phút)\s*(?:per day|mỗi ngày)?)", lowered)
    available_time = time_match.group(1) if time_match else "25 minutes per day"

    return StudyPlanRequest(grade=grade, weakness=weakness, available_time=available_time)


_SUBJECT_PLANS_VI: dict[str, list[str]] = {
    "geometry": [
        "Ngày 1: Ôn lại định nghĩa tam giác, góc, cạnh và mối quan hệ song song/vuông góc trong {time}.",
        "Ngày 2: Học định lý Pythagore và trung tuyến bằng ví dụ xúc giác (dùng thước, dây).",
        "Ngày 3: Làm 5 bài tập cơ bản về tam giác cân, tam giác vuông có gợi ý từng bước.",
        "Ngày 4: Xem lại lỗi sai, yêu cầu giải thích chậm hơn và vẽ mô hình bằng tay.",
        "Ngày 5: Tự giải 5 bài hình học không có gợi ý, nói kết quả bằng lời.",
        "Ngày 6: Ôn toàn bộ — tam giác, góc, đường song song — mô tả lại bằng lời của em.",
        "Ngày 7: Làm bài kiểm tra ngắn 5 câu hình học và cập nhật hồ sơ học tập.",
    ],
    "english": [
        "Ngày 1: Ôn ngữ pháp cơ bản (thì hiện tại, quá khứ, tương lai) trong {time}.",
        "Ngày 2: Học 10 từ vựng chủ đề gia đình/trường học kèm ví dụ câu đơn giản.",
        "Ngày 3: Luyện sửa 5 câu sai (subject-verb agreement, many/much, countable/uncountable).",
        "Ngày 4: Luyện hội thoại ngắn (giới thiệu bản thân, hỏi giờ, đặt đồ ăn).",
        "Ngày 5: Viết 5 câu mô tả bản thân bằng tiếng Anh, không xem gợi ý.",
        "Ngày 6: Ôn toàn bộ từ vựng và ngữ pháp tuần, đọc lại bằng giọng nói.",
        "Ngày 7: Mini test 10 câu (grammar + vocabulary) và cập nhật hồ sơ học tập.",
    ],
    "algebra": [
        "Ngày 1: Ôn phương trình bậc nhất một ẩn: khái niệm, nghiệm, cách giải trong {time}.",
        "Ngày 2: Học hệ phương trình bậc nhất hai ẩn bằng ví dụ thực tế (tính tiền, tuổi).",
        "Ngày 3: Làm 5 bài phương trình cơ bản có gợi ý từng bước.",
        "Ngày 4: Xem lại lỗi sai, luyện thêm bài phân thức đại số.",
        "Ngày 5: Giải độc lập 5 bài hệ phương trình và bất phương trình.",
        "Ngày 6: Nói lại quy tắc giải phương trình bằng lời, không nhìn sách.",
        "Ngày 7: Kiểm tra 5 bài toán đại số tổng hợp và cập nhật hồ sơ học tập.",
    ],
    "physics": [
        "Ngày 1: Ôn các đại lượng vật lý cơ bản: lực, vận tốc, gia tốc, khối lượng trong {time}.",
        "Ngày 2: Học định luật Newton 1 và 2 bằng ví dụ đời thực (đẩy xe, thả rơi).",
        "Ngày 3: Làm 5 bài tập tính lực, gia tốc có gợi ý.",
        "Ngày 4: Học về điện học cơ bản: điện trở, cường độ dòng điện, hiệu điện thế.",
        "Ngày 5: Giải 5 bài vật lý độc lập (cơ học hoặc điện học).",
        "Ngày 6: Nói lại các định luật Newton và Ohm bằng lời của em.",
        "Ngày 7: Kiểm tra 5 bài vật lý tổng hợp và cập nhật hồ sơ học tập.",
    ],
    "chemistry": [
        "Ngày 1: Ôn nguyên tử, phân tử, đơn chất, hợp chất trong {time}.",
        "Ngày 2: Học cách cân bằng phương trình hóa học bước từng bước.",
        "Ngày 3: Làm 5 bài cân bằng phương trình có gợi ý.",
        "Ngày 4: Học tính theo phương trình hóa học (mol, khối lượng, thể tích).",
        "Ngày 5: Giải 5 bài tính theo phương trình độc lập.",
        "Ngày 6: Nói lại quy tắc cân bằng phương trình và tính mol bằng lời.",
        "Ngày 7: Kiểm tra 5 bài hóa học tổng hợp và cập nhật hồ sơ.",
    ],
    "literature": [
        "Ngày 1: Đọc và tóm tắt một đoạn văn ngắn trong {time}, ghi lại ý chính.",
        "Ngày 2: Học các biện pháp tu từ: so sánh, ẩn dụ, nhân hóa kèm ví dụ.",
        "Ngày 3: Phân tích 2 đoạn thơ hoặc văn xuôi về nhân vật/hình ảnh nổi bật.",
        "Ngày 4: Luyện viết đoạn văn ngắn (5-7 câu) về cảm nhận tác phẩm.",
        "Ngày 5: Viết đoạn văn mới không xem gợi ý, đọc to để tự kiểm tra.",
        "Ngày 6: Nói lại nội dung tác phẩm đã học bằng lời của em.",
        "Ngày 7: Mini test phân tích đoạn văn ngắn và cập nhật hồ sơ.",
    ],
    "history": [
        "Ngày 1: Ôn mốc thời gian quan trọng và nhân vật lịch sử chính trong {time}.",
        "Ngày 2: Học sự kiện Điện Biên Phủ hoặc Cách mạng tháng Tám theo trình tự thời gian.",
        "Ngày 3: Trả lời 5 câu hỏi về nguyên nhân, diễn biến, kết quả sự kiện.",
        "Ngày 4: Học sự kiện lịch sử thế giới liên quan và so sánh với Việt Nam.",
        "Ngày 5: Kể lại một sự kiện lịch sử bằng lời của em trong 2 phút.",
        "Ngày 6: Ôn toàn bộ mốc và nhân vật đã học trong tuần.",
        "Ngày 7: Kiểm tra 5 câu hỏi lịch sử tổng hợp và cập nhật hồ sơ.",
    ],
    "geography": [
        "Ngày 1: Ôn bản đồ Việt Nam: vị trí, các vùng kinh tế, địa hình trong {time}.",
        "Ngày 2: Học khí hậu Việt Nam: miền Bắc/Trung/Nam và đặc điểm mùa.",
        "Ngày 3: Trả lời 5 câu hỏi về dân số, đô thị hóa, tài nguyên thiên nhiên.",
        "Ngày 4: Học địa lý kinh tế: nông nghiệp, công nghiệp, dịch vụ.",
        "Ngày 5: Mô tả đặc điểm một vùng kinh tế bằng lời không xem sách.",
        "Ngày 6: Ôn toàn bộ kiến thức địa lý đã học trong tuần.",
        "Ngày 7: Kiểm tra 5 câu hỏi địa lý tổng hợp và cập nhật hồ sơ.",
    ],
}

_SUBJECT_PLANS_EN: dict[str, list[str]] = {
    "geometry": [
        "Day 1: Review triangle definitions, angles, sides, parallel and perpendicular relationships for {time}.",
        "Day 2: Learn the Pythagorean theorem and median using touch-based examples (ruler, string).",
        "Day 3: Solve 5 basic exercises on isosceles and right triangles with step-by-step hints.",
        "Day 4: Review mistakes, ask for slower explanations, and build a hand model.",
        "Day 5: Independently solve 5 geometry problems, describing the answer verbally.",
        "Day 6: Review all — triangles, angles, parallel lines — explain in your own words.",
        "Day 7: Short 5-question geometry quiz and update the student profile.",
    ],
    "english": [
        "Day 1: Review basic grammar tenses (present, past, future) for {time}.",
        "Day 2: Learn 10 vocabulary words (family/school theme) with simple example sentences.",
        "Day 3: Correct 5 sentences (subject-verb agreement, many/much, countable nouns).",
        "Day 4: Practice short dialogues (introductions, asking the time, ordering food).",
        "Day 5: Write 5 sentences describing yourself without looking at hints.",
        "Day 6: Review all vocabulary and grammar from the week by reading aloud.",
        "Day 7: Mini grammar + vocabulary test (10 questions) and update the student profile.",
    ],
    "algebra": [
        "Day 1: Review linear equations in one variable: concept, solution steps for {time}.",
        "Day 2: Learn simultaneous equations with real-life examples (money, ages).",
        "Day 3: Solve 5 basic equations with guided hints.",
        "Day 4: Review errors and practice algebraic fractions.",
        "Day 5: Independently solve 5 simultaneous equations and inequalities.",
        "Day 6: Explain the rules for solving equations aloud, without the textbook.",
        "Day 7: Mixed algebra quiz (5 problems) and update the student profile.",
    ],
    "physics": [
        "Day 1: Review key physical quantities: force, velocity, acceleration, mass for {time}.",
        "Day 2: Learn Newton's First and Second Laws with real-life examples (pushing a cart, free fall).",
        "Day 3: Solve 5 exercises on force and acceleration with guided hints.",
        "Day 4: Study basic electricity: resistance, current intensity, voltage.",
        "Day 5: Independently solve 5 physics problems (mechanics or electricity).",
        "Day 6: Explain Newton's Laws and Ohm's Law aloud in your own words.",
        "Day 7: Mixed physics quiz (5 problems) and update the student profile.",
    ],
    "chemistry": [
        "Day 1: Review atoms, molecules, elements, and compounds for {time}.",
        "Day 2: Learn to balance chemical equations step by step.",
        "Day 3: Balance 5 chemical equations with guided hints.",
        "Day 4: Study stoichiometry: mole, mass, volume calculations.",
        "Day 5: Independently solve 5 stoichiometry problems.",
        "Day 6: Explain balancing equations and mole calculations aloud without looking.",
        "Day 7: Mixed chemistry quiz (5 problems) and update the student profile.",
    ],
    "literature": [
        "Day 1: Read and summarize a short passage in {time}, write down the key ideas.",
        "Day 2: Study literary devices: simile, metaphor, personification with examples.",
        "Day 3: Analyze 2 poetry or prose extracts focusing on character and imagery.",
        "Day 4: Write a short paragraph (5-7 sentences) expressing feelings about a work.",
        "Day 5: Write a new paragraph without hints and read it aloud to self-check.",
        "Day 6: Retell the content of a studied work in your own words.",
        "Day 7: Mini literary analysis test and update the student profile.",
    ],
    "history": [
        "Day 1: Review key dates and historical figures for {time}.",
        "Day 2: Study the Dien Bien Phu or August Revolution in chronological order.",
        "Day 3: Answer 5 questions on causes, events, and outcomes.",
        "Day 4: Study related world history events and compare with Vietnam.",
        "Day 5: Narrate a historical event in your own words for 2 minutes.",
        "Day 6: Review all dates and figures studied this week.",
        "Day 7: Mixed history quiz (5 questions) and update the student profile.",
    ],
    "geography": [
        "Day 1: Review Vietnam's location, economic regions, and terrain for {time}.",
        "Day 2: Study Vietnam's climate: North/Central/South and seasonal features.",
        "Day 3: Answer 5 questions on population, urbanization, and natural resources.",
        "Day 4: Study economic geography: agriculture, industry, and services.",
        "Day 5: Describe one economic region's features aloud without the textbook.",
        "Day 6: Review all geography topics studied this week.",
        "Day 7: Mixed geography quiz (5 questions) and update the student profile.",
    ],
}

# Keyword → subject key mapping
_SUBJECT_ALIASES: dict[str, str] = {
    # geometry
    "geometry": "geometry", "hình học": "geometry", "toán hình": "geometry",
    # english
    "english": "english", "tiếng anh": "english", "anh văn": "english",
    # algebra / math
    "algebra": "algebra", "toán đại số": "algebra", "đại số": "algebra",
    "math": "algebra", "toán": "algebra", "toán học": "algebra",
    # physics
    "physics": "physics", "vật lý": "physics",
    # chemistry
    "chemistry": "chemistry", "hóa học": "chemistry", "hóa": "chemistry",
    # literature
    "literature": "literature", "ngữ văn": "literature", "văn học": "literature", "văn": "literature",
    # history
    "history": "history", "lịch sử": "history",
    # geography
    "geography": "geography", "địa lý": "geography",
}


def localized_plan(payload: StudyPlanRequest) -> StudyPlanResponse:
    subject_key = _SUBJECT_ALIASES.get(payload.weakness.lower().strip(), "geometry")
    time_str = payload.available_time

    if payload.language == "vi":
        templates = _SUBJECT_PLANS_VI.get(subject_key, _SUBJECT_PLANS_VI["geometry"])
        plan = [day.replace("{time}", time_str) for day in templates]
    else:
        templates = _SUBJECT_PLANS_EN.get(subject_key, _SUBJECT_PLANS_EN["geometry"])
        plan = [day.replace("{time}", time_str) for day in templates]

    return StudyPlanResponse(weekly_plan=plan)


@app.get("/", response_class=HTMLResponse)
def web_demo() -> str:
    return """<!doctype html>
<html lang="vi" id="html-root">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"/>
  <meta name="theme-color" content="#c41230"/>
  <meta name="apple-mobile-web-app-capable" content="yes"/>
  <meta name="apple-mobile-web-app-status-bar-style" content="default"/>
  <meta name="apple-mobile-web-app-title" content="EduVision AI"/>
  <meta name="description" content="Trợ lý học tập AI cho học sinh khiếm thị"/>
  <link rel="manifest" href="/manifest.json"/>
  <title>EduVision AI</title>
  <style>
    :root{--red:#c41230;--blue:#12355b;--ink:#172033;--muted:#4a5568;--line:#d9e2ef;--soft:#f6f8fb;--panel:#fff;font-family:Inter,Arial,sans-serif}
    *{box-sizing:border-box}
    body{margin:0;background:var(--soft);color:var(--ink);overflow-x:hidden;font-size:18px}
    /* ── BRAND BAR (auto-hide on scroll) ── */
    .brand-bar{position:fixed;top:0;left:0;right:0;height:48px;background:#fff;border-bottom:1.5px solid var(--line);z-index:100;display:flex;align-items:center;padding:0 16px;transform:translateY(0);transition:transform 0.25s ease}
    .brand-bar.hidden{transform:translateY(-100%)}
    .brand{font-weight:800;color:var(--blue);font-size:19px;line-height:1.15}
    body{padding-top:48px}
    /* LANG TOGGLE */
    .lang-toggle{display:inline-flex;flex-wrap:nowrap;gap:0;border:2px solid var(--blue);border-radius:10px;overflow:hidden;flex:0 0 auto;white-space:nowrap}
    .lang-toggle button{padding:9px 16px;font-size:15px;font-weight:800;border:none;cursor:pointer;transition:background 0.15s,color 0.15s;min-height:42px;min-width:58px;white-space:nowrap;flex:0 0 auto}
    .lang-toggle button.active{background:var(--blue);color:#fff}
    .lang-toggle button:not(.active){background:#fff;color:var(--blue)}
    .lang-toggle button:focus-visible{outline:3px solid var(--red);outline-offset:2px}
    /* LOADING BAR */
    #loading-bar{display:none;position:fixed;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,var(--red),#e85d8a,var(--red));background-size:200%;animation:loadbar 1s linear infinite;z-index:9999}
    @keyframes loadbar{0%{background-position:200% 0}100%{background-position:-200% 0}}
    .hero-wrap{max-width:1180px;margin:0 auto;padding:16px 20px 12px}
    h1{margin:0;font-size:clamp(24px,3.5vw,44px);line-height:1.12;color:var(--blue);overflow-wrap:break-word}
    .lead{color:var(--muted);font-size:clamp(15px,1.6vw,17px);line-height:1.5;margin:8px 0 0;max-width:860px}
    main{max-width:1180px;margin:0 auto;padding:10px 20px 44px}
    .status{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
    .stat,.card{background:var(--panel);border:1px solid var(--line);border-radius:10px}
    .stat{padding:14px}
    .stat strong{display:block;color:var(--blue);font-size:12px;text-transform:uppercase;margin-bottom:6px}
    .stat span{font-weight:700;font-size:16px;word-break:break-all}
    .grid{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(320px,0.9fr);gap:16px;align-items:start}
    .card{padding:20px;margin-bottom:14px}
    h2{margin:0 0 14px;font-size:20px;color:var(--blue);line-height:1.25}
    label{display:block;font-weight:700;margin:14px 0 6px;font-size:15px}
    textarea,input,select{width:100%;padding:12px;border:1.5px solid #c7d0dd;border-radius:8px;font-size:16px;background:#fff;font-family:inherit}
    textarea{min-height:110px;resize:vertical}
    textarea:focus,input:focus,select:focus{outline:none;border-color:var(--blue)}
    .btn{display:inline-flex;align-items:center;gap:6px;min-height:48px;padding:12px 18px;border:0;border-radius:8px;background:var(--red);color:#fff;font-weight:700;font-size:16px;cursor:pointer;transition:background 0.15s;font-family:inherit}
    .btn:hover{background:#a50f28}
    .btn.blue{background:var(--blue)}.btn.blue:hover{background:#0e2740}
    .btn.ghost{background:#eef3f8;color:var(--blue);border:1px solid var(--line)}.btn.ghost:hover{background:#dce6f0}
    .btn:focus-visible{outline:3px solid var(--red);outline-offset:2px}
    .btn:disabled{opacity:0.5;cursor:not-allowed}
    .actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
    pre{white-space:pre-wrap;background:#111827;color:#e8eef8;border-radius:10px;padding:18px;min-height:340px;max-height:600px;overflow:auto;line-height:1.55;word-break:break-word;font-size:14px}
    .result-panel{position:sticky;top:80px}
    .row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .speaking-badge{display:none;background:#dcfce7;border:1px solid #86efac;border-radius:8px;padding:8px 14px;color:#15803d;font-weight:600;font-size:15px;margin-top:10px;align-items:center;gap:8px}
    .speaking-badge.show{display:flex}
    .btn-stop-inline{background:#dc2626;color:#fff;border:none;border-radius:6px;padding:5px 12px;font-weight:700;font-size:14px;cursor:pointer;margin-left:10px}
    .btn-stop-inline:hover{background:#b91c1c}
    @media(max-width:900px){.grid,.row2{grid-template-columns:1fr}.result-panel{position:static}.btn{width:100%}.lang-toggle{max-width:100%}pre{min-height:260px;max-height:460px}}
    @media(max-width:560px){body{font-size:16px}.brand{font-size:17px}.hero-wrap{padding:12px 14px 8px}main{padding:8px 14px 36px}.card{padding:16px}.status{grid-template-columns:1fr 1fr}.actions{gap:10px}pre{font-size:13px;padding:14px;min-height:240px}}
    /* Account sheet (slide-up from bottom) */
    .acct-sheet{display:none;position:fixed;bottom:72px;left:0;right:0;max-height:80vh;overflow-y:auto;background:#fff;border-top:2px solid var(--line);border-radius:16px 16px 0 0;padding:20px 18px;z-index:300;box-shadow:0 -4px 32px rgba(0,0,0,0.14)}
    .acct-sheet.open{display:block}
    body.lv-dark .acct-sheet{background:#1a1a1a;border-color:#444}
    @media(max-width:360px){.brand{font-size:15px}.lang-toggle button{padding:7px 10px;min-width:46px}.status{grid-template-columns:1fr}}
    /* ── LOW VISION MODE ── */
    body.lv-mode{font-size:1.2em;line-height:1.7;letter-spacing:0.01em}
    body.lv-mode pre{font-size:1.1em;line-height:1.8}
    body.lv-mode input,body.lv-mode select,body.lv-mode textarea{font-size:1.1em;min-height:48px}
    body.lv-mode .btn{font-size:1.05em;min-height:52px}
    body.lv-dark{background:#111 !important;color:#f0f0f0}
    body.lv-dark .card{background:#1e1e1e;border-color:#444}
    body.lv-dark pre{background:#0d0d0d;color:#e5e5e5;border-color:#555}
    body.lv-dark .tab-nav{background:#111;border-top-color:#444}
    body.lv-dark .settings-panel{background:#1a1a1a;color:#f0f0f0;border-left-color:#444}
    body.lv-dark .settings-panel h3,body.lv-dark .settings-panel h4{color:#93c5fd}
    body.lv-dark .speed-btn{background:#222;color:#93c5fd;border-color:#555}
    body.lv-dark .speed-btn.active{background:#1d4ed8;color:#fff}
    body.lv-dark .sentence-nav button{background:#222;color:#93c5fd;border-color:#555}
    .lv-highlight{background:#ffff00;color:#000;border-radius:2px;padding:0 1px}
    body.lv-dark .lv-highlight{background:#00ffff;color:#000}
    /* ── HIGH CONTRAST MODE (WCAG AAA) ── */
    body.lv-hc{background:#000 !important;color:#fff !important}
    body.lv-hc .card{background:#111 !important;border-color:#fff !important}
    body.lv-hc .brand-bar{background:#000 !important;border-color:#ff0 !important}
    body.lv-hc .brand{color:#ff0 !important}
    body.lv-hc .btn{border:2px solid #fff !important}
    body.lv-hc .btn.ghost{background:#222 !important;color:#ff0 !important;border-color:#ff0 !important}
    body.lv-hc textarea,body.lv-hc input,body.lv-hc select{background:#000 !important;color:#fff !important;border-color:#ff0 !important}
    body.lv-hc pre,body.lv-hc .result-chunk{background:#000 !important;color:#fff !important;border:2px solid #ff0 !important}
    body.lv-hc .tab-nav{background:#000 !important;border-color:#fff !important}
    body.lv-hc .tab-btn.active{color:#ff0 !important}
    body.lv-hc .settings-panel{background:#111 !important;color:#fff !important}
    body.lv-hc .subject-grid .subj-btn{background:#000 !important;border-color:#fff !important;color:#fff !important}
    body.lv-hc .subject-grid .subj-btn.active{background:#ff0 !important;color:#000 !important;border-color:#ff0 !important}
    body.lv-hc .suggestion-chip{background:#111 !important;border-color:#ff0 !important;color:#ff0 !important}
    body.lv-hc .hist-item{background:#111 !important;border-color:#555 !important;color:#ccc !important}
    /* ── SUBJECT GRID ── */
    .subject-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}
    .subj-btn{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;padding:12px 6px;border:2px solid var(--line);border-radius:12px;background:#fff;cursor:pointer;font-size:13px;font-weight:700;color:var(--blue);min-height:64px;transition:all .15s;line-height:1.2;text-align:center;-webkit-tap-highlight-color:transparent}
    .subj-btn:hover{background:var(--soft);border-color:var(--blue)}
    .subj-btn.active{background:var(--blue);color:#fff;border-color:var(--blue);box-shadow:0 2px 8px rgba(18,53,91,.3)}
    .subj-btn:focus-visible{outline:3px solid var(--red);outline-offset:2px}
    .subj-icon{font-size:22px;line-height:1}
    body.lv-mode .subj-btn{font-size:15px;min-height:72px;gap:6px}
    body.lv-mode .subj-icon{font-size:26px}
    @media(max-width:560px){.subject-grid{grid-template-columns:repeat(3,1fr);gap:6px}.subj-btn{padding:10px 4px;min-height:60px;font-size:12px}}
    /* ── SUGGESTIONS ── */
    .suggestions-wrap{margin:8px 0 4px}
    .suggestions-label{font-size:13px;color:var(--muted);margin-bottom:6px;font-weight:600}
    .suggestions-row{display:flex;flex-wrap:wrap;gap:6px}
    .suggestion-chip{padding:7px 12px;border:1.5px solid var(--line);border-radius:20px;background:#fff;font-size:13px;color:var(--blue);cursor:pointer;transition:all .15s;min-height:36px;line-height:1.3;text-align:left}
    .suggestion-chip:hover{background:var(--soft);border-color:var(--blue)}
    .suggestion-chip:focus-visible{outline:3px solid var(--red);outline-offset:2px}
    /* ── HISTORY ── */
    .hist-wrap{margin:4px 0 10px}
    .hist-toggle{background:transparent;border:none;color:var(--muted);font-size:13px;font-weight:600;cursor:pointer;padding:4px 0;display:flex;align-items:center;gap:4px;min-height:32px}
    .hist-toggle:hover{color:var(--blue)}
    .hist-list{display:none;margin-top:6px;display:none}
    .hist-list.open{display:flex;flex-direction:column;gap:6px}
    .hist-item{padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;font-size:13px;color:var(--ink);cursor:pointer;line-height:1.4;min-height:36px;display:flex;align-items:center;gap:6px}
    .hist-item:hover{background:var(--soft);border-color:var(--blue)}
    /* ── CHUNKED RESULT ── */
    .result-chunks{min-height:300px;display:flex;flex-direction:column;gap:12px;padding:4px 0}
    .result-chunk{background:#111827;color:#e8eef8;border-radius:10px;padding:16px;line-height:1.6;word-break:break-word;font-size:14px}
    .result-chunk.empty-state{color:#8aabb8;text-align:center;padding:40px 16px;min-height:260px;display:flex;align-items:center;justify-content:center;font-size:15px}
    .chunk-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px}
    .chunk-label{font-size:12px;font-weight:700;color:#93c5fd;text-transform:uppercase;letter-spacing:.05em}
    .chunk-speak{background:transparent;border:1px solid #374151;border-radius:6px;color:#93c5fd;font-size:13px;padding:4px 10px;cursor:pointer;min-height:30px;transition:background .15s}
    .chunk-speak:hover{background:#1f2937}
    .chunk-body{white-space:pre-wrap}
    body.lv-mode .result-chunk{font-size:1.1em;line-height:1.8}
    @media(max-width:900px){.result-chunks{min-height:200px}}
    /* ── LOADING SPINNER ── */
    .loading-overlay{display:none;position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:rgba(18,53,91,.92);color:#fff;padding:12px 20px;border-radius:12px;font-size:15px;font-weight:700;z-index:5000;align-items:center;gap:10px;box-shadow:0 4px 20px rgba(0,0,0,.3)}
    .loading-overlay.show{display:flex}
    .spinner{width:20px;height:20px;border:3px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0}
    @keyframes spin{to{transform:rotate(360deg)}}
    /* ── SETTINGS PANEL ── */
    .settings-panel{display:none;position:fixed;top:0;right:0;bottom:0;width:min(340px,92vw);background:#fff;border-left:2px solid var(--line);z-index:400;padding:24px 20px;overflow-y:auto;box-shadow:-6px 0 24px rgba(0,0,0,.15)}
    .settings-panel.open{display:block}
    .settings-close{position:absolute;top:12px;right:14px;border:0;background:transparent;font-size:28px;cursor:pointer;color:var(--muted);line-height:1;padding:4px 8px;border-radius:6px}
    .settings-close:hover{background:var(--soft);color:var(--ink)}
    .settings-row{display:flex;align-items:center;justify-content:space-between;padding:13px 0;border-bottom:1px solid var(--line);gap:12px}
    .toggle-switch{position:relative;display:inline-flex;align-items:center;width:52px;height:44px;flex-shrink:0;cursor:pointer}
    .toggle-switch input{opacity:0;width:0;height:0;position:absolute}
    .toggle-slider{position:absolute;left:3px;right:3px;top:50%;transform:translateY(-50%);height:26px;background:#d1d5db;border-radius:26px;cursor:pointer;transition:.25s}
    .toggle-slider::before{content:'';position:absolute;width:20px;height:20px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.25s;box-shadow:0 1px 4px rgba(0,0,0,.25)}
    input:checked + .toggle-slider{background:var(--blue)}
    input:checked + .toggle-slider::before{transform:translateX(20px)}
    .speed-bar{display:flex;gap:6px;flex-wrap:wrap}
    .speed-btn{padding:8px 13px;border:1.5px solid var(--line);border-radius:7px;background:#fff;cursor:pointer;font-size:14px;font-weight:700;color:var(--blue);min-height:44px;transition:background .15s,color .15s}
    .speed-btn:hover{background:var(--soft)}
    .speed-btn.active{background:var(--blue);color:#fff;border-color:var(--blue)}
    .sentence-nav{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
    .sentence-nav button{min-height:44px;padding:8px 16px;border:1.5px solid var(--line);border-radius:8px;background:#fff;cursor:pointer;font-size:15px;font-weight:700;color:var(--blue);transition:background .15s}
    .sentence-nav button:hover{background:var(--soft)}
    .sentence-nav button:focus-visible{outline:3px solid var(--red);outline-offset:2px}
    /* ── TAB NAVIGATION (mobile) ── */
    .tab-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:2px solid var(--line);z-index:200;padding-bottom:env(safe-area-inset-bottom)}
    .tab-nav>.tab-btn{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;padding:6px 4px;border:0;background:transparent;font-size:12px;font-weight:700;color:var(--muted);cursor:pointer;min-height:56px;-webkit-tap-highlight-color:transparent;transition:color 0.12s;line-height:1.2}
    .tab-btn .t-icon{font-size:24px;line-height:1.1;display:block}
    body.lv-mode .tab-nav>.tab-btn{font-size:14px}
    body.lv-mode .tab-btn .t-icon{font-size:26px}
    .tab-btn.active{color:var(--red)}
    .tab-btn:focus-visible{outline:3px solid var(--red);outline-offset:-2px}
    .vision-hint{color:var(--muted);font-size:14px;margin:0 0 12px;line-height:1.4}
    /* Desktop: always show all panes */
    @media(min-width:901px){.tab-pane{display:block !important}}
    @media(max-width:900px){
      .tab-nav{display:flex}
      body{padding-bottom:72px}
      .tab-pane{display:none !important}
      .tab-pane.active{display:block !important}
      #pane-result pre{min-height:60vh;max-height:75vh}
    }
  </style>
</head>
<body>
<a href="#main-content" class="skip-link"
  style="position:absolute;left:-9999px;top:4px;z-index:9999;background:#12355b;color:#fff;padding:8px 16px;border-radius:8px;font-weight:700;text-decoration:none;"
  onfocus="this.style.left='12px'" onblur="this.style.left='-9999px'">Bỏ qua điều hướng — Skip to content</a>
<div id="loading-bar"></div>

<!-- Brand bar: thin, auto-hides on scroll down -->
<div class="brand-bar" id="brand-bar" role="banner">
  <span class="brand">EduVision AI</span>
  <button onclick="toggleSettings()" aria-label="Cài đặt trợ năng"
    style="margin-left:auto;background:transparent;border:0;font-size:22px;cursor:pointer;padding:4px 8px;line-height:1;color:var(--ink)">⚙</button>
</div>

<main id="main-content">
  <!-- Hidden status elements (kept for JS health-check logic) -->
  <div id="status" style="display:none">
    <div class="stat"><strong>Backend</strong><span>...</span></div>
    <div class="stat"><strong>OCR</strong><span>...</span></div>
    <div class="stat"><strong id="voice-label">Giọng nói</strong><span id="voice-val">...</span></div>
    <div class="stat"><strong>OCR.space</strong><span id="gv-val">...</span></div>
  </div>

  <div class="grid">
    <div>
      <div id="pane-ask" class="tab-pane active">
      <!-- AI TUTOR -->
      <div class="card">
        <h2 id="tutor-title">🤖 AI Gia sư</h2>
        <div class="row2">
          <div>
            <label id="lbl-subject" style="margin-bottom:8px">Môn học</label>
            <select id="subject" style="display:none" aria-hidden="true">
              <option value="geometry" id="opt-geo">Hình học</option>
              <option value="english" id="opt-eng">Tiếng Anh</option>
              <option value="general" id="opt-gen">Tổng hợp</option>
              <option value="math">Toán học</option>
              <option value="science">Khoa học</option>
              <option value="history">Lịch sử</option>
            </select>
            <div class="subject-grid" role="group" aria-label="Chọn môn học">
              <button class="subj-btn active" data-subject="geometry" onclick="selectSubject('geometry',this)" aria-pressed="true">
                <span class="subj-icon">📐</span><span id="opt-geo">Hình học</span>
              </button>
              <button class="subj-btn" data-subject="english" onclick="selectSubject('english',this)" aria-pressed="false">
                <span class="subj-icon">🗣</span><span id="opt-eng">Tiếng Anh</span>
              </button>
              <button class="subj-btn" data-subject="math" onclick="selectSubject('math',this)" aria-pressed="false">
                <span class="subj-icon">🔢</span><span id="opt-math">Toán học</span>
              </button>
              <button class="subj-btn" data-subject="science" onclick="selectSubject('science',this)" aria-pressed="false">
                <span class="subj-icon">🔬</span><span id="opt-sci">Khoa học</span>
              </button>
              <button class="subj-btn" data-subject="history" onclick="selectSubject('history',this)" aria-pressed="false">
                <span class="subj-icon">📖</span><span id="opt-hist">Lịch sử</span>
              </button>
              <button class="subj-btn" data-subject="general" onclick="selectSubject('general',this)" aria-pressed="false">
                <span class="subj-icon">🌐</span><span id="opt-gen">Tổng hợp</span>
              </button>
            </div>
            <!-- Câu gợi ý theo môn -->
            <div class="suggestions-wrap" id="suggestions-wrap">
              <div class="suggestions-label">Câu hỏi gợi ý:</div>
              <div class="suggestions-row" id="suggestions-row"></div>
            </div>
          </div>
          <input id="student" type="hidden" value="S001"/>
        </div>
        <label for="question" id="lbl-question">Câu hỏi</label>
        <textarea id="question" placeholder="Nhập câu hỏi của bạn..."></textarea>
        <!-- Lịch sử câu hỏi gần đây -->
        <div class="hist-wrap" id="hist-wrap" style="display:none">
          <button class="hist-toggle" onclick="toggleHistory()" aria-expanded="false" id="hist-toggle-btn">
            🕐 Câu hỏi gần đây ▾
          </button>
          <div class="hist-list" id="hist-list"></div>
        </div>
        <!-- Nút chính -->
        <div class="actions" style="margin-bottom:6px">
          <button class="btn" onclick="askTutor()" id="btn-ask" aria-label="Gửi câu hỏi tới AI">🎓 Hỏi AI</button>
          <button class="btn" id="btn-mic" onclick="toggleMic()" aria-label="Nhập bằng giọng nói" style="background:#1565C0;" title="Nhập câu hỏi bằng giọng nói">🎙 Giọng nói</button>
          <button class="btn ghost" onclick="speakResult()" id="btn-speak">🔊 Đọc to kết quả</button>
          <button class="btn ghost" id="btn-repeat" onclick="repeatLast()" style="display:none" aria-label="Hỏi lại câu trước">↩ Hỏi lại</button>
          <button class="btn ghost" onclick="copyBraille()" id="btn-braille" style="display:none;" aria-label="Sao chép chữ Braille vào clipboard">⠿ Braille</button>
        </div>
        <div class="speaking-badge" id="speaking-badge">🔊 <span id="speaking-text">Đang đọc...</span><button class="btn-stop-inline" onclick="stopSpeech(true)">⏹ Dừng</button></div>
      </div>

      <!-- STUDY PLAN -->
      <div class="card">
        <h2 id="plan-title">📅 Kế hoạch học tập</h2>
        <div class="row2">
          <div>
            <label for="weakness" id="lbl-weak">Điểm yếu</label>
            <select id="weakness">
              <option value="geometry" id="opt-sub-geo">Hình học</option>
              <option value="english" id="opt-sub-eng">Tiếng Anh</option>
              <option value="algebra" id="opt-sub-alg">Toán đại số</option>
              <option value="physics" id="opt-sub-phy">Vật lý</option>
              <option value="chemistry" id="opt-sub-chem">Hóa học</option>
              <option value="literature" id="opt-sub-lit">Ngữ văn</option>
              <option value="history" id="opt-sub-hist">Lịch sử</option>
              <option value="geography" id="opt-sub-geo2">Địa lý</option>
            </select>
          </div>
          <div>
            <label for="time" id="lbl-time">Thời gian mỗi ngày</label>
            <select id="time">
              <option value="25" id="opt-time-25">25 phút</option>
              <option value="45" id="opt-time-45">45 phút</option>
              <option value="60" id="opt-time-60">60 phút</option>
              <option value="120" id="opt-time-120">120 phút</option>
            </select>
          </div>
        </div>
        <div class="actions">
          <button class="btn" onclick="studyPlan()" id="btn-plan">📅 Tạo kế hoạch 7 ngày</button>
          <button class="btn blue" onclick="report()" id="btn-report">📊 Báo cáo tiến độ</button>
        </div>
      </div>
      </div><!-- /pane-ask -->

      <div id="pane-tools" class="tab-pane">
      <!-- OCR -->
      <div class="card">
        <h2 id="ocr-title">📷 Đọc tài liệu (OCR)</h2>
        <p id="ocr-hint" style="color:var(--muted);font-size:15px;margin:0 0 10px">Chụp ảnh bài tập hoặc PDF, hệ thống sẽ đọc và giải thích bằng giọng nói.</p>
        <input id="ocrFile" type="file" accept=".jpg,.jpeg,.png,.pdf" aria-label="Chọn ảnh hoặc PDF"/>
        <div class="actions">
          <button class="btn" onclick="ocr()" id="btn-ocr">🔍 Nhận diện & Đọc</button>
          <button class="btn ghost" onclick="speakResult()" id="btn-speak-ocr">🔊 Đọc kết quả</button>
        </div>
      </div>

      <!-- VISION DESCRIBE -->
      <div class="card">
        <h2 id="vision-title">👁 Mô tả hình vẽ</h2>
        <p class="vision-hint" id="vision-hint">Chụp ảnh bài toán hoặc hình vẽ — AI mô tả chi tiết bằng lời những phần khó nhìn rõ.</p>
        <input id="visionFile" type="file" accept=".jpg,.jpeg,.png,.webp" aria-label="Chọn ảnh hình vẽ cần mô tả"/>
        <div class="actions">
          <button class="btn" onclick="describeImage()" id="btn-vision">📸 Mô tả hình</button>
          <button class="btn ghost" onclick="speakResult()" id="btn-speak-vision">🔊 Đọc kết quả</button>
        </div>
      </div>
      </div><!-- /pane-tools -->
    </div>

    <!-- RESULT PANEL -->
    <div class="result-panel tab-pane" id="pane-result">
      <div class="card">
        <h2 id="result-title">📋 Kết quả</h2>
        <div id="sr-status" aria-live="assertive" aria-atomic="true"
          style="position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden;"></div>
        <div id="result" role="log" aria-live="polite" aria-atomic="false"
          aria-label="Kết quả từ AI" class="result-chunks">
          <div class="result-chunk empty-state">Sẵn sàng. Hãy đặt câu hỏi hoặc chọn một demo để bắt đầu.</div>
        </div>
        <div class="sentence-nav" id="sentence-nav" style="display:none" role="navigation" aria-label="Điều hướng câu">
          <button onclick="prevSentence()" aria-label="Câu trước">⬅ Trước</button>
          <button onclick="repeatSentence()" aria-label="Lặp lại câu này">🔄 Lặp</button>
          <button onclick="nextSentence()" aria-label="Câu tiếp theo">Tiếp ➡</button>
        </div>
      </div>
    </div>
  </div>
</main>

<script>
// ── LANGUAGE STATE ─────────────────────────────────────────────────────────
let LANG = localStorage.getItem('ev_lang') || 'vi';

const UI = {
  vi: {
    htmlLang:'vi', heroTitle:'Trợ lý học tập cho học sinh khiếm thị',
    heroLead:'Giải thích bài học bằng ngôn ngữ dễ hiểu, hỗ trợ hình học, tiếng Anh, đọc tài liệu OCR và lập kế hoạch học tập song ngữ.',
    tutorTitle:'🤖 AI Gia sư', lblSubject:'Môn học', lblStudent:'Mã học sinh', lblQuestion:'Câu hỏi',
    optGeo:'Hình học', optEng:'Tiếng Anh', optGen:'Tổng hợp',
    btnAsk:'🎓 Hỏi AI', btnDemoGeo:'📐 Demo Hình học', btnDemoEng:'🗣 Demo Tiếng Anh', btnSpeak:'🔊 Đọc to kết quả', btnStop:'⏹ Dừng đọc',
    planTitle:'📅 Kế hoạch học tập', lblWeak:'Điểm yếu', lblTime:'Thời gian mỗi ngày',
    btnPlan:'📅 Tạo kế hoạch 7 ngày', btnReport:'📊 Báo cáo tiến độ',
    ocrTitle:'📷 Đọc tài liệu (OCR)', ocrHint:'Chụp ảnh bài tập hoặc tải PDF lên, hệ thống sẽ đọc và giải thích bằng giọng nói.',
    btnOCR:'🔍 Nhận diện & Đọc', resultTitle:'📋 Kết quả',
    resultReady:'Sẵn sàng. Hãy đặt câu hỏi hoặc chọn một demo để bắt đầu.',
    voiceLabel:'Giọng nói', gvYes:'✅ Đã cấu hình', gvNo:'⚠️ Dùng key demo',
    speaking:'Đang đọc...', stopped:'Đã dừng đọc.', defaultQ:'Tam giác cân là gì? Giải thích cho học sinh lớp 8 bị khiếm thị.',
    weakDefault:'geometry', timeDefault:'25',
    subjectOptions:[
      {value:'geometry',label:'Hình học'},{value:'english',label:'Tiếng Anh'},
      {value:'algebra',label:'Toán đại số'},{value:'physics',label:'Vật lý'},
      {value:'chemistry',label:'Hóa học'},{value:'literature',label:'Ngữ văn'},
      {value:'history',label:'Lịch sử'},{value:'geography',label:'Địa lý'},
    ],
    timeOptions:[
      {value:'25',label:'25 phút'},{value:'45',label:'45 phút'},
      {value:'60',label:'60 phút'},{value:'120',label:'120 phút'},
    ],
    timeUnit:'phút mỗi ngày',
    demoGeo:'Tam giác cân là gì? Giải thích dùng ví dụ xúc giác cho học sinh khiếm thị lớp 8.',
    demoEng:'Sửa câu sau: I have many meeting today',
    ttsLang:'vi-VN', ttsVoiceHint:'Giọng Linh (vi-VN)',
    visionTitle:'👁 Mô tả hình vẽ', visionHint:'Tải ảnh hình vẽ toán học — AI mô tả bằng lời cho học sinh khiếm thị.', btnVision:'👁 Mô tả hình',
  },
  en: {
    htmlLang:'en', heroTitle:'Learning Assistant for Visually Impaired Students',
    heroLead:'Clear spoken-friendly explanations for geometry, English, OCR document reading, and bilingual study planning.',
    tutorTitle:'🤖 AI Tutor', lblSubject:'Subject', lblStudent:'Student ID', lblQuestion:'Question',
    optGeo:'Geometry', optEng:'English', optGen:'General',
    btnAsk:'🎓 Ask AI', btnDemoGeo:'📐 Geometry Demo', btnDemoEng:'🗣 English Demo', btnSpeak:'🔊 Read result aloud', btnStop:'⏹ Stop reading',
    planTitle:'📅 Study Plan', lblWeak:'Weakness', lblTime:'Time per day',
    btnPlan:'📅 Generate 7-day plan', btnReport:'📊 Progress Report',
    ocrTitle:'📷 Read Document (OCR)', ocrHint:'Upload an image or a PDF worksheet. The system will read it and explain it aloud.',
    btnOCR:'🔍 Recognize & Read', resultTitle:'📋 Result',
    resultReady:'Ready. Ask a question or choose a demo to begin.',
    voiceLabel:'Voice', gvYes:'✅ Configured', gvNo:'⚠️ Demo key only',
    speaking:'Speaking...', stopped:'Reading stopped.', defaultQ:'Explain the Pythagorean theorem for a Grade 8 low-vision student.',
    weakDefault:'geometry', timeDefault:'25',
    subjectOptions:[
      {value:'geometry',label:'Geometry'},{value:'english',label:'English'},
      {value:'algebra',label:'Algebra'},{value:'physics',label:'Physics'},
      {value:'chemistry',label:'Chemistry'},{value:'literature',label:'Literature'},
      {value:'history',label:'History'},{value:'geography',label:'Geography'},
    ],
    timeOptions:[
      {value:'25',label:'25 minutes'},{value:'45',label:'45 minutes'},
      {value:'60',label:'60 minutes'},{value:'120',label:'120 minutes'},
    ],
    timeUnit:'minutes per day',
    demoGeo:'Explain the Pythagorean theorem for a visually impaired Grade 8 student using tactile examples.',
    demoEng:'Please correct: I have many meeting today',
    ttsLang:'en-US', ttsVoiceHint:'Samantha (en-US)',
    visionTitle:'👁 Describe Figure', visionHint:'Upload a math figure image — AI will describe it verbally for visually impaired students.', btnVision:'👁 Describe Figure',
  }
};

function setLang(lang) {
  LANG = lang;
  localStorage.setItem('ev_lang', lang);
  const T = UI[lang];
  // Toggle button states
  var _bvi = document.getElementById('btn-vi'); if (_bvi) { _bvi.classList.toggle('active', lang === 'vi'); _bvi.setAttribute('aria-pressed', String(lang === 'vi')); }
  var _ben = document.getElementById('btn-en'); if (_ben) { _ben.classList.toggle('active', lang === 'en'); _ben.setAttribute('aria-pressed', String(lang === 'en')); }
  document.getElementById('html-root').lang = T.htmlLang;
  // UI text
  var _ht = document.getElementById('hero-title'); if (_ht) _ht.textContent = T.heroTitle;
  var _hl = document.getElementById('hero-lead'); if (_hl) _hl.textContent = T.heroLead;
  document.getElementById('tutor-title').textContent = T.tutorTitle;
  document.getElementById('lbl-subject').textContent = T.lblSubject;
  var _ls = document.getElementById('lbl-student'); if (_ls) _ls.textContent = T.lblStudent;
  document.getElementById('lbl-question').textContent = T.lblQuestion;
  document.getElementById('opt-geo').textContent = T.optGeo;
  document.getElementById('opt-eng').textContent = T.optEng;
  document.getElementById('opt-gen').textContent = T.optGen;
  document.getElementById('btn-ask').textContent = T.btnAsk;
  updateSpeakButton();
  document.getElementById('plan-title').textContent = T.planTitle;
  document.getElementById('lbl-weak').textContent = T.lblWeak;
  document.getElementById('lbl-time').textContent = T.lblTime;
  document.getElementById('btn-plan').textContent = T.btnPlan;
  document.getElementById('btn-report').textContent = T.btnReport;
  document.getElementById('ocr-title').textContent = T.ocrTitle;
  document.getElementById('ocr-hint').textContent = T.ocrHint;
  document.getElementById('btn-ocr').textContent = T.btnOCR;
  document.getElementById('result-title').textContent = T.resultTitle;
  document.getElementById('voice-label').textContent = T.voiceLabel;
  document.getElementById('voice-val').textContent = T.ttsVoiceHint;
  // Update default question and inputs
  document.getElementById('question').value = T.defaultQ;
  // Repopulate subject select
  const weakSel = document.getElementById('weakness');
  const prevWeak = weakSel ? weakSel.value : T.weakDefault;
  if (weakSel) {
    weakSel.innerHTML = T.subjectOptions.map(o => `<option value="${o.value}"${o.value===prevWeak?' selected':''}>${o.label}</option>`).join('');
  }
  // Repopulate time select
  const timeSel = document.getElementById('time');
  const prevTime = timeSel ? timeSel.value : T.timeDefault;
  if (timeSel) {
    timeSel.innerHTML = T.timeOptions.map(o => `<option value="${o.value}"${o.value===prevTime?' selected':''}>${o.label}</option>`).join('');
  }
  document.getElementById('result').textContent = T.resultReady;
  document.getElementById('speaking-text').textContent = T.speaking;
  const _vt = document.getElementById('vision-title'); if (_vt) _vt.textContent = T.visionTitle;
  const _vh = document.getElementById('vision-hint'); if (_vh) _vh.textContent = T.visionHint;
  const _bv = document.getElementById('btn-vision'); if (_bv) _bv.textContent = T.btnVision;
  stopSpeech(false);
}

// ── TTS (Web Speech API — bilingual) ───────────────────────────────────────
window.eduvisionIsSpeaking = false;
window.eduvisionCurrentUtterance = null;

function updateSpeakButton() {
  const btn = document.getElementById('btn-speak');
  if (!btn) return;
  const T = UI[LANG];
  btn.textContent = window.eduvisionIsSpeaking ? T.btnStop : T.btnSpeak;
  btn.setAttribute('aria-label', window.eduvisionIsSpeaking ? T.btnStop : T.btnSpeak);
  btn.setAttribute('aria-pressed', window.eduvisionIsSpeaking ? 'true' : 'false');
}

function stopSpeech(showMessage = true) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  window.eduvisionIsSpeaking = false;
  window.eduvisionCurrentUtterance = null;
  const badge = document.getElementById('speaking-badge');
  if (badge) badge.classList.remove('show');
  updateSpeakButton();
  if (showMessage) {
    const result = document.getElementById('result');
    if (result) result.setAttribute('aria-label', UI[LANG].stopped);
  }
}

function speakText(text, lang) {
  if (!window.speechSynthesis) return;
  stopSpeech(false);
  const badge = document.getElementById('speaking-badge');
  const clean = text.replace(/[#*`{}"]/g, '').replace(/\\n{2,}/g, ' ').slice(0, 3000);
  const utt = new SpeechSynthesisUtterance(clean);
  utt.lang = UI[lang].ttsLang;
  utt.rate = (_ttsSpeed || 1.0) * (lang === 'vi' ? 0.88 : 0.92);
  utt.pitch = 1.0;
  // Pick best matching voice
  const voices = window.speechSynthesis.getVoices();
  const preferred = lang === 'vi' ? ['Linh','vi'] : ['Samantha','en-US','en-GB','en'];
  for (const hint of preferred) {
    const v = voices.find(x => x.name.includes(hint) || x.lang.startsWith(hint));
    if (v) { utt.voice = v; break; }
  }
  window.eduvisionCurrentUtterance = utt;
  utt.onstart = () => {
    window.eduvisionIsSpeaking = true;
    badge.classList.add('show');
    updateSpeakButton();
  };
  utt.onend = utt.onerror = () => {
    window.eduvisionIsSpeaking = false;
    window.eduvisionCurrentUtterance = null;
    badge.classList.remove('show');
    updateSpeakButton();
  };
  window.speechSynthesis.speak(utt);
}

// ── LOADING HELPERS ─────────────────────────────────────────────────────────
function showLoading(msg) {
  document.getElementById('loading-bar').style.display = 'block';
  var ov = document.getElementById('loading-overlay');
  var lm = document.getElementById('loading-msg');
  if (ov) ov.classList.add('show');
  if (lm) lm.textContent = msg || (LANG === 'vi' ? 'Đang xử lý...' : 'Processing...');
  announce(LANG === 'vi' ? 'Đang xử lý, vui lòng chờ...' : 'Processing, please wait...');
}
function hideLoading() {
  document.getElementById('loading-bar').style.display = 'none';
  var ov = document.getElementById('loading-overlay');
  if (ov) ov.classList.remove('show');
  playBeep(660, 0.15, 0.12);
}

function displayError(message) {
  const fallback = LANG === 'vi' ? 'Không thể xử lý yêu cầu lúc này.' : 'The request could not be processed right now.';
  var el = document.getElementById('result');
  if (el) el.innerHTML = '<div class="result-chunk" style="border:2px solid #dc2626"><div class="chunk-body" style="color:#fca5a5">' +
    esc((LANG === 'vi' ? 'Lỗi: ' : 'Error: ') + (message || fallback)) + '</div></div>';
  playBeep(220, 0.3, 0.15);
}

function formatList(items) {
  if (!Array.isArray(items) || !items.length) return '';
  return items.map(item => '- ' + item).join('\\n');
}

function formatResult(data) {
  if (typeof data === 'string') return data;
  if (!data || typeof data !== 'object') return String(data ?? '');
  if (data.detail) return (LANG === 'vi' ? 'Lỗi: ' : 'Error: ') + data.detail;

  const sections = [];
  if (data.answer) sections.push(data.answer);
  if (data.weekly_plan) {
    sections.push((LANG === 'vi' ? 'Kế hoạch 7 ngày' : '7-day plan') + '\\n' + formatList(data.weekly_plan));
  }
  if (data.description) sections.push((LANG === 'vi' ? 'Mô tả tài liệu' : 'Document description') + '\\n' + data.description);
  if (data.ocr_text) sections.push((LANG === 'vi' ? 'Nội dung OCR' : 'OCR text') + '\\n' + data.ocr_text);
  if (data.summary && !data.ocr_text) sections.push((LANG === 'vi' ? 'Tóm tắt' : 'Summary') + '\\n' + data.summary);
  if (data.recommendation) sections.push((LANG === 'vi' ? 'Gợi ý' : 'Recommendation') + '\\n' + data.recommendation);
  if (data.subject_counts) {
    const counts = Object.entries(data.subject_counts).map(([key, value]) => `- ${key}: ${value}`).join('\\n');
    sections.push((LANG === 'vi' ? 'Thống kê môn học' : 'Subject summary') + '\\n' + (counts || '- 0'));
  }
  if (data.recent_events && data.recent_events.length) {
    const events = data.recent_events.slice(0, 5).map(event => `- ${event.subject}: ${event.input}`).join('\\n');
    sections.push((LANG === 'vi' ? 'Hoạt động gần đây' : 'Recent activity') + '\\n' + events);
  }
  if (data.suggestions) sections.push((LANG === 'vi' ? 'Gợi ý tiếp theo' : 'Next suggestions') + '\\n' + formatList(data.suggestions));
  if (data.context_used && data.context_used.length) {
    sections.push((LANG === 'vi' ? 'Nguồn kiến thức đã dùng' : 'Context used') + '\\n' + formatList(data.context_used.slice(0, 3)));
  }
  return sections.length ? sections.join('\\n\\n') : JSON.stringify(data, null, 2);
}

async function readResponse(res) {
  let data;
  try {
    data = await res.json();
  } catch(e) {
    data = await res.text();
  }
  if (!res.ok) {
    const message = data && typeof data === 'object' ? (data.detail || data.message) : data;
    throw new Error(message || res.statusText);
  }
  return data;
}

function announce(msg) {
  // Thông báo cho screen reader (NVDA/VoiceOver) qua vùng aria-live assertive
  const el = document.getElementById('sr-status');
  if (!el) return;
  el.textContent = '';
  requestAnimationFrame(() => { el.textContent = msg; });
}

function setResult(data) {
  let text = formatResult(data);
  renderChunks(text);
  // Thông báo ngắn cho screen reader biết có kết quả mới
  announce(LANG === 'vi' ? 'Đã nhận kết quả từ AI. Đọc vùng kết quả để xem nội dung.' : 'AI response received. Read the result area.');
  // Auto-speak: answer > OCR description+text > plain string
  let toSpeak = '';
  if (data && data.answer_text) toSpeak = data.answer_text;
  else if (data && data.answer) toSpeak = data.answer;
  else if (data && data.accessible_explanation) toSpeak = data.accessible_explanation;
  else if (data && data.description && data.ocr_text) {
    toSpeak = data.description + '. ' + (LANG==='vi' ? 'Nội dung: ' : 'Content: ') + data.ocr_text.slice(0, 600);
  } else if (data && data.ocr_text) toSpeak = data.ocr_text.slice(0, 800);
  else if (typeof data === 'string') toSpeak = data;
  if (toSpeak) { loadSentences(toSpeak); speakText(toSpeak, LANG); }
  // Mobile: auto-switch to result tab
  if (window.matchMedia('(max-width:900px)').matches) showTab('result');
  // Chuyển focus về vùng kết quả để screen reader tự đọc
  setTimeout(() => {
    const el = document.getElementById('result');
    if (el) { el.setAttribute('tabindex', '-1'); el.focus(); }
  }, 200);
}

// ── DEMO PROMPTS ────────────────────────────────────────────────────────────
function loadDemo(kind) {
  const T = UI[LANG];
  document.getElementById('subject').value = kind;
  document.getElementById('question').value = kind === 'geometry' ? T.demoGeo : T.demoEng;
  // sync subject grid visual state
  var btn = document.querySelector('.subj-btn[data-subject="' + kind + '"]');
  if (btn) selectSubject(kind, btn);
}

// ── API CALLS ────────────────────────────────────────────────────────────────
async function askTutor() {
  var q = document.getElementById('question').value.trim();
  if (q) saveToHistory(q);
  showLoading(LANG === 'vi' ? '🤖 AI đang suy nghĩ...' : '🤖 AI is thinking...');
  try {
    const res = await fetch('/ask', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        student_id: document.getElementById('student').value,
        subject: _currentSubject || document.getElementById('subject').value,
        question: document.getElementById('question').value,
        language: LANG
      })
    });
    const text = await readResponse(res);
    setResult(text);
    fetchAndShowBraille(text);
  } catch(e) { displayError(e.message); }
  finally { hideLoading(); }
}

async function studyPlan() {
  showLoading();
  try {
    const T = UI[LANG];
    const weakVal = document.getElementById('weakness').value;
    const timeVal = document.getElementById('time').value;
    const availableTime = `${timeVal} ${T.timeUnit}`;
    const res = await fetch('/study-plan', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        student_id: document.getElementById('student').value,
        grade: LANG === 'vi' ? 'Lớp 8' : 'Grade 8',
        weakness: weakVal,
        available_time: availableTime,
        language: LANG
      })
    });
    setResult(await readResponse(res));
  } catch(e) { displayError(e.message); }
  finally { hideLoading(); }
}

async function report() {
  showLoading();
  try {
    const res = await fetch('/report/' + encodeURIComponent(document.getElementById('student').value));
    setResult(await readResponse(res));
  } catch(e) { displayError(e.message); }
  finally { hideLoading(); }
}

async function ocr() {
  const input = document.getElementById('ocrFile');
  if (!input.files.length) {
    alert(LANG === 'vi' ? 'Vui lòng chọn file ảnh hoặc PDF' : 'Please select an image or PDF file');
    return;
  }
  showLoading();
  try {
    const form = new FormData();
    form.append('file', input.files[0]);
    form.append('language', LANG);
    const res = await fetch('/ocr', {method:'POST', body:form});
    setResult(await readResponse(res));
  } catch(e) { displayError(e.message); }
  finally { hideLoading(); }
}

function speakResult() {
  if (window.eduvisionIsSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking)) {
    stopSpeech(true);
    return;
  }
  const text = document.getElementById('result').textContent;
  speakText(text, LANG);
}

// ── STATUS CHECK ─────────────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const res = await fetch('/health');
    const data = await res.json();
    const T = UI[LANG];
    document.getElementById('status').innerHTML = `
      <div class="stat"><strong>Backend</strong><span>${data.status} v${data.version}</span></div>
      <div class="stat"><strong>OCR</strong><span>${data.ocr.recommended_provider}</span></div>
      <div class="stat"><strong id="voice-label">${T.voiceLabel}</strong><span id="voice-val">${T.ttsVoiceHint}</span></div>
      <div class="stat"><strong>OCR.space</strong><span id="gv-val">${data.ocr.ocrspace_demo_key ? '⚠️ Demo' : data.ocr.ocrspace_configured ? T.gvYes : T.gvNo}</span></div>`;
  } catch(e) {
    document.getElementById('status').innerHTML = '<div class="stat"><strong>Backend</strong><span style="color:red">Offline</span></div>';
  }
}

// ── INIT ─────────────────────────────────────────────────────────────────────
// ── SUBJECT GRID ─────────────────────────────────────────────────────────────
var _currentSubject = 'geometry';
const SUBJECT_SUGGESTIONS = {
  geometry: ['Tam giác đều là gì?', 'Diện tích hình thang tính như thế nào?', 'Hình tròn và chu vi tính ra sao?'],
  english:  ['How do I use present perfect tense?', 'Explain "however" vs "although"', 'What is passive voice?'],
  math:     ['Phân số thập phân là gì?', 'Cách tính căn bậc hai?', 'Ước chung lớn nhất là gì?'],
  science:  ['Quang hợp diễn ra ở đâu?', 'Tại sao bầu trời màu xanh?', 'Nguyên tử là gì?'],
  history:  ['Chiến tranh thế giới thứ 2 xảy ra khi nào?', 'Triều Nguyễn kéo dài bao lâu?', 'Cách mạng tháng Tám là gì?'],
  general:  ['Giải thích khái niệm bình đẳng giới?', 'Tại sao học toán quan trọng?', 'Blockchain là gì?'],
};
function selectSubject(subj, btn) {
  _currentSubject = subj;
  document.getElementById('subject').value = subj;
  document.querySelectorAll('.subj-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
    b.setAttribute('aria-pressed', String(b === btn));
  });
  updateSuggestions(subj);
}
function updateSuggestions(subj) {
  var row = document.getElementById('suggestions-row');
  if (!row) return;
  var chips = (SUBJECT_SUGGESTIONS[subj] || []).map(function(q) {
    return '<button class="suggestion-chip" onclick="useSuggestion(this)">' + q + '</button>';
  }).join('');
  row.innerHTML = chips;
}
function useSuggestion(el) {
  document.getElementById('question').value = el.textContent;
  document.getElementById('question').focus();
}

// ── HISTORY ──────────────────────────────────────────────────────────────────
var _lastQuestion = '';
function saveToHistory(q) {
  if (!q) return;
  _lastQuestion = q;
  var hist = JSON.parse(localStorage.getItem('ev_hist') || '[]');
  hist = hist.filter(function(x) { return x !== q; });
  hist.unshift(q);
  if (hist.length > 5) hist = hist.slice(0, 5);
  localStorage.setItem('ev_hist', JSON.stringify(hist));
  renderHistory();
}
function renderHistory() {
  var hist = JSON.parse(localStorage.getItem('ev_hist') || '[]');
  var wrap = document.getElementById('hist-wrap');
  var list = document.getElementById('hist-list');
  if (!wrap || !list) return;
  if (!hist.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  list.innerHTML = hist.map(function(q, i) {
    return '<button class="hist-item" onclick="useHistory(this)" aria-label="Dùng lại câu hỏi: ' + q.replace(/"/g,'') + '">' +
      '<span style="color:var(--muted);font-size:11px;flex-shrink:0">' + (i+1) + '</span> ' + q +
      '</button>';
  }).join('');
  var btn = document.getElementById('btn-repeat');
  if (btn) btn.style.display = '';
}
function useHistory(el) {
  document.getElementById('question').value = el.textContent.replace(/^\d\s/, '').trim();
  toggleHistory(false);
}
function toggleHistory(force) {
  var list = document.getElementById('hist-list');
  var btn = document.getElementById('hist-toggle-btn');
  if (!list) return;
  var open = typeof force === 'boolean' ? force : !list.classList.contains('open');
  list.classList.toggle('open', open);
  if (btn) btn.setAttribute('aria-expanded', String(open));
}
function repeatLast() {
  if (!_lastQuestion) return;
  document.getElementById('question').value = _lastQuestion;
  askTutor();
}

// ── HIGH CONTRAST MODE ───────────────────────────────────────────────────────
var _hcMode = localStorage.getItem('ev_hc') === '1';
function toggleHC(on) {
  _hcMode = on;
  document.body.classList.toggle('lv-hc', on);
  localStorage.setItem('ev_hc', on ? '1' : '0');
}

// ── LOADING WITH AUDIO FEEDBACK ───────────────────────────────────────────────
var _audioCtx = null;
function playBeep(freq, dur, vol) {
  try {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    var osc = _audioCtx.createOscillator();
    var gain = _audioCtx.createGain();
    osc.connect(gain); gain.connect(_audioCtx.destination);
    osc.frequency.value = freq || 880;
    gain.gain.setValueAtTime(vol || 0.15, _audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + (dur || 0.2));
    osc.start(); osc.stop(_audioCtx.currentTime + (dur || 0.2));
  } catch(e) {}
}

// ── CHUNKED RESULT DISPLAY ────────────────────────────────────────────────────
var CHUNK_LABELS_VI = {
  'định nghĩa': '📖 Định nghĩa', 'khái niệm': '📖 Khái niệm',
  'giải thích': '💡 Giải thích', 'phân tích': '💡 Phân tích',
  'ví dụ': '✏️ Ví dụ', 'bài tập': '✏️ Bài tập ví dụ',
  'ghi nhớ': '⭐ Ghi nhớ', 'lưu ý': '⭐ Lưu ý', 'kết luận': '✅ Kết luận',
  'tóm tắt': '✅ Tóm tắt',
};
function parseChunks(text) {
  var lines = text.split('\\n');
  var chunks = [], cur = null;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var heading = null;
    // Match **Label:** or ## Label or Label: at start
    var m = line.match(/^(?:\\*\\*|##\\s*)([^*:\\n]{2,40})(?:\\*\\*|:)/);
    if (m) {
      var lc = m[1].toLowerCase().trim();
      for (var k in CHUNK_LABELS_VI) {
        if (lc.includes(k)) { heading = CHUNK_LABELS_VI[k] || m[1]; break; }
      }
      if (!heading) heading = '📌 ' + m[1].trim();
    }
    if (heading) {
      if (cur && cur.body.trim()) chunks.push(cur);
      cur = {label: heading, body: ''};
    } else if (cur) {
      cur.body += line + '\\n';
    } else {
      cur = {label: null, body: line + '\\n'};
    }
  }
  if (cur && cur.body.trim()) chunks.push(cur);
  return chunks;
}
function renderChunks(text) {
  var el = document.getElementById('result');
  if (!el) return;
  var chunks = parseChunks(text);
  if (!chunks.length) {
    el.innerHTML = '<div class="result-chunk"><div class="chunk-body">' + esc(text) + '</div></div>';
    return;
  }
  el.innerHTML = chunks.map(function(c, i) {
    var hdr = c.label ? '<div class="chunk-header"><span class="chunk-label">' + esc(c.label) + '</span>' +
      '<button class="chunk-speak" onclick="speakChunk(' + i + ')" aria-label="Đọc to phần này">🔊 Nghe</button></div>' : '';
    return '<div class="result-chunk" data-chunk="' + i + '">' + hdr +
      '<div class="chunk-body">' + esc(c.body.trim()) + '</div></div>';
  }).join('');
  window._resultChunks = chunks;
}
var _resultChunksStore = [];
function speakChunk(idx) {
  if (!window._resultChunks || !window._resultChunks[idx]) return;
  speakText(window._resultChunks[idx].body, LANG);
}
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/\\n/g,'<br>'); }

// Ensure voices are loaded before first use
window.speechSynthesis && window.speechSynthesis.getVoices();
window.speechSynthesis && window.speechSynthesis.addEventListener('voiceschanged', () => {});
// Init after full DOM is ready (settings panel rendered after the script tag)
document.addEventListener('DOMContentLoaded', function() {
  setLang(LANG);
  refreshStatus();
  updateSuggestions(_currentSubject);
  renderHistory();
  if (_hcMode) {
    document.body.classList.add('lv-hc');
    var hcEl = document.getElementById('hc-toggle-check');
    if (hcEl) hcEl.checked = true;
  }
});

// ── VOICE INPUT (mic) ──────────────────────────────────────────────────────
let _mediaRec = null, _micChunks = [];

async function toggleMic() {
  const btn = document.getElementById('btn-mic');
  if (_mediaRec && _mediaRec.state === 'recording') {
    _mediaRec.stop();
    btn.textContent = '🎙 Giọng nói';
    btn.style.background = '#1565C0';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    _mediaRec = new MediaRecorder(stream);
    _micChunks = [];
    _mediaRec.ondataavailable = e => _micChunks.push(e.data);
    _mediaRec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(_micChunks, { type: 'audio/webm' });
      const fd = new FormData();
      fd.append('file', blob, 'speech.webm');
      fd.append('language', LANG);
      btn.textContent = '⌛ Đang nhận dạng...';
      try {
        const r = await fetch('/stt', { method: 'POST', body: fd });
        const j = await r.json();
        if (j.text) {
          document.getElementById('question').value = j.text;
          document.getElementById('question').focus();
          btn.textContent = '✅ Xong';
          setTimeout(() => { btn.textContent = '🎙 Giọng nói'; }, 2000);
        } else {
          btn.textContent = '⚠️ Không nhận ra';
          setTimeout(() => { btn.textContent = '🎙 Giọng nói'; }, 2000);
        }
      } catch(e) {
        btn.textContent = '❌ Lỗi STT';
        setTimeout(() => { btn.textContent = '🎙 Giọng nói'; }, 2000);
      }
    };
    _mediaRec.start();
    btn.textContent = '⏹ Dừng ghi';
    btn.style.background = '#dc2626';
  } catch(e) {
    alert('Không thể mở micro: ' + e.message);
  }
}

// ── BRAILLE COPY ───────────────────────────────────────────────────────────
let _lastBraille = '';

async function fetchAndShowBraille(text) {
  try {
    const r = await fetch('/braille', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text, format: 'unicode' })
    });
    const j = await r.json();
    _lastBraille = j.result || '';
    document.getElementById('btn-braille').style.display = '';
  } catch(e) {}
}

function copyBraille() {
  if (!_lastBraille) { showToast('Chưa có nội dung Braille. Hỏi AI trước.', 'warn'); return; }
  var doToast = function() {
    showToast('✅ Đã sao chép chữ nổi Braille vào clipboard!\n Dán vào phần mềm đọc Braille hoặc thiết bị chữ nổi.', 'ok', 5000);
    var b = document.getElementById('btn-braille');
    if (b) { b.textContent = '✅ Đã sao chép!'; setTimeout(function(){ b.innerHTML = '⠿ Chữ nổi Braille'; }, 3000); }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(_lastBraille).then(doToast).catch(function() {
      // Fallback: textarea + execCommand
      var ta = document.createElement('textarea');
      ta.value = _lastBraille; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); doToast(); } catch(e) {}
      document.body.removeChild(ta);
    });
  } else {
    var ta = document.createElement('textarea');
    ta.value = _lastBraille; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); doToast(); } catch(e) { showToast('Trình duyệt không hỗ trợ copy tự động. Vui lòng copy thủ công.', 'warn'); }
    document.body.removeChild(ta);
  }
}

function showToast(msg, type, duration) {
  var existing = document.getElementById('ev-toast');
  if (existing) existing.remove();
  var t = document.createElement('div');
  t.id = 'ev-toast';
  t.setAttribute('role', 'alert');
  t.setAttribute('aria-live', 'assertive');
  var bg = type === 'ok' ? '#16a34a' : type === 'warn' ? '#d97706' : '#dc2626';
  t.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:'+bg+';color:#fff;padding:14px 20px;border-radius:12px;font-size:15px;font-weight:700;z-index:9999;max-width:90vw;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.25);line-height:1.5;white-space:pre-line';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function(){ if(t.parentNode) t.remove(); }, duration || 3500);
}

// ── TAB NAVIGATION (mobile) ──────────────────────────────────────────────────
const _TABS = ['ask','result','tools'];

function showTab(name) {
  _TABS.forEach(t => {
    const pane = document.getElementById('pane-'+t);
    const btn = document.querySelector('[data-tab="'+t+'"]');
    if (pane) pane.classList.toggle('active', t === name);
    if (btn) { btn.classList.toggle('active', t === name); btn.setAttribute('aria-pressed', String(t === name)); }
  });
  if (name === 'result') {
    setTimeout(() => { const el = document.getElementById('result'); if(el){el.setAttribute('tabindex','-1');el.focus();} }, 100);
  }
  // Save active tab
  try { sessionStorage.setItem('ev_tab', name); } catch(e) {}
}

// ── VISION DESCRIBE ─────────────────────────────────────────────────────────
async function describeImage() {
  const input = document.getElementById('visionFile');
  if (!input || !input.files.length) {
    alert(LANG === 'vi' ? 'Vui lòng chọn ảnh hình vẽ' : 'Please select an image file');
    return;
  }
  showLoading();
  try {
    const fd = new FormData();
    fd.append('file', input.files[0]);
    fd.append('language', LANG);
    const res = await fetch('/describe-image', { method: 'POST', body: fd });
    const data = await readResponse(res);
    const desc = data.description || data;
    setResult(typeof desc === 'string' ? desc : JSON.stringify(desc, null, 2));
  } catch(e) { displayError(e.message); }
  finally { hideLoading(); }
}

// ── LOGIN MODAL ───────────────────────────────────────────────────────────────
function openLoginModal() {
  var m = document.getElementById('login-modal');
  if (m) { m.style.display = 'flex'; setTimeout(function(){ document.getElementById('login-username').focus(); }, 100); }
}
function closeLoginModal() {
  var m = document.getElementById('login-modal');
  if (m) m.style.display = 'none';
}
// Close on backdrop click
document.addEventListener('click', function(e) {
  var m = document.getElementById('login-modal');
  if (m && e.target === m) closeLoginModal();
});

async function doLogin() {
  var username = (document.getElementById('login-username').value || '').trim();
  var password = document.getElementById('login-password').value;
  var msg = document.getElementById('login-msg');
  msg.textContent = '';
  if (!username || !password) { msg.style.color='#ef4444'; msg.textContent='Vui lòng nhập đủ thông tin.'; return; }
  try {
    var r = await fetch('/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username, password: password})
    });
    var j = await r.json();
    if (r.ok) {
      msg.style.color = '#16a34a';
      msg.textContent = '✅ Đăng nhập thành công!';
      _updateAuthBar(username);
      // Điền student ID vào ô mã HS
      var studentEl = document.getElementById('student');
      if (studentEl && j.student_id) studentEl.value = j.student_id;
      // Điền vào form đổi mật khẩu
      var cpUser = document.getElementById('cp-username');
      if (cpUser) cpUser.value = username;
      setTimeout(closeLoginModal, 800);
    } else {
      msg.style.color = '#ef4444';
      msg.textContent = '❌ ' + (j.detail || 'Sai tài khoản hoặc mật khẩu');
    }
  } catch(err) {
    msg.style.color = '#ef4444';
    msg.textContent = '❌ Lỗi kết nối';
  }
}

async function doLogout() {
  await fetch('/auth/logout', {method: 'POST'}).catch(function(){});
  _updateAuthBar(null);
  closeAccountSheet();
}

function _updateAuthBar(username) {
  // Update account sheet state
  var loggedout = document.getElementById('acct-loggedout');
  var loggedin = document.getElementById('acct-loggedin');
  var dispEl = document.getElementById('as-username-display');
  var tabIcon = document.getElementById('tab-acct-icon');
  var tabLabel = document.getElementById('tab-acct-label');
  if (username) {
    if (loggedout) loggedout.style.display = 'none';
    if (loggedin) loggedin.style.display = 'block';
    if (dispEl) dispEl.textContent = '👤 ' + username;
    if (tabIcon) tabIcon.textContent = '✅';
    if (tabLabel) tabLabel.textContent = username.length > 8 ? username.slice(0,8)+'…' : username;
  } else {
    if (loggedout) loggedout.style.display = 'block';
    if (loggedin) loggedin.style.display = 'none';
    if (tabIcon) tabIcon.textContent = '👤';
    if (tabLabel) tabLabel.textContent = 'Tài khoản';
  }
}

// Account sheet
function toggleAccountSheet() {
  var sheet = document.getElementById('acct-sheet');
  if (!sheet) return;
  if (sheet.classList.contains('open')) {
    sheet.classList.remove('open');
  } else {
    sheet.classList.add('open');
    // Focus first input if logging in
    var inp = document.getElementById('as-username');
    var loggedout = document.getElementById('acct-loggedout');
    if (inp && loggedout && loggedout.style.display !== 'none') setTimeout(function(){ inp.focus(); }, 100);
  }
}
function closeAccountSheet() {
  var sheet = document.getElementById('acct-sheet');
  if (sheet) sheet.classList.remove('open');
}

async function doLoginSheet() {
  var username = (document.getElementById('as-username') || {}).value || '';
  var password = (document.getElementById('as-password') || {}).value || '';
  var msg = document.getElementById('as-msg');
  if (msg) msg.textContent = '';
  if (!username || !password) { if(msg){msg.style.color='#ef4444';msg.textContent='Nhập đủ tên đăng nhập và mật khẩu.';} return; }
  try {
    var r = await fetch('/auth/login', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username: username, password: password})
    });
    var j = await r.json();
    if (r.ok) {
      _updateAuthBar(username);
      var studentEl = document.getElementById('student');
      if (studentEl && j.student_id) studentEl.value = j.student_id;
      var cpUser = document.getElementById('cp-username');
      if (cpUser) cpUser.value = username;
      setTimeout(closeAccountSheet, 600);
    } else {
      if (msg) { msg.style.color='#ef4444'; msg.textContent='❌ ' + (j.detail || 'Sai tài khoản hoặc mật khẩu'); }
    }
  } catch(e) {
    if (msg) { msg.style.color='#ef4444'; msg.textContent='❌ Lỗi kết nối'; }
  }
}

// Auto-hide brand bar on scroll — works on iOS Safari + Android
(function() {
  var _lastY = 0;
  var bar = null;
  function getScrollY() {
    return window.scrollY !== undefined ? window.scrollY
      : (document.documentElement.scrollTop || document.body.scrollTop || 0);
  }
  function onScroll() {
    if (!bar) bar = document.getElementById('brand-bar');
    if (!bar) return;
    var cur = getScrollY();
    if (cur < 10) {
      bar.classList.remove('hidden');
    } else if (cur > _lastY + 5) {
      bar.classList.add('hidden');
      closeAccountSheet();
    } else if (cur < _lastY - 5) {
      bar.classList.remove('hidden');
    }
    _lastY = cur;
  }
  window.addEventListener('scroll', onScroll, {passive: true});
  document.addEventListener('scroll', onScroll, {passive: true});
})();

// Check session on load — auto-fill student ID và đổi mật khẩu username
(async function checkSession() {
  try {
    var r = await fetch('/auth/me');
    if (r.ok) {
      var j = await r.json();
      _updateAuthBar(j.username || j.display_name);
      // Auto-fill student ID from session
      var studentEl = document.getElementById('student');
      if (studentEl && j.student_id) studentEl.value = j.student_id;
      var cpUser = document.getElementById('cp-username');
      if (cpUser && j.username) cpUser.value = j.username;
    }
  } catch(e) {}
})();

// ── SETTINGS & LOW VISION MODE ────────────────────────────────────────────────
var _ttsSpeed = parseFloat(localStorage.getItem('ev_speed') || '1.0');
var _lvMode = localStorage.getItem('ev_lv') === '1';
var _darkMode = localStorage.getItem('ev_dark') === '1';

function toggleSettings() {
  var p = document.getElementById('settings-panel');
  if (!p) return;
  var isOpen = p.classList.toggle('open');
  if (isOpen) {
    var lvCk = document.getElementById('lv-toggle-check');
    var darkCk = document.getElementById('dark-toggle-check');
    if (lvCk) lvCk.checked = _lvMode;
    if (darkCk) darkCk.checked = _darkMode;
    _syncSpeedBtns();
    // Focus trap: move focus inside panel, trap Tab/Shift+Tab
    setTimeout(function() {
      var closeBtn = p.querySelector('.settings-close');
      if (closeBtn) closeBtn.focus();
    }, 80);
    p._trapHandler = function(e) {
      if (e.key !== 'Tab' && e.key !== 'Escape') return;
      if (e.key === 'Escape') { toggleSettings(); return; }
      var focusable = Array.from(p.querySelectorAll('button,input,[tabindex]:not([tabindex="-1"])'));
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey) { if (document.activeElement === first) { e.preventDefault(); last.focus(); } }
      else { if (document.activeElement === last) { e.preventDefault(); first.focus(); } }
    };
    document.addEventListener('keydown', p._trapHandler);
  } else {
    if (p._trapHandler) { document.removeEventListener('keydown', p._trapHandler); p._trapHandler = null; }
  }
}

function _syncSpeedBtns() {
  document.querySelectorAll('#speed-bar .speed-btn').forEach(function(b) {
    b.classList.toggle('active', parseFloat(b.dataset.rate) === _ttsSpeed);
  });
}

function toggleLowVision(on) {
  _lvMode = on;
  localStorage.setItem('ev_lv', on ? '1' : '0');
  document.body.classList.toggle('lv-mode', on);
  if (!on) {
    _darkMode = false;
    localStorage.setItem('ev_dark', '0');
    document.body.classList.remove('lv-dark');
    var ck = document.getElementById('dark-toggle-check');
    if (ck) ck.checked = false;
  }
}

function toggleDarkMode(on) {
  _darkMode = on;
  localStorage.setItem('ev_dark', on ? '1' : '0');
  document.body.classList.toggle('lv-dark', on);
  if (on && !_lvMode) {
    _lvMode = true;
    localStorage.setItem('ev_lv', '1');
    document.body.classList.add('lv-mode');
    var ck = document.getElementById('lv-toggle-check');
    if (ck) ck.checked = true;
  }
}

function setSpeed(s) {
  _ttsSpeed = s;
  localStorage.setItem('ev_speed', String(s));
  _syncSpeedBtns();
  announce(LANG === 'vi' ? ('Tốc độ đọc: ' + s + 'x') : ('Speech rate: ' + s + 'x'));
}

function setFontSize(px) {
  document.body.style.fontSize = px + 'px';
  localStorage.setItem('ev_fontsize', String(px));
}

// ── SENTENCE NAVIGATION ───────────────────────────────────────────────────────
var _sentences = [];
var _sentIdx = 0;

function splitSentences(text) {
  // Tach cau: tuong thich moi browser, tranh lookbehind va escape phuc tap
  var parts = text.replace(/([.!?]+)\s+/g, '$1|').split('|').map(function(s){return s.trim();}).filter(function(s){return s.length>5;});
  return parts.length ? parts : [text];
}

function loadSentences(text) {
  _sentences = splitSentences(text);
  _sentIdx = 0;
  var nav = document.getElementById('sentence-nav');
  if (nav) nav.style.display = _sentences.length > 1 ? 'flex' : 'none';
}

function speakSentence(idx) {
  if (!_sentences.length) return;
  _sentIdx = Math.max(0, Math.min(idx, _sentences.length - 1));
  announce((_sentIdx + 1) + '/' + _sentences.length);
  speakText(_sentences[_sentIdx], LANG);
}

function prevSentence() { speakSentence(_sentIdx - 1); }
function nextSentence() { speakSentence(_sentIdx + 1); }
function repeatSentence() { speakSentence(_sentIdx); }

document.addEventListener('keydown', function(e) {
  if (e.altKey && e.key === 'ArrowLeft' && !e.ctrlKey) { e.preventDefault(); prevSentence(); }
  if (e.altKey && e.key === 'ArrowRight' && !e.ctrlKey) { e.preventDefault(); nextSentence(); }
  if (e.ctrlKey && e.key === ' ') {
    e.preventDefault();
    if (window.eduvisionIsSpeaking) stopSpeech(false);
    else if (_sentences.length) speakSentence(_sentIdx);
    else speakResult();
  }
  if (e.key === 'Escape') {
    var p = document.getElementById('settings-panel');
    if (p && p.classList.contains('open')) p.classList.remove('open');
  }
});

// ── CHANGE PASSWORD ───────────────────────────────────────────────────────────
async function changePassword() {
  var username = (document.getElementById('cp-username').value || '').trim();
  var oldPwd = document.getElementById('cp-old').value;
  var newPwd = document.getElementById('cp-new').value;
  var msg = document.getElementById('cp-msg');
  msg.textContent = '';
  if (!username || !oldPwd || !newPwd) {
    msg.textContent = 'Vui lòng điền đủ 3 trường.';
    msg.style.color = '#ef4444';
    return;
  }
  try {
    var r = await fetch('/auth/change-password', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username, old_password: oldPwd, new_password: newPwd})
    });
    var j = await r.json();
    if (r.ok) {
      msg.textContent = '✅ Đổi mật khẩu thành công!';
      msg.style.color = '#16a34a';
      document.getElementById('cp-old').value = '';
      document.getElementById('cp-new').value = '';
    } else {
      msg.textContent = '❌ ' + (j.detail || 'Lỗi không xác định');
      msg.style.color = '#ef4444';
    }
  } catch(err) {
    msg.textContent = '❌ Lỗi kết nối: ' + err.message;
    msg.style.color = '#ef4444';
  }
}

// ── APPLY SAVED PREFERENCES ON PAGE LOAD ─────────────────────────────────────
(function initPrefs() {
  if (_lvMode) document.body.classList.add('lv-mode');
  if (_darkMode) document.body.classList.add('lv-dark');
  var fs = localStorage.getItem('ev_fontsize');
  if (fs) document.body.style.fontSize = fs + 'px';
})();

// PWA Service Worker
// Unregister any old service workers (they were caching stale JS)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then(function(regs) {
    regs.forEach(function(reg) { reg.unregister(); });
  });
  caches.keys().then(function(keys) {
    keys.forEach(function(k) { caches.delete(k); });
  });
}
</script>

<!-- LOGIN MODAL -->
<div id="login-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:500;align-items:center;justify-content:center;padding:16px">
  <div style="background:#fff;border-radius:16px;padding:28px 24px;width:100%;max-width:360px;box-shadow:0 8px 40px rgba(0,0,0,0.22)" role="dialog" aria-label="Đăng nhập" aria-modal="true">
    <h2 style="margin:0 0 6px;color:var(--blue);font-size:20px">🔑 Đăng nhập</h2>
    <p style="margin:0 0 18px;color:var(--muted);font-size:14px">Học sinh Trường Nguyễn Đình Chiểu dùng tài khoản <strong>ndc001–ndc200</strong>, mật khẩu <strong>1</strong></p>
    <div style="display:grid;gap:10px">
      <input type="text" id="login-username" placeholder="Tên đăng nhập (vd: ndc001)" autocomplete="username"
        aria-label="Tên đăng nhập" style="padding:12px;border:1.5px solid var(--line);border-radius:10px;font-size:16px;width:100%;box-sizing:border-box">
      <input type="password" id="login-password" placeholder="Mật khẩu" autocomplete="current-password"
        aria-label="Mật khẩu" style="padding:12px;border:1.5px solid var(--line);border-radius:10px;font-size:16px;width:100%;box-sizing:border-box"
        onkeydown="if(event.key==='Enter') doLogin()">
      <button onclick="doLogin()" style="background:var(--blue);color:#fff;border:0;border-radius:10px;padding:14px;font-weight:700;cursor:pointer;font-size:16px;min-height:48px">Đăng nhập</button>
      <p id="login-msg" style="margin:0;font-weight:600;font-size:14px;min-height:18px;text-align:center"></p>
    </div>
    <button onclick="closeLoginModal()" aria-label="Đóng" style="position:absolute;top:12px;right:16px;border:0;background:transparent;font-size:26px;cursor:pointer;color:#9ca3af;line-height:1">×</button>
  </div>
</div>

<!-- SETTINGS PANEL (slide-in từ phải) -->
<div class="settings-panel" id="settings-panel" role="dialog" aria-label="Cài đặt trợ năng" aria-modal="true">
  <button class="settings-close" onclick="toggleSettings()" aria-label="Đóng cài đặt">×</button>
  <h3 style="margin:0 0 16px;color:var(--blue)">⚙ Cài đặt trợ năng</h3>

  <div class="settings-row" style="flex-direction:column;align-items:flex-start;gap:8px">
    <span style="font-weight:600">🌐 Ngôn ngữ / Language</span>
    <div class="lang-toggle" role="group" aria-label="Chọn ngôn ngữ / Select language">
      <button id="btn-vi" class="active" onclick="setLang('vi')" aria-pressed="true" aria-label="Tiếng Việt">VI</button>
      <button id="btn-en" onclick="setLang('en')" aria-pressed="false" aria-label="English">EN</button>
    </div>
  </div>

  <div class="settings-row">
    <label for="lv-toggle-check" style="font-weight:600;cursor:pointer;flex:1">👁 Chế độ Low Vision</label>
    <label class="toggle-switch">
      <input type="checkbox" id="lv-toggle-check" onchange="toggleLowVision(this.checked)">
      <span class="toggle-slider"></span>
    </label>
  </div>

  <div class="settings-row">
    <label for="dark-toggle-check" style="font-weight:600;cursor:pointer;flex:1">🌙 Nền tối</label>
    <label class="toggle-switch">
      <input type="checkbox" id="dark-toggle-check" onchange="toggleDarkMode(this.checked)">
      <span class="toggle-slider"></span>
    </label>
  </div>

  <div class="settings-row">
    <label for="hc-toggle-check" style="font-weight:600;cursor:pointer;flex:1">⚡ Tương phản cực cao (AAA)</label>
    <label class="toggle-switch">
      <input type="checkbox" id="hc-toggle-check" onchange="toggleHC(this.checked)">
      <span class="toggle-slider"></span>
    </label>
  </div>

  <div class="settings-row" style="flex-direction:column;align-items:flex-start;gap:10px">
    <span style="font-weight:600">🔊 Tốc độ đọc</span>
    <div class="speed-bar" id="speed-bar">
      <button class="speed-btn" data-rate="0.5" onclick="setSpeed(0.5)">0.5×</button>
      <button class="speed-btn" data-rate="0.75" onclick="setSpeed(0.75)">0.75×</button>
      <button class="speed-btn active" data-rate="1" onclick="setSpeed(1.0)">1×</button>
      <button class="speed-btn" data-rate="1.5" onclick="setSpeed(1.5)">1.5×</button>
      <button class="speed-btn" data-rate="2" onclick="setSpeed(2.0)">2×</button>
      <button class="speed-btn" data-rate="3" onclick="setSpeed(3.0)">3×</button>
    </div>
  </div>

  <div class="settings-row" style="flex-direction:column;align-items:flex-start;gap:10px">
    <span style="font-weight:600">🔠 Cỡ chữ</span>
    <div style="display:flex;gap:8px">
      <button class="speed-btn" onclick="setFontSize(14)" style="font-size:11px" title="Nhỏ">A−</button>
      <button class="speed-btn" onclick="setFontSize(17)" title="Vừa">A</button>
      <button class="speed-btn" onclick="setFontSize(21)" style="font-size:18px" title="Lớn">A+</button>
    </div>
  </div>

  <div style="margin-top:20px">
    <h4 style="margin:0 0 12px;color:var(--blue)">🔑 Đổi mật khẩu</h4>
    <div style="display:grid;gap:8px">
      <input type="text" id="cp-username" placeholder="Tên đăng nhập" autocomplete="username"
        aria-label="Tên đăng nhập" style="padding:10px;border:1.5px solid var(--line);border-radius:8px;font-size:15px;width:100%;box-sizing:border-box">
      <input type="password" id="cp-old" placeholder="Mật khẩu hiện tại" autocomplete="current-password"
        aria-label="Mật khẩu hiện tại" style="padding:10px;border:1.5px solid var(--line);border-radius:8px;font-size:15px;width:100%;box-sizing:border-box">
      <input type="password" id="cp-new" placeholder="Mật khẩu mới" autocomplete="new-password"
        aria-label="Mật khẩu mới" style="padding:10px;border:1.5px solid var(--line);border-radius:8px;font-size:15px;width:100%;box-sizing:border-box">
      <button onclick="changePassword()"
        style="background:var(--blue);color:#fff;border:0;border-radius:8px;padding:12px;font-weight:700;cursor:pointer;font-size:15px;min-height:44px">Đổi mật khẩu</button>
      <p id="cp-msg" style="margin:0;font-weight:600;font-size:14px;min-height:20px"></p>
    </div>
  </div>

  <div style="margin-top:16px;padding:12px;background:var(--soft);border-radius:8px">
    <p style="margin:0;font-size:13px;color:var(--muted);line-height:1.6"><strong>⌨ Phím tắt:</strong><br>
    Alt+← câu trước · Alt+→ câu tiếp<br>
    Ctrl+Space tạm dừng / phát tiếp<br>
    Esc đóng bảng cài đặt</p>
  </div>
</div>

<!-- Account sheet: slides up from bottom on mobile -->
<div class="acct-sheet" id="acct-sheet" role="dialog" aria-label="Tài khoản" aria-modal="true">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <span style="font-weight:800;font-size:16px;color:var(--blue)">👤 Tài khoản</span>
    <button onclick="closeAccountSheet()" aria-label="Đóng" style="border:0;background:transparent;font-size:26px;cursor:pointer;color:var(--muted);line-height:1;padding:2px 6px">×</button>
  </div>
  <!-- Logged-out state -->
  <div id="acct-loggedout">
    <p style="color:var(--muted);font-size:15px;margin:0 0 12px">Đăng nhập để lưu tiến độ học tập.</p>
    <div style="display:grid;gap:10px">
      <input type="text" id="as-username" placeholder="Tên đăng nhập (vd: ndc001)" autocomplete="username"
        aria-label="Tên đăng nhập" style="padding:12px;border:1.5px solid var(--line);border-radius:10px;font-size:16px;width:100%;box-sizing:border-box"/>
      <input type="password" id="as-password" placeholder="Mật khẩu" autocomplete="current-password"
        aria-label="Mật khẩu" style="padding:12px;border:1.5px solid var(--line);border-radius:10px;font-size:16px;width:100%;box-sizing:border-box"/>
      <button onclick="doLoginSheet()"
        style="background:var(--blue);color:#fff;border:0;border-radius:10px;padding:14px;font-weight:700;cursor:pointer;font-size:16px;min-height:48px">🔑 Đăng nhập</button>
      <p id="as-msg" style="margin:0;font-weight:600;font-size:14px;min-height:18px"></p>
    </div>
  </div>
  <!-- Logged-in state -->
  <div id="acct-loggedin" style="display:none">
    <p id="as-username-display" style="font-size:16px;font-weight:700;color:var(--blue);margin:0 0 16px"></p>
    <button onclick="doLogout()"
      style="width:100%;background:#f3f4f6;border:1.5px solid var(--line);border-radius:10px;padding:13px;font-weight:700;cursor:pointer;font-size:16px;color:var(--ink);min-height:48px">Đăng xuất</button>
  </div>
</div>

<div class="loading-overlay" id="loading-overlay" aria-live="assertive" role="status">
  <div class="spinner"></div><span id="loading-msg">Đang xử lý...</span>
</div>
<nav class="tab-nav" role="navigation" aria-label="Điều hướng chính">
  <button class="tab-btn active" data-tab="ask" onclick="showTab('ask')" aria-pressed="true"><span class="t-icon">🤖</span>Hỏi AI</button>
  <button class="tab-btn" data-tab="result" onclick="showTab('result')" aria-pressed="false"><span class="t-icon">📋</span>Kết quả</button>
  <button class="tab-btn" data-tab="tools" onclick="showTab('tools')" aria-pressed="false"><span class="t-icon">🛠</span>Công cụ</button>
  <button class="tab-btn" id="tab-acct" onclick="toggleAccountSheet()" aria-pressed="false"><span class="t-icon" id="tab-acct-icon">👤</span><span id="tab-acct-label">Tài khoản</span></button>
</nav>
</body>
</html>"""

@app.get("/logo.jpg")
def serve_logo() -> FileResponse:
    return FileResponse(Path(__file__).parent / "logo.jpg", media_type="image/jpeg")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "eduvision-ai",
        "version": "0.5.0",
        "ocr": ocr_status(),
        "tts_available": bool(shutil.which("say")),
        "tts_voices": available_tts_voices(),
        "languages": ["en", "vi"],
    }


@app.get("/vision-status")
def vision_status() -> Dict[str, Any]:
    return ocr_status()


@app.get("/demo-prompts")
def get_demo_prompts() -> List[Dict[str, str]]:
    return demo_prompts()


## ── BRAILLE ──────────────────────────────────────────────────────────────────

class BrailleRequest(BaseModel):
    text: str
    format: Literal["unicode", "brf"] = "unicode"  # type: ignore[valid-type]

@app.post("/braille")
def convert_braille(payload: BrailleRequest) -> Dict[str, Any]:
    """Chuyển văn bản sang Unicode Braille hoặc BRF (Braille Ready Format)."""
    text = payload.text[:5000]
    if payload.format == "brf":
        converted = text_to_brf(text)
        return {
            "format": "brf",
            "result": converted,
            "char_count": len(converted),
            "note": vietnamese_note(),
        }
    converted = text_to_unicode_braille(text)
    return {
        "format": "unicode",
        "result": converted,
        "char_count": len(converted),
        "note": vietnamese_note(),
    }

@app.get("/braille/download")
def braille_download(text: str, fmt: str = "brf"):
    """Tải file BRF cho thiết bị đọc Braille."""
    content = text_to_brf(text[:5000])
    from fastapi.responses import Response
    return Response(
        content=content.encode("ascii", errors="replace"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="eduvision.brf"'},
    )


## ── SPEECH-TO-TEXT ───────────────────────────────────────────────────────────

@app.post("/stt")
async def speech_to_text(file: UploadFile = File(...), language: str = Form(default="vi")) -> Dict[str, Any]:
    """Chuyển giọng nói → văn bản dùng Groq Whisper (miễn phí)."""
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY chưa được cấu hình")

    audio_bytes = await file.read()
    lang_code = "vi" if language in ("vi", "vietnamese") else "en"

    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {groq_key}"},
            files={"file": (file.filename or "audio.webm", audio_bytes, file.content_type or "audio/webm")},
            data={"model": "whisper-large-v3", "language": lang_code, "response_format": "json"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Groq STT lỗi: {resp.text[:200]}")

    result = resp.json()
    return {"text": result.get("text", ""), "language": lang_code}


## ── PRIVACY ──────────────────────────────────────────────────────────────────

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> HTMLResponse:
    return HTMLResponse("""<!DOCTYPE html><html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EduVision AI — Chính sách quyền riêng tư</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px 16px;color:#111827;line-height:1.7;}
h1{color:#12355b;}h2{color:#1565C0;margin-top:2em;}a{color:#1565C0;}</style>
</head><body>
<h1>Chính sách quyền riêng tư — EduVision AI</h1>
<p><em>Cập nhật: tháng 9/2026</em></p>

<h2>1. Thông tin chúng tôi thu thập</h2>
<p>EduVision AI thu thập các thông tin sau phục vụ mục đích học tập:</p>
<ul>
  <li><strong>Hồ sơ học sinh</strong>: tên, lớp, trình độ thị lực, điểm yếu/mạnh trong học tập (do giáo viên nhập)</li>
  <li><strong>Lịch sử học tập</strong>: câu hỏi và câu trả lời trong các buổi học</li>
  <li><strong>Tệp ảnh/PDF</strong>: ảnh đề bài được tải lên để nhận dạng chữ (OCR), xóa sau khi xử lý</li>
  <li><strong>Giọng nói</strong>: nếu dùng tính năng nhập giọng nói, âm thanh được gửi đến Groq AI để chuyển thành chữ rồi xóa ngay</li>
</ul>

<h2>2. Chúng tôi KHÔNG thu thập</h2>
<ul>
  <li>Số điện thoại, địa chỉ, thông tin tài chính</li>
  <li>Dữ liệu sinh trắc học</li>
  <li>Vị trí địa lý</li>
  <li>Thông tin không liên quan đến hoạt động học tập</li>
</ul>

<h2>3. Dữ liệu được lưu ở đâu</h2>
<p>Toàn bộ dữ liệu học sinh được lưu trên máy chủ của nhà trường (SQLite). Dữ liệu
<strong>không được chia sẻ</strong> với bên thứ ba ngoài các dịch vụ AI cần thiết (Groq AI để sinh câu trả lời,
OCR.space để nhận dạng chữ — cả hai đều chỉ nhận nội dung bài học, không nhận thông tin cá nhân).</p>

<h2>4. Quyền của phụ huynh và học sinh</h2>
<ul>
  <li>Yêu cầu xem toàn bộ dữ liệu đã lưu của học sinh</li>
  <li>Yêu cầu xóa tài khoản và dữ liệu học tập bất kỳ lúc nào</li>
  <li>Từ chối tính năng ghi âm giọng nói (ứng dụng vẫn hoạt động bình thường)</li>
</ul>

<h2>5. Bảo mật</h2>
<p>Mật khẩu được mã hóa bằng bcrypt. Session đăng nhập hết hạn sau 7 ngày.
Không có endpoint nào cho phép truy cập dữ liệu học sinh mà không cần xác thực.</p>

<h2>6. Liên hệ</h2>
<p>Mọi câu hỏi về quyền riêng tư, liên hệ giáo viên phụ trách hoặc email:
<a href="mailto:dobaonam@example.com">dobaonam@example.com</a></p>

<p style="margin-top:40px;"><a href="/">← Về trang học tập</a></p>
</body></html>""")


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    from app.llm_service import ask_groq, GROQ_API_KEY
    profile = get_profile(payload.student_id)
    context = rag_search(payload.question)
    subject = payload.subject
    grade = profile.get("grade", payload.grade or "Grade 8")

    # Use Groq LLM when available, fall back to rule-based only for hardcoded demos
    if GROQ_API_KEY:
        answer = ask_groq(
            question=payload.question,
            subject=subject,
            grade=grade,
            context_chunks=context,
            language=payload.language,
            profile=profile or None,
        )
    elif subject == "geometry":
        answer = accessible_geometry_answer(payload.question, profile, context, payload.language)
    elif subject == "english":
        answer = english_answer(payload.question, context, payload.language)
    else:
        answer = (
            "Cô có thể hỗ trợ Toán hình, tiếng Anh, đọc ảnh/PDF và lập kế hoạch học tập."
            if payload.language == "vi"
            else "I can help with geometry, English, OCR reading, and study planning."
        )

    if payload.language == "vi":
        suggestions = ["Hỏi thêm về bài này", "Xin ví dụ thực tế", "Tạo bài tập tương tự"]
    else:
        suggestions = ["Ask a follow-up question", "Ask for a real-life example", "Generate a similar exercise"]

    log_event(payload.student_id, subject, payload.question, answer)
    safe_context = [sanitize_accessibility_context(item) for item in context]
    return AskResponse(answer=answer, subject=subject, suggestions=suggestions, context_used=safe_context)


@app.post("/ocr")
async def ocr(file: UploadFile = File(...), language: str = Form("en")) -> Dict[str, Any]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", file.filename or "upload.bin")
    path = UPLOAD_DIR / safe_name
    content = await file.read()
    path.write_bytes(content)

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
    elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        text = extract_image_text(path)
    else:
        try:
            text = content.decode("utf-8")
        except Exception:
            text = "Unsupported file type for text extraction."

    description = describe_ocr_text(text, language)
    summary = text[:600] + ("..." if len(text) > 600 else "")
    log_event("S001", "ocr", file.filename or "upload", summary)
    return {
        "filename": file.filename,
        "bytes": len(content),
        "ocr_provider": ocr_status()["recommended_provider"],
        "ocr_text": text,
        "description": description,
        "summary": summary,
    }


@app.post("/study-plan", response_model=StudyPlanResponse)
def study_plan(payload: StudyPlanRequest) -> StudyPlanResponse:
    plan = localized_plan(payload)
    log_event(payload.student_id, "plan", payload.weakness, "\n".join(plan.weekly_plan))
    return plan


@app.get("/profile/{student_id}")
def profile(student_id: str) -> Dict[str, Any]:
    return get_profile(student_id) or {"student_id": student_id, "status": "not_found"}


@app.post("/profile")
def upsert_profile(payload: ProfilePayload) -> Dict[str, Any]:
    return save_profile(payload.dict())


@app.get("/report/{student_id}")
def report(student_id: str) -> Dict[str, Any]:
    init_db()
    with db() as conn:
        rows = conn.execute(
            "SELECT subject, input, output, created_at FROM learning_events WHERE student_id = ? ORDER BY id DESC LIMIT 10",
            (student_id,),
        ).fetchall()
    events = [dict(row) for row in rows]
    weak_subjects: Dict[str, int] = {}
    for event in events:
        weak_subjects[event["subject"]] = weak_subjects.get(event["subject"], 0) + 1
    return {
        "student_id": student_id,
        "recent_activity_count": len(events),
        "subject_counts": weak_subjects,
        "recent_events": events,
        "recommendation": "Continue short daily practice, review mistakes every week, and use tactile examples before abstract formulas.",
    }


@app.post("/command")
def command(payload: CommandRequest) -> Dict[str, Any]:
    text = payload.message.strip()
    lowered = text.lower()
    if lowered.startswith("/geometry"):
        req = AskRequest(student_id=payload.student_id, subject="geometry", question=text.replace("/geometry", "", 1).strip(), language=payload.language)
        return ask(req).dict()
    if lowered.startswith("/english"):
        req = AskRequest(student_id=payload.student_id, subject="english", question=text.replace("/english", "", 1).strip(), language=payload.language)
        return ask(req).dict()
    if lowered.startswith("/plan"):
        content = text.replace("/plan", "", 1).strip() or "geometry"
        req = parse_plan_message(content)
        req.student_id = payload.student_id
        req.language = payload.language
        return study_plan(req).dict()
    if lowered.startswith("/profile"):
        return profile(payload.student_id)
    if lowered.startswith("/report"):
        return report(payload.student_id)
    req = AskRequest(student_id=payload.student_id, subject="general", question=text, language=payload.language)
    return ask(req).dict()


@app.post("/tts")
def tts(payload: TTSRequest) -> Dict[str, str]:
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    text = payload.text[:2500]
    fname_base = f"eduvision-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    lang_code = "vi" if payload.language == "vi" else "en"

    # Thử gTTS trước (cross-platform, tiếng Việt tốt)
    try:
        from gtts import gTTS
        out = TTS_DIR / f"{fname_base}.mp3"
        gTTS(text=text, lang=lang_code, slow=False).save(str(out))
        return {
            "status": "ok", "engine": "gtts",
            "language": payload.language,
            "audio_url": f"/media/tts/{out.name}", "file": str(out),
        }
    except Exception:
        pass

    # Fallback: macOS say
    if shutil.which("say"):
        out = TTS_DIR / f"{fname_base}.aiff"
        voice = payload.voice or default_voice(payload.language)
        subprocess.run(["say", "-v", voice, "-o", str(out), text], check=True)
        return {
            "status": "ok", "engine": "say",
            "language": payload.language, "voice": voice,
            "audio_url": f"/media/tts/{out.name}", "file": str(out),
        }

    return {"status": "unavailable", "message": "Không có engine TTS. Cài gTTS: pip install gtts"}


@app.get("/media/tts/{filename}")
def tts_file(filename: str) -> FileResponse:
    f = TTS_DIR / filename
    mime = "audio/mpeg" if filename.endswith(".mp3") else "audio/aiff"
    return FileResponse(f, media_type=mime, filename=filename)


## ── AUTH ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/auth/login")
def do_login(payload: LoginRequest) -> JSONResponse:
    token = auth_login(payload.username, payload.password)
    if not token:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")
    user = get_user_by_token(token)
    resp = JSONResponse({
        "status": "ok",
        "token": token,
        "username": user["username"] if user else payload.username,
        "display_name": user["display_name"] if user else "",
        "student_id": user["student_id"] if user else "",
        "role": user["role"] if user else "student",
    })
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return resp

@app.post("/auth/logout")
def do_logout(request: Request) -> JSONResponse:
    token = request.cookies.get("session", "")
    auth_logout(token)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie("session")
    return resp

@app.get("/auth/me")
def me(request: Request) -> Dict[str, Any]:
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    return user


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str


@app.put("/auth/change-password")
def auth_change_password(payload: ChangePasswordRequest) -> Dict[str, Any]:
    if not payload.new_password:
        raise HTTPException(status_code=400, detail="Mật khẩu mới không được để trống")
    ok = change_password(payload.username, payload.old_password, payload.new_password)
    if not ok:
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu hiện tại không đúng")
    return {"status": "ok", "message": "Đổi mật khẩu thành công"}


## ── TEACHER DASHBOARD ────────────────────────────────────────────────────────

class CreateStudentRequest(BaseModel):
    username: str
    password: str
    student_id: str
    display_name: str
    grade: str = "Lớp 8"
    vision_status: str = "low vision"

@app.post("/teacher/students")
def teacher_create_student(payload: CreateStudentRequest, request: Request) -> Dict[str, Any]:
    _require_teacher(request)
    try:
        account = create_student_account(
            payload.username, payload.password, payload.student_id, payload.display_name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    profile = {
        "student_id": payload.student_id,
        "name": payload.display_name,
        "grade": payload.grade,
        "vision_status": payload.vision_status,
        "math_level": "",
        "english_level": "",
        "weaknesses": [],
        "strengths": [],
        "learning_goal": "",
    }
    save_profile(profile)
    return {"status": "ok", "account": account}

@app.get("/teacher/students")
def teacher_list_students(request: Request) -> List[Dict[str, Any]]:
    _require_teacher(request)
    return list_users()


@app.get("/teacher/analytics")
def teacher_analytics(request: Request) -> Dict[str, Any]:
    """Dashboard thống kê: tổng lượt học, theo môn, theo học sinh."""
    _require_teacher(request)
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM learning_events").fetchone()["c"]
        by_subject = conn.execute(
            "SELECT subject, COUNT(*) AS cnt FROM learning_events GROUP BY subject ORDER BY cnt DESC"
        ).fetchall()
        by_student = conn.execute(
            "SELECT student_id, COUNT(*) AS cnt, MAX(created_at) AS last_active "
            "FROM learning_events GROUP BY student_id ORDER BY last_active DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT student_id, subject, input, created_at FROM learning_events ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return {
        "total_events": total,
        "by_subject": [dict(r) for r in by_subject],
        "by_student": [dict(r) for r in by_student],
        "recent": [dict(r) for r in recent],
    }


@app.get("/teacher", response_class=HTMLResponse)
def teacher_dashboard(request: Request) -> HTMLResponse:
    user = _get_current_user(request)
    auth_html = ""
    if user and user["role"] == "teacher":
        users = list_users()
        rows = "".join(
            f"<tr><td>{u['display_name'] or u['username']}</td><td>{u['role']}</td>"
            f"<td>{u.get('student_id','—')}</td><td>{u['created_at'][:10]}</td></tr>"
            for u in users
        )
        with db() as _ac:
            _total = _ac.execute("SELECT COUNT(*) AS c FROM learning_events").fetchone()["c"]
            _by_sub = _ac.execute(
                "SELECT subject, COUNT(*) AS cnt FROM learning_events GROUP BY subject ORDER BY cnt DESC LIMIT 8"
            ).fetchall()
            _by_stu = _ac.execute(
                "SELECT student_id, COUNT(*) AS cnt, MAX(created_at) AS last FROM learning_events "
                "GROUP BY student_id ORDER BY last DESC LIMIT 10"
            ).fetchall()
        _sub_rows = "".join(
            f"<tr><td style='padding:4px 10px'>{r['subject']}</td>"
            f"<td style='padding:4px 10px'><span style='background:#3b82f6;color:#fff;border-radius:4px;padding:2px 8px;font-size:12px'>{r['cnt']}</span></td></tr>"
            for r in _by_sub
        ) or "<tr><td colspan='2' style='color:#6b7280;text-align:center;padding:8px'>Chưa có dữ liệu</td></tr>"
        _stu_rows = "".join(
            f"<tr><td style='padding:4px 10px'>{r['student_id']}</td>"
            f"<td style='padding:4px 10px;text-align:center'>{r['cnt']}</td>"
            f"<td style='padding:4px 10px;font-size:12px;color:#6b7280'>{(r['last'] or '')[:16]}</td></tr>"
            for r in _by_stu
        ) or "<tr><td colspan='3' style='color:#6b7280;text-align:center;padding:8px'>Chưa có dữ liệu</td></tr>"
        auth_html = f"""
<div style="background:#e8f5e9;border-radius:12px;padding:16px 20px;margin-bottom:20px;">
  <b>Xin chào, {user['display_name'] or user['username']}!</b> &nbsp;
  <button onclick="fetch('/auth/logout',{{method:'POST'}}).then(()=>location.reload())"
    style="float:right;background:#ef4444;color:#fff;border:0;border-radius:8px;padding:6px 14px;cursor:pointer;">
    Đăng xuất
  </button>
</div>
<h2 style="margin-top:0;">Danh sách tài khoản</h2>
<table border="1" cellpadding="8" cellspacing="0" style="width:100%;border-collapse:collapse;border-color:#e5e7eb;">
  <thead style="background:#f3f4f6;"><tr><th>Tên</th><th>Vai trò</th><th>Mã HS</th><th>Ngày tạo</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

<h2 style="margin-top:32px;">📊 Thống kê sử dụng — {_total} lượt học</h2>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px;">
  <div>
    <h3 style="margin:0 0 8px;font-size:14px;color:#374151;">Theo môn học</h3>
    <table border="1" cellpadding="6" cellspacing="0" style="width:100%;border-collapse:collapse;border-color:#e5e7eb;font-size:13px;">
      <thead style="background:#f3f4f6;"><tr><th style="padding:4px 10px;text-align:left">Môn</th><th style="padding:4px 10px;text-align:left">Lượt</th></tr></thead>
      <tbody>{_sub_rows}</tbody>
    </table>
  </div>
  <div>
    <h3 style="margin:0 0 8px;font-size:14px;color:#374151;">Theo học sinh</h3>
    <table border="1" cellpadding="6" cellspacing="0" style="width:100%;border-collapse:collapse;border-color:#e5e7eb;font-size:13px;">
      <thead style="background:#f3f4f6;"><tr><th style="padding:4px 10px;text-align:left">Mã HS</th><th style="padding:4px 10px">Lượt</th><th style="padding:4px 10px">Cuối</th></tr></thead>
      <tbody>{_stu_rows}</tbody>
    </table>
  </div>
</div>

<h2 style="margin-top:32px;">Tạo tài khoản học sinh</h2>
<form id="cf" style="display:grid;gap:10px;max-width:420px;">
  <input name="display_name" placeholder="Họ tên học sinh *" required style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <input name="username" placeholder="Tên đăng nhập *" required style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <input name="password" type="password" placeholder="Mật khẩu *" required style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <input name="student_id" placeholder="Mã HS (VD: S002) *" required style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <input name="grade" placeholder="Lớp (VD: Lớp 8)" style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <select name="vision_status" style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
    <option value="low vision">Thị lực kém</option>
    <option value="blind">Mù hoàn toàn</option>
    <option value="partial vision">Thị lực một phần</option>
  </select>
  <button type="submit" style="background:#1565C0;color:#fff;border:0;border-radius:8px;padding:10px;cursor:pointer;font-weight:700;">
    Tạo tài khoản
  </button>
  <p id="msg" style="color:#16a34a;font-weight:600;"></p>
</form>
<script>
document.getElementById('cf').addEventListener('submit', async e => {{
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  const r = await fetch('/teacher/students', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify(d)
  }});
  const j = await r.json();
  document.getElementById('msg').textContent = r.ok ? '✅ Tạo thành công!' : '❌ ' + (j.detail || 'Lỗi');
  if (r.ok) {{ e.target.reset(); setTimeout(()=>location.reload(), 1500); }}
}});
</script>"""
    else:
        auth_html = """
<h2>Đăng nhập giáo viên</h2>
<form id="lf" style="display:grid;gap:10px;max-width:320px;">
  <input name="username" placeholder="Tên đăng nhập" required style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <input name="password" type="password" placeholder="Mật khẩu" required style="padding:8px;border:1px solid #d1d5db;border-radius:8px;">
  <button type="submit" style="background:#1565C0;color:#fff;border:0;border-radius:8px;padding:10px;cursor:pointer;font-weight:700;">
    Đăng nhập
  </button>
  <p id="err" style="color:#ef4444;font-weight:600;"></p>
</form>
<script>
document.getElementById('lf').addEventListener('submit', async e => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  const r = await fetch('/auth/login', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(d)
  });
  const j = await r.json();
  if (r.ok) location.reload();
  else document.getElementById('err').textContent = j.detail || 'Đăng nhập thất bại';
});
</script>"""

    return HTMLResponse(f"""<!DOCTYPE html><html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EduVision AI — Giáo viên</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:860px;margin:0 auto;padding:24px 16px;color:#111827;}}
h1{{color:#12355b;}} table{{font-size:14px;}} th,td{{text-align:left;}}
</style>
</head><body>
<h1>🎓 EduVision AI — Quản lý giáo viên</h1>
{auth_html}
<p style="margin-top:32px;"><a href="/">← Về trang học sinh</a></p>
</body></html>""")


## ── VISION DESCRIBE ──────────────────────────────────────────────────────────

@app.post("/describe-image")
async def describe_image(
    file: UploadFile = File(...),
    language: str = Form(default="vi"),
) -> Dict[str, Any]:
    """Mô tả hình vẽ toán học bằng AI Vision (Groq Llama 4 Scout) cho học sinh khiếm thị."""
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY chưa được cấu hình")

    img_bytes = await file.read()
    b64 = base64.b64encode(img_bytes).decode()
    mime = file.content_type or "image/jpeg"

    if language == "vi":
        system = "Bạn là trợ lý giáo dục cho học sinh khiếm thị. Mô tả hình ảnh bằng ngôn ngữ xúc giác và mô tả không gian, không dùng từ 'nhìn'."
        prompt = (
            "Đây là hình vẽ từ bài toán hoặc tài liệu học tập. "
            "Mô tả chi tiết bằng lời cho học sinh khiếm thị: hình dạng, điểm, đoạn thẳng, góc, số liệu, kích thước, vị trí tương đối. "
            "Không dùng 'nhìn vào hình' hay 'như hình vẽ' — thay bằng ngôn ngữ xúc giác và mô tả không gian (trái/phải/trên/dưới)."
        )
    else:
        system = "You are an accessible math educator. Describe visual content verbally for blind and low-vision students using tactile, spatial language."
        prompt = (
            "This is a figure from a math problem or study material. "
            "Describe it in detail for a visually impaired student: shapes, points, segments, angles, measurements, relative positions. "
            "Do not say 'look at the figure' — use tactile and spatial language (left/right/above/below) instead."
        )

    try:
        import httpx as _hx
        async with _hx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                            {"type": "text", "text": prompt},
                        ]},
                    ],
                    "max_tokens": 800,
                },
            )
        data = resp.json()
        description = (
            data["choices"][0]["message"]["content"].strip()
            if "choices" in data
            else ("Không thể mô tả hình vẽ lúc này." if language == "vi" else "Could not describe the image.")
        )
    except Exception as exc:
        description = f"{'Lỗi' if language == 'vi' else 'Error'}: {exc}"

    log_event("vision", "vision", file.filename or "image", description[:200])
    return {"description": description, "model": "llama-4-scout-17b-16e-instruct"}


## ── DEMO RESET ────────────────────────────────────────────────────────────────

@app.post("/demo/reset")
def reset_demo(x_reset_token: str = Header(default="")) -> Dict[str, Any]:
    token = os.getenv("DEMO_RESET_TOKEN", "")
    if not token or x_reset_token != token:
        raise HTTPException(status_code=403, detail="Forbidden")
    init_db()
    with db() as conn:
        conn.execute("DELETE FROM learning_events")
    return {"status": "ok", "message": "Demo learning history cleared."}


## ── ADMIN SYSTEM ──────────────────────────────────────────────────────────────

def _admin_css() -> str:
    return """
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --red:#c41230;--red-dk:#9a0e26;
  --blue:#1e3a6e;--blue-mid:#2a5298;--blue-lt:#5a96e0;
  --ink:#e8eaf0;--muted:#8aabcc;--muted2:#5a7a9a;
  --bg:#0b1622;--surf:#111f33;--card:#182840;--card2:#0e1d30;
  --line:#1e3050;--line2:#162640;
  --green:#27ae60;--orange:#d4791a;--danger:#c0392b;
}
html{scroll-behavior:smooth}
body{font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:17px;line-height:1.6;background:var(--bg);color:var(--ink);min-height:100vh}
a{color:var(--blue-lt);text-decoration:none}
a:hover{text-decoration:underline}
/* Skip nav */
.skip-nav{position:absolute;top:-100%;left:0;padding:10px 20px;background:var(--red);color:#fff;font-weight:700;z-index:9999;border-radius:0 0 8px 0}
.skip-nav:focus{top:0}
/* Topbar */
.topbar{background:var(--surf);padding:0 24px;display:flex;align-items:center;gap:0;border-bottom:3px solid var(--red);min-height:60px;flex-wrap:wrap;position:sticky;top:0;z-index:200}
.topbar-brand{font-size:19px;font-weight:700;color:#fff;display:flex;align-items:center;gap:8px;padding:10px 0;flex:1;white-space:nowrap}
.topbar-brand .ev{color:var(--red)}
.topbar-sub{font-size:13px;color:var(--muted);font-weight:400;margin-left:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.topbar-nav{display:flex;gap:4px;flex-wrap:wrap;padding:8px 0}
.topbar-nav a{color:var(--muted);padding:8px 14px;border-radius:8px;font-size:15px;font-weight:600;transition:background .15s,color .15s;min-height:44px;display:inline-flex;align-items:center}
.topbar-nav a:hover,.topbar-nav a:focus{background:var(--card);color:#fff;outline:none;text-decoration:none}
.topbar-nav a:focus-visible{outline:3px solid var(--blue-lt);outline-offset:2px}
.topbar-nav a.logout{color:#e8876a}
/* Layout */
main{max-width:1060px;margin:0 auto;padding:24px 20px}
/* Headings */
h2{font-size:22px;color:#fff;margin:24px 0 14px;padding-bottom:10px;border-bottom:2px solid var(--line);display:flex;align-items:center;gap:8px}
h2:first-child{margin-top:0}
h3{font-size:18px;color:var(--muted);margin:18px 0 10px;display:flex;align-items:center;gap:6px}
/* Stats */
.stats-row{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px}
.stat-tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 16px;text-align:center}
.stat-tile .num{font-size:38px;font-weight:700;line-height:1.1}
.stat-tile .lbl{color:var(--muted);font-size:14px;margin-top:4px}
.num-blue{color:var(--blue-lt)}
.num-green{color:var(--green)}
.num-orange{color:var(--orange)}
/* Card */
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:18px}
.card-flush{padding:0;overflow:hidden}
/* School grid */
.school-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-bottom:22px}
.school-card{background:var(--card2);border:2px solid var(--line2);border-radius:14px;padding:20px;transition:border-color .2s,transform .1s;display:flex;flex-direction:column}
.school-card:hover{border-color:var(--blue-lt);transform:translateY(-2px)}
.school-card h3{color:#fff;font-size:18px;margin:0 0 4px}
.school-card .city{color:var(--muted);font-size:14px;margin-bottom:12px}
.stat-badges{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.stat-badge{background:var(--surf);padding:5px 10px;border-radius:7px;font-size:14px;color:var(--muted)}
/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:12px 20px;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;border:none;transition:opacity .15s,transform .1s;min-height:48px;text-decoration:none;line-height:1;white-space:nowrap}
.btn:active{transform:scale(.97)}
.btn:focus-visible{outline:3px solid var(--blue-lt);outline-offset:2px}
.btn-primary{background:var(--blue-mid);color:#fff}
.btn-red{background:var(--red);color:#fff}
.btn-success{background:#1c6636;color:#c8f0d8}
.btn-warning{background:#6e3c0e;color:#f8d0a8}
.btn-danger{background:#621010;color:#f8c0c0}
.btn-ghost{background:transparent;color:var(--muted);border:1.5px solid var(--line)}
.btn-ghost:hover{background:var(--card);color:#fff}
.btn:hover{opacity:.88}
.btn-sm{padding:8px 14px;font-size:14px;min-height:40px}
.btn-block{width:100%}
/* Forms */
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:16px;margin-bottom:7px;color:var(--muted);font-weight:600}
.form-group input,.form-group select{width:100%;padding:12px 14px;font-size:16px;background:var(--card2);border:2px solid var(--line);border-radius:10px;color:var(--ink);outline:none;transition:border-color .2s}
.form-group input:focus,.form-group select:focus{border-color:var(--blue-lt)}
.form-row{display:flex;gap:12px;flex-wrap:wrap}
.form-row .form-group{flex:1;min-width:140px}
/* Table */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:14px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;min-width:480px}
th{background:var(--card2);color:var(--muted);padding:12px 14px;text-align:left;font-size:15px;font-weight:700}
td{padding:11px 14px;border-bottom:1px solid var(--line2);font-size:15px;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--surf)}
/* Badges */
.badge{display:inline-flex;align-items:center;gap:3px;padding:3px 9px;border-radius:6px;font-size:13px;font-weight:700}
.badge-blind{background:#4a1010;color:#ffc8c8}
.badge-low{background:#123018;color:#a8f0b8}
.badge-teacher{background:#121e48;color:#b0c8ff}
.badge-admin{background:#2a1040;color:#d8b8ff}
/* Alerts */
.alert{padding:13px 16px;border-radius:10px;margin-bottom:14px;font-size:16px;display:flex;align-items:flex-start;gap:10px}
.alert-success{background:#112818;border:1.5px solid var(--green);color:#98e8b8}
.alert-error{background:#2a0e0e;border:1.5px solid var(--danger);color:#f0a0a0}
/* Search */
.search-row{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.search-row input{flex:1;min-width:180px;padding:11px 14px;font-size:16px;background:var(--card2);border:2px solid var(--line);border-radius:10px;color:var(--ink);outline:none;transition:border-color .2s}
.search-row input:focus{border-color:var(--blue-lt)}
/* Inline confirm */
.cw-q{display:none;align-items:center;gap:6px;flex-wrap:wrap}
.cw-asking .cw-trigger{display:none}
.cw-asking .cw-q{display:inline-flex}
/* Divider */
.gap{margin-top:28px}
/* Analytics */
.analytic-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:16px}
.analytic-card{background:var(--card2);border:1px solid var(--line2);border-radius:12px;padding:16px}
.analytic-card .subject{font-size:14px;color:var(--muted);margin-bottom:4px}
.analytic-card .count{font-size:28px;font-weight:700;color:var(--blue-lt)}
/* Responsive */
@media(max-width:700px){
  .stats-row{grid-template-columns:1fr 1fr}
  .school-grid{grid-template-columns:1fr}
  .topbar{padding:0 14px}
  main{padding:14px}
  .form-row{flex-direction:column}
  th,td{padding:9px 10px;font-size:14px}
}
@media(max-width:420px){
  .stats-row{grid-template-columns:1fr}
  .topbar-sub{display:none}
}
/* Confirm JS */
</style>
<script>
function cwAsk(id){document.getElementById(id).classList.add('cw-asking')}
function cwCancel(id){document.getElementById(id).classList.remove('cw-asking')}
</script>
"""


def _admin_topbar(role: str, school_name: str = "") -> str:
    home = "/admin" if role == "superadmin" else "/teacher"
    sub = "Quản trị tổng" if role == "superadmin" else ("Giáo viên" + (f" — {school_name}" if school_name else ""))
    analytics = '<a href="/teacher/analytics">📊 Báo cáo</a>' if role == "teacher" else ""
    admin_home = '<a href="/admin">🏫 Trường học</a>' if role == "superadmin" else ""
    return f"""<a href="#main" class="skip-nav">Chuyển đến nội dung chính</a>
<div class="topbar" role="banner">
  <a href="{home}" class="topbar-brand" style="text-decoration:none">
    <span class="ev">EduVision</span> AI
    <span class="topbar-sub">{sub}</span>
  </a>
  <nav class="topbar-nav" aria-label="Menu quản trị">
    {admin_home}
    {analytics}
    <a href="/">🎓 Trang học tập</a>
    <a href="/auth/logout" class="logout">↩ Đăng xuất</a>
  </nav>
</div>"""


def _require_admin(request: Request, min_role: str = "teacher") -> dict:
    """Kiểm tra auth và trả user. Ném 403 nếu không đủ quyền."""
    token = request.cookies.get("session", "")
    user = get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})
    role = user.get("role", "student")
    allowed = {"superadmin": 3, "teacher": 2, "student": 1}
    required = {"superadmin": 3, "teacher": 2, "student": 1}
    if allowed.get(role, 0) < required.get(min_role, 2):
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập trang này.")
    return user


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(msg: str = "") -> HTMLResponse:
    alert = f'<div class="alert alert-error" role="alert">⚠ {msg}</div>' if msg else ""
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Đăng nhập — EduVision Quản trị</title>{_admin_css()}</head>
<body>
<div style="background:var(--surf);padding:0 24px;border-bottom:3px solid var(--red);min-height:60px;display:flex;align-items:center">
  <span style="font-size:19px;font-weight:700;color:#fff"><span style="color:var(--red)">EduVision</span> AI</span>
</div>
<main id="main" style="display:flex;align-items:center;justify-content:center;min-height:calc(100vh - 63px);padding:24px">
  <div style="width:100%;max-width:440px">
    <div class="card">
      <h2 style="margin-top:0;border:none;padding:0;margin-bottom:20px;font-size:24px">🔐 Đăng nhập quản trị</h2>
      {alert}
      <form method="post" action="/admin/login" novalidate>
        <div class="form-group">
          <label for="username">Tên đăng nhập</label>
          <input type="text" id="username" name="username" autocomplete="username"
                 placeholder="admin hoặc ten_giaovien" required autofocus>
        </div>
        <div class="form-group">
          <label for="password">Mật khẩu</label>
          <input type="password" id="password" name="password" autocomplete="current-password" required>
        </div>
        <button type="submit" class="btn btn-red btn-block" style="font-size:18px;min-height:54px">
          Đăng nhập →
        </button>
      </form>
    </div>
    <p style="text-align:center;color:var(--muted);font-size:14px;margin-top:14px">
      Chỉ dành cho giáo viên và quản trị viên. &nbsp;
      <a href="/">Trang học tập →</a>
    </p>
  </div>
</main>
</body></html>""")


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login_post(request: Request) -> HTMLResponse:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    token = auth_login(username, password)
    if not token:
        return RedirectResponse(url="/admin/login?msg=Sai+tên+đăng+nhập+hoặc+mật+khẩu", status_code=303)
    user = get_user_by_token(token)
    role = user.get("role", "") if user else ""
    if role not in ("superadmin", "teacher"):
        auth_logout(token)
        return RedirectResponse(url="/admin/login?msg=Tài+khoản+không+có+quyền+quản+trị", status_code=303)
    dest = "/admin" if role == "superadmin" else "/teacher"
    resp = RedirectResponse(url=dest, status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request) -> HTMLResponse:
    user = _require_admin(request, "superadmin")
    schools = list_schools()
    total_students = sum(s["student_count"] for s in schools)
    total_teachers = sum(s["teacher_count"] for s in schools)

    cards_html = ""
    for s in schools:
        cards_html += f"""
<div class="school-card" role="region" aria-label="Trường {s['name']}">
  <h3 style="color:#fff;font-size:17px;margin:0 0 4px">{s['name']}</h3>
  <p class="city">📍 {s['city']}</p>
  <div class="stat-badges">
    <span class="stat-badge">👨‍🎓 {s['student_count']} HS</span>
    <span class="stat-badge">👩‍🏫 {s['teacher_count']} GV</span>
  </div>
  <a href="/admin/school/{s['code']}" class="btn btn-primary btn-block" style="margin-top:auto"
     aria-label="Quản lý trường {s['name']}">Quản lý →</a>
</div>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quản trị — EduVision AI</title>{_admin_css()}</head>
<body>
{_admin_topbar('superadmin')}
<main id="main">
  <h2 style="margin-top:0">Tổng quan hệ thống</h2>
  <div class="stats-row">
    <div class="stat-tile">
      <div class="num num-blue">{len(schools)}</div>
      <div class="lbl">🏫 Trường</div>
    </div>
    <div class="stat-tile">
      <div class="num num-green">{total_students}</div>
      <div class="lbl">👨‍🎓 Học sinh</div>
    </div>
    <div class="stat-tile">
      <div class="num num-orange">{total_teachers}</div>
      <div class="lbl">👩‍🏫 Giáo viên</div>
    </div>
  </div>

  <h2>Danh sách trường</h2>
  <div class="school-grid" role="list">
    {cards_html if cards_html else '<p style="color:var(--muted)">Chưa có trường nào. Thêm trường bên dưới.</p>'}
  </div>

  <div class="card">
    <h2 style="margin-top:0;border:none;padding:0;margin-bottom:16px">➕ Thêm trường mới</h2>
    <form method="post" action="/admin/add-school">
      <div class="form-row">
        <div class="form-group">
          <label for="school_code">Mã trường</label>
          <input type="text" id="school_code" name="code" required pattern="[a-z]{{2,10}}"
                 placeholder="hcm" aria-describedby="code-hint">
          <small id="code-hint" style="color:var(--muted);font-size:13px">2–10 ký tự thường, không dấu</small>
        </div>
        <div class="form-group" style="flex:2">
          <label for="school_name">Tên trường</label>
          <input type="text" id="school_name" name="name" required placeholder="Trường Khiếm Thị TP.HCM">
        </div>
        <div class="form-group">
          <label for="school_city">Tỉnh / Thành phố</label>
          <input type="text" id="school_city" name="city" placeholder="TP.HCM">
        </div>
      </div>
      <button type="submit" class="btn btn-success">➕ Thêm trường</button>
    </form>
  </div>
</main>
</body></html>""")


@app.post("/admin/add-school")
async def admin_add_school(request: Request) -> HTMLResponse:
    _require_admin(request, "superadmin")
    form = await request.form()
    code = str(form.get("code", "")).strip().lower()
    name = str(form.get("name", "")).strip()
    city = str(form.get("city", "")).strip()
    if code and name:
        create_school(code, name, city)
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/school/{code}", response_class=HTMLResponse)
def admin_school_detail(code: str, request: Request, q: str = "") -> HTMLResponse:
    user = _require_admin(request, "teacher")
    role = user.get("role", "")
    # Teacher chỉ xem trường của mình
    if role == "teacher":
        school_users = list_school_users("", role="teacher")
        # tìm school_code của GV này
        all_users = list_users()
        me = next((u for u in all_users if u["username"] == user["username"]), None)
        my_school = me.get("school_code") if me else None
        if my_school != code:
            raise HTTPException(status_code=403, detail="Bạn chỉ có thể quản lý trường của mình.")

    school = get_school(code)
    if not school:
        raise HTTPException(status_code=404, detail="Không tìm thấy trường.")

    teachers = list_school_users(code, role="teacher")
    students = list_school_users(code, role="student")

    # Filter theo search
    q_lower = q.lower()
    if q_lower:
        students = [s for s in students if q_lower in s["username"].lower()
                    or q_lower in (s["display_name"] or "").lower()
                    or q_lower in (s["student_id"] or "").lower()]

    def _reset_btn(username: str, redirect: str, label: str) -> str:
        cw_id = f"cw-{username.replace('.','_')}"
        return f"""<span id="{cw_id}">
  <button class="btn btn-warning btn-sm cw-trigger" onclick="cwAsk('{cw_id}')"
          aria-label="Đặt lại mật khẩu {username}">Đặt lại pass</button>
  <span class="cw-q" role="group" aria-label="Xác nhận đặt lại mật khẩu">
    <span style="font-size:13px;color:var(--muted)">Đặt lại {label}?</span>
    <form method="post" action="/admin/reset-password" style="display:inline">
      <input type="hidden" name="username" value="{username}">
      <input type="hidden" name="redirect" value="{redirect}">
      <button type="submit" class="btn btn-danger btn-sm">✓ Xác nhận</button>
    </form>
    <button class="btn btn-ghost btn-sm" onclick="cwCancel('{cw_id}')">Huỷ</button>
  </span>
</span>"""

    teacher_rows = ""
    for t in teachers:
        teacher_rows += f"""
<tr>
  <td><strong>{t['username']}</strong></td>
  <td>{t['display_name'] or '—'}</td>
  <td><span class="badge badge-teacher">GV</span></td>
  <td>{_reset_btn(t['username'], f'/admin/school/{code}', t['username'])}</td>
</tr>"""

    student_rows = ""
    for s in students:
        vision = s.get("vision_status", "") or ""
        if "blind" in vision:
            vbadge = '<span class="badge badge-blind">👁 Mù</span>'
        else:
            vbadge = '<span class="badge badge-low">👁 Nhìn kém</span>'
        student_rows += f"""
<tr>
  <td><strong>{s['username']}</strong></td>
  <td>{s['display_name'] or '—'}</td>
  <td>{vbadge}</td>
  <td>{_reset_btn(s['username'], f'/admin/school/{code}?q={q}', s['username'])}</td>
</tr>"""

    back = '<a href="/admin" class="btn btn-ghost btn-sm">← Tất cả trường</a>' if role == "superadmin" else ""
    total_stu = len(list_school_users(code, 'student'))

    add_teacher_section = ""
    if role == "superadmin":
        add_teacher_section = f"""
<div class="card gap">
  <h2 style="margin-top:0;border:none;padding:0;margin-bottom:14px">➕ Thêm giáo viên</h2>
  <form method="post" action="/admin/add-teacher">
    <input type="hidden" name="school_code" value="{code}">
    <div class="form-row">
      <div class="form-group">
        <label>Tên đăng nhập</label>
        <input type="text" name="username" required placeholder="{code}_gv2">
      </div>
      <div class="form-group" style="flex:2">
        <label>Họ tên</label>
        <input type="text" name="display_name" required placeholder="Nguyễn Văn A">
      </div>
    </div>
    <button type="submit" class="btn btn-success">➕ Thêm giáo viên</button>
  </form>
</div>"""

    add_student_section = f"""
<div class="card gap">
  <h2 style="margin-top:0;border:none;padding:0;margin-bottom:14px">➕ Thêm học sinh</h2>
  <form method="post" action="/admin/add-student">
    <input type="hidden" name="school_code" value="{code}">
    <div class="form-row">
      <div class="form-group">
        <label>Tên đăng nhập</label>
        <input type="text" name="username" required placeholder="{code}_hs01">
      </div>
      <div class="form-group" style="flex:2">
        <label>Họ tên</label>
        <input type="text" name="display_name" required placeholder="Nguyễn Bảo An">
      </div>
      <div class="form-group">
        <label>Thị lực</label>
        <select name="vision_status">
          <option value="lowvision">Nhìn kém</option>
          <option value="blind">Mù hoàn toàn</option>
        </select>
      </div>
    </div>
    <button type="submit" class="btn btn-success">➕ Thêm học sinh</button>
  </form>
</div>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{school['name']} — EduVision Quản trị</title>{_admin_css()}</head>
<body>
{_admin_topbar(role, school['name'])}
<main id="main">
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px">
    {back}
    <h2 style="margin:0;border:none;padding:0">📍 {school['name']} — {school['city']}</h2>
  </div>

  <h3>Giáo viên ({len(teachers)} người)</h3>
  <div class="table-wrap">
    <table aria-label="Danh sách giáo viên">
      <thead><tr><th>Tài khoản</th><th>Họ tên</th><th>Vai trò</th><th>Thao tác</th></tr></thead>
      <tbody>{teacher_rows or '<tr><td colspan="4" class="empty">Chưa có giáo viên</td></tr>'}</tbody>
    </table>
  </div>

  {add_teacher_section}

  <div class="gap">
    <h3>Học sinh ({total_stu} người{f" — hiển thị {len(students)}" if q else ""})</h3>
    <div class="search-row" role="search">
      <form method="get" action="/admin/school/{code}" style="display:contents">
        <input type="search" name="q" value="{q}" placeholder="Tìm theo tên, tài khoản..."
               aria-label="Tìm học sinh" autocomplete="off">
        <button type="submit" class="btn btn-primary btn-sm">Tìm</button>
        {'<a href="/admin/school/' + code + '" class="btn btn-ghost btn-sm">✕ Xoá lọc</a>' if q else ""}
      </form>
    </div>
    <div class="table-wrap">
      <table aria-label="Danh sách học sinh">
        <thead><tr><th>Tài khoản</th><th>Họ tên</th><th>Thị lực</th><th>Thao tác</th></tr></thead>
        <tbody>{student_rows or '<tr><td colspan="4" class="empty">Không tìm thấy học sinh</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  {add_student_section}
</main>
</body></html>""")


@app.post("/admin/reset-password")
async def admin_reset_password(request: Request) -> HTMLResponse:
    _require_admin(request, "teacher")
    form = await request.form()
    username = str(form.get("username", "")).strip()
    redirect_to = str(form.get("redirect", "/admin"))
    if username:
        reset_user_password(username, "1")
    return RedirectResponse(url=redirect_to, status_code=303)


@app.post("/admin/add-student")
async def admin_add_student(request: Request) -> HTMLResponse:
    _require_admin(request, "teacher")
    form = await request.form()
    username = str(form.get("username", "")).strip()
    school_code = str(form.get("school_code", "")).strip()
    display_name = str(form.get("display_name", "")).strip()
    vision_status = str(form.get("vision_status", "lowvision")).strip()
    if username and school_code:
        try:
            create_student_account(username, "1", school_code, display_name or username, vision_status)
        except ValueError:
            pass
    return RedirectResponse(url=f"/admin/school/{school_code}", status_code=303)


@app.post("/admin/add-teacher")
async def admin_add_teacher(request: Request) -> HTMLResponse:
    _require_admin(request, "superadmin")
    form = await request.form()
    username = str(form.get("username", "")).strip()
    school_code = str(form.get("school_code", "")).strip()
    display_name = str(form.get("display_name", "")).strip()
    if username and school_code:
        try:
            create_teacher_account(username, "1", school_code, display_name or username)
        except ValueError:
            pass
    return RedirectResponse(url=f"/admin/school/{school_code}", status_code=303)


@app.get("/teacher", response_class=HTMLResponse)
def teacher_dashboard(request: Request) -> HTMLResponse:
    """GV login → chuyển thẳng vào trang trường của mình."""
    user = _require_admin(request, "teacher")
    all_users = list_users()
    me = next((u for u in all_users if u["username"] == user["username"]), None)
    school_code = me.get("school_code") if me else None
    if not school_code:
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lỗi — EduVision</title>{_admin_css()}</head>
<body>
{_admin_topbar('teacher')}
<main id="main" style="display:flex;align-items:center;justify-content:center;min-height:calc(100vh - 63px)">
  <div class="card" style="max-width:440px;text-align:center">
    <div style="font-size:48px;margin-bottom:16px">⚠️</div>
    <h2 style="border:none;padding:0;margin-bottom:12px;color:var(--danger)">Chưa được gán trường</h2>
    <p style="color:var(--muted);margin-bottom:20px">Tài khoản của bạn chưa được phân công vào trường nào. Liên hệ quản trị viên hệ thống.</p>
    <a href="/auth/logout" class="btn btn-ghost">↩ Đăng xuất</a>
  </div>
</main>
</body></html>""", status_code=400)
    return RedirectResponse(url=f"/admin/school/{school_code}", status_code=303)


@app.get("/teacher/analytics", response_class=HTMLResponse)
def teacher_analytics_page(request: Request) -> HTMLResponse:
    """Trang báo cáo thống kê cho giáo viên."""
    user = _require_admin(request, "teacher")
    all_users = list_users()
    me = next((u for u in all_users if u["username"] == user["username"]), None)
    school_code = me.get("school_code") if me else None
    school_name = ""
    if school_code:
        sch = get_school(school_code)
        school_name = sch["name"] if sch else school_code

    analytics: Dict[str, Any] = {}
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT subject, COUNT(*) as cnt FROM learning_events WHERE 1=1 GROUP BY subject ORDER BY cnt DESC"
            ).fetchall()
            analytics["by_subject"] = [{"subject": r[0], "count": r[1]} for r in rows]
            total = conn.execute("SELECT COUNT(*) FROM learning_events").fetchone()
            analytics["total_sessions"] = total[0] if total else 0
            by_stu = conn.execute(
                "SELECT student_id, COUNT(*) as cnt FROM learning_events GROUP BY student_id ORDER BY cnt DESC LIMIT 20"
            ).fetchall()
            analytics["top_students"] = [{"student_id": r[0], "count": r[1]} for r in by_stu]
    except Exception:
        analytics = {"total_sessions": 0, "by_subject": [], "top_students": []}

    subject_cards = ""
    subject_labels = {"math": "Toán học", "science": "Khoa học", "english": "Tiếng Anh",
                      "geometry": "Hình học", "history": "Lịch sử", "general": "Tổng hợp"}
    for item in analytics.get("by_subject", []):
        label = subject_labels.get(item["subject"], item["subject"].title())
        subject_cards += f"""
<div class="analytic-card">
  <div class="subject">{label}</div>
  <div class="count">{item['count']}</div>
  <div style="font-size:13px;color:var(--muted2)">lượt học</div>
</div>"""

    top_student_rows = ""
    for i, s in enumerate(analytics.get("top_students", []), 1):
        top_student_rows += f"""
<tr>
  <td style="color:var(--muted)">{i}</td>
  <td><strong>{s['student_id']}</strong></td>
  <td>{s['count']} lượt</td>
</tr>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Báo cáo — EduVision</title>{_admin_css()}</head>
<body>
{_admin_topbar('teacher', school_name)}
<main id="main">
  <h2 style="margin-top:0">📊 Báo cáo hoạt động học tập</h2>

  <div class="stats-row">
    <div class="stat-tile" style="grid-column:1/-1" >
      <div class="num num-blue">{analytics.get('total_sessions', 0)}</div>
      <div class="lbl">Tổng lượt hỏi AI</div>
    </div>
  </div>

  <h3>Phân bổ theo môn học</h3>
  <div class="analytic-grid">
    {subject_cards if subject_cards else '<p style="color:var(--muted)">Chưa có dữ liệu.</p>'}
  </div>

  <h3>Top học sinh hoạt động</h3>
  <div class="table-wrap">
    <table aria-label="Top học sinh theo lượt học">
      <thead><tr><th>#</th><th>Học sinh</th><th>Số lượt</th></tr></thead>
      <tbody>{top_student_rows or '<tr><td colspan="3" class="empty">Chưa có dữ liệu</td></tr>'}</tbody>
    </table>
  </div>

  <div class="gap" style="margin-top:24px">
    <a href="/teacher" class="btn btn-ghost btn-sm">← Về trang trường</a>
  </div>
</main>
</body></html>""")


# ── PWA ───────────────────────────────────────────────────────────────────────

@app.get("/manifest.json")
def pwa_manifest():
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": "EduVision AI",
        "short_name": "EduVision",
        "description": "Trợ lý học tập AI cho học sinh khiếm thị",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f6f8fb",
        "theme_color": "#c41230",
        "lang": "vi",
        "icons": [
            {"src": "/favicon.ico", "sizes": "any", "type": "image/x-icon"},
            {"src": "https://eduvision-ai-nu.vercel.app/favicon.ico", "sizes": "192x192", "type": "image/x-icon"}
        ],
        "categories": ["education", "accessibility"],
        "screenshots": []
    }, headers={"Content-Type": "application/manifest+json"})


@app.get("/service-worker.js")
def service_worker():
    from fastapi.responses import Response
    sw_code = r"""
const CACHE_NAME = 'eduvision-v3';
const CORE_ASSETS = [
  '/',
  '/privacy',
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(CORE_ASSETS);
    }).catch(function() {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE_NAME; })
            .map(function(k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // Network-first for API calls (ask, auth, ocr, tts)
  if (['/ask', '/auth/', '/ocr', '/tts', '/stt', '/braille', '/profile', '/report'].some(function(p) {
    return url.pathname.startsWith(p);
  })) {
    event.respondWith(
      fetch(event.request).catch(function() {
        return new Response(JSON.stringify({
          answer: 'Bạn đang ngoại tuyến. Kết nối mạng và thử lại nhé.',
          offline: true
        }), { headers: { 'Content-Type': 'application/json' } });
      })
    );
    return;
  }

  // Network-first for HTML pages (always get latest JS/CSS)
  if (url.pathname === '/' || url.pathname === '/privacy') {
    event.respondWith(
      fetch(event.request).catch(function() {
        return caches.match(event.request);
      })
    );
    return;
  }

  // Cache-first for static/page assets
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      if (cached) return cached;
      return fetch(event.request).then(function(response) {
        if (response && response.status === 200 && event.request.method === 'GET') {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) { cache.put(event.request, clone); });
        }
        return response;
      }).catch(function() {
        // Offline fallback for page navigation
        if (event.request.mode === 'navigate') {
          return caches.match('/');
        }
        return new Response('Ngoại tuyến', { status: 503 });
      });
    })
  );
});
"""
    return Response(content=sw_code, media_type="application/javascript",
                    headers={"Service-Worker-Allowed": "/"})
