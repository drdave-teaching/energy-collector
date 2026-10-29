# ============================================================================================================
# Lab 6: the class HRRR weather job (hrrr-bdl). Paste into Cloud Shell in the COLLECTOR project. Safe to re-run.
# Every hour at :05 it writes the 8 newest HRRR runs at Bradley to the PUBLIC bucket, where every student's
# forecast-24h (range_method "hrrr_qrf_pooled") reads them at :20:
#   https://storage.googleapis.com/opim5512-leaderboard-f26/weather/bdl_hrrr_runs.csv
# ============================================================================================================
PROJECT_ID="opim5512-collector-f26"
REGION="us-central1"
PUBLIC_BUCKET="opim5512-leaderboard-f26"
COLLECTOR_SA="collector@${PROJECT_ID}.iam.gserviceaccount.com"     # already writes the public bucket
INVOKER_SA="collector-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"
rm -rf ~/energy-collector && git clone -q https://github.com/drdave-teaching/energy-collector.git ~/energy-collector
gcloud functions deploy hrrr-bdl --gen2 --region="${REGION}" --runtime=python312 \
  --source="${HOME}/energy-collector/cloud_function/hrrr-bdl" --entry-point=hrrr_bdl_http \
  --trigger-http --no-allow-unauthenticated --service-account="${COLLECTOR_SA}" \
  --set-env-vars="PUBLIC_BUCKET=${PUBLIC_BUCKET}" --memory=2Gi --timeout=540 --quiet \
  && echo "✅ 1. hrrr-bdl deployed"

gcloud functions add-invoker-policy-binding hrrr-bdl --gen2 --region="${REGION}" \
  --member="serviceAccount:${INVOKER_SA}" >/dev/null
URL=$(gcloud functions describe hrrr-bdl --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
gcloud scheduler jobs create http hrrr-bdl-hourly --location="${REGION}" --schedule="5 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST --attempt-deadline=540s \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null 2>&1 \
|| gcloud scheduler jobs update http hrrr-bdl-hourly --location="${REGION}" --schedule="5 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST --attempt-deadline=540s \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null
echo "✅ 2. runs every hour at :05"

gcloud scheduler jobs run hrrr-bdl-hourly --location="${REGION}" && echo "✅ 3. first run started (about 2 minutes)"
echo "Check in 3 minutes: curl -s https://storage.googleapis.com/${PUBLIC_BUCKET}/weather/bdl_hrrr_status.json"
