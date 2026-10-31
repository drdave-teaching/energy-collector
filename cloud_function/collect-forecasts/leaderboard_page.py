# leaderboard_page: turns the leaderboard tables into the public web page students see.
# Nicknames only: NetIDs, buckets and the roster never leave the private collector bucket.

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

EASTERN = ZoneInfo("America/New_York")
INSTRUCTOR_NICKNAME = "dr. dave"     # roster nicknames starting with this are the instructor's models, not students

# The boxes above the live chart. A forecaster you add keeps its color until you uncheck it; the page's 10-minute refresh
# keeps your choices in this browser only.
LINE_PICKER_SCRIPT = """<script>
(function () {
  var box = document.getElementById("linepicker");
  if (!box) { return; }
  var STORE = "leaderboard-lines";
  var COLORS = ["#cf79ec", "#297722", "#9c9116", "#fb766a"];     // chosen to stay apart from the fixed lines, colorblind views included
  var GRAY = "#8d96a8";
  var inputs = box.querySelectorAll("input[data-line]");
  var more = box.querySelector("details.more");
  var defaults = {};
  var assigned = {};
  inputs.forEach(function (input) { defaults[input.dataset.line] = input.checked; });

  function isForecaster(id) { return /^f[0-9]+$/.test(id); }

  function nextColor() {
    var used = Object.values(assigned);
    var free = COLORS.filter(function (color) { return used.indexOf(color) === -1; });
    return free.length > 0 ? free[0] : GRAY;
  }

  function show() {
    var chosen = [];
    inputs.forEach(function (input) {
      var id = input.dataset.line;
      if (input.checked) { chosen.push(id); }
      if (isForecaster(id)) {
        if (!input.checked) { delete assigned[id]; }
      }
    });
    inputs.forEach(function (input) {
      var id = input.dataset.line;
      var color = GRAY;
      if (isForecaster(id) && input.checked) {
        if (!assigned[id]) { assigned[id] = nextColor(); }
        color = assigned[id];
      }
      document.querySelectorAll('svg g[data-line="' + id + '"]').forEach(function (group) {
        group.classList.toggle("off", !input.checked);
        if (isForecaster(id)) { group.style.color = color; }
      });
      if (isForecaster(id)) { input.parentNode.querySelector("i").style.background = color; }
    });
    return chosen;
  }

  function save(chosen) {
    try {
      localStorage.setItem(STORE, JSON.stringify({lines: chosen, open: more ? more.open : false, colors: assigned}));
    } catch (e) { }
  }

  function changed() { save(show()); }

  try {
    var saved = JSON.parse(localStorage.getItem(STORE));
    if (saved && saved.lines) {
      inputs.forEach(function (input) { input.checked = saved.lines.indexOf(input.dataset.line) !== -1; });
      assigned = saved.colors || {};
      if (more) { more.open = !!saved.open; }
    }
  } catch (e) { }

  inputs.forEach(function (input) { input.addEventListener("change", changed); });
  if (more) { more.addEventListener("toggle", changed); }
  var all = document.getElementById("lines-all");
  if (all) {
    all.addEventListener("click", function () {
      inputs.forEach(function (input) { if (isForecaster(input.dataset.line)) { input.checked = true; } });
      changed();
    });
  }
  var reset = document.getElementById("lines-reset");
  if (reset) {
    reset.addEventListener("click", function () {
      inputs.forEach(function (input) { input.checked = defaults[input.dataset.line]; });
      changed();
    });
  }
  show();
})();
</script>
"""


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


