import os
import csv
import io
import json
import re
import requests
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

BASE_URL = "https://monecloud.aboveo.com"
SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"

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


def parse_csv_text(csv_text):
    rows = list(csv.reader(io.StringIO(csv_text)))
    max_cols = max((len(r) for r in rows), default=0)

    if max_cols <= 1:
        rows = list(csv.reader(io.StringIO(csv_text), delimiter="\t"))
        max_cols = max((len(r) for r in rows), default=0)

    for r in rows:
        if len(r) < max_cols:
            r.extend([""] * (max_cols - len(r)))

    return rows


def build_store_block(store_name, csv_text):
    rows = parse_csv_text(csv_text)

    block = []
    block.append(["STORE", store_name])
    block.append([""])

    block.extend(rows)

    block.append([""])
    block.append([""])

    return block


def upload_combined_to_raw_csv(all_rows):
    log("Uploading to RAW_CSV")

    client = get_google_client()
    sheet = client.open_by_key(SHEET_ID)

    raw_ws = sheet.worksheet("RAW_CSV")

    raw_ws.clear()
    raw_ws.update("A1", all_rows)

    log("Upload complete")


def trigger_fill(store_name):
    apps_script_url = os.environ["APPS_SCRIPT_URL"]

    log(f"Triggering Apps Script for {store_name}")

    r = requests.get(apps_script_url, params={"store": store_name})

    log(f"Apps Script status {r.status_code}")


def login_and_download_first_report(playwright, store_name, username, password):
    browser = playwright.chromium.launch(headless=True)

    context = browser.new_context()

    page = context.new_page()

    try:
        log(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle")

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation():
            page.click("#submitButton")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle")

        # CLICK SALES DAY TAB
        page.click("text=Sales - Day")

        page.wait_for_timeout(3000)

        table_text = page.locator("table").first.inner_text()

        dates = re.findall(r"\d{2}/\d{2}/\d{4}", table_text)

        if not dates:
            raise Exception("No report date found")

        shift_date = dates[0]

        log(f"{store_name} latest date {shift_date}")

        csv_text = page.evaluate(
            """
            async (args) => {
                const resp = await fetch(args.url, {
                    method: "POST",
                    credentials: "include",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    body: new URLSearchParams({
                        shiftDate: args.shiftDate,
                        csv: "true"
                    })
                });
                return await resp.text();
            }
            """,
            {
                "url": f"{BASE_URL}/shifts/createDailyPdf",
                "shiftDate": shift_date,
            },
        )

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

    log("All stores completed")


if __name__ == "__main__":
    main()
