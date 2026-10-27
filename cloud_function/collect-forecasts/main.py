# collect-forecasts: Dr. Dave's class collector for the OPIM 5512 energy forecasts (Lab 5, Lab 6, final project).
#
# Every hour (:35, after every student's forecast-24h at :20) this function:
#   1. reads the roster:                gs://<collector bucket>/roster/roster.csv     (netid, bucket)
#   2. for each student, reads (read-only, the student granted it in Lab 5):
#        gs://<their bucket>/energy/datasets/forecasts_master.csv     every forecast they made since Lab 5
#        gs://<their bucket>/energy/models/model_card.json            their live model
#   3. writes, in the collector bucket:
#        class/students/<netid>.csv        one student's forecasts (refreshed every hour; kept if they stop sharing)
#        class/forecasts_master.csv        EVERYONE's forecasts in one table, keyed by netid
#        class/latest_by_student.csv       each student's newest 24-hour forecast
#        class/model_cards.csv             one row per student: their live model card
#        class/status_latest.csv           who is ok / not shared yet / no forecasts yet / stale
#        class/status_history.csv          every hour's status, so you can see when someone stopped
# HTTP entrypoint: collect_http

import io
import json
import logging
import os
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from google.api_core import exceptions as gexc
from google.cloud import storage

COLLECTOR_BUCKET = os.getenv("COLLECTOR_BUCKET", "")
ACTUALS_BUCKET = os.getenv("ACTUALS_BUCKET", "")     # a bucket whose scraper-isone runs (Dr. Dave's own), shared with the collector
ROSTER_KEY = "roster/roster.csv"
STUDENT_MASTER_KEY = "energy/datasets/forecasts_master.csv"
STUDENT_CARD_KEY = "energy/models/model_card.json"
ACTUAL_LOAD_KEY = "energy/datasets/ct_load_5min_hourly_master.csv"     # live CT load, five-minute values averaged per hour
ISONE_FORECAST_KEY = "energy/datasets/isone_ct_forecast_master.csv"     # ISO New England's own CT forecast, every version
STALE_HOURS = 2          # newest forecast older than this = "stale" (their forecast-24h stopped)
LEAD_BLOCKS = [(1, 6), (7, 12), (13, 18), (19, 24)]      # every block of lead times counts the same in the skill score
INTERVAL_ALPHA = 0.2     # low/high are an 80% range: the 10th and 90th percentiles

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def read_csv(bucket, key):
    blob = bucket.blob(key)
    if not blob.exists():
        return None
    return pd.read_csv(io.BytesIO(blob.download_as_bytes()))


def write_csv(bucket, key, table):
    bucket.blob(key).upload_from_string(table.to_csv(index=False), content_type="text/csv")


def collect_one(client, netid, bucket_name, now):
    """Copy one student's forecasts. Returns (status row, forecasts table or None, model card dict or None)."""
    status = {"collected_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "netid": netid, "bucket": bucket_name,
              "status": "", "forecast_rows": 0, "newest_issued_utc": "", "model_version": "", "message": ""}
    their_bucket = client.bucket(bucket_name)
    try:
        blob = their_bucket.blob(STUDENT_MASTER_KEY)
        if not blob.exists():
            # exists() says False for a missing BUCKET too: listing tells the two apart (NotFound = no such bucket)
            for _ in client.list_blobs(bucket_name, prefix="energy/", max_results=1):
                break
            status["status"] = "no_forecasts_yet"
            status["message"] = "Shared, but no forecasts_master.csv yet (no model shipped, Lab 5 Part 7)."
            return status, None, None
        forecasts = pd.read_csv(io.BytesIO(blob.download_as_bytes()))
        card = None
        card_blob = their_bucket.blob(STUDENT_CARD_KEY)
        if card_blob.exists():
            card = json.loads(card_blob.download_as_text())
    except gexc.Forbidden:
        status["status"] = "not_shared"
        status["message"] = "No read access yet: the student has not run the share cell (Lab 5 Part 7e)."
        return status, None, None
    except gexc.NotFound:
        status["status"] = "bucket_not_found"
        status["message"] = "No bucket with this name: check the roster."
        return status, None, None

    forecasts.insert(0, "netid", netid)
    newest = pd.to_datetime(forecasts["issued_at_utc"], utc=True).max()
    status["forecast_rows"] = int(len(forecasts))
    status["newest_issued_utc"] = newest.strftime("%Y-%m-%dT%H:%M:%SZ")
    status["model_version"] = str(forecasts["model_version"].iloc[-1])
    age_hours = (pd.Timestamp(now) - newest).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        status["status"] = "stale"
        status["message"] = "Newest forecast is " + str(round(age_hours)) + " hours old: their forecast-24h stopped."
    else:
        status["status"] = "ok"
    return status, forecasts, card


