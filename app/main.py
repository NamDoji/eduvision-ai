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
DB_FILE = DATA_DIR / "eduvision.db"
MEDIA_DIR = BASE_DIR / "media"
TTS_DIR = MEDIA_DIR / "tts"

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
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
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
    with db() as conn:
        conn.execute(
            "INSERT INTO learning_events (student_id, subject, input, output, created_at) VALUES (?, ?, ?, ?, ?)",
            (student_id, subject, input_text, output_text, datetime.utcnow().isoformat()),
        )


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
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    return {
        "google_vision_package": importlib.util.find_spec("google.cloud.vision") is not None,
        "google_application_credentials": credentials or "",
        "google_credentials_file_exists": bool(credentials and Path(credentials).exists()),
        "tesseract_available": shutil.which("tesseract") or "",
        "recommended_provider": (
            "google_vision"
            if credentials and Path(credentials).exists()
            else "tesseract"
            if shutil.which("tesseract")
            else "text_pdf_only"
        ),
    }


def default_voice(language: str) -> str:
    return "Linh" if language == "vi" else "Samantha"


def available_tts_voices() -> Dict[str, str]:
    if not shutil.which("say"):
        return {"en": "", "vi": ""}
    return {"en": "Samantha", "vi": "Linh"}


def accessible_geometry_answer(question: str, profile: Dict[str, Any], context: List[str], language: str = "en") -> str:
    grade = profile.get("grade", "your grade")
    context_text = "\n".join(f"- {item}" for item in context[:2])
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


def extract_image_text(path: Path) -> str:
    status = ocr_status()
    if status["recommended_provider"] == "google_vision":
        text = extract_image_text_google_vision(path)
        if text and "failed" not in text.lower() and "not configured" not in text.lower():
            return text
    if status["tesseract_available"]:
        return extract_image_text_tesseract(path)
    return extract_image_text_google_vision(path)


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


def localized_plan(payload: StudyPlanRequest) -> StudyPlanResponse:
    if payload.language == "vi":
        return StudyPlanResponse(
            weekly_plan=[
                f"Ngày 1: Ôn lại kiến thức cơ bản về {payload.weakness} trong {payload.available_time}.",
                "Ngày 2: Học một khái niệm mới bằng mô tả lời nói và ví dụ có thể sờ/chạm.",
                "Ngày 3: Làm 5 bài tập cơ bản có gợi ý từng bước.",
                "Ngày 4: Xem lại lỗi sai và yêu cầu giải thích chậm hơn nếu cần.",
                "Ngày 5: Làm 5 bài tương tự một cách độc lập.",
                "Ngày 6: Tự nói lại khái niệm bằng lời của mình.",
                "Ngày 7: Làm bài kiểm tra ngắn và cập nhật hồ sơ học tập.",
            ]
        )
    return StudyPlanResponse(
        weekly_plan=[
            f"Day 1: Review the basic ideas of {payload.weakness} for {payload.available_time}.",
            "Day 2: Learn one concept using touch-based examples and simple words.",
            "Day 3: Practice 5 basic exercises with step-by-step hints.",
            "Day 4: Review mistakes and ask for a slower explanation.",
            "Day 5: Do 5 similar exercises independently.",
            "Day 6: Explain the concept back in your own words.",
            "Day 7: Take a short review quiz and update the student profile.",
        ]
    )


