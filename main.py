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


def save_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


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
        const rows = Array.from(document.querySelectorAll("#dayResultsTable tbody tr"));
        const dates = [];

        for (const row of rows) {
            const firstCell = row.querySelector("td");
            if (!firstCell) continue;
            const txt = (firstCell.innerText || "").trim();
            if (/^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(txt)) {
                dates.push(txt);
            }
        }

        return {
            dates,
            firstDate: dates.length ? dates[0] : null,
            tableHtml: document.querySelector("#dayResultsTable") ? document.querySelector("#dayResultsTable").outerHTML.slice(0, 4000) : null
        };
    }
    """

    result = page.evaluate(js)
    save_text(f"{store_name}_date_debug.json", json.dumps(result, indent=2))

    picked = result.get("firstDate")
    if not picked:
        raise Exception(f"No report date found for {store_name}")

    log(f"{store_name}: latest shift date = {picked}")
    return picked


def download_csv_via_browser(page, store_name, shift_date):
    log(f"{store_name}: invoking browser downloadCSV('{shift_date}')")

    if not page.evaluate("() => typeof downloadCSV === 'function'"):
        raise Exception(f"{store_name}: downloadCSV function is not available on page")

    with page.expect_download(timeout=120000) as download_info:
        page.evaluate("(date) => downloadCSV(date)", shift_date)

    download = download_info.value
    suggested_name = download.suggested_filename
    log(f"{store_name}: browser download started: {suggested_name}")

    temp_path = download.path()
    if not temp_path:
        raise Exception(f"{store_name}: download path not available")

    with open(temp_path, "rb") as f:
        raw_bytes = f.read()

    save_bytes(f"{store_name}_downloaded.bin", raw_bytes)

    text = None
    for enc in ["utf-8-sig", "utf-8", "latin-1"]:
        try:
            text = raw_bytes.decode(enc)
            break
        except Exception:
            continue

    if text is None:
        raise Exception(f"{store_name}: could not decode downloaded file")

    save_text(f"{store_name}_raw.csv", text)

    if "<html" in text.lower() or "<!doctype html" in text.lower():
        save_text(f"{store_name}_raw_response.html", text)
        raise Exception(f"{store_name}: downloaded HTML instead of CSV")

    if not text.strip():
        raise Exception(f"{store_name}: downloaded file is empty")

    log(f"{store_name}: saved proper CSV download")
    return text


def login_and_fetch_csv(playwright, store_name, username, password):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        log(f"Running {store_name}")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.click("#submitButton")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        page.click("text=Sales - Day")
        page.wait_for_timeout(5000)

        page.wait_for_selector("#dayResultsTable", timeout=120000)
        page.wait_for_function("() => typeof downloadCSV === 'function'", timeout=120000)

        shift_date = get_latest_shift_date(page, store_name)
        csv_text = download_csv_via_browser(page, store_name, shift_date)

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
