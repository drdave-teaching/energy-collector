# ============================================================================================================
# OPIM 5509 leaderboard: a second copy of the class collector, for the deep learning final project.
# Paste into Cloud Shell AFTER setup_collector.sh has run (same project, same 'collector' service account, so
# 5509 students share their bucket with collector@opim5512-collector-f26.iam.gserviceaccount.com, like 5512).
# It gets its OWN roster and class tables (a private bucket) and its OWN public page (a public bucket), so 5509's
# LSTMs only compete with each other. REQUIRE_KERAS=true: only forecasts made by forecast-24h-dl with a Keras network
# are scored. Takes about 5 minutes. Safe to re-run.
# ============================================================================================================
PROJECT_ID="opim5512-collector-f26"           # the collector project that already runs 5512's collect-forecasts
REGION="us-central1"
ACTUALS_BUCKET="energy-dww-practice-f26"       # same live CT load as 5512
BUCKET="opim5509-collector-f26"                # NEW private bucket: roster in, class tables out
PUBLIC_BUCKET="opim5509-leaderboard-f26"       # NEW public page (nicknames only)
FUNCTION="collect-forecasts-5509"
JOB="collect-forecasts-5509-hourly"
COLLECTOR_SA="collector@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="collector-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')

# 1. The private bucket (roster + class tables) and an empty roster
if ! gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" --role="roles/storage.objectAdmin" >/dev/null \
  && echo "✅ 1. bucket gs://${BUCKET}"
if ! gcloud storage objects describe "gs://${BUCKET}/roster/roster.csv" >/dev/null 2>&1; then
  printf "netid,bucket,nickname\n" | gcloud storage cp - "gs://${BUCKET}/roster/roster.csv"
fi
echo "✅ 1b. roster at gs://${BUCKET}/roster/roster.csv"

# 2. The public page: readable by anyone, written only by the collector
if ! gcloud storage buckets describe "gs://${PUBLIC_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${PUBLIC_BUCKET}" --location="${REGION}" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://${PUBLIC_BUCKET}" --member="allUsers" --role="roles/storage.objectViewer" >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${PUBLIC_BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" --role="roles/storage.objectAdmin" >/dev/null \
  && echo "✅ 2. public page: https://storage.googleapis.com/${PUBLIC_BUCKET}/index.html"

# 3. Deploy the same collector code with 5509's settings ("^@^" lets values contain spaces and parentheses)
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
  && echo "✅ 3. ${FUNCTION} deployed"

# 4. Run it every hour at :40 (students' forecast-24h-dl runs at :20; 5512's collector runs at :35)
gcloud functions add-invoker-policy-binding "${FUNCTION}" --gen2 --region="${REGION}" \
  --member="serviceAccount:${INVOKER_SA}" >/dev/null
URL=$(gcloud functions describe "${FUNCTION}" --gen2 --region="${REGION}" --format='value(serviceConfig.uri)')
gcloud scheduler jobs create http "${JOB}" --location="${REGION}" --schedule="40 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null 2>&1 \
|| gcloud scheduler jobs update http "${JOB}" --location="${REGION}" --schedule="40 * * * *" \
  --time-zone="Etc/UTC" --uri="${URL}" --http-method=POST \
  --oidc-service-account-email="${INVOKER_SA}" --oidc-token-audience="${URL}" >/dev/null
echo "✅ 4. runs every hour at :40"

echo
echo "5509 students share their bucket with:  ${COLLECTOR_SA}"
echo "Add students:  gcloud storage cat gs://${BUCKET}/roster/roster.csv  (netid,bucket,nickname per line)"
echo "Run it now:    gcloud scheduler jobs run ${JOB} --location=${REGION}"
echo "Who is in:     gcloud storage cat gs://${BUCKET}/class/status_latest.csv"
echo "5509 leaderboard: https://storage.googleapis.com/${PUBLIC_BUCKET}/index.html"
