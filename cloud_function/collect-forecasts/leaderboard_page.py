# leaderboard_page: turns the leaderboard tables into the public web page students see.
# Nicknames only: NetIDs, buckets and the roster never leave the private collector bucket.

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

EASTERN = ZoneInfo("America/New_York")
LINE_COLORS = ["#F2A900", "#2E86DE", "#10AC84", "#EE5253", "#8E44AD", "#FF9F43"]
CHART_TOP = 6            # how many forecasters get a line in the chart


def display_names(roster):
    """netid -> what the page shows: the student's nickname, or 'Forecaster 07' (their roster position)."""
    names = {}
    used = set()
    for i in range(len(roster)):
        netid = roster["netid"].iloc[i]
        nickname = str(roster["nickname"].iloc[i]).strip()[:24]
        if nickname == "" or nickname.lower() == "nan":
            nickname = "Forecaster " + str(i + 1).zfill(2)
        if nickname.lower() in used:
            nickname = nickname + " (" + str(i + 1) + ")"
        used.add(nickname.lower())
        names[netid] = nickname
    return names


def eastern(text_utc):
    stamp = pd.Timestamp(text_utc)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    local = stamp.tz_convert(EASTERN)
    hour = local.hour % 12
    if hour == 0:
        hour = 12
    return local.strftime("%a %b ") + str(local.day) + ", " + str(hour) + local.strftime(":%M %p")


def number(value, digits=0, suffix=""):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "&ndash;"
    return format(value, "," + "." + str(digits) + "f") + suffix


def skill_chart(by_lead, board, names):
    """SVG: skill vs same hour yesterday at every lead time, for the top forecasters and ISO New England."""
    students = board[~board["netid"].str.startswith("BASELINE")]["netid"].tolist()[:CHART_TOP]
    width, height, left, right, top, bottom = 760, 300, 52, 16, 16, 40
    plot_w = width - left - right
    plot_h = height - top - bottom
    lines = []
    for netid in students:
        rows = by_lead[by_lead["netid"] == netid].set_index("lead_hours")
        skill = 100 * (1 - rows["score"] / rows["same_hour_yesterday_mae_mw"])
        lines.append((names.get(netid, netid), skill))
    iso = by_lead[by_lead["isone_mae_mw"].notna()].copy()
    if len(iso) > 0:
        iso["iso_total"] = iso["isone_mae_mw"] * iso["forecasts"]
        iso["base_total"] = iso["same_hour_yesterday_mae_mw"] * iso["forecasts"]
        sums = iso.groupby("lead_hours")[["iso_total", "base_total"]].sum()
        lines.append(("ISO New England", 100 * (1 - sums["iso_total"] / sums["base_total"])))

    values = [0.0]
    for _, skill in lines:
        values.extend([float(v) for v in skill.dropna()])
    low = max(-100.0, min(-25.0, np.floor(min(values) / 25) * 25))     # anything worse than -100% sits on the bottom edge
    high = min(100.0, max(50.0, np.ceil(max(values) / 25) * 25))
    step = 25 if high - low <= 125 else 50

    def x(lead):
        return left + (lead - 1) / 23 * plot_w

    def y(value):
        value = min(max(value, low), high)
        return top + (high - value) / (high - low) * plot_h

    parts = ['<svg viewBox="0 0 ' + str(width) + " " + str(height) + '" role="img" aria-label="Skill by hours ahead">']
    tick = low
    while tick <= high:
        parts.append('<line x1="' + str(left) + '" x2="' + str(width - right) + '" y1="' + format(y(tick), ".1f") + '" y2="'
                     + format(y(tick), ".1f") + '" class="' + ("zero" if tick == 0 else "grid") + '"/>')
        parts.append('<text x="' + str(left - 8) + '" y="' + format(y(tick) + 4, ".1f") + '" class="axis" text-anchor="end">'
                     + str(int(tick)) + "%</text>")
        tick += step
    for lead in [1, 6, 12, 18, 24]:
        parts.append('<text x="' + format(x(lead), ".1f") + '" y="' + str(height - 16) + '" class="axis" text-anchor="middle">'
                     + str(lead) + " h</text>")
    parts.append('<text x="' + format(left + plot_w / 2, ".1f") + '" y="' + str(height - 1)
                 + '" class="axis" text-anchor="middle">hours ahead</text>')
    legend = []
    for i, (label, skill) in enumerate(lines):
        is_iso = label == "ISO New England"
        color = "var(--muted)" if is_iso else LINE_COLORS[i % len(LINE_COLORS)]
        dash = ' stroke-dasharray="6 4"' if is_iso else ""
        points = []
        for lead, value in skill.dropna().items():
            points.append(format(x(int(lead)), ".1f") + "," + format(y(float(value)), ".1f"))
        if len(points) == 1:
            parts.append('<circle cx="' + points[0].split(",")[0] + '" cy="' + points[0].split(",")[1] + '" r="4" fill="'
                         + color + '"/>')
        elif len(points) > 1:
            parts.append('<polyline fill="none" stroke="' + color + '" stroke-width="2.5"' + dash + ' points="'
                         + " ".join(points) + '"/>')
        legend.append('<span class="key"><i style="background:' + color + '"></i>' + html.escape(label) + "</span>")
    parts.append("</svg>")
    return "".join(parts), "".join(legend)