# ----------------------------------------------------------------------------------------------------------------
# Leaderboard. One score per forecast hour, the same for the Lab 5 (point) and final project (point + range) boards:
#   point only:        |actual - expected|                                     (MAE)
#   with low and high: weighted interval score = (0.5*|actual - expected| + (alpha/2)*interval_score) / 1.5
#                      interval_score = width + (2/alpha) * how far the actual fell outside the low-high range
# A point-only forecast's weighted interval score IS its absolute error, so both boards rank on one scale and a range
# only helps when it is narrow AND honest. Skill = 1 - score / (same-hour-yesterday error), worked out separately for
# each block of lead times (1-6, 7-12, 13-18, 19-24 hours ahead) and averaged, so the hard, far-ahead hours count as
# much as the easy ones. Everyone is compared on the exact hours they forecast, so a late start is no penalty.
# ----------------------------------------------------------------------------------------------------------------
def load_actuals(client):
    table = read_csv(client.bucket(ACTUALS_BUCKET), ACTUAL_LOAD_KEY)
    if table is None or len(table) == 0:
        return None
    table["hour_utc"] = pd.to_datetime(table["hour_utc"], utc=True)
    if "n_intervals" in table.columns:
        table = table[table["n_intervals"] >= 11]          # only hours that are over (12 five-minute values)
    return table.set_index("hour_utc")["load_mw_5min_avg"].sort_index()


def load_isone(client):
    table = read_csv(client.bucket(ACTUALS_BUCKET), ISONE_FORECAST_KEY)
    if table is None or len(table) == 0:
        return None
    table["issued_at_utc"] = pd.to_datetime(table["issued_at_utc"], utc=True)
    table["target_hour_utc"] = pd.to_datetime(table["target_hour_utc"], utc=True)
    return table.sort_values("issued_at_utc")


def score_forecasts(master, actual, isone):
    """One row per (forecast, target hour) whose actual load is known, with every error the leaderboard needs."""
    pairs = master.copy()
    pairs["issued"] = pd.to_datetime(pairs["issued_at_utc"], utc=True)
    pairs["target"] = pd.to_datetime(pairs["target_hour_utc"], utc=True)
    pairs["actual_mw"] = actual.reindex(pairs["target"]).values
    pairs = pairs[pairs["actual_mw"].notna() & pairs["predicted_load_mw"].notna()].copy()
    if len(pairs) == 0:
        return pairs

    # Baseline: same hour yesterday, using only load that was already over when the forecast was made
    source = pairs["target"] - pd.Timedelta(hours=24)
    too_new = source + pd.Timedelta(hours=1) > pairs["issued"]
    source = source.where(~too_new, source - pd.Timedelta(hours=24))
    pairs["same_hour_yesterday_mw"] = actual.reindex(source).values

    # ISO-NE's own forecast: the newest version published before the student's forecast
    pairs["isone_forecast_mw"] = np.nan
    if isone is not None:
        pairs = pairs.sort_values("issued")
        matched = pd.merge_asof(pairs[["issued", "target"]].reset_index(), isone, left_on="issued",
                                right_on="issued_at_utc", by=None, left_by="target", right_by="target_hour_utc",
                                direction="backward")
        pairs["isone_forecast_mw"] = matched.set_index("index")["isone_forecast_mw"]

    pairs["error_mw"] = pairs["predicted_load_mw"] - pairs["actual_mw"]
    pairs["abs_error_mw"] = pairs["error_mw"].abs()
    pairs["pct_error"] = 100 * pairs["abs_error_mw"] / pairs["actual_mw"]
    pairs["baseline_abs_error_mw"] = (pairs["same_hour_yesterday_mw"] - pairs["actual_mw"]).abs()
    pairs["isone_abs_error_mw"] = (pairs["isone_forecast_mw"] - pairs["actual_mw"]).abs()

    pairs["score"] = pairs["abs_error_mw"]
    pairs["inside_range"] = np.nan
    pairs["range_width_mw"] = np.nan
    if "predicted_low_mw" in pairs.columns and "predicted_high_mw" in pairs.columns:
        has_range = pairs["predicted_low_mw"].notna() & pairs["predicted_high_mw"].notna()
        low = pairs["predicted_low_mw"]
        high = pairs["predicted_high_mw"]
        y = pairs["actual_mw"]
        width = high - low
        miss = (low - y).clip(lower=0) + (y - high).clip(lower=0)
        interval_score = width + (2 / INTERVAL_ALPHA) * miss
        wis = (0.5 * pairs["abs_error_mw"] + (INTERVAL_ALPHA / 2) * interval_score) / 1.5
        pairs.loc[has_range, "score"] = wis[has_range]
        pairs.loc[has_range, "inside_range"] = ((y >= low) & (y <= high))[has_range].astype(float)
        pairs.loc[has_range, "range_width_mw"] = width[has_range]
    pairs["lead_hours"] = pairs["lead_hours"].astype(int)
    return pairs


