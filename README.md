# EduVision AI

Trợ lý học tập thông minh cho học sinh **khiếm thị và thị lực kém** — hỗ trợ tiếng Việt và tiếng Anh.

Được xây dựng bởi **Đỗ Bảo Nam** để tặng miễn phí cho các trường khiếm thị tại Việt Nam.

---

## Tính năng

- Giải thích bài học bằng ngôn ngữ xúc giác (không dùng hình ảnh)
- OCR đọc đề bài từ ảnh chụp — 5 tầng fallback, chạy offline lẫn online
- Text-to-Speech tiếng Việt và tiếng Anh
- Kế hoạch học tập cá nhân
- Song ngữ Việt / Anh trong mọi câu trả lời
- Chi phí vận hành gần 0đ (dùng Groq free tier)

---

## Cài đặt (5 bước)

**Yêu cầu**: Python 3.12, pip, git

```bash
# 1. Clone repo
git clone https://github.com/NamDoji/eduvision-ai.git
cd eduvision-ai

# 2. Tạo môi trường ảo
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 3. Cài thư viện
pip install -r requirements.txt

# 4. Cấu hình API key
cp .env.example .env
# Mở file .env, điền GROQ_API_KEY (lấy miễn phí tại https://console.groq.com)

# 5. Khởi động
uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

Mở trình duyệt: **http://localhost:8010**

---

## Lấy Groq API Key (miễn phí)

1. Truy cập https://console.groq.com → Đăng ký bằng Google
2. Vào **API Keys** → **Create API Key**
3. Sao chép key (bắt đầu bằng `gsk_...`) vào file `.env`

---

## Kiểm tra nhanh

```bash
# Kiểm tra hệ thống
curl http://localhost:8010/health

# Hỏi bài (tiếng Việt)
curl -X POST http://localhost:8010/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":"S001","question":"Giải thích tam giác cân cho học sinh khiếm thị","subject":"geometry","language":"vi"}'

# OCR ảnh đề bài
curl -X POST http://localhost:8010/ocr \
  -F "file=@demo_assets/demo_geometry_ocr.png"
```

---

## Kịch bản demo tại trường

Xem file **`DEMO_GUIDE.md`** — kịch bản demo 5 phút đầy đủ, kèm kế hoạch dự phòng khi mất mạng.

---

## Cấu trúc dự án

```
app/
  main.py           — FastAPI backend + UI
  llm_service.py    — Groq Llama 3.3 70B
  ocr_service.py    — OCR 5 tầng fallback
  accessibility.py  — Bộ lọc ngôn ngữ xúc giác
  rag_service.py    — Tìm kiếm kho tri thức
data/
  knowledge_base.md — Kho tri thức học tập
  student_profiles.json
demo_assets/        — Ảnh demo OCR
tests/              — pytest
```

---

## Tài liệu kỹ thuật

Xem **`TECHNICAL_REPORT.md`** để biết kiến trúc, bảo mật, và roadmap.

---

## License

MIT © 2026 Đỗ Bảo Nam — Miễn phí cho mọi mục đích giáo dục, kể cả sửa đổi và phân phối lại.
