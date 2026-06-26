from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path


OCR_PROVIDER = os.environ.get("OCR_PROVIDER", "auto")
# auto: tries ocrspace → google_vision → paddleocr → easyocr → tesseract
# ocrspace:    OCR.space API (FREE 500 pages/day, cloud, works on Vercel)
# google_vision: Google Cloud Vision (1000 free units/month)
# paddleocr:   PaddleOCR (free, local, good for Vietnamese)
# easyocr:     EasyOCR (free, local, good for multilingual)
# tesseract:   Tesseract (free, local, needs install)

OCR_SPACE_API_KEY = os.environ.get("OCR_SPACE_API_KEY", "helloworld")
# Register free at https://ocr.space/ocrapi → get API key (500 pages/day, no credit card)
# Use OCR_SPACE_API_KEY env var on Vercel. Default 'helloworld' is demo-only.


def provider_status() -> dict[str, str | bool]:
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    tesseract = shutil.which("tesseract") or ""
    paddle_ok = importlib.util.find_spec("paddleocr") is not None
    easy_ok = importlib.util.find_spec("easyocr") is not None
    google_ok = importlib.util.find_spec("google.cloud.vision") is not None
    google_configured = bool(credentials and Path(credentials).exists())
    ocrspace_key = OCR_SPACE_API_KEY
    ocrspace_configured = bool(ocrspace_key)  # any key (incl. demo 'helloworld') enables OCR.space
    ocrspace_demo = ocrspace_key == "helloworld"

    if OCR_PROVIDER != "auto":
        recommended = OCR_PROVIDER
    elif ocrspace_configured:
        recommended = "ocrspace"
    elif google_configured:
        recommended = "google_vision"
    elif paddle_ok:
        recommended = "paddleocr"
    elif easy_ok:
        recommended = "easyocr"
    elif tesseract:
        recommended = "tesseract"
    else:
        recommended = "text_pdf_only"

    return {
        "configured_provider": OCR_PROVIDER,
        "ocrspace_configured": ocrspace_configured,
        "ocrspace_demo_key": ocrspace_demo,
        "ocrspace_key_hint": ocrspace_key[:4] + "****" if ocrspace_key else "",
        "google_vision_package": google_ok,
        "google_application_credentials": credentials,
        "google_credentials_file_exists": google_configured,
        "paddleocr_available": paddle_ok,
        "easyocr_available": easy_ok,
        "tesseract_available": tesseract,
        "recommended_provider": recommended,
        "free_cloud_providers": ["ocrspace (500 pages/day, register at ocr.space)"],
        "free_local_providers": ["paddleocr", "easyocr", "tesseract"],
        "paid_with_free_tier": ["google_vision (1000 req/month)"],
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


def extract_with_paddleocr(path: Path, lang: str = "vi") -> str:
    """PaddleOCR — free, local, excellent for Vietnamese and math."""
    try:
        from paddleocr import PaddleOCR
        ocr_lang = "vi" if lang in ("vi", "vn") else "en"
        ocr = PaddleOCR(use_angle_cls=True, lang=ocr_lang, show_log=False)
        result = ocr.ocr(str(path), cls=True)
        lines = []
        for block in result or []:
            if block:
                for line in block:
                    if line and len(line) >= 2 and line[1]:
                        text_info = line[1]
                        if isinstance(text_info, (list, tuple)) and text_info:
                            lines.append(str(text_info[0]))
                        else:
                            lines.append(str(text_info))
        return "\n".join(lines).strip() or "PaddleOCR did not find readable text."
    except Exception as exc:
        return f"PaddleOCR failed: {exc}"


def extract_with_easyocr(path: Path, lang: str = "vi") -> str:
    """EasyOCR — free, local, supports 80+ languages including Vietnamese."""
    try:
        import easyocr
        langs = ["vi", "en"] if lang == "vi" else ["en"]
        reader = easyocr.Reader(langs, gpu=False, verbose=False)
        result = reader.readtext(str(path))
        lines = [item[1] for item in result if item and len(item) >= 2]
        return "\n".join(lines).strip() or "EasyOCR did not find readable text."
    except Exception as exc:
        return f"EasyOCR failed: {exc}"


def extract_with_ocrspace(path: Path, lang: str = "vi") -> str:
    """OCR.space — FREE cloud OCR, 500 pages/day. Works on Vercel serverless.
    Register free API key at https://ocr.space/ocrapi (no credit card).
    Set OCR_SPACE_API_KEY env var on Vercel. Default 'helloworld' = demo only.
    """
    try:
        import requests
        lang_map = {"vi": "vie", "en": "eng"}
        ocr_lang = lang_map.get(lang, "eng")
        suffix = path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".pdf": "application/pdf",
                    ".webp": "image/webp"}
        mime = mime_map.get(suffix, "application/octet-stream")
        with open(path, "rb") as f:
            resp = requests.post(
                "https://api.ocr.space/parse/image",
                files={"file": (path.name, f, mime)},
                data={
                    "apikey": OCR_SPACE_API_KEY,
                    "language": ocr_lang,
                    "isTable": "true",
                    "scale": "true",
                    "OCREngine": "2",  # Engine 2 is more accurate
                },
                timeout=30,
            )
        result = resp.json()
        if result.get("IsErroredOnProcessing"):
            msgs = result.get("ErrorMessage") or []
            return f"OCR.space failed: {'; '.join(msgs) if isinstance(msgs, list) else msgs}"
        texts = [p.get("ParsedText", "") for p in result.get("ParsedResults", [])]
        return "\n".join(texts).strip() or "OCR.space did not find readable text."
    except Exception as exc:
        return f"OCR.space failed: {exc}"