def skill_by_block(rows, score_column):
    """Average over the lead-time blocks of (1 - score / same-hour-yesterday error), in percent."""
    skills = []
    for first, last in LEAD_BLOCKS:
        block = rows[(rows["lead_hours"] >= first) & (rows["lead_hours"] <= last) & rows["baseline_abs_error_mw"].notna()
                     & rows[score_column].notna()]
        if len(block) > 0 and block["baseline_abs_error_mw"].mean() > 0:
            skills.append(1 - block[score_column].mean() / block["baseline_abs_error_mw"].mean())
    if len(skills) == 0:
        return np.nan
    return round(100 * float(np.mean(skills)), 1)


def leaderboard_row(name, rows, score_column, prediction_column):
    row = {"netid": name, "forecasts_scored": int(len(rows)), "hours_scored": int(rows["target"].nunique()),
           "first_issued_utc": rows["issued"].min().strftime("%Y-%m-%dT%H:%MZ"),
           "last_issued_utc": rows["issued"].max().strftime("%Y-%m-%dT%H:%MZ"),
           "skill_pct": skill_by_block(rows, score_column)}
    error = rows[prediction_column] - rows["actual_mw"]
    row["mae_mw"] = round(float(error.abs().mean()), 1)
    row["mape_pct"] = round(float((100 * error.abs() / rows["actual_mw"]).mean()), 2)
    row["bias_mw"] = round(float(error.mean()), 1)
    for first, last in LEAD_BLOCKS:
        block = error[(rows["lead_hours"] >= first) & (rows["lead_hours"] <= last)]
        row["mae_lead_" + str(first) + "_" + str(last)] = round(float(block.abs().mean()), 1) if len(block) else np.nan
    row["same_hour_yesterday_mae_mw"] = round(float(rows["baseline_abs_error_mw"].mean()), 1)
    row["isone_mae_same_hours_mw"] = round(float(rows["isone_abs_error_mw"].mean()), 1)
    row["range_coverage_pct"] = round(100 * float(rows["inside_range"].mean()), 1) if rows["inside_range"].notna().any() else np.nan
    row["range_width_mw"] = round(float(rows["range_width_mw"].mean()), 1) if rows["range_width_mw"].notna().any() else np.nan
    row["score"] = round(float(rows[score_column].mean()), 1)
    return row


