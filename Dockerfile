FROM python:3.12-slim

# Cài Tesseract OCR (fallback khi không có OCR.space)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-vie \
    liblouis-dev \
    python3-louis \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV EDUVISION_WRITE_DIR=/data
VOLUME ["/data"]

EXPOSE 8010

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
