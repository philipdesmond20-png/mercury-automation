import os
import io
import csv
import json
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
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def save_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def save_debug(page, store_name, suffix):
    page.screenshot(path=f"{store_name}{suffix}.png", full_page=True)
    save_text(f"{store_name}{suffix}.html", page.content())
    save_text(f"{store_name}{suffix}.txt", page.locator("body").inner_text())


def parse_csv_text(csv_text):
    attempts = [
        csv.reader(io.StringIO(csv_text)),
        csv.reader(io.StringIO(csv_text), delimiter="\t"),
        csv.reader(io.StringIO(csv_text), delimiter=";"),
    ]

    best_rows = []
    best_width = 0

    for reader in attempts:
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
    log("Uploading combined data to RAW_CSV")
    gc = get_google_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("RAW_CSV")
    ws.clear()
    ws.update("A1", all_rows)
    log("RAW_CSV updated")


def trigger_fill(store_name):
    url = os.environ["APPS_SCRIPT_URL"]
    log(f"Triggering Apps Script for {store_name}")
    r = requests.get(url, params={"store": store_name}, timeout=60)
    log(f"Apps Script response for {store_name}: {r.status_code}")


def get_latest_shift_date(page, store_name):
    page.wait_for_timeout(4000)
    save_debug(page, store_name, "_sales_day")

    js = """
    () => {
        const bodyText = document.body.innerText || "";
        const matches = [...bodyText.matchAll(/\\b\\d{2}\\/\\d{2}\\/\\d{4}\\b/g)].map(m => m[0]);

        const rows = Array.from(document.querySelectorAll("tr"));
        const rowDates = [];
        for (const row of rows) {
            const txt = row.innerText || "";
            const m = txt.match(/\\b\\d{2}\\/\\d{2}\\/\\d{4}\\b/);
            if (m) rowDates.push(m[0]);
        }

        const picked = rowDates.length ? rowDates[0] : (matches.length ? matches[0] : null);

        return {
            picked,
            rowDates: rowDates.slice(0, 10),
            allDates: matches.slice(0, 20),
            bodyPreview: bodyText.slice(0, 4000)
        };
    }
    """

    result = page.evaluate(js)
    save_text(f"{store_name}_date_debug.json", json.dumps(result, indent=2))

    picked = result.get("picked")
    if not picked:
        raise Exception(f"No report date found for {store_name}")

    log(f"{store_name}: latest shift date = {picked}")
    return picked


def download_csv_inside_session(page, store_name, shift_date):
    log(f"{store_name}: requesting CSV for {shift_date}")

    js = """
    async (args) => {
        const resp = await fetch('/shifts/createDailyPdf', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://monecloud.aboveo.com/shifts/index'
            },
            body: new URLSearchParams({
                shiftDate: args.shiftDate,
                csv: 'true'
            })
        });

        const text = await resp.text();
        return {
            status: resp.status,
            text: text
        };
    }
    """

    result = page.evaluate(js, {"shiftDate": shift_date})
    status = result["status"]
    csv_text = result["text"]

    save_text(f"{store_name}_raw_response.txt", csv_text[:20000])

    if status != 200:
        raise Exception(f"{store_name}: CSV request returned status {status}")

    if not csv_text.strip():
        raise Exception(f"{store_name}: Empty CSV response")

    if "<html" in csv_text.lower() or "<!doctype html" in csv_text.lower():
        save_text(f"{store_name}_raw_response.html", csv_text)
        raise Exception(f"{store_name}: Received HTML instead of CSV")

    raw_path = f"{store_name}_raw.csv"
    save_text(raw_path, csv_text)
    log(f"{store_name}: saved raw CSV to {raw_path}")

    return csv_text


def login_and_fetch_csv(playwright, store_name, username, password):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    try:
        log(f"Running {store_name}")

        # START directly on shifts page, not homepage
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.click("#submitButton")

        # Re-open shifts page after login to ensure proper module context
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        page.click("text=Sales - Day")
        page.wait_for_timeout(5000)

        shift_date = get_latest_shift_date(page, store_name)
        csv_text = download_csv_inside_session(page, store_name, shift_date)

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
            csv_text = login_and_fetch_csv(playwright, store_name, username, password)
            combined_rows.extend(build_store_block(store_name, csv_text))

    upload_combined_to_raw_csv(combined_rows)

    for store_name, _, _ in stores:
        trigger_fill(store_name)

    log("All stores completed successfully")


if __name__ == "__main__":
    main()
