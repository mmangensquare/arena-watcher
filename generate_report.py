#!/usr/bin/env python3
"""
Arena Daily Watcher — generate index.html from live Arena PLM data.

Requires env vars: ARENA_EMAIL, ARENA_PASSWORD, ARENA_WORKSPACE_ID (optional)
Dependency: httpx  (pip install httpx)
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import httpx

ARENA_BASE_URL = os.environ.get("ARENA_BASE_URL", "https://api.arenasolutions.com/v1")
ARENA_EMAIL = os.environ["ARENA_EMAIL"]
ARENA_PASSWORD = os.environ["ARENA_PASSWORD"]
ARENA_WORKSPACE_ID = os.environ.get("ARENA_WORKSPACE_ID")

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "index.html")

# ---------------------------------------------------------------------------
# Auth & HTTP
# ---------------------------------------------------------------------------

def login() -> str:
    body: dict = {"email": ARENA_EMAIL, "password": ARENA_PASSWORD}
    if ARENA_WORKSPACE_ID:
        body["workspaceId"] = int(ARENA_WORKSPACE_ID)
    resp = httpx.post(f"{ARENA_BASE_URL}/login", json=body, timeout=30)
    if resp.status_code != 200:
        print(f"Arena login failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)
    session_id = resp.json()["arenaSessionId"]
    print("Arena login successful")
    return session_id


def arena_get(session_id: str, path: str, params: dict | None = None) -> dict:
    resp = httpx.get(
        f"{ARENA_BASE_URL}{path}",
        headers={"arena_session_id": session_id},
        params=params or {},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Arena API {resp.status_code} on GET {path}: {resp.text}")
    return resp.json()


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_submitted_changes(session_id: str) -> list:
    """Fetch up to 100 SUBMITTED changes."""
    changes = []
    for offset in [0, 50]:
        data = arena_get(session_id, "/changes", {
            "lifecycleStatus.type": "SUBMITTED",
            "limit": 50,
            "offset": offset,
        })
        batch = data.get("results", [])
        changes.extend(batch)
        if len(batch) < 50:
            break
    print(f"Fetched {len(changes)} submitted changes")
    return changes


def fetch_effective_changes(session_id: str, today: datetime.date) -> tuple[list, list]:
    """Scan all EFFECTIVE changes, returning (deviations, became_effective_this_week).

    Arena has no category filter for changes, so we page through all EFFECTIVE
    results. We collect:
      - deviations: any change whose number starts with DEV-
      - effective_this_week: any change that became effective in the last 7 days
    """
    week_ago = today - timedelta(days=7)
    deviations: list = []
    effective_this_week: list = []
    seen_dev_guids: set = set()
    offset = 0
    total_scanned = 0
    while True:
        data = arena_get(session_id, "/changes", {
            "lifecycleStatus.type": "EFFECTIVE",
            "limit": 50,
            "offset": offset,
        })
        batch = data.get("results", [])
        if not batch:
            break
        total_scanned += len(batch)

        for ch in batch:
            number = ch.get("number", "").upper()
            guid = ch.get("guid")

            if number.startswith("DEV-") and guid not in seen_dev_guids:
                deviations.append(ch)
                seen_dev_guids.add(guid)

            # Determine when the change became effective
            # Try effectiveDateTime first, fall back to submissionDateTime
            eff_dt_str = ch.get("effectiveDateTime") or ch.get("submissionDateTime") or ""
            eff_dt = parse_date(eff_dt_str)
            if eff_dt and week_ago < eff_dt.date() <= today:
                effective_this_week.append(ch)

        if len(batch) < 50:
            break
        offset += 50

    print(
        f"Fetched {len(deviations)} effective deviations, "
        f"{len(effective_this_week)} became effective this week "
        f"(scanned {total_scanned} EFFECTIVE changes)"
    )
    return deviations, effective_this_week


def fetch_items_this_week(session_id: str) -> list:
    """Fetch items created in the last 7 days using Arena criteria filter."""
    import json
    today = datetime.now(timezone.utc)
    week_ago = today - timedelta(days=7)
    criteria = [[{
        "attribute": "creationDateTime",
        "operator": "IS_BETWEEN",
        "value": [
            week_ago.strftime("%Y-%m-%dT00:00:00Z"),
            today.strftime("%Y-%m-%dT23:59:59Z"),
        ],
    }]]
    try:
        data = arena_get(session_id, "/items", {
            "criteria": json.dumps(criteria),
            "responseview": "full",
            "limit": 50,
            "offset": 0,
        })
        items = data.get("results", [])
        # Sort newest first (API has no sort parameter)
        items.sort(key=lambda i: i.get("creationDateTime") or "", reverse=True)
        print(f"Fetched {len(items)} items created this week")
        return items
    except Exception as e:
        print(f"Warning: items criteria filter failed: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_date(iso_str: str) -> str:
    dt = parse_date(iso_str)
    if not dt:
        return ""
    return dt.strftime("%b %-d, %Y")


def cat_from_number(number: str) -> tuple[str, str]:
    """Return (css_class, badge_label) derived from the change number prefix."""
    prefix = number.split("-")[0].upper() if "-" in number else ""
    mapping = {
        "ECO": ("cat-eco", "ECO"),
        "PCO": ("cat-pco", "PCO"),
        "DEV": ("cat-dev", "DEV"),
        "3PP": ("cat-3pp", "3PP"),
    }
    return mapping.get(prefix, ("cat-other", prefix or "?"))


def creator_name(obj: dict) -> str:
    # Arena returns creator under different keys depending on endpoint
    for key in ("creator", "createdBy", "submittedBy"):
        person = obj.get(key)
        if isinstance(person, dict):
            return person.get("fullName") or person.get("name") or ""
    return ""


def extract_url(raw_url) -> str:
    """Arena url field can be a plain string or a dict with 'app'/'api' keys."""
    if isinstance(raw_url, dict):
        return raw_url.get("app") or raw_url.get("api") or "#"
    if isinstance(raw_url, str) and raw_url:
        return raw_url
    return "#"


def days_left(expiry_iso: str, today: datetime.date) -> int:
    dt = parse_date(expiry_iso)
    if not dt:
        return 9999
    return (dt.date() - today).days


# ---------------------------------------------------------------------------
# Classify changes
# ---------------------------------------------------------------------------

def classify(changes: list, effective_devs: list, today: datetime.date) -> dict:
    week_ago = today - timedelta(days=7)
    submitted_today, submitted_week, pending_old = [], [], []
    # Seed deviations from effective list; submitted DEVs will be merged below
    seen_guids = {ch.get("guid") for ch in effective_devs}
    deviations = list(effective_devs)

    for ch in changes:
        sub_dt = ch.get("submissionDateTime", "")
        cat_name = (ch.get("category") or {}).get("name", "")
        is_dev = "deviation" in cat_name.lower() or ch.get("number", "").upper().startswith("DEV-")

        if is_dev and ch.get("guid") not in seen_guids:
            deviations.append(ch)
            seen_guids.add(ch.get("guid"))

        if not sub_dt:
            continue
        sub_date = parse_date(sub_dt)
        if not sub_date:
            continue
        sub_day = sub_date.date()

        if sub_day == today:
            submitted_today.append(ch)
        elif week_ago < sub_day < today:
            submitted_week.append(ch)
        else:
            pending_old.append(ch)

    # Sort deviations by expiration ascending (soonest first)
    deviations.sort(key=lambda d: d.get("expirationDateTime") or "9999")

    return {
        "submitted_today": submitted_today,
        "submitted_week": submitted_week,
        "pending_old": pending_old,
        "deviations": deviations,
    }


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def h(text: str) -> str:
    """HTML-escape a string."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def change_row(ch: dict, include_status: bool = True, date_field: str = "submissionDateTime") -> str:
    number = ch.get("number", "")
    title = ch.get("title", "") or ""
    url = extract_url(ch.get("url"))
    css_cls, badge = cat_from_number(number)
    cname = creator_name(ch)
    sub_date = fmt_date(ch.get(date_field, "") or ch.get("submissionDateTime", ""))
    status = (ch.get("lifecycleStatus") or {}).get("type", "SUBMITTED")
    status_cls = f"status-{status.lower()}"

    status_td = f'<td><span class="status {h(status_cls)}">{h(status.title())}</span></td>' if include_status else ""

    return (
        f'<tr>'
        f'<td><a class="num-link" href="{h(url)}" target="_blank">{h(number)}</a></td>'
        f'<td>{h(title)}</td>'
        f'<td><span class="cat {css_cls}">{badge}</span></td>'
        f'<td class="creator">{h(cname)}</td>'
        f'<td class="date">{h(sub_date)}</td>'
        f'{status_td}'
        f'</tr>\n'
    )