def skill_chart(by_lead, board, names, instructors=()):
    """SVG: skill vs same hour yesterday at every lead time for #1, the middle of the class, last place, Dr. Dave and ISO."""
    ranked = board[~board["netid"].str.startswith("BASELINE") & board["skill_pct"].notna()]["netid"].tolist()
    students = []
    for netid in ranked:
        if netid not in instructors:
            students.append(netid)
    width, height, left, right, top, bottom = 760, 300, 52, 16, 16, 40
    plot_w = width - left - right
    plot_h = height - top - bottom

    def skill_of(netid):
        rows = by_lead[by_lead["netid"] == netid].set_index("lead_hours")
        return 100 * (1 - rows["score"] / rows["same_hour_yesterday_mae_mw"])

    lines = []                                   # (label, skill by lead, color, dashed?)
    if len(students) >= 2:
        lines.append(("Last place", skill_of(students[-1]), "#9aa5b1", False))
    if len(students) >= 3:
        everyone = {}
        for netid in students:
            everyone[netid] = skill_of(netid)
        lines.append(("Middle of the class (median)", pd.DataFrame(everyone).median(axis=1), "#d62828", False))
    for netid in instructors:
        if netid in by_lead["netid"].values:
            lines.append((names.get(netid, "Dr. Dave"), skill_of(netid), "var(--gold)", False))
    if len(students) >= 1:
        lines.append(("#1: " + names.get(students[0], "Forecaster"), skill_of(students[0]), "#1f6feb", False))
    iso = by_lead[by_lead["isone_mae_mw"].notna()].copy()
    if len(iso) > 0:
        iso["iso_total"] = iso["isone_mae_mw"] * iso["forecasts"]
        iso["base_total"] = iso["same_hour_yesterday_mae_mw"] * iso["forecasts"]
        sums = iso.groupby("lead_hours")[["iso_total", "base_total"]].sum()
        lines.append(("ISO New England", 100 * (1 - sums["iso_total"] / sums["base_total"]), "var(--muted)", True))

    values = [0.0]
    for _, skill, _, _ in lines:
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
    for label, skill, color, dashed in lines:
        dash = ' stroke-dasharray="6 4"' if dashed else ""
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


