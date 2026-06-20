# EduVision AI Technical Report

## Objective

EduVision AI is a local OpenClaw-based demo system for personalized learning support for visually impaired and low-vision students. It supports accessible geometry tutoring, English correction, OCR reading, study planning, text-to-speech, student profiles, and progress reports.

## Architecture

```text
Student
  -> Telegram / Web / Future Zalo / Future WhatsApp
  -> OpenClaw Gateway
  -> eduvision-ai Skill
  -> FastAPI Backend
  -> OCR + RAG + Student Profile + LLM-ready Tutor Logic
  -> Text or Audio Response
```

## Implemented Components

- FastAPI backend.
- Local web demo.
- OpenClaw skill `eduvision-ai`.
- SQLite profile and learning history database.
- `/ask` AI tutor endpoint.
- `/command` command router for `/geometry`, `/english`, `/plan`, `/profile`, `/report`.
- `/ocr` file reader endpoint.
- Google Vision OCR integration with credential-based activation.
- Tesseract OCR fallback.
- `/study-plan` endpoint.
- `/profile` and `/report` endpoints.
- `/tts` endpoint using macOS `say`.
- `/demo/reset` endpoint for conference rehearsal cleanup.
- Synthetic train.csv and train.jsonl samples.
- RAG-style local knowledge base.

## API Summary

### POST /ask

Handles tutoring questions for `geometry`, `english`, or `general`.

### POST /command

Routes OpenClaw-style chat commands to the correct backend function.

### POST /ocr

Accepts PDF, image, text, or CSV files. For images, uses Google Vision if credentials are configured, otherwise uses Tesseract when installed.

### POST /study-plan

Creates a weekly study plan from grade, weakness, and available learning time.

### GET /profile/{student_id}

Returns student profile.

### POST /profile

Creates or updates student profile.

### GET /report/{student_id}

Returns recent learning activity and recommendations.

### POST /tts

Creates an audio file from text using macOS voice output.

### POST /demo/reset

Clears demo learning history so the progress report can be shown cleanly during rehearsal or live presentation.

## Data

The demo uses synthetic data only:

- `data/train.csv`
- `data/train.jsonl`
- `data/knowledge_base.md`
- `data/student_profiles.json`
- `data/eduvision.db`

Real student names should not be used for training or demo logs without consent and anonymization.

## Security Model

EduVision keeps student-facing capability narrow:

- No student shell access.
- No email/calendar/private account permissions.
- No broad filesystem permission.
- Backend exposes only learning-specific APIs.
- Sensitive student data should be masked in reports.
- Unsafe content should be escalated to teacher or parent review.

## Current Local Demo Limits

- The demo has deterministic tutor logic plus RAG-style context. It is LLM-ready but not yet wired to a production LLM endpoint for every answer.
- Google Vision requires a Google Cloud service-account JSON key.
- SQLite is used for local demo. PostgreSQL and Qdrant should be used for production.
- Zalo and WhatsApp are future channels.

## Production Roadmap

1. Add production LLM provider with guardrails.
2. Replace simple RAG with Qdrant or ChromaDB.
3. Move SQLite to PostgreSQL.
4. Add teacher/parent dashboard.
5. Add consent, anonymization, audit logging, and role-based access control.
6. Add Zalo/WhatsApp channel adapters.
7. Add evaluation with visually impaired students and teachers.
