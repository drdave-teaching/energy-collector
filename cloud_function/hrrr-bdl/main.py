# hrrr-bdl: the HRRR weather forecast for Bradley Airport, for the whole class, every hour.
#
# Every hour (:05) this function:
#   1. opens NOAA's HRRR 48-hour forecast archive on dynamical.org (noaa-hrrr-forecast-48-hour; free, no key)
#   2. reads the 8 NEWEST runs (00, 06, 12 and 18 UTC, so the last ~2 days) at the 3 km square nearest Bradley,
#      every hour 0 to 48 hours ahead: temperature, dew point, humidity, wind, cloud cover and sunlight
#   3. writes them, in the same units as the students' weather scrapers (F, %, mph, W/m2), to the PUBLIC bucket:
#        gs://<PUBLIC_BUCKET>/weather/bdl_hrrr_runs.csv      <- one row per (run, hour); students' forecast-24h reads it
#        gs://<PUBLIC_BUCKET>/weather/bdl_hrrr_status.json   <- newest run, when it was written
# Same reader and the same grid square as the Lab 6 archive (Module4/data/bdl_hrrr_asissued_2023_2026.csv.gz), so a
# model trained on the archive is fed exactly the same kind of numbers in production.
# One central job instead of 30: each student's forecast-24h only downloads a ~60 KB CSV.
# HTTP entrypoint: hrrr_bdl_http

import json
import logging
import os
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from google.cloud import storage

PUBLIC_BUCKET = os.getenv("PUBLIC_BUCKET", "")
RUNS_KEY = os.getenv("RUNS_KEY", "weather/bdl_hrrr_runs.csv")
STATUS_KEY = os.getenv("STATUS_KEY", "weather/bdl_hrrr_status.json")
N_RUNS = int(os.getenv("N_RUNS", "8"))
LAT = float(os.getenv("LAT", "41.94"))           # the Lab 6 archive's point (Bradley International Airport)
LON = float(os.getenv("LON", "-72.68"))
DATASET = "noaa-hrrr-forecast-48-hour"
VARS = ["temperature_2m", "dew_point_temperature_2m", "relative_humidity_2m", "wind_u_10m", "wind_v_10m",
        "total_cloud_cover_atmosphere", "downward_short_wave_radiation_flux_surface"]

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def read_runs(n_runs: int) -> pd.DataFrame:
    """The n newest HRRR 48-hour runs at the grid square nearest (LAT, LON), in the students' units."""
    import dynamical_catalog

    ds = dynamical_catalog.open(DATASET)
    d = (ds["latitude"] - LAT) ** 2 + ((ds["longitude"] % 360) - (LON % 360)) ** 2
    iy, ix = np.unravel_index(int(np.argmin(d.values)), d.shape)
    dims = ds["latitude"].dims
    inits = ds["init_time"].values[-n_runs:]
    raw = ds[VARS].isel({dims[0]: iy, dims[1]: ix}).sel(init_time=inits).load()
    r = raw.to_dataframe().reset_index()
    r[VARS] = r[VARS].astype(float)
    out = pd.DataFrame()
    out["init_utc"] = pd.to_datetime(r["init_time"]).dt.tz_localize("UTC")
    out["lead_hours"] = (pd.to_timedelta(r["lead_time"]) / pd.Timedelta(hours=1)).round().astype(int)
    out["hour_utc"] = out["init_utc"] + pd.to_timedelta(out["lead_hours"], unit="h")
    out["temp_f"] = (r["temperature_2m"] * 9 / 5 + 32).round(1)
    out["dewpoint_f"] = (r["dew_point_temperature_2m"] * 9 / 5 + 32).round(1)
    out["rel_humidity_pct"] = r["relative_humidity_2m"].round(1)
    out["wind_speed_mph"] = (np.hypot(r["wind_u_10m"], r["wind_v_10m"]) * 2.23694).round(1)
    out["cloud_cover_pct"] = r["total_cloud_cover_atmosphere"].round(0)
    out["solar_wm2"] = r["downward_short_wave_radiation_flux_surface"].round(0)
    out = out.dropna(subset=["temp_f"]).sort_values(["init_utc", "lead_hours"])
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    out["init_utc"] = out["init_utc"].dt.strftime(fmt)
    out["hour_utc"] = out["hour_utc"].dt.strftime(fmt)
    return out


def run_once(dry_run: bool = False) -> dict:
    runs = read_runs(N_RUNS)
    newest = runs["init_utc"].max()
    summary = {"status": "ok", "newest_run_utc": newest, "runs": int(runs["init_utc"].nunique()), "rows": int(len(runs)),
               "first_run_utc": runs["init_utc"].min(), "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if dry_run:
        summary["dry_run"] = True
        return summary
    bucket = storage.Client().bucket(PUBLIC_BUCKET)
    blob = bucket.blob(RUNS_KEY)
    blob.cache_control = "public, max-age=60"
    blob.upload_from_string(runs.to_csv(index=False), content_type="text/csv")
    status = bucket.blob(STATUS_KEY)
    status.cache_control = "public, max-age=60"
    status.upload_from_string(json.dumps(summary), content_type="application/json")
    summary["runs_key"] = RUNS_KEY
    return summary


def hrrr_bdl_http(request):
    try:
        body = request.get_json(silent=True) or {}
        result = run_once(dry_run=bool(body.get("dry_run", False)))
        logging.info(json.dumps(result))
        return (json.dumps(result), 200, {"Content-Type": "application/json"})
    except Exception as e:
        logging.error("Error: %s\n%s", e, traceback.format_exc())
        return (json.dumps({"status": "error", "error": str(e)}), 500, {"Content-Type": "application/json"})