def draw(series, x, y, color, width, extra="", tooltip=""):
    parts = []
    title = "<title>" + html.escape(tooltip) + "</title>" if tooltip else ""
    for points in series_points(series, x, y):
        if len(points) == 1:
            xy = points[0].split(",")
            parts.append('<circle cx="' + xy[0] + '" cy="' + xy[1] + '" r="' + str(width + 1) + '" fill="' + color + '"/>')
        else:
            parts.append('<polyline fill="none" stroke="' + color + '" stroke-width="' + str(width) + '"' + extra
                         + ' stroke-linejoin="round" points="' + " ".join(points) + '">' + title + "</polyline>")
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
    for key in ["actual", "past_median", "future", "isone", "baseline_yesterday", "baseline_last_week"]:
        if live.get(key) is not None:
            values.extend([float(v) for v in live[key].dropna()])
    if past is not None and past.shape[1] > 0:
        values.extend([float(v) for v in past.stack().dropna()])
    future_students = live.get("future_by_student")
    same_note = ""
    if future_students is not None and future_students.shape[1] > 0:
        values.extend([float(v) for v in future_students.stack().dropna()])
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
    mine = live.get("future_netid")
    if mine is None and past is not None:
        for netid in live.get("instructor_netids", []):
            if netid in past.columns:
                mine = netid
                break

    def series_of(table, netid):
        if table is not None and netid in table.columns:
            return table[netid]
        return None

    def line_group(line_id, shown, content, color=""):
        """One <g> per line, so the boxes above the chart can show or hide it. Ids are positions, never NetIDs."""
        return ('<g class="line' + ("" if shown else " off") + '" data-line="' + line_id + '"'
                + (' style="color:' + color + '"' if color else "") + ">" + content + "</g>")

    def pick_box(line_id, shown, color, label):
        return ('<label class="pick"><input type="checkbox" data-line="' + line_id + '"' + (" checked" if shown else "")
                + '><i style="background:' + color + '"></i><span>' + html.escape(label) + "</span></label>")

    # Every other forecaster stays hidden until someone checks the box: leaderboard order first, then anyone not ranked yet
    roster_ids = {}
    for netid in names:
        roster_ids[netid] = "f" + str(len(roster_ids))
    others = []
    for netid in live.get("ranked_netids", []):
        if netid != top and netid != mine and netid in roster_ids:
            others.append(netid)
    for table in [past, future_students]:
        if table is not None:
            for netid in table.columns:
                if netid != top and netid != mine and netid in roster_ids and netid not in others:
                    others.append(netid)
    more_boxes = []
    lines_drawn = 0
    for netid in others:
        content = ""
        if series_of(past, netid) is not None:
            content = draw(past[netid], x, y, "currentColor", 1.8, tooltip=names.get(netid, "Forecaster"))
        if series_of(future_students, netid) is not None:
            content = content + draw(future_students[netid], x, y, "currentColor", 1.8, tooltip=names.get(netid, "Forecaster"))
        if content:
            parts.append(line_group(roster_ids[netid], False, content, "#8d96a8"))
            more_boxes.append(pick_box(roster_ids[netid], False, "#8d96a8", names.get(netid, "Forecaster")))
            lines_drawn += 1

    median_content = ""
    if live.get("past_median") is not None:
        median_content = draw(live["past_median"], x, y, "#d62828", 2.2, tooltip="Class median")
    if live.get("future_median") is not None and len(live["future_median"]) > 0:
        median_content = median_content + draw(live["future_median"], x, y, "#d62828", 2.2, ' stroke-dasharray="7 4"', tooltip="Class median")
    baseline_boxes = []
    if median_content:
        parts.append(line_group("median", False, median_content))
        baseline_boxes.append(pick_box("median", False, "#d62828", "Class median"))
    baselines = [("baseline_last_week", "lastweek", "Same hour last week", "#2a9d8f", False),
                 ("baseline_yesterday", "yesterday", "Same hour yesterday", "#7b2cbf", True)]   # the flat 6-hour average stays in the table only
    default_boxes = []
    for key, line_id, label, color, shown in baselines:
        if live.get(key) is not None and live[key].notna().any():
            parts.append(line_group(line_id, shown, draw(live[key], x, y, color, 2, ' stroke-dasharray="2 3"', tooltip="Baseline: " + label)))
            box = pick_box(line_id, shown, color, label + " (dotted)")
            if shown:
                default_boxes.append(box)
            else:
                baseline_boxes.append(box)
    if live.get("isone") is not None and len(live["isone"]) > 0:
        parts.append(line_group("iso", True, draw(live["isone"], x, y, "var(--muted)", 2, ' stroke-dasharray="5 4"', tooltip="ISO New England")))
        default_boxes.insert(0, pick_box("iso", True, "var(--muted)", "ISO New England (dashed)"))
    if top is not None and top != mine:
        name = names.get(top, "Forecaster")
        content = ""
        if series_of(past, top) is not None:
            content = draw(past[top], x, y, "#1f6feb", 2.6, tooltip=name)
        if series_of(future_students, top) is not None:
            content = content + draw(future_students[top], x, y, "#1f6feb", 2.6, ' stroke-dasharray="7 4"', tooltip=name)
        if content:
            parts.append(line_group("top", True, content))
            default_boxes.insert(1, pick_box("top", True, "#1f6feb", "#1 student: " + name))
    if mine is not None:
        name = names.get(mine, "Dr. Dave")
        content = ""
        if series_of(past, mine) is not None:
            content = draw(past[mine], x, y, "var(--gold)", 2.6, tooltip=name)
        if live.get("future") is not None and len(live["future"]) > 0:
            content = content + draw(live["future"], x, y, "var(--gold)", 2.6, ' stroke-dasharray="7 4"', tooltip=name)
        if content:
            parts.append(line_group("dave", True, content))
            default_boxes.insert(1, pick_box("dave", True, "var(--gold)", name))
    if live.get("actual") is not None and len(live["actual"]) > 0:
        parts.append(line_group("actual", True, draw(live["actual"], x, y, "var(--ink)", 3, tooltip="Actual load")))
        default_boxes.insert(0, pick_box("actual", True, "var(--ink)", "Actual load"))

    picker = '<div class="picker" id="linepicker">' + "".join(default_boxes)
    if len(baseline_boxes) + len(more_boxes) > 0:
        picker = (picker + '<details class="more"><summary>Add others</summary><div class="morelist">' + "".join(baseline_boxes)
                  + "".join(more_boxes) + '</div><div class="morebuttons">')
        if lines_drawn > 0:
            picker = picker + '<button type="button" id="lines-all">Add all ' + str(lines_drawn) + " forecasters</button>"
        picker = picker + '<button type="button" id="lines-reset">Back to the defaults</button></div></details>'
    picker = picker + "</div>"

    if future_students is not None and future_students.shape[1] > 0:
        # How many students sent the exact same forecast (usually the default Lab 5 champion)?
        groups = {}
        for netid in future_students.columns:
            signature = tuple(future_students[netid].round(0).fillna(-1).tolist())
            groups[signature] = groups.get(signature, 0) + 1
        biggest = max(groups.values())
        if biggest >= 2:
            same_note = (str(biggest) + " of " + str(future_students.shape[1]) + " students sent the exact same 24 hours "
                         "(the default Lab 5 model), so their lines sit on top of each other. Change one thing in Lab 5 "
                         "Part 5 or 6, re-run 7a to 7d, and get a line of your own.")

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
    if same_note:
        legend.append('<span class="key" style="flex-basis:100%"><b>Same model?</b> ' + html.escape(same_note) + "</span>")
    return "".join(parts), "".join(legend), picker