def build_leaderboard(client, mine, master):
    actual = load_actuals(client)
    if actual is None:
        return 0
    pairs = score_forecasts(master, actual, load_isone(client))
    if len(pairs) == 0:
        return 0
    rows = []
    for netid, group in pairs.groupby("netid"):
        row = leaderboard_row(netid, group, "score", "predicted_load_mw")
        row["model_version"] = str(group["model_version"].iloc[-1])
        rows.append(row)

    # Baselines, scored on every hour any student forecast (one copy per issue hour and target hour)
    common = pairs.assign(issue_hour=pairs["issued"].dt.floor("h")).drop_duplicates(["issue_hour", "target"])
    common = common.assign(inside_range=np.nan, range_width_mw=np.nan)      # baselines give no range
    baseline = common.assign(baseline_score=common["baseline_abs_error_mw"])
    rows.append(leaderboard_row("BASELINE: same hour yesterday", baseline, "baseline_score", "same_hour_yesterday_mw"))
    iso = common[common["isone_forecast_mw"].notna()]
    if len(iso) > 0:
        rows.append(leaderboard_row("BASELINE: ISO New England's forecast", iso, "isone_abs_error_mw", "isone_forecast_mw"))

    board = pd.DataFrame(rows).sort_values("skill_pct", ascending=False, na_position="last")
    board.insert(0, "rank", range(1, len(board) + 1))
    board["updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    write_csv(mine, "class/leaderboard.csv", board)

    by_lead = pairs.groupby(["netid", "lead_hours"]).agg(
        forecasts=("score", "size"), mae_mw=("abs_error_mw", "mean"), score=("score", "mean"),
        same_hour_yesterday_mae_mw=("baseline_abs_error_mw", "mean"), isone_mae_mw=("isone_abs_error_mw", "mean")).round(1)
    write_csv(mine, "class/leaderboard_by_lead.csv", by_lead.reset_index())
    write_csv(mine, "class/scored_forecasts.csv", pairs.drop(columns=["issued", "target"]))
    return int(len(board))


def run_once():
    now = datetime.now(timezone.utc)
    client = storage.Client()
    mine = client.bucket(COLLECTOR_BUCKET)
    roster = read_csv(mine, ROSTER_KEY)
    if roster is None or len(roster) == 0:
        return {"status": "no_roster", "message": "Upload gs://" + COLLECTOR_BUCKET + "/" + ROSTER_KEY + " (netid,bucket)."}
    roster["netid"] = roster["netid"].astype(str).str.strip().str.lower()
    roster["bucket"] = roster["bucket"].astype(str).str.strip().str.replace("gs://", "", regex=False).str.strip("/")
    roster = roster.drop_duplicates("netid", keep="last")

    statuses = []
    cards = []
    for i in range(len(roster)):
        netid = roster["netid"].iloc[i]
        bucket_name = roster["bucket"].iloc[i]
        try:
            status, forecasts, card = collect_one(client, netid, bucket_name, now)
        except Exception as err:             # one broken student never stops the rest of the class
            status = {"collected_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "netid": netid, "bucket": bucket_name,
                      "status": "error", "forecast_rows": 0, "newest_issued_utc": "", "model_version": "",
                      "message": str(err)[:300]}
            forecasts = None
            card = None
        if forecasts is not None:
            # Frozen: once a forecast is in the class copy, a later edit in the student's bucket can't change it.
            # Only forecasts issued after the newest one we already hold are added.
            frozen = read_csv(mine, "class/students/" + netid + ".csv")
            if frozen is not None and len(frozen) > 0:
                newest_frozen = pd.to_datetime(frozen["issued_at_utc"], utc=True).max()
                is_new = pd.to_datetime(forecasts["issued_at_utc"], utc=True) > newest_frozen
                forecasts = pd.concat([frozen, forecasts[is_new]], ignore_index=True)
            write_csv(mine, "class/students/" + netid + ".csv", forecasts)
        if card is not None:
            card_row = {"netid": netid}
            for key in card:
                card_row[key] = json.dumps(card[key]) if isinstance(card[key], (list, dict)) else card[key]
            cards.append(card_row)
        statuses.append(status)

    # Everyone's forecasts in one table (a student who stopped sharing keeps their last copy)
    tables = []
    for netid in roster["netid"]:
        table = read_csv(mine, "class/students/" + netid + ".csv")
        if table is not None:
            tables.append(table)
    master_rows = 0
    if len(tables) > 0:
        master = pd.concat(tables, ignore_index=True)
        master = master.drop_duplicates(["netid", "issued_at_utc", "target_hour_utc"], keep="last")
        master = master.sort_values(["netid", "issued_at_utc", "lead_hours"])
        write_csv(mine, "class/forecasts_master.csv", master)
        newest_issue = master.groupby("netid")["issued_at_utc"].transform("max")
        write_csv(mine, "class/latest_by_student.csv", master[master["issued_at_utc"] == newest_issue])
        master_rows = int(len(master))
    if len(cards) > 0:
        write_csv(mine, "class/model_cards.csv", pd.DataFrame(cards))

    leaderboard_rows = 0
    if len(tables) > 0 and ACTUALS_BUCKET:
        try:                                  # a leaderboard problem never stops the collecting
            leaderboard_rows = build_leaderboard(client, mine, master)
        except Exception as err:
            logging.error("Leaderboard: %s\n%s", err, traceback.format_exc())

    status_table = pd.DataFrame(statuses)
    write_csv(mine, "class/status_latest.csv", status_table)
    history = read_csv(mine, "class/status_history.csv")
    if history is not None:
        status_table = pd.concat([history, status_table], ignore_index=True)
    write_csv(mine, "class/status_history.csv", status_table)

    counts = pd.DataFrame(statuses)["status"].value_counts().to_dict()
    return {"status": "ok", "students": int(len(roster)), "by_status": counts, "class_master_rows": master_rows,
            "leaderboard_rows": leaderboard_rows}


def collect_http(request):
    try:
        result = run_once()
        logging.info(json.dumps(result)[:500])
        return (json.dumps(result), 200, {"Content-Type": "application/json"})
    except Exception as e:
        logging.error("Error: %s\n%s", e, traceback.format_exc())
        return (json.dumps({"status": "error", "error": str(e)}), 500, {"Content-Type": "application/json"})
