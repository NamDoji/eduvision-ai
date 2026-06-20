# EduVision AI Conference Demo Guide

## Demo Links

- Local web: http://localhost:8010/
- LAN web: http://192.168.6.101:8010/
- API docs: http://localhost:8010/docs
- Health/OCR status: http://localhost:8010/health

## 5-Minute Demo Flow

### 1. Open The Web Demo

Open the local or LAN URL. Show the status cards:

- Backend is OK.
- OCR provider is Google Vision, Tesseract, or text/PDF fallback.
- Voice output is available through macOS `say`.

### 2. Geometry Tutor

Prompt:

```text
Explain the Pythagorean theorem for a Grade 8 low-vision student
```

Expected talking point:

EduVision does not say "look at the figure". It explains with the corner of a book, two rulers, sticks, strings, and step-by-step reasoning.

### 3. English Tutor

Prompt:

```text
I have many meeting today
```

Expected result:

The system corrects the sentence to "I have many meetings today", explains plural countable nouns, and gives practice examples.

### 4. OCR Reader

Upload the prepared demo image:

```text
demo_assets/demo_geometry_ocr.png
```

It contains:

```text
Triangle ABC
AB = AC
Find angle B and angle C relationship
```

Expected talking point:

EduVision extracts text, identifies geometry relationships, and describes the diagram verbally.

### 5. Study Plan

Prompt:

```text
I am in Grade 8, weak at geometry, and can study 25 minutes per day
```

Expected result:

The system creates a weekly study plan with review, tactile explanation, practice, and progress check.

### 6. Progress Report

Click "Progress Report". Show that learning events are saved locally in SQLite and can be summarized for teachers or parents.

## Telegram/OpenClaw Demo Commands

Use these command patterns when routing through OpenClaw:

```text
/geometry I do not understand an isosceles triangle
/english I have many meeting today
/plan I am in Grade 8, weak at geometry, and can study 25 minutes per day
/profile
/report
```

## Google Vision Setup

Google Vision is already integrated in the backend. To activate it:

1. Create a Google Cloud project.
2. Enable the Cloud Vision API.
3. Create a service account JSON key.
4. Save the JSON key on this Mac.
5. Start the backend with:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/google-vision-service-account.json
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

If this variable is not set, the backend falls back to local Tesseract OCR when available.

## Fallback Plan For Live Demo

If internet or Google credentials are unavailable:

- Use Tesseract OCR for simple images.
- Use PDF/text upload for reliable extraction.
- Use prepared prompts for Geometry, English, Study Plan, and Report.

## Reset Demo Data

Before the live session, clear rehearsal history:

```bash
curl -X POST http://127.0.0.1:8010/demo/reset
```

## One-Sentence Pitch

EduVision AI turns chat, images, PDFs, and voice into accessible step-by-step tutoring for visually impaired and low-vision students.
