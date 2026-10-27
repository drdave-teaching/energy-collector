# ============================================================================================================
# Dr. Dave's class forecast collector: one-time setup. Paste into Cloud Shell, in your NEW collector project.
# Takes about 5 minutes. Safe to re-run (every step skips what already exists).
# ============================================================================================================
PROJECT_ID="opim5512-collector-f26"          # <-- the project ID you just created (Console > New project)
REGION="us-central1"
BUCKET="${PROJECT_ID}"
COLLECTOR_SA="collector@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="collector-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')

# 1. Services
gcloud services enable cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com logging.googleapis.com \
  && echo "✅ 1. services on"

# 2. Two service accounts: 'collector' reads student buckets, 'collector-invoker' starts it every hour
gcloud iam service-accounts create collector --display-name="Class forecast collector (reads student buckets)" 2>/dev/null || true
gcloud iam service-accounts create collector-invoker --display-name="Starts the class collector every hour" 2>/dev/null || true
sleep 10
echo "✅ 2. service accounts: ${COLLECTOR_SA}"

# 3. Your bucket (roster in, class tables out)
if ! gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" --role="roles/storage.objectAdmin" >/dev/null \
  && echo "✅ 3. bucket gs://${BUCKET}"

# 4. An empty roster (add students later, see README)
if ! gcloud storage objects describe "gs://${BUCKET}/roster/roster.csv" >/dev/null 2>&1; then
  printf "netid,bucket\n" | gcloud storage cp - "gs://${BUCKET}/roster/roster.csv"
fi
echo "✅ 4. roster at gs://${BUCKET}/roster/roster.csv"

# 5. Deploy the collector function
rm -rf ~/energy-collector && git clone -q https://github.com/drdave-teaching/energy-collector.git ~/energy-collector
gcloud functions deploy collect-forecasts --gen2 --region="${REGION}" --runtime=python312 \
  --source="${HOME}/energy-collector/cloud_function/collect-forecasts" --entry-point=collect_http \
  --trigger-http --no-allow-unauthenticated --service-account="${COLLECTOR_SA}" \
  --set-env-vars="COLLECTOR_BUCKET=${BUCKET}" --memory=1Gi --timeout=540 --quiet \
  && echo "✅ 5. collect-forecasts deployed"

# 6. Run it every hour at :35 (students' forecast-24h runs at :20)
gcloud functions add-invoker-policy-binding collect-forecasts --gen2 --region="${REGION}" \
  --member="serviceAccount:${INVOKER_SA}" >/dev/null
gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null 2>&1 || true
URL=$(gcloud functions describe collect-forecasts --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
gcloud scheduler jobs create http collect-forecasts-hourly --location="${REGION}" --schedule="35 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null 2>&1 \
|| gcloud scheduler jobs update http collect-forecasts-hourly --location="${REGION}" --schedule="35 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null
echo "✅ 6. runs every hour at :35"

echo
echo "Students share their bucket with:  ${COLLECTOR_SA}"
echo "Run it now:  gcloud scheduler jobs run collect-forecasts-hourly --location=${REGION}"
echo "See who is in: gcloud storage cat gs://${BUCKET}/class/status_latest.csv"
