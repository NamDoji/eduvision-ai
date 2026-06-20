# EduVision AI Implementation Plan

## Phase 1: MVP, 2-4 weeks

1. Telegram bot through OpenClaw.
2. `eduvision-ai` Skill with `/ask`, `/geometry`, `/english`, `/plan`, `/ocr`, `/profile`, `/report`.
3. FastAPI backend.
4. Basic student profile JSON/PostgreSQL table.
5. Seed `train.csv` and `train.jsonl`.
6. OCR placeholder, then Tesseract or Google Vision.
7. Simple RAG from curated textbook/worksheet excerpts.

## Phase 2: Research Demo, 1-2 months

1. Better OCR for images/PDFs.
2. Voice output.
3. Teacher dashboard.
4. 100-500 curated training/RAG samples.
5. Pilot evaluation report.

## Phase 3: Product, 3-6 months

1. Mobile app or web app.
2. Fine-grained personalization.
3. Fine-tuning based on approved data.
4. More subjects.
5. School integration.

## OpenClaw Tool Policy

Student-facing skill should only call the EduVision backend API and media/OCR pipeline. It should not receive broad shell, email, filesystem, or calendar permissions.
