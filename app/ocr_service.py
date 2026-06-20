from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path


def provider_status() -> dict[str, str | bool]:
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    tesseract = shutil.which("tesseract") or ""
    return {
        "google_vision_package": importlib.util.find_spec("google.cloud.vision") is not None,
        "google_application_credentials": credentials,
        "google_credentials_file_exists": bool(credentials and Path(credentials).exists()),
        "tesseract_available": tesseract,
        "recommended_provider": "google_vision" if credentials and Path(credentials).exists() else "tesseract" if tesseract else "text_pdf_only",
    }


def classify_content(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ["triangle", "tam giác", "angle", "góc", "ab", "ac"]):
        return "geometry"
    if "|" in text or "\t" in text or "," in text and "\n" in text:
        return "table"
    if any(token in lowered for token in ["axis", "chart", "graph", "biểu đồ", "trục"]):
        return "chart_or_graph"
    return "plain_text"


def accessible_description(text: str, language: str = "en") -> str:
    kind = classify_content(text)
    if not text or "not available" in text or "failed" in text.lower():
        return "Chưa trích xuất được chữ đáng tin cậy." if language == "vi" else "I could not extract reliable text yet."
    if language == "vi":
        return {
            "geometry": "Tài liệu có nội dung hình học. Cô sẽ mô tả điểm, cạnh, góc, cạnh bằng nhau, đường song song/vuông góc và yêu cầu bài toán bằng lời.",
            "table": "Tài liệu có thể chứa bảng. Cô sẽ đọc tiêu đề, hàng, cột và các giá trị quan trọng.",
            "chart_or_graph": "Tài liệu có thể chứa biểu đồ. Cô sẽ đọc tiêu đề, trục, nhãn, giá trị, xu hướng và kết luận.",
            "plain_text": "Tài liệu có vẻ là văn bản. Cô sẽ tóm tắt và giải thích nhiệm vụ học tập.",
        }[kind]
    return {
        "geometry": "The material appears to contain geometry. I will describe points, lines, angles, equal sides, parallel or perpendicular lines, and the task verbally.",
        "table": "The material may contain a table. I will read the title, columns, rows, and key values.",
        "chart_or_graph": "The material may contain a chart or graph. I will read the title, axes, labels, values, trends, and conclusion.",
        "plain_text": "The material appears to be text. I will summarize it and explain the learning task.",
    }[kind]