def local_hour_label(stamp):
    local = stamp.tz_convert(EASTERN)
    hour = local.hour % 12
    if hour == 0:
        hour = 12
    label = str(hour) + (" AM" if local.hour < 12 else " PM")
    if local.hour == 0:
        label = local.strftime("%a ") + label
    return label


def sky_words(cloud_pct):
    if cloud_pct < 25:
        return "mostly clear"
    if cloud_pct < 60:
        return "partly cloudy"
    if cloud_pct < 85:
        return "mostly cloudy"
    return "overcast"


def weather_summary(live):
    """One sentence about the next 24 hours at Bradley Airport, from the newest National Weather Service forecast."""
    fc = live.get("weather_forecast")
    if fc is None or len(fc) == 0 or "temp_f" not in fc.columns:
        return ""
    parts = ["high " + format(fc["temp_f"].max(), ".0f") + "&deg;F", "low " + format(fc["temp_f"].min(), ".0f") + "&deg;F"]
    if "cloud_cover_pct" in fc.columns and fc["cloud_cover_pct"].notna().any():
        parts.append(sky_words(float(fc["cloud_cover_pct"].mean())))
    if "precip_chance_pct" in fc.columns and fc["precip_chance_pct"].notna().any():
        parts.append("rain chance up to " + format(fc["precip_chance_pct"].max(), ".0f") + "%")
    if "wind_speed_mph" in fc.columns and fc["wind_speed_mph"].notna().any():
        wind = "wind up to " + format(fc["wind_speed_mph"].max(), ".0f") + " mph"
        if "wind_gust_mph" in fc.columns and fc["wind_gust_mph"].notna().any():
            wind = wind + ", gusts " + format(fc["wind_gust_mph"].max(), ".0f")
        parts.append(wind)
    return "<b>Next 24 hours at Bradley Airport</b> (National Weather Service): " + ", ".join(parts) + "."


def series_points(series, x, y):
    """SVG polyline point lists for one time series, split wherever an hour is missing."""
    runs = []
    points = []
    previous = None
    for stamp, value in series.sort_index().items():
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        if previous is not None and stamp - previous > pd.Timedelta(hours=1) and len(points) > 0:
            runs.append(points)
            points = []
        points.append(format(x(stamp), ".1f") + "," + format(y(float(value)), ".1f"))
        previous = stamp
    if len(points) > 0:
        runs.append(points)
    return runs


def draw(series, x, y, color, width, extra=""):
    parts = []
    for points in series_points(series, x, y):
        if len(points) == 1:
            xy = points[0].split(",")
            parts.append('<circle cx="' + xy[0] + '" cy="' + xy[1] + '" r="' + str(width + 1) + '" fill="' + color + '"/>')
        else:
            parts.append('<polyline fill="none" stroke="' + color + '" stroke-width="' + str(width) + '"' + extra
                         + ' stroke-linejoin="round" points="' + " ".join(points) + '"/>')
    return "".join(parts)


