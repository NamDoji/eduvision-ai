# EduVision AI Local Conference Demo

EduVision AI is an OpenClaw-based learning assistant for visually impaired and low-vision students.

## Components

- OpenClaw Gateway for Telegram/Zalo/WhatsApp/Web entry points
- EduVision Skill: `eduvision-ai`
- FastAPI backend with `/ask`, `/ocr`, `/study-plan`, `/command`, `/profile`, `/report`, `/tts`, `/vision-status`, `/demo/reset`
- SQLite student profile and learning history database
- Training data seeds: `train.csv`, `train.jsonl`
- RAG seed knowledge base
- Local web demo at `/`
- Google Vision OCR integration when credentials are configured
- Tesseract OCR fallback for local image OCR
- macOS voice output through `say`
- Language selector for English/Vietnamese responses and matching TTS voices (`Samantha` for English, `Linh` for Vietnamese)
- Conference documents: `DEMO_GUIDE.md`, `TECHNICAL_REPORT.md`
- Prepared OCR demo image: `demo_assets/demo_geometry_ocr.png`

## Run Locally

```bash
cd /Users/cuongdoji/.openclaw/workspace/code_projects/eduvision-ai
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

## Test

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/vision-status

curl -X POST http://127.0.0.1:8010/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":"S001","question":"I do not understand an isosceles triangle","subject":"geometry","language":"en"}'

curl -X POST http://127.0.0.1:8010/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":"S001","question":"Giải thích định lý Pythagore cho học sinh nhìn mờ","subject":"geometry","language":"vi"}'

curl -X POST http://127.0.0.1:8010/command \
  -H 'Content-Type: application/json' \
  -d '{"student_id":"S001","message":"/english I have many meeting today","language":"vi"}'

curl -X POST http://127.0.0.1:8010/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Xin chào, đây là EduVision AI.","language":"vi"}'

curl http://127.0.0.1:8010/report/S001
curl -X POST http://127.0.0.1:8010/demo/reset
```

## Google Vision

Google Vision support is already coded. To enable it, set a service-account credential before starting the backend:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/google-vision-service-account.json
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Without that variable, the backend uses Tesseract when installed, then PDF/text fallback.

## Conference Demo

Open `DEMO_GUIDE.md` for the 5-minute demo script and fallback plan.

## Security Notes

- Keep OpenClaw tool policy narrow.
- Do not grant shell/email/calendar access to student-facing sessions.
- Store student profiles with privacy controls.
- Log requests but mask personally identifiable data in reports.
- Escalate unsafe or non-learning content to teacher/parent review.
