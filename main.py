import os
import csv
import io
import json
import re
import datetime
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
    Read the Sales Day page HTML and pick the latest date shown in the table.
    This avoids timezone issues and avoids assuming /shifts/searchDays returns JSON.
    """
    url = BASE_URL + "/shifts/index"
    r = session.get(url, timeout=60)

    if r.status_code != 200:
        raise Exception(f"Failed to open shifts page: HTTP {r.status_code}")

    html = r.text

    if "loginUserName" in html or "Session expired" in html:
        raise Exception("Session expired before reading shifts page")

    # Find all dates like 03/07/2026 in page HTML
    date_strings = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", html)

    if not date_strings:
        raise Exception("Could not find any shift dates on /shifts/index")

    parsed_dates = []
    for ds in date_strings:
        try:
            dt = datetime.datetime.strptime(ds, "%m/%d/%Y").date()
            parsed_dates.append((dt, ds))
        except ValueError:
            continue

    if not parsed_dates:
        raise Exception("Found date-like strings, but none parsed successfully")

    # Choose the latest date shown on page
    latest_dt, latest_str = max(parsed_dates, key=lambda x: x[0])
    print(f"Latest shift detected from page: {latest_str}")
    return latest_str


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

    # If Mercury sends HTML, it means export failed / session expired
    if "<html" in r.text.lower() or "<!doctype html" in r.text.lower():
        raise Exception(f"Download failed for shiftDate={shift_date}: received HTML instead of CSV")

    if len(r.text.strip()) == 0:
        raise Exception(f"Download failed for shiftDate={shift_date}: empty response")

    return r.text


def upload_to_raw_csv(csv_text):
    client = get_google_client()
    sheet = client.open_by_key(SHEET_ID)
    raw_ws = sheet.worksheet("RAW_CSV")

    raw_ws.clear()

    reader = csv.reader(io.StringIO(csv_text))
    rows = [row for row in reader]

    if not rows:
        raise Exception("Parsed CSV is empty")

    raw_ws.update("A1", rows)


def trigger_fill(store_name):
    apps_script_url = os.environ["APPS_SCRIPT_URL"]
    r = requests.get(apps_script_url, params={"store": store_name}, timeout=60)
    if r.status_code != 200:
        raise Exception(f"Apps Script trigger failed for {store_name}: HTTP {r.status_code}")


def run_store(store_name, username, password):
    print(f"Running {store_name}")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    login(session, username, password)

    shift_date = get_latest_shift_date(session)
    print(f"{store_name} latest shift: {shift_date}")

    csv_text = download_csv(session, shift_date)
    upload_to_raw_csv(csv_text)
    trigger_fill(store_name)

    print(f"Completed {store_name}")


def main():
    run_store(
        "Texaco",
        os.environ["STORE_TEXACO_USERNAME"],
        os.environ["STORE_TEXACO_PASSWORD"],
    )

    run_store(
        "Dalton",
        os.environ["STORE_DALTON_USERNAME"],
        os.environ["STORE_DALTON_PASSWORD"],
    )

    run_store(
        "Rome KS3",
        os.environ["STORE_ROME_USERNAME"],
        os.environ["STORE_ROME_PASSWORD"],
    )


if __name__ == "__main__":
    main()