def live_chart(live, names):
    """SVG: load for the last 24 hours and the next 24, with the weather at Bradley underneath."""
    width, left, right = 760, 56, 44
    load_top, load_h = 18, 230
    wx_top, wx_h = load_top + load_h + 30, 90
    height = wx_top + wx_h + 28
    plot_w = width - left - right
    start, end, now = live["start"], live["end"], live["now"]
    span = (end - start) / pd.Timedelta(hours=1)

    def x(stamp):
        return left + ((stamp - start) / pd.Timedelta(hours=1)) / span * plot_w

    past = live.get("past_by_student")
    values = []
    for key in ["actual", "past_median", "future", "isone"]:
        if live.get(key) is not None:
            values.extend([float(v) for v in live[key].dropna()])
    if past is not None and past.shape[1] > 0:
        values.extend([float(v) for v in past.stack().dropna()])
    if len(values) == 0:
        return "", ""
    low = np.floor((min(values) - 50) / 250) * 250
    high = np.ceil((max(values) + 50) / 250) * 250
    step = 250 if high - low <= 1500 else 500

    def y(value):
        return load_top + (high - min(max(value, low), high)) / (high - low) * load_h

    parts = ['<svg viewBox="0 0 ' + str(width) + " " + str(height) + '" role="img" aria-label="Load for the last 24 hours and the next 24">']
    parts.append('<rect x="' + format(x(now), ".1f") + '" y="' + str(load_top) + '" width="' + format(x(end) - x(now), ".1f")
                 + '" height="' + str(wx_top + wx_h - load_top) + '" class="future"/>')
    tick = low
    while tick <= high:
        parts.append('<line x1="' + str(left) + '" x2="' + str(width - right) + '" y1="' + format(y(tick), ".1f") + '" y2="'
                     + format(y(tick), ".1f") + '" class="grid"/>')
        parts.append('<text x="' + str(left - 8) + '" y="' + format(y(tick) + 4, ".1f") + '" class="axis" text-anchor="end">'
                     + format(tick, ",.0f") + "</text>")
        tick += step
    middle = format(load_top + load_h / 2, ".1f")
    parts.append('<text x="12" y="' + middle + '" class="axis" text-anchor="middle" transform="rotate(-90 12 ' + middle + ')">MW</text>')

    legend = []
    top = live.get("top_netid")
    if past is not None and past.shape[1] > 0:
        for netid in past.columns:
            if netid != top:
                parts.append(draw(past[netid], x, y, "var(--student)", 1.2))
        legend.append('<span class="key"><i style="background:var(--student)"></i>Each forecaster (shown once the hour is over)</span>')
    if live.get("isone") is not None and len(live["isone"]) > 0:
        parts.append(draw(live["isone"], x, y, "var(--muted)", 1.8, ' stroke-dasharray="5 4"'))
        legend.append('<span class="key"><i style="background:var(--muted)"></i>ISO New England (dashed)</span>')
    if past is not None and top in past.columns:
        parts.append(draw(past[top], x, y, "#1f6feb", 2.6))
        legend.append('<span class="key"><i style="background:#1f6feb"></i>#1 on the leaderboard: ' + html.escape(names.get(top, "Forecaster")) + "</span>")
    if live.get("past_median") is not None and past is not None and past.shape[1] >= 2:
        parts.append(draw(live["past_median"], x, y, "#d62828", 2.6))
        legend.append('<span class="key"><i style="background:#d62828"></i>Class median</span>')
    if live.get("future") is not None and len(live["future"]) > 0:
        color = "#d62828" if live.get("future_label", "").startswith("Class") else "var(--gold)"
        parts.append(draw(live["future"], x, y, color, 2.6, ' stroke-dasharray="7 4"'))
        legend.append('<span class="key"><i style="background:' + color + '"></i>' + html.escape(live.get("future_label", "")) + " (dashed)</span>")
    if live.get("actual") is not None and len(live["actual"]) > 0:
        parts.append(draw(live["actual"], x, y, "var(--ink)", 3))
        legend.append('<span class="key"><i style="background:var(--ink)"></i>Actual load</span>')

    # Weather panel: temperature (line) and chance of rain (bars)
    temps = []
    obs = live.get("weather_obs")
    fc = live.get("weather_forecast")
    old_fc = live.get("weather_day_ahead")
    if old_fc is not None and ("temp_f" not in old_fc.columns or old_fc["temp_f"].notna().sum() == 0):
        old_fc = None
    if old_fc is not None:
        temps.extend([float(v) for v in old_fc["temp_f"].dropna()])
    if obs is not None and "temp_f" in obs.columns:
        temps.extend([float(v) for v in obs["temp_f"].dropna()])
    if fc is not None and "temp_f" in fc.columns:
        temps.extend([float(v) for v in fc["temp_f"].dropna()])
    if len(temps) > 0:
        t_low = np.floor((min(temps) - 2) / 10) * 10
        t_high = np.ceil((max(temps) + 2) / 10) * 10

        def ty(value):
            return wx_top + (t_high - min(max(value, t_low), t_high)) / (t_high - t_low) * wx_h

        parts.append('<line x1="' + str(left) + '" x2="' + str(width - right) + '" y1="' + str(wx_top + wx_h) + '" y2="'
                     + str(wx_top + wx_h) + '" class="grid"/>')
        for t in [t_low, t_high]:
            parts.append('<text x="' + str(left - 8) + '" y="' + format(ty(t) + 4, ".1f") + '" class="axis" text-anchor="end">'
                         + format(t, ".0f") + "&deg;F</text>")
        has_rain = fc is not None and "precip_chance_pct" in fc.columns and fc["precip_chance_pct"].notna().any()
        if has_rain:
            bar_w = max(plot_w / span - 2, 2)
            for stamp, chance in fc["precip_chance_pct"].dropna().items():
                if chance > 0:
                    bar_h = chance / 100 * wx_h
                    parts.append('<rect x="' + format(x(stamp) - bar_w / 2, ".1f") + '" y="' + format(wx_top + wx_h - bar_h, ".1f")
                                 + '" width="' + format(bar_w, ".1f") + '" height="' + format(bar_h, ".1f") + '" class="rain"/>')
            parts.append('<text x="' + str(width - right + 6) + '" y="' + str(wx_top + 10) + '" class="axis">100%</text>')
            parts.append('<text x="' + str(width - right + 6) + '" y="' + str(wx_top + wx_h) + '" class="axis">0%</text>')
        if old_fc is not None:
            parts.append(draw(old_fc["temp_f"], x, ty, "#e67e22", 1.6, ' stroke-dasharray="1.5 3" opacity="0.8"'))
        if obs is not None and "temp_f" in obs.columns:
            parts.append(draw(obs["temp_f"], x, ty, "#e67e22", 2.2))
        if fc is not None and "temp_f" in fc.columns:
            parts.append(draw(fc["temp_f"], x, ty, "#e67e22", 2.2, ' stroke-dasharray="5 4"'))
            for stamp, temp in fc["temp_f"].dropna().items():
                if stamp.tz_convert(EASTERN).hour % 6 == 0:
                    parts.append('<text x="' + format(x(stamp), ".1f") + '" y="' + format(ty(temp) - 7, ".1f")
                                 + '" class="axis" text-anchor="middle">' + format(temp, ".0f") + "&deg;</text>")
        legend.append('<span class="key"><i style="background:#e67e22"></i>Temperature at Bradley (solid = measured, dashed = forecast)</span>')
        if old_fc is not None:
            # How far off was the forecast made ~24 hours ahead? Compare it with the measured temperature, hour by hour.
            miss = ""
            if obs is not None and "temp_f" in obs.columns:
                both = pd.concat([old_fc["temp_f"].rename("said"), obs["temp_f"].rename("was")], axis=1).dropna()
                if len(both) > 0:
                    miss = ", missed by " + format((both["said"] - both["was"]).abs().mean(), ".1f") + "&deg;F on average"
            age = format(old_fc["age_hours"].median(), ".0f") if "age_hours" in old_fc.columns else "24"
            legend.append('<span class="key"><i style="background:#e67e22;opacity:0.6"></i>Dotted = what the forecast said about '
                          + age + " hours ahead" + miss + "</span>")
        if has_rain:
            legend.append('<span class="key"><i style="background:var(--rain)"></i>Chance of rain (bars, right scale)</span>')

    # Time axis and the "now" line
    stamp = start
    while stamp <= end:
        if stamp.tz_convert(EASTERN).hour % 6 == 0:
            parts.append('<line x1="' + format(x(stamp), ".1f") + '" x2="' + format(x(stamp), ".1f") + '" y1="' + str(load_top)
                         + '" y2="' + str(wx_top + wx_h) + '" class="grid"/>')
            parts.append('<text x="' + format(x(stamp), ".1f") + '" y="' + str(height - 8) + '" class="axis" text-anchor="middle">'
                         + local_hour_label(stamp) + "</text>")
        stamp += pd.Timedelta(hours=1)
    parts.append('<line x1="' + format(x(now), ".1f") + '" x2="' + format(x(now), ".1f") + '" y1="' + str(load_top - 6) + '" y2="'
                 + str(wx_top + wx_h) + '" class="now"/>')
    parts.append('<text x="' + format(x(now) - 6, ".1f") + '" y="' + str(load_top - 6) + '" class="axis" text-anchor="end">&larr; what happened</text>')
    parts.append('<text x="' + format(x(now) + 6, ".1f") + '" y="' + str(load_top - 6) + '" class="axis">what&rsquo;s next &rarr;</text>')
    parts.append("</svg>")
    return "".join(parts), "".join(legend)


