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

import pandas as pd
from google.api_core import exceptions as gexc
from google.cloud import storage

COLLECTOR_BUCKET = os.getenv("COLLECTOR_BUCKET", "")
ROSTER_KEY = "roster/roster.csv"
STUDENT_MASTER_KEY = "energy/datasets/forecasts_master.csv"
STUDENT_CARD_KEY = "energy/models/model_card.json"
STALE_HOURS = 2          # newest forecast older than this = "stale" (their forecast-24h stopped)

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

    status_table = pd.DataFrame(statuses)
    write_csv(mine, "class/status_latest.csv", status_table)
    history = read_csv(mine, "class/status_history.csv")
    if history is not None:
        status_table = pd.concat([history, status_table], ignore_index=True)
    write_csv(mine, "class/status_history.csv", status_table)

    counts = pd.DataFrame(statuses)["status"].value_counts().to_dict()
    return {"status": "ok", "students": int(len(roster)), "by_status": counts, "class_master_rows": master_rows}


def collect_http(request):
    try:
        result = run_once()
        logging.info(json.dumps(result)[:500])
        return (json.dumps(result), 200, {"Content-Type": "application/json"})
    except Exception as e:
        logging.error("Error: %s\n%s", e, traceback.format_exc())
        return (json.dumps({"status": "error", "error": str(e)}), 500, {"Content-Type": "application/json"})
