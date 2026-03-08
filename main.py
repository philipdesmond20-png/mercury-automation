import os
import csv
import io
import json
import re
import requests
import gspread
from google.oauth2.service_account import Credentials

BASE_URL = "https://monecloud.aboveo.com"
SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_client():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def login(session, username, password):
    url = BASE_URL + "/user/authenticateLogin/loginForm"

    payload = {
        "loginUserName": username,
        "loginPassword": password,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE_URL + "/user/homepage",
    }

    r = session.post(url, data=payload, headers=headers, allow_redirects=True, timeout=60)

    bad_markers = [
        "Session expired",
        'name="loginUserName"',
        'name="loginPassword"',
        "/user/authenticateLogin/loginForm",
        "<title>MercuryOne</title>",
    ]
    if any(marker in r.text for marker in bad_markers):
        raise Exception("Login failed: received login page instead of authenticated session")


def get_latest_shift_date(session):
    """
    Read the Sales Day page HTML and take the first visible table date.
    This matches the top row in Mercury's Sales Day screen.
    """
    url = BASE_URL + "/shifts/index"
    r = session.get(url, timeout=60)

    if r.status_code != 200:
        raise Exception(f"Failed loading shifts page: HTTP {r.status_code}")

    html = r.text

    if "loginUserName" in html or "Session expired" in html:
        raise Exception("Session expired before reading shifts page")

    match = re.search(r'<td[^>]*>\s*(\d{2}/\d{2}/\d{4})\s*</td>', html, re.IGNORECASE)

    if not match:
        raise Exception("Could not locate shift date in Sales Day table")

    latest_date = match.group(1)
    print(f"Latest shift detected from table: {latest_date}")
    return latest_date


def download_csv(session, shift_date):
    url = BASE_URL + "/shifts/createDailyPdf"

    payload = {
        "shiftDate": shift_date,
        "csv": "true",
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": BASE_URL + "/user/homepage",
        "User-Agent": "Mozilla/5.0",
    }

    r = session.post(url, data=payload, headers=headers, allow_redirects=True, timeout=120)

    text = r.text.strip()

    if "<html" in text.lower() or "<!doctype html" in text.lower():
        raise Exception(f"Download failed for shiftDate={shift_date}: received HTML instead of CSV")

    if not text:
        raise Exception(f"Download failed for shiftDate={shift_date}: empty response")

    return text


def parse_csv_text(csv_text):
    """
    Mercury export parser.
    Handles normal CSV, tab-separated fallback, and pads rows to rectangular shape.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    max_cols = max((len(r) for r in rows), default=0)

    if max_cols <= 1:
        rows = list(csv.reader(io.StringIO(csv_text), delimiter="\t"))
        max_cols = max((len(r) for r in rows), default=0)

    if max_cols <= 1:
        rows = [line.split("\t") for line in csv_text.splitlines()]
        max_cols = max((len(r) for r in rows), default=0)

    if not rows:
        raise Exception("Parsed CSV is empty")

    for r in rows:
        if len(r) < max_cols:
            r.extend([""] * (max_cols - len(r)))

    return rows


def build_store_block(store_name, shift_date, csv_text):
    rows = parse_csv_text(csv_text)
    width = max((len(r) for r in rows), default=2)
    width = max(width, 2)

    block = []
    block.append(["STORE", store_name] + [""] * (width - 2))
    block.append(["SHIFT_DATE", shift_date] + [""] * (width - 2))
    block.append([""] * width)
    block.extend(rows)
    block.append([""] * width)
    block.append([""] * width)
    return block


def upload_combined_to_raw_csv(all_rows):
    client = get_google_client()
    sheet = client.open_by_key(SHEET_ID)
    raw_ws = sheet.worksheet("RAW_CSV")

    raw_ws.clear()
    raw_ws.update("A1", all_rows)


def trigger_fill(store_name):
    apps_script_url = os.environ["APPS_SCRIPT_URL"]
    r = requests.get(apps_script_url, params={"store": store_name}, timeout=60)
    if r.status_code != 200:
        raise Exception(f"Apps Script trigger failed for {store_name}: HTTP {r.status_code}")


def fetch_store_block(store_name, username, password):
    print(f"Running {store_name}")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    login(session, username, password)
    shift_date = get_latest_shift_date(session)
    print(f"{store_name} latest shift: {shift_date}")

    csv_text = download_csv(session, shift_date)
    block = build_store_block(store_name, shift_date, csv_text)

    print(f"Completed download for {store_name}")
    return block, shift_date


def main():
    combined_rows = []

    stores = [
        ("Texaco", os.environ["STORE_TEXACO_USERNAME"], os.environ["STORE_TEXACO_PASSWORD"]),
        ("Dalton", os.environ["STORE_DALTON_USERNAME"], os.environ["STORE_DALTON_PASSWORD"]),
        ("Rome KS3", os.environ["STORE_ROME_USERNAME"], os.environ["STORE_ROME_PASSWORD"]),
    ]

    for store_name, username, password in stores:
        block, shift_date = fetch_store_block(store_name, username, password)
        combined_rows.extend(block)

    upload_combined_to_raw_csv(combined_rows)

    # Trigger fill AFTER all blocks are written
    for store_name, _, _ in stores:
        trigger_fill(store_name)

    print("All stores completed")


if __name__ == "__main__":
    main()
