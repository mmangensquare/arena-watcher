"""
BK01C-SNAA Full BOM Refresh
Fetches the complete multi-level BOM from Arena PLM and writes it to Google Sheets.
"""

import os
import time
import json
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ARENA_BASE_URL = "https://api.arenasolutions.com/v1"
ARENA_EMAIL    = os.environ["ARENA_EMAIL"]
ARENA_PASSWORD = os.environ["ARENA_PASSWORD"]

GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]

SHEET_ID        = "15msF5Ju3B4P6CBJ2O_MEjXrbahTqkgUtihzuKFcE9hk"
TOP_ITEM_NUMBER = "BK01C-SNAA"


# ---------------------------------------------------------------------------
# Arena helpers
# ---------------------------------------------------------------------------

def arena_login():
    resp = requests.post(
        f"{ARENA_BASE_URL}/login",
        json={"email": ARENA_EMAIL, "password": ARENA_PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["arenaSessionId"]


def arena_get(session_id, path, params=None):
    headers = {"arena_session_id": session_id}
    resp = requests.get(
        f"{ARENA_BASE_URL}{path}", headers=headers, params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Recursive BOM fetch
# ---------------------------------------------------------------------------

def fetch_bom_recursive(session_id, item_guid, parent_number, level, rows, visited):
    key = (item_guid, parent_number)
    if key in visited:
        return
    visited.add(key)

    bom = arena_get(session_id, f"/items/{item_guid}/bom")
    time.sleep(0.15)  # gentle rate limiting

    for entry in bom.get("results", []):
        child      = entry["item"]
        child_guid = child["guid"]
        child_num  = child["number"]
        child_name = child["name"]
        child_rev  = child["revisionNumber"]
        child_qty  = entry.get("quantity", 1)
        child_ref  = entry.get("refDes") or ""
        child_url  = child.get("url", {}).get("app", f"https://app.bom.com/{child_guid}")

        rows.append([level, parent_number, child_num, child_name,
                     child_rev, child_qty, child_ref, child_url])

        # Always attempt recursion — the BOM call returns empty for leaf parts
        fetch_bom_recursive(
            session_id, child_guid, child_num, level + 1, rows, visited
        )


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

LEVEL_COLORS = {
    0: {"red": 0.07, "green": 0.22, "blue": 0.55},
    1: {"red": 0.17, "green": 0.46, "blue": 0.79},
    2: {"red": 0.56, "green": 0.74, "blue": 0.93},
    3: {"red": 0.42, "green": 0.73, "blue": 0.39},
    4: {"red": 1.0,  "green": 0.95, "blue": 0.20},
    5: {"red": 1.0,  "green": 0.60, "blue": 0.0},
}


def build_sheets_service():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())
    return build("sheets", "v4", credentials=creds)


def apply_formatting(sheet_svc, sheet_gid=0):
    requests_body = [
        {
            "repeatCell": {
                "range": {"sheetId": sheet_gid, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                    },
                    "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
                }},
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_gid,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_gid,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 8,
                }
            }
        },
    ]
    for level, color in LEVEL_COLORS.items():
        requests_body.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_gid,
                        "startRowIndex": 1,
                        "endRowIndex": 5000,
                        "startColumnIndex": 0,
                        "endColumnIndex": 8,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_EQ",
                            "values": [{"userEnteredValue": str(level)}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
                "index": level,
            }
        })

    sheet_svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID, body={"requests": requests_body}
    ).execute()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Login to Arena
    print("Logging in to Arena...")
    session_id = arena_login()

    # 2. Find top-level item
    print(f"Searching for {TOP_ITEM_NUMBER}...")
    search = arena_get(session_id, "/items", params={"number": TOP_ITEM_NUMBER})
    results = search.get("results", [])
    if not results:
        raise ValueError(f"{TOP_ITEM_NUMBER} not found in Arena")

    top      = results[0]
    top_guid = top["guid"]
    top_name = top["name"]
    top_rev  = top["revisionNumber"]
    top_url  = top.get("url", {}).get("app", f"https://app.bom.com/{top_guid}")
    print(f"Found: {TOP_ITEM_NUMBER} — {top_name} (rev {top_rev})")

    # 3. Recursive BOM fetch
    rows    = [[0, "", TOP_ITEM_NUMBER, top_name, top_rev, 1, "", top_url]]
    visited = set()
    fetch_bom_recursive(session_id, top_guid, TOP_ITEM_NUMBER, 1, rows, visited)
    print(f"BOM complete: {len(rows)} rows, {max(r[0] for r in rows) + 1} levels")

    # 4. Update Google Sheet
    print("Connecting to Google Sheets...")
    svc   = build_sheets_service()
    sheet = svc.spreadsheets()

    header   = [["Level", "Parent Number", "Item Number", "Item Name",
                  "Revision", "Quantity", "Ref Des", "Arena Link"]]
    all_rows = header + rows

    # Clear old data first (including old conditional format rules)
    sheet.batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{
            "deleteConditionalFormatRule": {
                "sheetId": 0,
                "index": 0,
            }
        }]},
    ).execute()
    # Suppress errors if no rules exist — just clear and write
    sheet.values().clear(
        spreadsheetId=SHEET_ID, range="Sheet1!A1:H5000"
    ).execute()
    sheet.values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": all_rows},
    ).execute()

    apply_formatting(svc)

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Done — {len(rows)} BOM rows written at {ts}")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")


if __name__ == "__main__":
    main()
