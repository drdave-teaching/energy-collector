# energy-collector: every student's forecasts, every hour, in one table

Dr. Dave's side of the OPIM 5512 energy project (Lab 5, Lab 6, final project). Each student's own Google Cloud project
runs `forecast-24h` every hour at :20 and appends to `energy/datasets/forecasts_master.csv` in their bucket. This
collector runs at :35 in **your** project, reads every student's bucket (read-only, they grant it), and keeps one class
table keyed by NetID.

```
student buckets (one per student)                         your collector bucket
  energy/datasets/forecasts_master.csv  ──┐               class/forecasts_master.csv     everyone, every forecast
  energy/models/model_card.json         ──┼─ :35 hourly ─▶ class/latest_by_student.csv    everyone's newest 24 hours
                                          │               class/model_cards.csv          everyone's live model
  roster/roster.csv (netid,bucket) ───────┘               class/status_latest.csv        who is ok / not shared / stale
                                                          class/students/<netid>.csv     one file per student
```

Because it copies each student's whole `forecasts_master.csv`, it backfills automatically: a student who shares on
Nov 10 still shows every forecast since their Lab 5 night. If a student stops sharing, their last copy is kept.

## Once: set it up (about 5 minutes)
1. Console → **New project**, for example `opim5512-collector-f26`, on your billing account. Add a small budget alert.
2. Open Cloud Shell in that project and paste [`setup_collector.sh`](setup_collector.sh) (change `PROJECT_ID` on the first
   line if you picked another name). Count six ✅ lines.
3. The last lines print the account students share with: `collector@<your project>.iam.gserviceaccount.com`.
   That goes in the Lab 5 notebook's share cell (Part 7e).

## In class: students share, you add them to the roster
Each student runs **Lab 5 Part 7e** (one cell). It grants your collector read access to their bucket and prints one line,
`netid,bucket,nickname`, which they paste into the **Submit Lab 5** text box. Then, in Cloud Shell:

```bash
BUCKET=opim5512-collector-f26
gcloud storage cp gs://$BUCKET/roster/roster.csv roster.csv
cat >> roster.csv          # paste the students' lines, then press Ctrl+D
gcloud storage cp roster.csv gs://$BUCKET/roster/roster.csv
gcloud scheduler jobs run collect-forecasts-hourly --location=us-central1
```

## Watch it
```bash
gcloud storage cat gs://$BUCKET/class/status_latest.csv
```

| Status | Meaning | Fix |
|---|---|---|
| `ok` | Newest forecast is less than 2 hours old | nothing |
| `not_shared` | Your collector can't read their bucket | they run Part 7e (with the right Gmail) |
| `no_forecasts_yet` | Shared, but they never shipped a model | Lab 5 Part 7 |
| `stale` | Their forecasts stopped | their `forecast-24h` or scheduler is broken or paused |
| `bucket_not_found` | Typo in the roster | fix the roster line |

Load the class table anywhere with `pd.read_csv("gs://<bucket>/class/forecasts_master.csv")` (Colab, after
`auth.authenticate_user()` with your Gmail), or open it in the Console.

## The leaderboard (`class/leaderboard.csv`, refreshed every hour)
Every forecast hour whose actual load is known gets one **score**, the same for every lab:

| Student gives | Score per forecast hour |
|---|---|
| expected MW only (Lab 5) | absolute error, \|actual − expected\| |
| low, expected, high MW (Lab 6, final project; columns `predicted_low_mw`, `predicted_high_mw` = 10th and 90th percentiles) | weighted interval score = (0.5·\|actual − expected\| + 0.1·interval score) / 1.5, where interval score = width + 10 × how far the actual fell outside low–high |

A point-only forecast's weighted interval score is just its absolute error, so everyone ranks on one scale, and a
range only helps when it is narrow **and** honest.

**Rank = skill %**: 1 − score ÷ (error of "same hour yesterday"), worked out separately for 1–6, 7–12, 13–18 and 19–24
hours ahead and averaged, so the hard far-ahead hours count as much as the easy ones. Everyone is compared on the
exact hours they forecast, so joining late is no penalty. Two baseline rows: same hour yesterday (skill 0) and ISO New
England's own forecast (the newest version published before each forecast).

- Actual load = live Connecticut five-minute load averaged per hour (provisional), read from `ACTUALS_BUCKET`.
- Forecasts are frozen when collected: a later edit in a student's bucket changes nothing.
- `class/leaderboard_by_lead.csv` = error by hours ahead; `class/scored_forecasts.csv` = every scored forecast hour.

## The student page
Every hour the collector also writes **https://storage.googleapis.com/opim5512-leaderboard-f26/index.html**: the ranked
table, the two baselines, skill by hours ahead and how it's scored. It shows **nicknames only** (the roster's third
column; a blank nickname shows as "Forecaster 07", their roster position). NetIDs, buckets and forecasts stay private.
To rename someone, edit their nickname in the roster. Link it from HuskyCT.

## Cost and stopping
One function run per hour reading about 30 small CSVs: well under $1 a month. To pause:
`gcloud scheduler jobs pause collect-forecasts-hourly --location=us-central1`.
