Run the Arena Daily Watcher report. Complete all steps without asking for confirmation.

Today's date: use the current system date.

STEP 1 — Fetch data from Arena MCP:
- Call arena_search_changes with status="SUBMITTED", limit=50
- Call arena_search_changes with status="SUBMITTED", limit=50, offset=50
- Fetch ALL items created this week (Monday 00:00:00Z through today 23:59:59Z) by calling arena_search_items with created_after="<MONDAY>T00:00:00Z", created_before="<TODAY>T23:59:59Z", limit=50, offset=0. Keep incrementing offset by 50 and calling again until the result returns fewer than 50 items. Collect all results across pages. Sort them by creationDateTime descending (newest first).

STEP 2 — Classify results (use today's date YYYY-MM-DD for comparisons):
- submitted_today: submissionDateTime starts with today's date
- submitted_week: submissionDateTime within last 7 days (not today)
- pending_old: submissionDateTime older than 7 days, status still SUBMITTED
- deviations: any result where category.name == "Deviation" — include ALL of them regardless of date

STEP 3 — Compute expiry info for each deviation:
- days_left = (expirationDateTime - today) in whole days
- urgent = days_left < 7 (red)
- warning = 7 <= days_left <= 14 (amber)
- ok = days_left > 14 (green)
- Sort deviations by expirationDateTime ascending

STEP 4 — Build category badge CSS class:
- "Eng Change Order" or "ECO" → cat-eco (purple)
- "Packaging Change Order" or "PCO" → cat-pco (blue)
- "Deviation" or "DEV" → cat-dev (amber)
- "3PP" → cat-3pp (green)
- anything else → cat-other (gray)
Extract the prefix (ECO/PCO/DEV/3PP/etc.) from the change number for the badge label.

STEP 5 — Count stats:
- stat_today = len(submitted_today)
- stat_week = len(submitted_week) + len(submitted_today)
- stat_deviations = len(deviations)
- stat_expiring = count of deviations with days_left <= 14
- stat_items = total count of items fetched across all pages

STEP 6 — Generate a complete self-contained HTML page and write it to:
/Users/mmangen/projects/arena-watcher/index.html

Use this exact HTML structure and style (copy the CSS faithfully):

<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Arena Daily Watcher</title>
<style>
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
.expire-urgent{color:#dc2626;font-weight:700}
.expire-warn{color:#d97706;font-weight:600}
.expire-ok{color:#15803d}
.creator{color:#64748b;font-size:.8rem}
.date{color:#94a3b8;font-size:.78rem;white-space:nowrap}
.note{padding:12px 18px;font-size:.78rem;color:#94a3b8;border-top:1px solid #f1f5f9;background:#fafafa}
footer{text-align:center;padding:20px;color:#94a3b8;font-size:.78rem;border-top:1px solid #e2e8f0;margin-top:12px}
@media(max-width:768px){.stats-row{grid-template-columns:repeat(2,1fr)}.topbar{padding:10px 14px}}
</style>
</head>
<body>
[FILL IN BODY using real data from Steps 1-5 — today's date in header, real counts in stats, real rows in tables, real expiry colors on deviations. Include an "Items Created This Week" section after the deviations section with a table showing all items (Number, Name, Category, Created By, Created date) sorted newest first. If no items, show an empty state message.]
</body>
</html>

STEP 7 — Deploy to Blockcell:
Call manage_site with action="upload", site_name="arena-daily-watcher", directory_path="/Users/mmangen/projects/arena-watcher"

STEP 8 — Print a one-line summary:
"Arena Watcher updated YYYY-MM-DD: N submitted today, N this week, N deviations (N expiring soon)"