def render(board, by_lead, roster, waiting, now=None, live=None, statuses=None, lab6=None):
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
        range_heads = '<th title="Average CRPS per forecast hour, lower is better. With no range it equals Avg miss">CRPS</th>' +             '<th title="Share of hours the actual load fell between your low and high">Inside range</th><th>Range width (MW)</th>'
    instructors = []
    if "nickname" in roster.columns:
        for i in range(len(roster)):
            if str(roster["nickname"].iloc[i]).strip().lower().startswith(INSTRUCTOR_NICKNAME):
                instructors.append(str(roster["netid"].iloc[i]).strip().lower())
    chart_svg, chart_legend = skill_chart(by_lead, board, names, instructors)
    live_card = ""
    if live is not None:
        live_svg, live_legend, live_picker = live_chart(live, names)
        if live_svg:
            live_card = """<div class="card"><h2>The last 24 hours and the next 24</h2>
<p class="note" style="margin-top:0">""" + weather_summary(live) + """</p>
""" + live_picker + live_svg + '<div class="legend">' + live_legend + """</div>
<p class="note">Check a box to show a line and uncheck it to hide it. Add others lists the rest of the class in leaderboard order, plus the class median and same hour last week. On the left, each forecaster's line is their latest call for an hour, made before it began. For Lab 5 the next 24 hours show everyone&rsquo;s newest forecast, so you can see how your model compares before the hour arrives (hover a line for its nickname). A forecast counts only for hours that start after the collector first sees it, so a late or backdated forecast scores nothing.</p></div>
"""
    waiting_note = ""
    if waiting > 0:
        waiting_note = "<p class=\"note\">" + str(waiting) + " more forecaster" + ("s are" if waiting > 1 else " is") + \
            " signed up and waiting for a first scored hour.</p>"

    # Who is forecasting right now (nicknames only), so a new forecaster sees their name before their first score
    status_words = {"ok": "forecasting &#10003;", "stale": "stopped: check forecast-24h",
                    "not_shared": "share pending (Lab 5 Part 7e)", "no_forecasts_yet": "no forecast yet (Lab 5 Part 7d)",
                    "bucket_not_found": "bucket not found: ask Dr. Dave", "error": "collector error: ask Dr. Dave"}
    live_now = ""
    if statuses:
        scored = set(board["netid"])
        items = []
        live_count = 0
        for status in statuses:
            name = html.escape(names.get(status.get("netid"), "Forecaster"))
            word = status_words.get(status.get("status"), html.escape(str(status.get("status"))))
            if status.get("status") == "ok":
                live_count += 1
                if status.get("netid") not in scored:
                    word = word + ", first score after its first full hour"
            items.append('<li><b>' + name + '</b>: ' + word + "</li>")
        live_now = ('<div class="card"><h2>Forecasting now: ' + str(live_count) + " of " + str(len(statuses)) + "</h2>"
                    '<p class="note" style="margin-top:0">Every forecaster the collector reads each hour. A new forecast '
                    "is scored once the hour it forecasts is over and Connecticut's actual load is in, so a model shipped "
                    "tonight shows up in the table above within about two hours.</p><ul class=\"forecasting\">"
                    + "".join(items) + "</ul></div>" + chr(10))

    # Lab 6: ranges only, ranked by CRPS skill
    lab6_card = ""
    if lab6 is not None and len(lab6) > 0:
        lab6_rows = []
        for i in range(len(lab6)):
            row = lab6.iloc[i]
            css = ' class="top"' if i == 0 else ""
            lab6_rows.append("<tr" + css + "><td>" + str(i + 1) + '</td><td class="name">' +
                             html.escape(names.get(row["netid"], "Forecaster")) + '</td><td class="skill">' +
                             number(row["crps_skill_pct"], 1, "%") + "</td><td>" + number(row["crps_mw"], 1) + "</td><td>" +
                             number(row["range_coverage_pct"], 0, "%") + "</td><td>" + number(row["range_width_mw"]) +
                             "</td><td>" + number(row["mae_mw"]) + "</td><td>" + number(row["hours_scored"]) + "</td></tr>")
        lab6_card = """<div class="card scroll"><h2>Lab 6 &middot; Ranges, ranked by CRPS</h2>
<p class="note" style="margin-top:0">Only forecasts with a low and a high, made since Lab 6 opened. <b>CRPS</b> is the score per forecast hour in MW (lower is better; see "Scoring a range" below). <b>CRPS skill</b> compares it with same hour yesterday on the exact hours you forecast, so joining late is no penalty. Aim for about 80% inside.</p>
<table><thead><tr><th>Rank</th><th>Forecaster</th><th>CRPS skill</th><th>CRPS (MW)</th><th>Inside range</th><th>Range width (MW)</th><th>Avg miss (MW)</th><th>Hours scored</th></tr></thead>
<tbody>""" + "".join(lab6_rows) + """</tbody></table></div>
"""

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
ul.forecasting { columns: 2; column-gap: 32px; } @media (max-width: 600px) { ul.forecasting { columns: 1; } }
svg .off { display:none; }
.picker { display:flex; flex-wrap:wrap; gap:6px 16px; align-items:center; margin:10px 0 8px; font-size:13px; }
.pick { cursor:pointer; display:inline-flex; align-items:center; min-height:24px; }
.pick input { margin:0 6px 0 0; }
.pick i { display:inline-block; width:14px; height:4px; border-radius:2px; margin-right:6px; flex:none; }
.more { flex-basis:100%; }
.more summary { cursor:pointer; color:var(--navy); font-weight:600; }
.morelist { display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:2px 16px; margin-top:6px; }
.morebuttons { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
.morebuttons button { font:inherit; color:var(--ink); background:var(--card); border:1px solid var(--line); border-radius:6px;
                      padding:3px 10px; cursor:pointer; }
</style></head>
<body><main>
<h1>Connecticut Load Forecast Leaderboard</h1>
<p class="sub">OPIM 5512 &middot; updated """ + eastern(now.isoformat()) + """ (Eastern), every hour &middot; """ + \
        str(len(students)) + """ forecaster""" + ("" if len(students) == 1 else "s") + """ scored &middot; forecasts made """ + \
        eastern(first) + " to " + eastern(last) + """</p>

""" + lab6_card + """<div class="card scroll"><table>
<thead><tr><th>Rank</th><th>Forecaster</th><th title="How much better than same hour yesterday, averaged over 1-6, 7-12, 13-18 and 19-24 hours ahead">Skill</th>
<th>Avg miss (MW)</th><th>1&ndash;6 h</th><th>7&ndash;12 h</th><th>13&ndash;18 h</th><th>19&ndash;24 h</th><th>Beat ISO-NE?</th>""" + \
        range_heads + """<th title="The 2026 test-year MAE from your notebook (Lab 5 Part 7a), using the weather that actually happened">Test-year MAE</th><th>Hours scored</th></tr></thead>
<tbody>""" + "".join(rows) + """</tbody></table>""" + waiting_note + """</div>
""" + live_now + """
""" + live_card + """<div class="card"><h2>Skill by hours ahead</h2>
<p class="note" style="margin-top:0">Above 0% = better than "same hour yesterday" at that many hours ahead. Far-ahead hours are harder, so they count as much as next-hour ones. Lines: #1 on the leaderboard, the middle of the class (the median student at each hour ahead), last place, Dr. Dave's model and ISO New England.</p>
""" + chart_svg + '<div class="legend">' + chart_legend + """</div></div>

<div class="card"><h2>How it's scored</h2><ul>
<li>Every hour your model forecasts the next 24 hours. Each forecast hour is scored once Connecticut's actual load for that hour is in.</li>
<li><b>Avg miss</b> = average |actual &minus; your expected value|, in MW. The four columns split it by how far ahead you forecast.</li>
<li><b>Skill</b> = how much smaller your miss is than "same hour yesterday" on the exact hours you forecast, worked out for 1&ndash;6, 7&ndash;12, 13&ndash;18 and 19&ndash;24 hours ahead and averaged. Rank is by skill, so joining late is no penalty.</li>
<li><b>Test-year MAE</b> is the score your notebook gave your model on 2026 (Lab 5 Part 7a), using the weather that actually happened. Your live <b>Avg miss</b> is usually higher, because in production your model only has the weather <i>forecast</i>, which is wrong too. That gap is the price of forecasting the weather first.</li>
<li>The grey rows are simple forecasts to beat: <b>same hour yesterday</b> (the 0% line for skill), <b>same hour last week</b>, the <b>average of the last 6 hours</b> held flat for all 24 hours, and <b>ISO New England</b>, the grid operator's own forecast (the newest version published before each of yours).</li>
<li>Actual load is ISO New England's live five-minute estimate for Connecticut, averaged per hour, so it can still shift a little.</li>
</ul></div>

<div class="card"><h2>Scoring a range: low, expected and high, the CRPS (Lab 6 and the final project)</h2>
<p style="margin:0 0 8px">Your low and high are your 10th and 90th percentiles: about 8 hours in 10 should land between them. Every forecast hour gets one score, in MW, lower is better:</p>
<p style="margin:0 0 8px;text-align:center"><b>CRPS = ( 0.5 &times; |actual &minus; expected| + 0.1 &times; width + 1 &times; outside ) &divide; 1.5</b></p>
<ul style="margin-bottom:10px">
<li><b>width</b> = high &minus; low. <b>outside</b> = how many MW the actual fell outside your range (0 when it lands inside).</li>
<li>A MW outside costs <b>10 times</b> a MW of width. Wide is safe but adds up; too narrow is cheap until the actual gets out.</li>
<li>With no range (low = expected = high) the score is exactly your Avg miss, |actual &minus; expected|, the Lab 5 score. So everyone ranks on one scale.</li>
</ul>
<p style="margin:0 0 6px">Each hourly run sends <b>24 rows</b>, one per hour ahead, each with its own low, expected and high. At 2:20 p.m. your model forecasts 3 p.m. today through 2 p.m. tomorrow. When one of those hours is over, that row gets scored, and your score is the average over every row of every run. Far-ahead rows are harder, so an honest range is usually wider for them.</p>
<p style="margin:0 0 6px">Take one of those rows: <b>1 p.m. tomorrow</b>, 23 hours ahead. The actual load turns out to be 3,100 MW. Four forecasters all expected 3,000 MW, so all four missed by 100 MW, but their ranges differ:</p>
<div class="scroll"><table style="min-width:640px">
<thead><tr><th style="text-align:left">Forecaster</th><th>Low</th><th>High</th><th>Width</th><th>Outside</th><th>Score</th></tr></thead>
<tbody>
<tr><td class="name">No range (Lab 5)</td><td>3,000</td><td>3,000</td><td>0</td><td>100</td><td>(50 + 0 + 100) &divide; 1.5 = <b>100.0</b></td></tr>
<tr><td class="name">Too narrow</td><td>2,980</td><td>3,020</td><td>40</td><td>80</td><td>(50 + 4 + 80) &divide; 1.5 = <b>89.3</b></td></tr>
<tr class="top"><td class="name">Honest</td><td>2,850</td><td>3,200</td><td>350</td><td>0</td><td>(50 + 35 + 0) &divide; 1.5 = <b>56.7</b></td></tr>
<tr><td class="name">Too wide</td><td>2,000</td><td>4,000</td><td>2,000</td><td>0</td><td>(50 + 200 + 0) &divide; 1.5 = <b>166.7</b></td></tr>
</tbody></table></div>
<p class="note">Over many hours the lowest score goes to the range that holds about 80% of actual loads, no more, no less: honesty is the winning strategy. Watch your <b>Inside range</b> column: well under 80% means widen, well over means narrow. This is the CRPS (continuous ranked probability score) worked out from three percentiles, also called the weighted interval score, the score forecasting competitions use.</p>
</div>
<p class="note">Data: ISO New England (ISO Express), National Weather Service, Iowa State Mesonet. This page refreshes itself every 10 minutes.</p>""" + LINE_PICKER_SCRIPT + """
</main></body></html>
"""