def deviation_row(ch: dict, today: datetime.date) -> str:
    number = ch.get("number", "")
    title = ch.get("title", "") or ""
    url = extract_url(ch.get("url"))
    cname = creator_name(ch)
    exp_iso = ch.get("expirationDateTime", "")
    exp_fmt = fmt_date(exp_iso)
    dl = days_left(exp_iso, today)
    status = (ch.get("lifecycleStatus") or {}).get("type", "SUBMITTED")
    status_cls = f"status-{status.lower()}"

    if dl < 0:
        exp_cls = "expire-urgent"
        dl_str = "EXPIRED"
    elif dl <= 10:
        exp_cls = "expire-urgent"
        dl_str = f"{dl} day{'s' if dl != 1 else ''}"
    elif dl <= 30:
        exp_cls = "expire-warn"
        dl_str = f"{dl} days"
    else:
        exp_cls = "expire-ok"
        dl_str = f"{dl} days"

    return (
        f'<tr>'
        f'<td><a class="num-link" href="{h(url)}" target="_blank">{h(number)}</a></td>'
        f'<td>{h(title)}</td>'
        f'<td><span class="status {h(status_cls)}">{h(status.title())}</span></td>'
        f'<td class="creator">{h(cname)}</td>'
        f'<td class="date">{h(exp_fmt)}</td>'
        f'<td><span class="{exp_cls}">{dl_str}</span></td>'
        f'</tr>\n'
    )


