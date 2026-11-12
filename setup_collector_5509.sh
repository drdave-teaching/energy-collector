# ============================================================================================================
# OPIM 5509 leaderboard, in its OWN Google Cloud project (split from OPIM 5512 on 11/12/2026).
# 5509 never shares a project, a bucket, a service account or a page with 5512.
#
# BEFORE you paste: Console > New project > Project ID = opim5509-collector-f26 (or edit PROJECT_ID below),
# and link billing. Then paste this whole block into Cloud Shell. About 8 minutes. Safe to re-run.
#
# It builds, in that project:
#   collect-forecasts-5509  every hour at :40, scores every 5509 student's forecast (Keras + forecast-24h-dl only)
#   hrrr-bdl                every hour at :05, the class HRRR weather file that students' forecast-24h-dl reads at :20
#   gs://opim5509-dl-collector-f26     private: roster in, class tables out
#   gs://opim5509-dl-leaderboard-f26   public: the leaderboard page (nicknames only) + weather/bdl_hrrr_runs.csv
# ============================================================================================================
PROJECT_ID="opim5509-collector-f26"
REGION="us-central1"
ACTUALS_BUCKET="energy-dww05002-f26-5509"     # a 5509 energy bucket where scraper-isone runs: the live CT load it scores against
BUCKET="opim5509-dl-collector-f26"            # private: roster in, class tables out
PUBLIC_BUCKET="opim5509-dl-leaderboard-f26"   # public: the page students see
FUNCTION="collect-forecasts-5509"
JOB="collect-forecasts-5509-hourly"
COLLECTOR_SA="collector@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="collector-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
if [ -z "${PROJECT_NUMBER}" ]; then echo "⚠️  Project ${PROJECT_ID} not found: create it (and link billing), then paste this again. Ignore the errors below."; fi

# 1. Services
gcloud services enable cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com logging.googleapis.com \
  && echo "✅ 1. services on"

# 2. Two service accounts: 'collector' reads student buckets, 'collector-invoker' starts the jobs every hour
gcloud iam service-accounts create collector --display-name="OPIM 5509 forecast collector (reads student buckets)" 2>/dev/null || true
gcloud iam service-accounts create collector-invoker --display-name="Starts the OPIM 5509 collector every hour" 2>/dev/null || true
sleep 15
echo "✅ 2. service accounts: ${COLLECTOR_SA}"

# 3. The private bucket and an empty roster
if ! gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" --role="roles/storage.objectAdmin" >/dev/null \
  && echo "✅ 3. bucket gs://${BUCKET}"
if ! gcloud storage objects describe "gs://${BUCKET}/roster/roster.csv" >/dev/null 2>&1; then
  printf "netid,bucket,nickname\n" | gcloud storage cp - "gs://${BUCKET}/roster/roster.csv"
fi
echo "✅ 3b. roster at gs://${BUCKET}/roster/roster.csv"

# 4. The collector reads the actual load (and ISO-NE's forecast) from a 5509 energy bucket you own
gcloud storage buckets add-iam-policy-binding "gs://${ACTUALS_BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" --role="roles/storage.objectViewer" >/dev/null \
  && echo "✅ 4. actuals from gs://${ACTUALS_BUCKET}"

# 5. The public page: readable by anyone, written only by the collector
if ! gcloud storage buckets describe "gs://${PUBLIC_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${PUBLIC_BUCKET}" --location="${REGION}" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${PUBLIC_BUCKET}" --member="allUsers" --role="roles/storage.objectViewer" >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${PUBLIC_BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" --role="roles/storage.objectAdmin" >/dev/null \
  && echo "✅ 5. public page: https://storage.googleapis.com/${PUBLIC_BUCKET}/index.html"

# Cloud Scheduler may mint tokens for the invoker
gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null 2>&1 || true

