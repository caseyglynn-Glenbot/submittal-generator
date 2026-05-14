# Submittal Generator service — runs on Render or any Docker host.
# Includes Python + Tesseract OCR + poppler-utils for the PDF pipeline.

FROM python:3.11-slim

# System dependencies for OCR and PDF rasterization
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (caches well across code changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY *.py ./
COPY templates/ ./templates/

# Render sets $PORT; default to 8765 for local dev
ENV PORT=8765
EXPOSE 8765

# Use shell form so $PORT expands
CMD uvicorn service:app --host 0.0.0.0 --port $PORT --workers 1
