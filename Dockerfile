# ── Stage 1: Build React frontend ────────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python 3.11 + Chromium runtime ──────────────────────────────────
FROM python:3.11-slim

# Chromium stays in sync with its driver — no version mismatch
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium chromium-driver \
        wget ca-certificates gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Tell the scraper where the binaries live
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

# Python deps (cached layer)
COPY requirements_api.txt .
RUN pip install --no-cache-dir -r requirements_api.txt

# Dutch spaCy model (needed by predictor)
RUN python -m spacy download nl_core_news_sm || true

# Application code + model
COPY backend/ backend/
COPY config.json .
COPY model/ model/

# Built frontend from Stage 1
COPY --from=frontend-builder /build/dist frontend/dist

# Writable output dirs
RUN mkdir -p scraped_data predictions

# HF Spaces runs containers as uid 1000
RUN chown -R 1000:1000 /app
USER 1000

EXPOSE 7860

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
