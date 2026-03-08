import os
import csv
import io
import json
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"
BASE_URL = "https://monecloud.aboveo.com"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_client():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def parse_csv_text(csv_text: str):
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


def build_store_block(store_name: str, csv_text: str):
    rows = parse_csv_text(csv_text)
    width = max((len(r) for r in rows), default=2)
    width = max(width, 2)

    block = []
    block.append(["STORE", store_name] + [""] * (width - 2))
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


def trigger_fill(store_name: str):
    apps_script_url = os.environ["APPS_SCRIPT_URL"]
    import requests
    r = requests.get(apps_script_url, params={"store": store_name}, timeout=60)
    if r.status_code != 200:
        raise Exception(f"Apps Script trigger failed for {store_name}: HTTP {r.status_code}")


def login_and_download_first_report(playwright, store_name: str, username: str, password: str) -> str:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        print(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle", timeout=120000)

        # Login form based on your page HTML
        page.locator('input[name="loginUserName"]').fill(username)
        page.locator('input[name="loginPassword"]').fill(password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.locator("#submitButton").click()

        # Go to Sales Day page
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        # Wait for table rows to appear
        page.wait_for_selector("table tr", timeout=120000)

        # Click the first yellow CSV icon in the report column.
        # From your screenshots, the report column contains icons; yellow CSV is the last icon in the first row.
        # We target the first data row's report cell, then click the CSV image/link inside it.
        first_row = page.locator("table tr").nth(1)

        # Safer: look for download link/image in first data row that appears to be CSV/export
        report_cell = first_row.locator("td").last

        # Try common patterns
        clicked = False
        download = None

        candidates = [
            report_cell.locator('a:has(img[src*="csv"])'),
            report_cell.locator('img[src*="csv"]'),
            report_cell.locator('a[title*="CSV"], a[title*="csv"]'),
            report_cell.locator("a").last,
            report_cell.locator("img").last,
        ]

        for candidate in candidates:
            try:
                if candidate.count() > 0:
                    with page.expect_download(timeout=120000) as download_info:
                        candidate.first.click()
                    download = download_info.value
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked or download is None:
            raise Exception(f"Could not click first CSV icon for {store_name}")

        path = download.path()
        if not path:
            raise Exception(f"Download path not available for {store_name}")

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            csv_text = f.read()

        if "<html" in csv_text.lower() or "<!doctype html" in csv_text.lower():
            raise Exception(f"Downloaded HTML instead of CSV for {store_name}")

        print(f"Completed download for {store_name}")
        return csv_text

    finally:
        context.close()
        browser.close()


def main():
    stores = [
        ("Texaco", os.environ["STORE_TEXACO_USERNAME"], os.environ["STORE_TEXACO_PASSWORD"]),
        ("Dalton", os.environ["STORE_DALTON_USERNAME"], os.environ["STORE_DALTON_PASSWORD"]),
        ("Rome KS3", os.environ["STORE_ROME_USERNAME"], os.environ["STORE_ROME_PASSWORD"]),
    ]

    combined_rows = []

    with sync_playwright() as playwright:
        for store_name, username, password in stores:
            csv_text = login_and_download_first_report(playwright, store_name, username, password)
            block = build_store_block(store_name, csv_text)
            combined_rows.extend(block)

    upload_combined_to_raw_csv(combined_rows)

    for store_name, _, _ in stores:
        trigger_fill(store_name)

    print("All stores completed")


if __name__ == "__main__":
    main()
