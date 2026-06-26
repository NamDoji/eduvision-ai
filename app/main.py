from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field


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
    version="0.4.1",
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
    init_db()


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
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>EduVision AI</title>
  <style>
    :root{--red:#c41230;--blue:#12355b;--ink:#172033;--muted:#667085;--line:#d9e2ef;--soft:#f6f8fb;--panel:#fff;font-family:Inter,Arial,sans-serif}
    *{box-sizing:border-box}
    body{margin:0;background:var(--soft);color:var(--ink);overflow-x:hidden;font-size:17px}
    header{background:#fff;border-bottom:2px solid var(--line);position:sticky;top:0;z-index:100}
    .topbar{max-width:1180px;margin:0 auto;padding:12px 20px;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px}
    .brand{display:flex;align-items:center;gap:10px;font-weight:800;color:var(--blue);font-size:20px;min-width:0;line-height:1.15}
    .brand div{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
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
    @media(max-width:560px){body{font-size:16px}.topbar{padding:10px 14px;gap:8px}.brand{font-size:17px}.hero-wrap{padding:12px 14px 8px}main{padding:8px 14px 36px}.card{padding:16px}.status{grid-template-columns:1fr 1fr}.lang-toggle button{padding:8px 12px;font-size:14px;min-width:50px}.actions{gap:10px}pre{font-size:13px;padding:14px;min-height:240px}}
    @media(max-width:360px){.brand{font-size:15px}.lang-toggle button{padding:7px 10px;min-width:46px}.status{grid-template-columns:1fr}}
  </style>
</head>
<body>
<div id="loading-bar"></div>

<header>
  <div class="topbar">
    <div class="brand">
      <div>EduVision AI</div>
    </div>
    <!-- LANGUAGE TOGGLE -->
    <div class="lang-toggle" role="group" aria-label="Chọn ngôn ngữ / Select language">
      <button id="btn-vi" class="active" onclick="setLang('vi')" aria-pressed="true" aria-label="Chuyển sang Tiếng Việt">VI</button>
      <button id="btn-en" onclick="setLang('en')" aria-pressed="false" aria-label="Switch to English">EN</button>
    </div>
  </div>
  <div class="hero-wrap">
    <h1 id="hero-title">Trợ lý học tập cho học sinh khiếm thị</h1>
    <p class="lead" id="hero-lead">Giải thích bài học bằng ngôn ngữ dễ hiểu, hỗ trợ hình học, tiếng Anh, đọc tài liệu OCR và lập kế hoạch học tập song ngữ.</p>
  </div>
</header>

<main>
  <div class="status" id="status">
    <div class="stat"><strong>Backend</strong><span>Đang kiểm tra...</span></div>
    <div class="stat"><strong>OCR</strong><span>...</span></div>
    <div class="stat"><strong id="voice-label">Giọng nói</strong><span id="voice-val">...</span></div>
    <div class="stat"><strong>OCR.space</strong><span id="gv-val">...</span></div>
  </div>

  <div class="grid">
    <div>
      <!-- AI TUTOR -->
      <div class="card">
        <h2 id="tutor-title">🤖 AI Gia sư</h2>
        <div class="row2">
          <div>
            <label for="subject" id="lbl-subject">Môn học</label>
            <select id="subject">
              <option value="geometry" id="opt-geo">Hình học</option>
              <option value="english" id="opt-eng">Tiếng Anh</option>
              <option value="general" id="opt-gen">Tổng hợp</option>
            </select>
          </div>
          <div>
            <label for="student" id="lbl-student">Mã học sinh</label>
            <input id="student" value="S001"/>
          </div>
        </div>
        <label for="question" id="lbl-question">Câu hỏi</label>
        <textarea id="question">Tam giác cân là gì? Giải thích cho học sinh lớp 8 bị khiếm thị.</textarea>
        <div class="actions">
          <button class="btn" onclick="askTutor()" id="btn-ask" aria-label="Gửi câu hỏi tới AI">🎓 Hỏi AI</button>
          <button class="btn blue" onclick="loadDemo('geometry')" id="btn-demo-geo">📐 Demo Hình học</button>
          <button class="btn blue" onclick="loadDemo('english')" id="btn-demo-eng">🗣 Demo Tiếng Anh</button>
          <button class="btn ghost" onclick="speakResult()" id="btn-speak">🔊 Đọc to kết quả</button>
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
    </div>

    <!-- RESULT PANEL -->
    <div class="result-panel">
      <div class="card">
        <h2 id="result-title">📋 Kết quả</h2>
        <pre id="result" aria-live="polite" aria-label="Kết quả từ AI">Sẵn sàng. Hãy đặt câu hỏi hoặc chọn một demo để bắt đầu.</pre>
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
  }
};

function setLang(lang) {
  LANG = lang;
  localStorage.setItem('ev_lang', lang);
  const T = UI[lang];
  // Toggle button states
  document.getElementById('btn-vi').classList.toggle('active', lang === 'vi');
  document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  document.getElementById('btn-vi').setAttribute('aria-pressed', lang === 'vi');
  document.getElementById('btn-en').setAttribute('aria-pressed', lang === 'en');
  document.getElementById('html-root').lang = T.htmlLang;
  // UI text
  document.getElementById('hero-title').textContent = T.heroTitle;
  document.getElementById('hero-lead').textContent = T.heroLead;
  document.getElementById('tutor-title').textContent = T.tutorTitle;
  document.getElementById('lbl-subject').textContent = T.lblSubject;
  document.getElementById('lbl-student').textContent = T.lblStudent;
  document.getElementById('lbl-question').textContent = T.lblQuestion;
  document.getElementById('opt-geo').textContent = T.optGeo;
  document.getElementById('opt-eng').textContent = T.optEng;
  document.getElementById('opt-gen').textContent = T.optGen;
  document.getElementById('btn-ask').textContent = T.btnAsk;
  document.getElementById('btn-demo-geo').textContent = T.btnDemoGeo;
  document.getElementById('btn-demo-eng').textContent = T.btnDemoEng;
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
  utt.rate = lang === 'vi' ? 0.88 : 0.92;
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
function showLoading() { document.getElementById('loading-bar').style.display='block'; }
function hideLoading() { document.getElementById('loading-bar').style.display='none'; }

function displayError(message) {
  const fallback = LANG === 'vi' ? 'Không thể xử lý yêu cầu lúc này.' : 'The request could not be processed right now.';
  document.getElementById('result').textContent = (LANG === 'vi' ? 'Lỗi: ' : 'Error: ') + (message || fallback);
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

function setResult(data) {
  let text = formatResult(data);
  document.getElementById('result').textContent = text;
  // Auto-speak: answer > OCR description+text > plain string
  let toSpeak = '';
  if (data && data.answer_text) toSpeak = data.answer_text;
  else if (data && data.answer) toSpeak = data.answer;
  else if (data && data.accessible_explanation) toSpeak = data.accessible_explanation;
  else if (data && data.description && data.ocr_text) {
    toSpeak = data.description + '. ' + (LANG==='vi' ? 'Nội dung: ' : 'Content: ') + data.ocr_text.slice(0, 600);
  } else if (data && data.ocr_text) toSpeak = data.ocr_text.slice(0, 800);
  else if (typeof data === 'string') toSpeak = data;
  if (toSpeak) speakText(toSpeak, LANG);
}

// ── DEMO PROMPTS ────────────────────────────────────────────────────────────
function loadDemo(kind) {
  const T = UI[LANG];
  document.getElementById('subject').value = kind;
  document.getElementById('question').value = kind === 'geometry' ? T.demoGeo : T.demoEng;
}

// ── API CALLS ────────────────────────────────────────────────────────────────
async function askTutor() {
  showLoading();
  try {
    const res = await fetch('/ask', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        student_id: document.getElementById('student').value,
        subject: document.getElementById('subject').value,
        question: document.getElementById('question').value,
        language: LANG
      })
    });
    setResult(await readResponse(res));
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
// Ensure voices are loaded before first use
window.speechSynthesis && window.speechSynthesis.getVoices();
window.speechSynthesis && window.speechSynthesis.addEventListener('voiceschanged', () => {});
// Apply saved language on load
setLang(LANG);
refreshStatus();
</script>
</body>
</html>"""

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "eduvision-ai",
        "version": "0.4.1",
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


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    profile = get_profile(payload.student_id)
    context = rag_search(payload.question)
    subject = payload.subject

    if subject == "geometry":
        answer = accessible_geometry_answer(payload.question, profile, context, payload.language)
        suggestions = (
            ["Yêu cầu giải thích chậm hơn", "Xin bài tương tự", "Nghe phiên bản giọng nói"]
            if payload.language == "vi"
            else ["Ask for a slower explanation", "Ask for similar exercises", "Ask for an audio version"]
        )
    elif subject == "english":
        answer = english_answer(payload.question, context, payload.language)
        suggestions = (
            ["Xin thêm ví dụ", "Luyện hội thoại ngắn", "Tạo 5 câu luyện tập"]
            if payload.language == "vi"
            else ["Ask for more examples", "Ask for a short dialogue", "Ask for 5 practice sentences"]
        )
    else:
        if payload.language == "vi":
            answer = "Cô có thể hỗ trợ Toán hình, tiếng Anh, đọc ảnh/PDF bằng OCR và lập kế hoạch học tập. Em hãy nói môn học và phần em thấy khó."
            suggestions = ["Thử /geometry", "Thử /english", "Thử /plan"]
        else:
            answer = (
                "I can help with geometry, English, OCR reading, and study planning. "
                "Please tell me the subject and what you find difficult."
            )
            suggestions = ["Try /geometry", "Try /english", "Try /plan"]

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
    out = TTS_DIR / f"eduvision-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.aiff"
    if not shutil.which("say"):
        return {"status": "unavailable", "message": "macOS say command is not available."}
    voice = payload.voice or default_voice(payload.language)
    subprocess.run(["say", "-v", voice, "-o", str(out), text], check=True)
    return {"status": "ok", "language": payload.language, "voice": voice, "audio_url": f"/media/tts/{out.name}", "file": str(out)}


@app.get("/media/tts/{filename}")
def tts_file(filename: str) -> FileResponse:
    return FileResponse(TTS_DIR / filename, media_type="audio/aiff", filename=filename)


@app.post("/demo/reset")
def reset_demo() -> Dict[str, Any]:
    init_db()
    with db() as conn:
        conn.execute("DELETE FROM learning_events")
    return {"status": "ok", "message": "Demo learning history cleared."}