@app.get("/", response_class=HTMLResponse)
def web_demo() -> str:
    return """
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EduVision AI Conference Demo</title>
  <style>
    :root {
      color-scheme: light;
      --red: #c41230;
      --blue: #12355b;
      --ink: #172033;
      --muted: #667085;
      --line: #d9e2ef;
      --soft: #f6f8fb;
      --panel: #ffffff;
      font-family: Inter, Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--soft); color: var(--ink); }
    header { background: #fff; border-bottom: 1px solid var(--line); }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 20px; }
    .topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
    .brand { display: flex; align-items: center; gap: 12px; font-weight: 800; color: var(--blue); }
    .mark { width: 38px; height: 38px; border-radius: 8px; background: var(--red); color: #fff; display: grid; place-items: center; font-weight: 900; }
    .badge { border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; color: var(--muted); font-size: 14px; background: #fff; }
    .hero { padding: 28px 20px 12px; }
    h1 { margin: 0; font-size: clamp(30px, 4vw, 52px); line-height: 1.04; color: var(--blue); letter-spacing: 0; max-width: 860px; }
    .lead { color: var(--muted); font-size: 18px; line-height: 1.55; max-width: 760px; margin: 14px 0 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 8px 20px 44px; }
    .status { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 18px 0; }
    .stat, section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
    .stat { padding: 14px; min-height: 78px; }
    .stat strong { display: block; color: var(--blue); margin-bottom: 6px; font-size: 13px; text-transform: uppercase; }
    .stat span { color: var(--ink); font-weight: 700; overflow-wrap: anywhere; }
    .grid { display: grid; grid-template-columns: 1.05fr .95fr; gap: 16px; align-items: start; }
    section { padding: 18px; margin-bottom: 16px; }
    h2 { margin: 0 0 12px; font-size: 20px; color: var(--blue); }
    h3 { margin: 16px 0 8px; font-size: 15px; color: var(--blue); }
    label { display: block; font-weight: 700; margin: 12px 0 6px; }
    textarea, input, select {
      width: 100%; padding: 12px; border: 1px solid #c7d0dd; border-radius: 6px; font-size: 16px; background: #fff;
    }
    textarea { min-height: 118px; resize: vertical; }
    button {
      margin-top: 12px; margin-right: 8px; padding: 12px 15px; border: 0; border-radius: 6px;
      background: var(--red); color: white; font-weight: 800; cursor: pointer;
    }
    button.secondary { background: var(--blue); }
    button.ghost { background: #eef3f8; color: var(--blue); border: 1px solid var(--line); }
    pre {
      white-space: pre-wrap; background: #111827; color: #e8eef8; border-radius: 8px; padding: 16px;
      min-height: 360px; max-height: 620px; overflow: auto; line-height: 1.45;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .row.three { grid-template-columns: 1fr 1fr 1fr; }
    .script { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
    .script li { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; background: #fbfcfe; color: #344054; }
    .script b { color: var(--red); }
    .muted { color: var(--muted); font-size: 14px; line-height: 1.45; }
    .actions { display: flex; flex-wrap: wrap; gap: 0; }
    @media (max-width: 860px) {
      .grid, .row, .row.three, .status { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; }
      button { width: 100%; margin-right: 0; }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap topbar">
      <div class="brand"><div class="mark">EV</div><div>EduVision AI</div></div>
      <div class="badge">Conference Local Demo</div>
    </div>
    <div class="wrap hero">
      <h1>Trợ lý học tập cá nhân hóa cho học sinh khiếm thị và nhìn mờ</h1>
      <p class="lead">Demo chạy local qua OpenClaw + FastAPI, hỗ trợ Toán hình, tiếng Anh, OCR ảnh/PDF, kế hoạch học tập, giọng nói và báo cáo tiến bộ.</p>
    </div>
  </header>

  <main>
    <div class="status" id="status">
      <div class="stat"><strong>Backend</strong><span>Checking...</span></div>
      <div class="stat"><strong>OCR</strong><span>Checking...</span></div>
      <div class="stat"><strong>Voice</strong><span>macOS say</span></div>
      <div class="stat"><strong>Gateway</strong><span>OpenClaw ready</span></div>
    </div>

    <div class="grid">
      <div>
        <section>
          <h2>AI Tutor</h2>
          <div class="row three">
            <div>
              <label for="subject">Subject</label>
              <select id="subject">
                <option value="geometry">Geometry</option>
                <option value="english">English</option>
                <option value="general">General</option>
              </select>
            </div>
            <div>
              <label for="student">Student ID</label>
              <input id="student" value="S001" />
            </div>
            <div>
              <label for="language">Language / Ngôn ngữ</label>
              <select id="language">
                <option value="en">English voice</option>
                <option value="vi">Tiếng Việt - giọng Linh</option>
              </select>
            </div>
          </div>
          <label for="question">Question</label>
          <textarea id="question">Explain the Pythagorean theorem for a Grade 8 low-vision student</textarea>
          <div class="actions">
            <button onclick="askTutor()">Ask Tutor</button>
            <button class="secondary" onclick="loadPrompt('geometry')">Geometry Demo</button>
            <button class="secondary" onclick="loadPrompt('english')">English Demo</button>
            <button class="ghost" onclick="speakResult()">Speak Result</button>
          </div>
        </section>

        <section>
          <h2>Study Plan & Report</h2>
          <div class="row">
            <div>
              <label for="weakness">Weakness</label>
              <input id="weakness" value="geometry" />
            </div>
            <div>
              <label for="time">Available time</label>
              <input id="time" value="25 minutes per day" />
            </div>
          </div>
          <button onclick="studyPlan()">Create Plan</button>
          <button class="secondary" onclick="report()">Progress Report</button>
          <button class="ghost" onclick="resetDemo()">Reset Demo Data</button>
        </section>

        <section>
          <h2>OCR Learning Reader</h2>
          <p class="muted">Upload ảnh hoặc PDF bài tập. Hệ thống ưu tiên Google Vision khi có credential, sau đó fallback Tesseract local.</p>
          <input id="ocrFile" type="file" />
          <button onclick="ocr()">Read File</button>
        </section>

        <section>
          <h2>Demo Script</h2>
          <ul class="script">
            <li><b>1.</b> Geometry: explain Pythagorean theorem without visual-only language.</li>
            <li><b>2.</b> English: correct “I have many meeting today”.</li>
            <li><b>3.</b> OCR: read image/PDF and describe geometry relationships.</li>
            <li><b>4.</b> Plan: create a 7-day personalized learning plan.</li>
            <li><b>5.</b> Report: show saved learning history and recommendation.</li>
          </ul>
        </section>
      </div>

      <section>
        <h2>Result</h2>
        <pre id="result">Ready for conference demo.</pre>
      </section>
    </div>
  </main>

  <script>
    const prompts = {
      geometry: {subject: 'geometry', text: 'Explain the Pythagorean theorem for a Grade 8 low-vision student'},
      english: {subject: 'english', text: 'I have many meeting today'}
    };
    async function refreshStatus() {
      const res = await fetch('/health');
      const data = await res.json();
      document.getElementById('status').innerHTML = `
        <div class="stat"><strong>Backend</strong><span>${data.status} - ${data.version}</span></div>
        <div class="stat"><strong>OCR</strong><span>${data.ocr.recommended_provider}</span></div>
        <div class="stat"><strong>Voice</strong><span>${data.tts_available ? 'available' : 'unavailable'}</span></div>
        <div class="stat"><strong>Google Vision</strong><span>${data.ocr.google_credentials_file_exists ? 'configured' : 'not configured'}</span></div>`;
    }
    function loadPrompt(kind) {
      document.getElementById('subject').value = prompts[kind].subject;
      document.getElementById('question').value = prompts[kind].text;
    }
    async function askTutor() {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          student_id: document.getElementById('student').value,
          subject: document.getElementById('subject').value,
          question: document.getElementById('question').value,
          language: document.getElementById('language').value
        })
      });
      document.getElementById('result').textContent = JSON.stringify(await res.json(), null, 2);
    }

    async function studyPlan() {
      const res = await fetch('/study-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          student_id: document.getElementById('student').value,
          grade: 'Grade 8',
          weakness: document.getElementById('weakness').value,
          available_time: document.getElementById('time').value,
          language: document.getElementById('language').value
        })
      });
      document.getElementById('result').textContent = JSON.stringify(await res.json(), null, 2);
    }

    async function report() {
      const res = await fetch('/report/' + encodeURIComponent(document.getElementById('student').value));
      document.getElementById('result').textContent = JSON.stringify(await res.json(), null, 2);
    }

    async function ocr() {
      const input = document.getElementById('ocrFile');
      if (!input.files.length) return;
      const form = new FormData();
      form.append('file', input.files[0]);
      form.append('language', document.getElementById('language').value);
      const res = await fetch('/ocr', { method: 'POST', body: form });
      document.getElementById('result').textContent = JSON.stringify(await res.json(), null, 2);
    }

    async function speakResult() {
      const text = document.getElementById('result').textContent;
      const res = await fetch('/tts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, language: document.getElementById('language').value})
      });
      const data = await res.json();
      document.getElementById('result').textContent = JSON.stringify(data, null, 2);
      if (data.audio_url) window.open(data.audio_url, '_blank');
    }
    async function resetDemo() {
      const res = await fetch('/demo/reset', { method: 'POST' });
      document.getElementById('result').textContent = JSON.stringify(await res.json(), null, 2);
    }
  </script>
  <script>refreshStatus();</script>
</body>
</html>
"""


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
    return AskResponse(answer=answer, subject=subject, suggestions=suggestions, context_used=context)


@app.post("/ocr")
async def ocr(file: UploadFile = File(...), language: str = Form("en")) -> Dict[str, Any]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", file.filename or "upload.bin")
    path = MEDIA_DIR / safe_name
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
