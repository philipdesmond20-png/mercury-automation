import os
import csv
import io
import json
import re
import requests
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://monecloud.aboveo.com"
SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def log(msg: str):
    print(msg, flush=True)


def get_google_client():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def save_text(path: str, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def parse_csv_text(csv_text: str):
    candidates = [
        csv.reader(io.StringIO(csv_text)),
        csv.reader(io.StringIO(csv_text), delimiter="\t"),
        csv.reader(io.StringIO(csv_text), delimiter=";"),
    ]

    best_rows = []
    best_width = 0

    for reader in candidates:
        rows = list(reader)
        width = max((len(r) for r in rows), default=0)
        if width > best_width:
            best_rows = rows
            best_width = width

    if not best_rows:
        return [[""]]

    for r in best_rows:
        if len(r) < best_width:
            r.extend([""] * (best_width - len(r)))

    return best_rows


def build_store_block(store_name: str, csv_text: str):
    rows = parse_csv_text(csv_text)

    block = []
    block.append(["STORE", store_name])
    block.append([""])
    block.extend(rows)
    block.append([""])
    block.append([""])

    return block


def upload_combined_to_raw_csv(all_rows):
    log("Uploading combined data to RAW_CSV")
    client = get_google_client()
    sheet = client.open_by_key(SHEET_ID)
    raw_ws = sheet.worksheet("RAW_CSV")
    raw_ws.clear()
    raw_ws.update("A1", all_rows)
    log("RAW_CSV updated")


def trigger_fill(store_name: str):
    apps_script_url = os.environ["APPS_SCRIPT_URL"]
    log(f"Triggering Apps Script for {store_name}")
    r = requests.get(apps_script_url, params={"store": store_name}, timeout=60)
    log(f"Apps Script response for {store_name}: {r.status_code}")


def save_debug(page, store_name: str, suffix: str):
    png_name = f"{store_name}{suffix}.png"
    html_name = f"{store_name}{suffix}.html"
    txt_name = f"{store_name}{suffix}.txt"

    page.screenshot(path=png_name, full_page=True)
    save_text(html_name, page.content())
    save_text(txt_name, page.locator("body").inner_text())

    log(f"Saved debug files: {png_name}, {html_name}, {txt_name}")


def find_first_csv_icon(page, store_name: str):
    # Save debug right before searching
    save_debug(page, store_name, "_sales_day")

    # Try several selectors, from most specific to broadest
    selectors = [
        'table tbody tr td img[src*="csv"]',
        'table tbody tr td a img[src*="csv"]',
        'img[src*="csv"]',
        'a[href*="csv"]',
        'td img',
    ]

    for selector in selectors:
        locator = page.locator(selector)
        count = locator.count()
        log(f"{store_name}: selector {selector} -> {count} matches")

        if count > 0:
            for i in range(count):
                try:
                    el = locator.nth(i)
                    if el.is_visible():
                        log(f"{store_name}: using selector {selector} index {i}")
                        return el
                except Exception:
                    pass

    return None


def login_and_download_first_report(playwright, store_name: str, username: str, password: str):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        log(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle", timeout=120000)

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.click("#submitButton")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        # Click Sales - Day tab
        page.click("text=Sales - Day")
        page.wait_for_timeout(5000)

        # Debug snapshot after Sales - Day opens
        save_debug(page, store_name, "_after_sales_day_click")

        # Find and click actual CSV icon
        csv_icon = find_first_csv_icon(page, store_name)
        if csv_icon is None:
            raise Exception(f"Could not find any visible CSV icon for {store_name}")

        raw_csv_path = f"{store_name}_raw.csv"

        try:
            with page.expect_download(timeout=60000) as download_info:
                csv_icon.click()
            download = download_info.value
        except PlaywrightTimeoutError:
            raise Exception(f"Timed out waiting for CSV download for {store_name}")

        suggested_name = download.suggested_filename
        log(f"{store_name}: suggested downloaded filename = {suggested_name}")

        download.save_as(raw_csv_path)
        log(f"{store_name}: saved raw CSV to {raw_csv_path}")

        with open(raw_csv_path, "r", encoding="utf-8", errors="ignore") as f:
            csv_text = f.read()

        if not csv_text.strip():
            raise Exception(f"Downloaded CSV was empty for {store_name}")

        # Save a debug preview too
        save_text(f"{store_name}_raw_preview.txt", csv_text[:5000])

        log(f"{store_name}: raw CSV length = {len(csv_text)}")
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
            csv_text = login_and_download_first_report(
                playwright, store_name, username, password
            )
            block = build_store_block(store_name, csv_text)
            combined_rows.extend(block)

    upload_combined_to_raw_csv(combined_rows)

    for store_name, _, _ in stores:
        trigger_fill(store_name)

    log("All stores completed")


if __name__ == "__main__":
    main()
