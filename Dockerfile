# ── Stage 1: Build React frontend ────────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python 3.11 runtime ─────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (cached layer)
COPY requirements_api.txt .
RUN pip install --no-cache-dir -r requirements_api.txt

# Dutch spaCy model (needed by predictor)
RUN python -m spacy download nl_core_news_sm || true

# Application code
COPY backend/ backend/
COPY config.json .

# Download model files from HF Model Hub
RUN pip install --no-cache-dir huggingface_hub && \
    python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='isamwata/belgian-staatsblad-model', local_dir='./model', repo_type='model')"

# Built frontend from Stage 1
COPY --from=frontend-builder /build/dist frontend/dist

# Writable output dirs
RUN mkdir -p scraped_data predictions

# HF Spaces runs containers as uid 1000
RUN chown -R 1000:1000 /app
USER 1000

EXPOSE 7860

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
