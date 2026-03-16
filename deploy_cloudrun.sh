#!/bin/bash
# Deploy administrative-burden-model to Google Cloud Run
# Prerequisites:
#   - gcloud CLI installed and authenticated: gcloud auth login
#   - Project set: gcloud config set project top-branch-484813-r0
#   - APIs enabled (run once): see STEP 0 below

set -e

PROJECT_ID="admin-burdens-prod"
REGION="europe-west1"            # Belgium-adjacent; change to us-central1 for more free quota
SERVICE="admin-burdens"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}"

# ── STEP 0: Enable required APIs (run once) ──────────────────────────────────
# gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com

# ── STEP 1: Build and push image via Cloud Build ─────────────────────────────
echo "Building image with Cloud Build..."
gcloud builds submit \
  --tag "${IMAGE}" \
  --timeout=20m \
  .

# ── STEP 2: Deploy to Cloud Run ───────────────────────────────────────────────
echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 1 \
  --max-instances 3 \
  --timeout 300 \
  --set-env-vars "OPENAI_API_KEY=${OPENAI_API_KEY},POSTGRES_HOST=${POSTGRES_HOST},POSTGRES_DATABASE=${POSTGRES_DATABASE},POSTGRES_USER=${POSTGRES_USER},POSTGRES_PASSWORD=${POSTGRES_PASSWORD},POSTGRES_PORT=${POSTGRES_PORT:-25060},POSTGRES_SSLMODE=${POSTGRES_SSLMODE:-require}" \
  --port 8080

echo ""
echo "Done! Service URL:"
gcloud run services describe "${SERVICE}" --region "${REGION}" --format="value(status.url)"