def render(board, by_lead, roster, waiting, now=None, live=None):
    now = now or datetime.now(timezone.utc)
    names = display_names(roster)
    has_range = board["range_coverage_pct"].notna().any()
    students = board[~board["netid"].str.startswith("BASELINE")]
    first = board["first_issued_utc"].min()
    last = board["last_issued_utc"].max()

    rows = []
    rank = 0
    for _, row in board.iterrows():
        baseline = row["netid"].startswith("BASELINE")
        if baseline:
            label = row["netid"].replace("BASELINE: ", "")
            label = label[0].upper() + label[1:]
            rank_cell = ""
            css = ' class="baseline"'
        else:
            rank += 1
            label = names.get(row["netid"], "Forecaster")
            rank_cell = str(rank)
            css = ' class="top"' if rank == 1 else ""
        beat_iso = "&ndash;"
        if not baseline and not np.isnan(row["isone_mae_same_hours_mw"]):
            beat_iso = "yes" if row["mae_mw"] < row["isone_mae_same_hours_mw"] else "not yet"
        cells = ["<td>" + rank_cell + "</td>", '<td class="name">' + html.escape(label) + "</td>",
                 '<td class="skill">' + number(row["skill_pct"], 1, "%") + "</td>",
                 "<td>" + number(row["mae_mw"]) + "</td>",
                 "<td>" + number(row["mae_lead_1_6"]) + "</td>", "<td>" + number(row["mae_lead_7_12"]) + "</td>",
                 "<td>" + number(row["mae_lead_13_18"]) + "</td>", "<td>" + number(row["mae_lead_19_24"]) + "</td>",
                 "<td>" + beat_iso + "</td>"]
        if has_range:
            cells.append("<td>" + number(row["score"], 1) + "</td>")
            cells.append("<td>" + number(row["range_coverage_pct"], 0, "%") + "</td>")
            cells.append("<td>" + number(row["range_width_mw"]) + "</td>")
        test_cell = "&ndash;"
        if "test_mae_mw" in row and not baseline and not pd.isna(row["test_mae_mw"]):
            test_cell = number(row["test_mae_mw"])
        cells.append("<td>" + test_cell + "</td>")
        cells.append("<td>" + number(row["hours_scored"]) + "</td>")
        rows.append("<tr" + css + ">" + "".join(cells) + "</tr>")

    range_heads = ""
    if has_range:
        range_heads = '<th title="Average score per forecast hour, lower is better. With no range it equals Avg miss">Score</th>' +             '<th title="Share of hours the actual load fell between your low and high">Inside range</th><th>Range width (MW)</th>'
    chart_svg, chart_legend = skill_chart(by_lead, board, names)
    live_card = ""
    if live is not None:
        live_svg, live_legend = live_chart(live, names)
        if live_svg:
            live_card = """<div class="card"><h2>The last 24 hours and the next 24</h2>
<p class="note" style="margin-top:0">""" + weather_summary(live) + """</p>
""" + live_svg + '<div class="legend">' + live_legend + """</div>
<p class="note">Gray lines are each forecaster's latest call for an hour, made before it began. They appear only after the hour is over, so nobody can copy a forecast. The next 24 hours show only the class median, never one person's forecast.</p></div>
"""
    waiting_note = ""
    if waiting > 0:
        waiting_note = "<p class=\"note\">" + str(waiting) + " more forecaster" + ("s are" if waiting > 1 else " is") + \
            " signed up and waiting for a first scored hour.</p>"

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="600">
<title>Load Forecast Leaderboard</title>
<style>
:root { --bg:#f6f7fb; --card:#ffffff; --ink:#1c1c1e; --muted:#6b7280; --line:#e3e6ee; --navy:#0A1F44; --gold:#F2A900;
        --baseline:#f1f3f8; --top:#fff6dc; --student:#b9c0cc; --rain:#9cc3ef; --future-bg:#f3f6fc; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0d1220; --card:#161d2f; --ink:#e8eaf0;
        --muted:#9aa3b5; --line:#2a3350; --navy:#9db8ff; --baseline:#1d2539; --top:#3a3214; --student:#4a5470;
        --rain:#2f5d8f; --future-bg:#1a2236; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 "Segoe UI", system-ui, -apple-system, Arial, sans-serif; }
main { max-width:1040px; margin:0 auto; padding:28px 16px 48px; }
h1 { margin:0; font-size:26px; color:var(--navy); }
.sub { color:var(--muted); margin:4px 0 18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; margin-bottom:18px; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; min-width:720px; font-variant-numeric:tabular-nums; }
th { text-align:right; font-size:12px; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); padding:8px;
     border-bottom:2px solid var(--line); white-space:nowrap; }