# 6. The collector, with 5509's settings ("^@^" lets values contain spaces and parentheses)
rm -rf ~/energy-collector && git clone -q https://github.com/drdave-teaching/energy-collector.git ~/energy-collector
ENV_VARS="^@^COLLECTOR_BUCKET=${BUCKET}@ACTUALS_BUCKET=${ACTUALS_BUCKET}@PUBLIC_BUCKET=${PUBLIC_BUCKET}"
ENV_VARS="${ENV_VARS}@COURSE_LABEL=OPIM 5509 (deep learning)@RANGE_TABLE_LABEL=Final Project Part 2"
ENV_VARS="${ENV_VARS}@LAB6_START_UTC=2026-11-09T05:00:00Z@FINAL_START_LOCAL=2026-11-26@FINAL_END_LOCAL=2026-12-02"
ENV_VARS="${ENV_VARS}@SHOW_STUDENT_FUTURE=false@REQUIRE_KERAS=true@POINT_LABEL=Final Project Part 1@FORECAST_FUNCTION=forecast-24h-dl"
ENV_VARS="${ENV_VARS}@SHIP_STEP=FP1 Part 8@SHARE_STEP=FP1 Part 9@TEST_MAE_SOURCE=your FP1 or FP2 notebook, Part 7"
gcloud functions deploy "${FUNCTION}" --gen2 --region="${REGION}" --runtime=python312 \
  --source="${HOME}/energy-collector/cloud_function/collect-forecasts" --entry-point=collect_http \
  --trigger-http --no-allow-unauthenticated --service-account="${COLLECTOR_SA}" \
  --set-env-vars="${ENV_VARS}" --memory=1Gi --timeout=540 --quiet \
  && echo "✅ 6. ${FUNCTION} deployed"
gcloud functions add-invoker-policy-binding "${FUNCTION}" --gen2 --region="${REGION}" \
  --member="serviceAccount:${INVOKER_SA}" >/dev/null
URL=$(gcloud functions describe "${FUNCTION}" --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
gcloud scheduler jobs create http "${JOB}" --location="${REGION}" --schedule="40 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST --attempt-deadline=540s \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null 2>&1 \
|| gcloud scheduler jobs update http "${JOB}" --location="${REGION}" --schedule="40 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST --attempt-deadline=540s \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null
echo "✅ 6b. ${FUNCTION} runs every hour at :40"

# 7. The class HRRR weather file (students' forecast-24h-dl reads it at :20)
gcloud functions deploy hrrr-bdl --gen2 --region="${REGION}" --runtime=python312 \
  --source="${HOME}/energy-collector/cloud_function/hrrr-bdl" --entry-point=hrrr_bdl_http \
  --trigger-http --no-allow-unauthenticated --service-account="${COLLECTOR_SA}" \
  --set-env-vars="PUBLIC_BUCKET=${PUBLIC_BUCKET}" --memory=2Gi --timeout=540 --quiet \
  && echo "✅ 7. hrrr-bdl deployed"
gcloud functions add-invoker-policy-binding hrrr-bdl --gen2 --region="${REGION}" \
  --member="serviceAccount:${INVOKER_SA}" >/dev/null
URL=$(gcloud functions describe hrrr-bdl --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
gcloud scheduler jobs create http hrrr-bdl-hourly --location="${REGION}" --schedule="5 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST --attempt-deadline=540s \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null 2>&1 \
|| gcloud scheduler jobs update http hrrr-bdl-hourly --location="${REGION}" --schedule="5 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST --attempt-deadline=540s \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null
echo "✅ 7b. hrrr-bdl runs every hour at :05"

# 8. First runs now, so the page and the weather file exist right away
gcloud scheduler jobs run hrrr-bdl-hourly --location="${REGION}" && echo "✅ 8. HRRR first run started (about 2 minutes)"
gcloud scheduler jobs run "${JOB}" --location="${REGION}" && echo "✅ 8b. collector first run started"

echo
echo "5509 students share their bucket with:  ${COLLECTOR_SA}"
echo "Roster:        gcloud storage cat gs://${BUCKET}/roster/roster.csv   (netid,bucket,nickname per line)"
echo "Who is in:     gcloud storage cat gs://${BUCKET}/class/status_latest.csv"
echo "HRRR check:    curl -s https://storage.googleapis.com/${PUBLIC_BUCKET}/weather/bdl_hrrr_status.json"
echo "5509 leaderboard: https://storage.googleapis.com/${PUBLIC_BUCKET}/index.html"
