#!/bin/bash
# Deploy Cloud Run Job (batch_ingest) + Cloud Scheduler (monthly trigger)
#
# Prerequisites:
#   export OPENAI_API_KEY="..."
#   export POSTGRES_HOST="..."
#   export POSTGRES_DATABASE="defaultdb"
#   export POSTGRES_USER="doadmin"
#   export POSTGRES_PASSWORD="..."
#   export POSTGRES_PORT="25060"
#   export POSTGRES_SSLMODE="require"
#
# Run once:
#   bash deploy_job.sh

set -e

PROJECT_ID="admin-burdens-prod"
REGION="europe-west1"
JOB_NAME="batch-ingest"
IMAGE="gcr.io/${PROJECT_ID}/${JOB_NAME}"
SCHEDULER_JOB="monthly-staatsblad-ingest"

# ── STEP 1: Build and push image ──────────────────────────────────────────────
echo "Building job image..."
gcloud builds submit \
  --tag "${IMAGE}" \
  --timeout=25m \
  --dockerfile=Dockerfile.job \
  .

# ── STEP 2: Create (or update) the Cloud Run Job ──────────────────────────────
echo "Deploying Cloud Run Job..."
gcloud run jobs create "${JOB_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600 \
  --max-retries 2 \
  --set-env-vars "\
OPENAI_API_KEY=${OPENAI_API_KEY},\
POSTGRES_HOST=${POSTGRES_HOST},\
POSTGRES_DATABASE=${POSTGRES_DATABASE},\
POSTGRES_USER=${POSTGRES_USER},\
POSTGRES_PASSWORD=${POSTGRES_PASSWORD},\
POSTGRES_PORT=${POSTGRES_PORT:-25060},\
POSTGRES_SSLMODE=${POSTGRES_SSLMODE:-require}" \
  --args="--start,1997-06" \
  2>/dev/null || \
gcloud run jobs update "${JOB_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600 \
  --max-retries 2 \
  --set-env-vars "\
OPENAI_API_KEY=${OPENAI_API_KEY},\
POSTGRES_HOST=${POSTGRES_HOST},\
POSTGRES_DATABASE=${POSTGRES_DATABASE},\
POSTGRES_USER=${POSTGRES_USER},\
POSTGRES_PASSWORD=${POSTGRES_PASSWORD},\
POSTGRES_PORT=${POSTGRES_PORT:-25060},\
POSTGRES_SSLMODE=${POSTGRES_SSLMODE:-require}" \
  --args="--start,1997-06"

# ── STEP 3: Create Cloud Scheduler — runs on the 2nd of every month ──────────
echo "Setting up Cloud Scheduler..."

# Get the project number for the Cloud Run invoker SA
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
INVOKER_SA="batch-ingest-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# Create invoker service account if it doesn't exist
gcloud iam service-accounts describe "${INVOKER_SA}" 2>/dev/null || \
gcloud iam service-accounts create "batch-ingest-invoker" \
  --display-name="Batch Ingest Cloud Run Invoker" \
  --project="${PROJECT_ID}"

# Grant it permission to invoke Cloud Run Jobs
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INVOKER_SA}" \
  --role="roles/run.invoker" \
  --condition=None \
  --quiet

gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
  --location="${REGION}" \
  --schedule="0 4 2 * *" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
  --message-body="{}" \
  --oauth-service-account-email="${INVOKER_SA}" \
  --time-zone="Europe/Brussels" \
  2>/dev/null || \
gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
  --location="${REGION}" \
  --schedule="0 4 2 * *" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
  --message-body="{}" \
  --oauth-service-account-email="${INVOKER_SA}" \
  --time-zone="Europe/Brussels"

echo ""
echo "✅  Deployment complete"
echo ""
echo "To kick off the historical backfill immediately:"
echo "  gcloud run jobs execute ${JOB_NAME} --region ${REGION}"
echo ""
echo "Scheduler: runs at 04:00 Brussels time on the 2nd of each month"
echo "To check job executions:"
echo "  gcloud run jobs executions list --job ${JOB_NAME} --region ${REGION}"