def item_row(item: dict) -> str:
    number = item.get("number", "")
    name = item.get("name", "") or ""
    cat_name = (item.get("category") or {}).get("name", "")
    phase = (item.get("lifecyclePhase") or {}).get("name", "")
    created = fmt_date(item.get("creationDateTime") or item.get("createdDateTime", ""))
    cname = creator_name(item)
    url = extract_url(item.get("url"))

    return (
        f'<tr>'
        f'<td><a class="num-link" href="{h(url)}" target="_blank">{h(number)}</a></td>'
        f'<td>{h(name)}</td>'
        f'<td><span class="cat cat-other" style="font-size:.7rem">{h(cat_name)}</span></td>'
        f'<td class="creator">{h(cname)}</td>'
        f'<td class="date">{h(created)}</td>'
        f'<td class="date">{h(phase)}</td>'
        f'</tr>\n'
    )


def table_or_empty(rows: list, headers: list, empty_msg: str) -> str:
    if not rows:
        return f'<div class="empty">{empty_msg}</div>\n'
    th = "".join(f"<th>{h(col)}</th>" for col in headers)
    tbody = "".join(rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>\n'


def badge_class(count: int, warn_at: int = 0, hot_at: int = 0) -> str:
    if hot_at and count >= hot_at:
        return "hot"
    if warn_at and count >= warn_at:
        return "warn"
    return ""


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;line-height:1.5}
.topbar{position:sticky;top:0;z-index:100;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);border-bottom:1px solid #e2e8f0;padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.topbar h1{font-size:1.05rem;font-weight:700;color:#1e293b;white-space:nowrap}
.topbar .subtitle{font-size:.8rem;color:#64748b}
.topbar .refresh{margin-left:auto;font-size:.78rem;color:#94a3b8}
.container{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.stat .num{font-size:2rem;font-weight:700;line-height:1}
.stat .lbl{font-size:.78rem;color:#64748b;margin-top:4px}
.stat.blue .num{color:#3b82f6}
.stat.green .num{color:#10b981}
.stat.amber .num{color:#f59e0b}
.stat.red .num{color:#ef4444}
.section{background:#fff;border:1px solid #e2e8f0;border-radius:12px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.05);overflow:hidden}
.section-h{padding:14px 18px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #f1f5f9}
.section-h .icon{font-size:1rem}
.section-h h2{font-size:.9rem;font-weight:700;color:#1e293b}
.section-h .badge{margin-left:auto;background:#f1f5f9;border-radius:20px;padding:2px 10px;font-size:.72rem;font-weight:600;color:#64748b}
.section-h .badge.hot{background:#fef2f2;color:#dc2626}
.section-h .badge.warn{background:#fffbeb;color:#b45309}
.section-h .badge.ok{background:#f0fdf4;color:#15803d}
.empty{padding:18px;text-align:center;color:#94a3b8;font-size:.85rem}
table{width:100%;border-collapse:collapse}
th{background:#f8fafc;padding:9px 14px;text-align:left;font-weight:600;font-size:.73rem;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid #e2e8f0;color:#64748b}
td{padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:.84rem;color:#334155;vertical-align:top}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8fafc}
.num-link{font-weight:600;color:#4f46e5;text-decoration:none;font-size:.82rem}
.num-link:hover{text-decoration:underline}
.cat{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.68rem;font-weight:600;white-space:nowrap}
.cat-eco{background:#ede9fe;color:#5b21b6}
.cat-pco{background:#e0f2fe;color:#0369a1}
.cat-dev{background:#fef3c7;color:#92400e}
.cat-3pp{background:#f0fdf4;color:#15803d}
.cat-other{background:#f1f5f9;color:#475569}
.status{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.7rem;font-weight:600}
.status-submitted{background:#dbeafe;color:#1d4ed8}
.status-effective{background:#dcfce7;color:#15803d}
.status-open{background:#f1f5f9;color:#475569}
.status-locked{background:#fef9c3;color:#713f12}
.expire-urgent{color:#dc2626;font-weight:700}
.expire-warn{color:#d97706;font-weight:600}
.expire-ok{color:#15803d}
.creator{color:#64748b;font-size:.8rem}
.date{color:#94a3b8;font-size:.78rem;white-space:nowrap}
.note{padding:12px 18px;font-size:.78rem;color:#94a3b8;border-top:1px solid #f1f5f9;background:#fafafa}
footer{text-align:center;padding:20px;color:#94a3b8;font-size:.78rem;border-top:1px solid #e2e8f0;margin-top:12px}
@media(max-width:768px){.stats-row{grid-template-columns:repeat(2,1fr)}.topbar{padding:10px 14px}}"""


def build_html(data: dict, items: list, today: datetime.date) -> str:
    print(f"DEBUG build_html: received {len(items)} items, truthy={bool(items)}")
    s = data
    submitted_today = s["submitted_today"]
    submitted_week = s["submitted_week"]
    pending_old = s["pending_old"]
    deviations = s["deviations"]
    effective_this_week = s.get("effective_this_week", [])

    stat_today = len(submitted_today)
    stat_week = len(submitted_week) + len(submitted_today)
    stat_devs = len(deviations)
    stat_expiring = sum(1 for d in deviations if days_left(d.get("expirationDateTime", ""), today) <= 30)
    stat_effective_week = len(effective_this_week)
    stat_items = len(items)

    gen_date = today.strftime("%B %-d, %Y")

    # --- section bodies ---
    today_rows = [change_row(ch) for ch in submitted_today]
    today_body = table_or_empty(
        today_rows,
        ["Number", "Title", "Type", "Submitted By", "Submitted", "Status"],
        f"No changes submitted today ({gen_date})",
    )

    week_rows = [change_row(ch) for ch in submitted_week]
    week_body = table_or_empty(
        week_rows,
        ["Number", "Title", "Type", "Submitted By", "Submitted", "Status"],
        "No other changes submitted this week",
    )

    dev_rows = [deviation_row(d, today) for d in deviations]
    dev_body = table_or_empty(
        dev_rows,
        ["Number", "Title", "Status", "Created By", "Expiration", "Days Left"],
        "No active deviations found",
    )

    pending_rows = [change_row(ch, include_status=False) for ch in pending_old]
    pending_body = table_or_empty(
        pending_rows,
        ["Number", "Title", "Type", "Submitted By", "Submitted"],
        "No changes pending longer than 7 days",
    )

    effective_week_rows = [change_row(ch, include_status=False, date_field="effectiveDateTime") for ch in effective_this_week]
    effective_week_body = table_or_empty(
        effective_week_rows,
        ["Number", "Title", "Type", "Submitted By", "Approved"],
        "No changes became effective this week",
    )

    if items:
        item_rows = [item_row(i) for i in items]
        items_body = table_or_empty(
            item_rows,
            ["Number", "Name", "Category", "Created By", "Created", "Phase"],
            "",
        )
    else:
        items_body = (
            '<div class="empty" style="padding:20px">'
            "Items created this week could not be retrieved (API may not support date filtering)."
            "</div>\n"
        )

    dev_badge_cls = badge_class(stat_expiring, warn_at=1, hot_at=3)
    week_badge_cls = "hot" if stat_week >= 5 else ("warn" if stat_week >= 2 else "")

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Arena Daily Watcher</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>&#x1F4E1; Arena Daily Watcher</h1>
    <div class="subtitle">Change orders, submissions &amp; deviation tracking</div>
  </div>
  <div class="refresh">Generated: <strong>{gen_date}</strong></div>
</div>

<div class="container">

<div class="stats-row">
  <div class="stat blue">
    <div class="num">{stat_today}</div>
    <div class="lbl">Submitted Today</div>
  </div>
  <div class="stat green">
    <div class="num">{stat_week}</div>
    <div class="lbl">Submitted This Week</div>
  </div>
  <div class="stat amber">
    <div class="num">{stat_devs}</div>
    <div class="lbl">Active Deviations</div>
  </div>
  <div class="stat red">
    <div class="num">{stat_expiring}</div>
    <div class="lbl">Expiring &lt; 30 Days</div>
  </div>
</div>

<div class="section">
  <div class="section-h">
    <span class="icon">&#x2705;</span>
    <h2>Submitted Today</h2>
    <span class="badge {'ok' if stat_today == 0 else 'hot'}">{stat_today} change{'s' if stat_today != 1 else ''}</span>
  </div>
  {today_body}
</div>

<div class="section">
  <div class="section-h">
    <span class="icon">&#x1F4CB;</span>
    <h2>Submitted This Week</h2>
    <span class="badge {week_badge_cls}">{len(submitted_week)} change{'s' if len(submitted_week) != 1 else ''}</span>
  </div>
  {week_body}
</div>

<div class="section">
  <div class="section-h">
    <span class="icon">&#x2705;</span>
    <h2>Became Effective This Week</h2>
    <span class="badge {'ok' if stat_effective_week == 0 else ''}">{stat_effective_week} change{'s' if stat_effective_week != 1 else ''}</span>
  </div>
  {effective_week_body}
</div>

<div class="section">
  <div class="section-h">
    <span class="icon">&#x26A0;&#xFE0F;</span>
    <h2>Active Deviations</h2>
    <span class="badge {dev_badge_cls}">{stat_devs} active · {stat_expiring} expiring soon</span>
  </div>
  {dev_body}
  <div class="note">Sorted by expiration date. Red = &lt;10 days (urgent), amber = 10–30 days (warning), green = &gt;30 days.</div>
</div>

<div class="section">
  <div class="section-h">
    <span class="icon">&#x1F550;</span>
    <h2>Still Pending Approval</h2>
    <span class="badge">Submitted &gt; 7 days ago</span>
  </div>
  {pending_body}
</div>

<div class="section">
  <div class="section-h">
    <span class="icon">&#x1F4E6;</span>
    <h2>Items Created This Week</h2>
    <span class="badge">{stat_items} item{'s' if stat_items != 1 else ''}</span>
  </div>
  {items_body}
</div>

</div>

<footer>Arena Daily Watcher &bull; Powered by Arena REST API &bull; Refreshed weekdays at 8 AM PT</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    today = datetime.now(timezone.utc).date()
    print(f"Arena Watcher — {today}")

    session_id = login()
    changes = fetch_submitted_changes(session_id)
    effective_devs, effective_this_week = fetch_effective_changes(session_id, today)
    items = fetch_items_this_week(session_id)

    data = classify(changes, effective_devs, today)
    data["effective_this_week"] = effective_this_week

    stat_today = len(data["submitted_today"])
    stat_week = len(data["submitted_week"]) + stat_today
    stat_devs = len(data["deviations"])
    stat_expiring = sum(
        1 for d in data["deviations"]
        if days_left(d.get("expirationDateTime", ""), today) <= 30
    )

    print(f"DEBUG main: passing {len(items)} items to build_html")
    html = build_html(data, items, today)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Written: {OUTPUT_PATH}")
    print(
        f"Arena Watcher updated {today}: "
        f"{stat_today} submitted today, {stat_week} this week, "
        f"{stat_devs} deviations ({stat_expiring} expiring soon)"
    )


if __name__ == "__main__":
    main()