td { text-align:right; padding:9px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
th:nth-child(2), td.name { text-align:left; }
td.name { font-weight:600; }
td.skill { font-weight:700; color:var(--navy); }
tr.top td { background:var(--top); }
tr.baseline td { background:var(--baseline); color:var(--muted); font-style:italic; }
tr.baseline td.skill { color:var(--muted); }
h2 { font-size:17px; margin:0 0 6px; color:var(--navy); }
.note { color:var(--muted); font-size:13px; margin:10px 0 0; }
svg { width:100%; height:auto; }
svg .grid { stroke:var(--line); } svg .zero { stroke:var(--muted); stroke-width:1.5; }
svg .axis { fill:var(--muted); font-size:12px; }
svg .future { fill:var(--future-bg); } svg .now { stroke:var(--ink); stroke-width:1.5; stroke-dasharray:3 3; }
svg .rain { fill:var(--rain); opacity:.7; }
.legend { display:flex; flex-wrap:wrap; gap:6px 16px; margin-top:6px; font-size:13px; }
.key i { display:inline-block; width:14px; height:4px; border-radius:2px; margin-right:6px; vertical-align:middle; }
ul { margin:6px 0 0; padding-left:20px; } li { margin:3px 0; }
</style></head>
<body><main>
<h1>Connecticut Load Forecast Leaderboard</h1>
<p class="sub">OPIM 5512 &middot; updated """ + eastern(now.isoformat()) + """ (Eastern), every hour &middot; """ + \
        str(len(students)) + """ forecaster""" + ("" if len(students) == 1 else "s") + """ scored &middot; forecasts made """ + \
        eastern(first) + " to " + eastern(last) + """</p>

<div class="card scroll"><table>
<thead><tr><th>Rank</th><th>Forecaster</th><th title="How much better than same hour yesterday, averaged over 1-6, 7-12, 13-18 and 19-24 hours ahead">Skill</th>
<th>Avg miss (MW)</th><th>1&ndash;6 h</th><th>7&ndash;12 h</th><th>13&ndash;18 h</th><th>19&ndash;24 h</th><th>Beat ISO-NE?</th>""" + \
        range_heads + """<th title="The 2026 test-year MAE from your notebook (Lab 5 Part 7a), using the weather that actually happened">Test-year MAE</th><th>Hours scored</th></tr></thead>
<tbody>""" + "".join(rows) + """</tbody></table>""" + waiting_note + """</div>

""" + live_card + """<div class="card"><h2>Skill by hours ahead</h2>
<p class="note" style="margin-top:0">Above 0% = better than "same hour yesterday" at that many hours ahead. Far-ahead hours are harder, so they count as much as next-hour ones.</p>
""" + chart_svg + '<div class="legend">' + chart_legend + """</div></div>

<div class="card"><h2>How it's scored</h2><ul>
<li>Every hour your model forecasts the next 24 hours. Each forecast hour is scored once Connecticut's actual load for that hour is in.</li>
<li><b>Avg miss</b> = average |actual &minus; your expected value|, in MW. The four columns split it by how far ahead you forecast.</li>
<li><b>Skill</b> = how much smaller your miss is than "same hour yesterday" on the exact hours you forecast, worked out for 1&ndash;6, 7&ndash;12, 13&ndash;18 and 19&ndash;24 hours ahead and averaged. Rank is by skill, so joining late is no penalty.</li>
<li><b>Test-year MAE</b> is the score your notebook gave your model on 2026 (Lab 5 Part 7a), using the weather that actually happened. Your live <b>Avg miss</b> is usually higher, because in production your model only has the weather <i>forecast</i>, which is wrong too. That gap is the price of forecasting the weather first.</li>
<li>The grey rows are simple forecasts to beat: <b>same hour yesterday</b> (the 0% line for skill), <b>same hour last week</b>, the <b>average of the last 6 hours</b> held flat for all 24 hours, and <b>ISO New England</b>, the grid operator's own forecast (the newest version published before each of yours).</li>
<li>Actual load is ISO New England's live five-minute estimate for Connecticut, averaged per hour, so it can still shift a little.</li>
</ul></div>

<div class="card"><h2>Scoring a range: low, expected and high (Lab 6 and the final project)</h2>
<p style="margin:0 0 8px">Your low and high are your 10th and 90th percentiles: about 8 hours in 10 should land between them. Every forecast hour gets one score, in MW, lower is better:</p>
<p style="margin:0 0 8px;text-align:center"><b>score = ( 0.5 &times; |actual &minus; expected| + 0.1 &times; width + 1 &times; outside ) &divide; 1.5</b></p>
<ul style="margin-bottom:10px">
<li><b>width</b> = high &minus; low. <b>outside</b> = how many MW the actual fell outside your range (0 when it lands inside).</li>
<li>A MW outside costs <b>10 times</b> a MW of width. Wide is safe but adds up; too narrow is cheap until the actual gets out.</li>
<li>With no range (low = expected = high) the score is exactly your Avg miss, |actual &minus; expected|, the Lab 5 score. So everyone ranks on one scale.</li>
</ul>
<p style="margin:0 0 6px">One hour, actual load 3,100 MW. All four expected 3,000 MW, so all four missed by 100 MW:</p>
<div class="scroll"><table style="min-width:640px">
<thead><tr><th style="text-align:left">Forecaster</th><th>Low</th><th>High</th><th>Width</th><th>Outside</th><th>Score</th></tr></thead>
<tbody>
<tr><td class="name">No range (Lab 5)</td><td>3,000</td><td>3,000</td><td>0</td><td>100</td><td>(50 + 0 + 100) &divide; 1.5 = <b>100.0</b></td></tr>
<tr><td class="name">Too narrow</td><td>2,980</td><td>3,020</td><td>40</td><td>80</td><td>(50 + 4 + 80) &divide; 1.5 = <b>89.3</b></td></tr>
<tr class="top"><td class="name">Honest</td><td>2,850</td><td>3,200</td><td>350</td><td>0</td><td>(50 + 35 + 0) &divide; 1.5 = <b>56.7</b></td></tr>
<tr><td class="name">Too wide</td><td>2,000</td><td>4,000</td><td>2,000</td><td>0</td><td>(50 + 200 + 0) &divide; 1.5 = <b>166.7</b></td></tr>
</tbody></table></div>
<p class="note">Over many hours the lowest score goes to the range that holds about 80% of actual loads, no more, no less: honesty is the winning strategy. Watch your <b>Inside range</b> column: well under 80% means widen, well over means narrow. This is the weighted interval score used in forecasting competitions.</p>
</div>
<p class="note">Data: ISO New England (ISO Express), National Weather Service, Iowa State Mesonet. This page refreshes itself every 10 minutes.</p>
</main></body></html>
"""
