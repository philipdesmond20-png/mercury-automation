import os
import csv
import io
import json
import requests
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"
BASE_URL = "https://monecloud.aboveo.com"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def log(msg):
    print(msg, flush=True)


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
    log(f"Uploading to RAW_CSV: {len(all_rows)} rows")
    client = get_google_client()
    sheet = client.open_by_key(SHEET_ID)
    raw_ws = sheet.worksheet("RAW_CSV")
    raw_ws.clear()
    raw_ws.update("A1", all_rows)
    log("RAW_CSV upload complete")


def trigger_fill(store_name: str):
    apps_script_url = os.environ["APPS_SCRIPT_URL"]
    log(f"Triggering fill for {store_name}")
    r = requests.get(apps_script_url, params={"store": store_name}, timeout=60)
    log(f"Apps Script response for {store_name}: {r.status_code}")
    if r.status_code != 200:
        raise Exception(f"Apps Script trigger failed for {store_name}: HTTP {r.status_code}")


def login_and_download_first_report(playwright, store_name: str, username: str, password: str):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        log(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle")

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation():
            page.click("#submitButton")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle")

        page.wait_for_selector("table")

        rows = page.locator("table tbody tr")
        row_count = rows.count()

        log(f"{store_name}: table rows found = {row_count}")

        if row_count == 0:
            raise Exception("No table rows found")

        # FIRST DATA ROW (latest report)
        target_row = rows.nth(0)

        report_cell = target_row.locator("td").last

        icons = report_cell.locator("img")

        icon_count = icons.count()

        log(f"{store_name}: report icons found = {icon_count}")

        if icon_count < 1:
            raise Exception("No report icons found")

        # CSV icon is the SECOND icon usually
        csv_icon = icons.nth(1)

        with page.expect_download() as download_info:
            csv_icon.click()

        download = download_info.value

        path = download.path()

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            csv_text = f.read()

        log(f"{store_name}: downloaded length {len(csv_text)}")

        return csv_text

    finally:
        context.close()
        browser.close()

    try:
        log(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle", timeout=120000)

        page.locator('input[name="loginUserName"]').fill(username)
        page.locator('input[name="loginPassword"]').fill(password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.locator("#submitButton").click()

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)
        page.wait_for_selector("table", timeout=120000)

        rows = page.locator("table tr")
        row_count = rows.count()
        log(f"{store_name}: table rows found = {row_count}")

        target_row = None

        for i in range(row_count):
            row = rows.nth(i)
            links = row.locator("a")
            if links.count() >= 3:
                target_row = row
                log(f"{store_name}: using row {i} as latest report row")
                break

        if target_row is None:
            page.screenshot(path=f"{store_name}_no_report_row.png", full_page=True)
            raise Exception(f"Could not find report row for {store_name}")

        csv_link = target_row.locator("a").nth(2)

        with page.expect_download(timeout=120000) as download_info:
            csv_link.click()

        download = download_info.value
        path = download.path()

        if not path:
            raise Exception(f"Download path not available for {store_name}")

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            csv_text = f.read()

        log(f"{store_name}: downloaded file length = {len(csv_text)}")

        if "<html" in csv_text.lower() or "<!doctype html" in csv_text.lower():
            raise Exception(f"Downloaded HTML instead of CSV for {store_name}")

        log(f"Completed download for {store_name}")
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
            log(f"{store_name}: block rows added = {len(block)}")

    upload_combined_to_raw_csv(combined_rows)

    for store_name, _, _ in stores:
        trigger_fill(store_name)

    log("All stores completed")


if __name__ == "__main__":
    main()
