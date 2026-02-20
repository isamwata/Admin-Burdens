# ── Backend: Python 3.11 + Chromium ──────────────────────────────────────────
FROM python:3.11-slim

# Install Chromium and its driver (stays in sync, no version mismatch)
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        wget \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Tell the scraper which binaries to use
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

# Install Python deps first (layer cached unless requirements change)
COPY requirements_api.txt .
RUN pip install --no-cache-dir -r requirements_api.txt

# Download Dutch spaCy model
RUN python -m spacy download nl_core_news_lg

# Copy the rest of the project
COPY . .

# Ensure output dirs exist
RUN mkdir -p scraped_data predictions

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
