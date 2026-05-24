import os
import io
import csv
import json
import requests
import gspread
from datetime import date, timedelta
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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


def save_json(path, payload):
    save_text(path, json.dumps(payload, indent=2))


def get_page_debug_state(page):
    return page.evaluate(
        """
        () => {
            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const takeTexts = (selector, limit = 30) =>
                Array.from(document.querySelectorAll(selector))
                    .map((node) => clean(node.innerText || node.textContent))
                    .filter(Boolean)
                    .slice(0, limit);

            const optionSummary = Array.from(document.querySelectorAll("select")).map((select) => ({
                id: select.id || null,
                name: select.name || null,
                value: select.value || null,
                options: Array.from(select.options).map((option) => ({
                    text: clean(option.textContent),
                    value: option.value
                })).slice(0, 20)
            }));

            const inputSummary = Array.from(document.querySelectorAll("input")).map((input) => {
                const type = (input.type || "text").toLowerCase();
                const payload = {
                    id: input.id || null,
                    name: input.name || null,
                    type,
                    checked: !!input.checked
                };
                if (!["password", "text", "email"].includes(type)) {
                    payload.value = input.value || null;
                }
                return payload;
            }).slice(0, 40);

            const functions = Object.keys(window)
                .filter((key) =>
                    typeof window[key] === "function" &&
                    /(download|export|csv)/i.test(key)
                )
                .slice(0, 40);

            return {
                url: window.location.href,
                title: document.title,
                links: takeTexts("a"),
                buttons: takeTexts("button, input[type='button'], input[type='submit']"),
                headings: takeTexts("h1, h2, h3, .pageTitle, .title"),
                inputSummary,
                selectSummary: optionSummary,
                hasDayResultsTable: !!document.querySelector("#dayResultsTable"),
                hasSearchDayForm: !!document.querySelector("#searchDayForm"),
                hasMonthSelect: !!document.querySelector("#searchDayMonth"),
                hasYearSelect: !!document.querySelector("#searchDayYear"),
                hasLoginUserName: !!document.querySelector("input[name='loginUserName']"),
                hasLoginPassword: !!document.querySelector("input[name='loginPassword']"),
                downloadFunctions: functions
            };
        }
        """
    )


def log_page_debug_state(page, store_name, suffix):
    state = get_page_debug_state(page)
    log(f"{store_name}{suffix} state: {json.dumps(state, sort_keys=True)}")
    return state


def save_debug(page, store_name, suffix):
    page.screenshot(path=f"{store_name}{suffix}.png", full_page=True)
    save_text(f"{store_name}{suffix}.html", page.content())
    save_text(f"{store_name}{suffix}.txt", page.locator("body").inner_text())
    save_json(f"{store_name}{suffix}.json", get_page_debug_state(page))


def has_sales_day_controls(page):
    selectors = [
        "#searchDayMonth",
        "#searchDayYear",
        "#searchDayForm",
        "#dayResultsTable",
    ]
    return any(page.locator(selector).count() > 0 for selector in selectors)


def click_first_available(page, selectors, label, timeout=20000):
    last_error = None
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        target = locator.first
        try:
            target.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        try:
            target.click(timeout=timeout)
            log(f"Clicked {label} via selector: {selector}")
            return selector
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise Exception(f"No selector matched for {label}")


def wait_for_login_success(page, store_name):
    page.wait_for_load_state("networkidle", timeout=120000)
    page.wait_for_timeout(2000)
    if page.locator('input[name="loginUserName"]').count() > 0:
        log_page_debug_state(page, store_name, "_login_failed")
        save_debug(page, store_name, "_login_failed")
        raise Exception(f"{store_name}: still on login page after submit")


def select_first_non_empty_option(options):
    for option in options:
        value = str(option.get("value") or "").strip()
        if value:
            return option
    return None


def handle_location_selection(page, store_name):
    is_location_page = (
        "/user/viewLocations" in page.url or
        page.locator("#multipleLocations").count() > 0
    )
    if not is_location_page:
        return

    log(f"{store_name}: location selection page detected")
    log_page_debug_state(page, store_name, "_locations")

    select_locator = page.locator("#multipleLocations")
    if select_locator.count() > 0:
        options = select_locator.evaluate(
            """
            (node) => Array.from(node.options).map((option) => ({
                text: (option.textContent || "").trim(),
                value: option.value
            }))
            """
        )
        chosen = select_first_non_empty_option(options)
        if not chosen:
            save_json(f"{store_name}_location_options.json", options)
            save_debug(page, store_name, "_locations_missing")
            raise Exception(f"{store_name}: location selector has no usable options")

        log(f"{store_name}: selecting location {chosen['text']} ({chosen['value']})")
        page.select_option("#multipleLocations", value=chosen["value"])

        if page.evaluate("() => typeof changeLocation === 'function'"):
            page.evaluate("() => changeLocation()")
        else:
            click_first_available(
                page,
                [
                    "button:has-text('Continue')",
                    "input[type='button'][value='Continue']",
                    "input[type='submit'][value='Continue']",
                    "text=Continue",
                ],
                "location continue",
                timeout=10000
            )

        page.wait_for_load_state("networkidle", timeout=120000)
        page.wait_for_timeout(2000)
        log_page_debug_state(page, store_name, "_location_selected")
        return

    choice_inputs = page.locator("input[type='radio'], input[type='checkbox']")
    if choice_inputs.count() > 0:
        try:
            choice_inputs.first.check(timeout=10000)
            log(f"{store_name}: selected first available location choice input")
        except Exception:
            try:
                choice_inputs.first.click(timeout=10000)
                log(f"{store_name}: clicked first available location choice input")
            except Exception:
                pass

    try:
        clicked_selector = click_first_available(
            page,
            [
                "button:has-text('Continue')",
                "input[type='button'][value='Continue']",
                "input[type='submit'][value='Continue']",
                "text=Continue",
                "button:has-text('Ok')",
                "input[type='button'][value='Ok']",
                "input[type='submit'][value='Ok']",
                "text=Ok",
            ],
            "location continue",
            timeout=10000
        )
        log(f"{store_name}: attempted location continue via {clicked_selector}")
        page.wait_for_load_state("networkidle", timeout=120000)
        page.wait_for_timeout(2000)
        if "/user/viewLocations" not in page.url and page.locator("#multipleLocations").count() == 0:
            log_page_debug_state(page, store_name, "_location_selected")
            return
    except Exception:
        pass

    clicked_selector = click_first_available(
        page,
        [
            "a[href*='switchLocation']",
            "a[href*='homepage']",
            "a[href*='shifts']",
            "a[href*='locationId']",
        ],
        "location link",
        timeout=10000
    )
    log(f"{store_name}: followed location link via {clicked_selector}")
    page.wait_for_load_state("networkidle", timeout=120000)
    page.wait_for_timeout(2000)
    log_page_debug_state(page, store_name, "_location_selected")


def open_sales_day_view(page, store_name):
    if has_sales_day_controls(page):
        log(f"{store_name}: sales day controls already visible")
        return

    clicked_selector = click_first_available(
        page,
        [
            "text=Sales - Day",
            "a:has-text('Sales - Day')",
            "li:has-text('Sales - Day')",
            "[onclick*='Sales - Day']",
            "[href*='searchDays']",
        ],
        "Sales - Day"
    )

    page.wait_for_timeout(3000)

    try:
        page.wait_for_function(
            """
            () => {
                return !!(
                    document.querySelector("#searchDayMonth") ||
                    document.querySelector("#searchDayYear") ||
                    document.querySelector("#dayResultsTable") ||
                    document.querySelector("#searchDayForm")
                );
            }
            """,
            timeout=30000
        )
    except PlaywrightTimeoutError:
        log_page_debug_state(page, store_name, "_sales_day_missing")
        save_debug(page, store_name, "_sales_day_missing")
        raise Exception(
            f"{store_name}: Sales - Day click succeeded ({clicked_selector}) but day controls did not appear"
        )


def select_option_with_fallback(page, selector, wanted_label, store_name):
    page.wait_for_selector(selector, timeout=30000)
    wanted = str(wanted_label).strip().lower()

    try:
        page.select_option(selector, label=wanted_label)
        return
    except Exception:
        pass

    options = page.locator(selector).evaluate(
        """
        (node) => Array.from(node.options).map((option) => ({
            text: (option.textContent || "").trim(),
            value: option.value
        }))
        """
    )

    for option in options:
        text = option["text"].strip().lower()
        value = str(option["value"]).strip().lower()
        if text == wanted or value == wanted:
            page.select_option(selector, value=option["value"])
            return

    save_json(f"{store_name}_{selector.replace('#', '')}_options.json", options)
    raise Exception(f"{store_name}: could not find option '{wanted_label}' for {selector}")


def click_get_results(page):
    return click_first_available(
        page,
        [
            "#searchDayForm button:has-text('Get Results')",
            "#searchDayForm input[type='submit'][value='Get Results']",
            "button:has-text('Get Results')",
            "input[type='submit'][value='Get Results']",
            "text=Get Results",
        ],
        "Get Results"
    )


def wait_for_shift_rows(page, store_name):
    try:
        page.wait_for_selector("#dayResultsTable tbody tr", timeout=60000)
        return
    except PlaywrightTimeoutError:
        pass

    try:
        page.wait_for_function(
            """
            () => {
                const rows = Array.from(document.querySelectorAll("table tbody tr"));
                return rows.some((row) => {
                    const firstCell = row.querySelector("td");
                    if (!firstCell) return false;
                    return /^\\d{2}\\/\\d{2}\\/\\d{4}$/.test((firstCell.innerText || "").trim());
                });
            }
            """,
            timeout=30000
        )
    except PlaywrightTimeoutError:
        log_page_debug_state(page, store_name, "_day_rows_missing")
        save_debug(page, store_name, "_day_rows_missing")
        raise Exception(f"{store_name}: no day result rows appeared after searching")


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


def get_target_month_year():
    """
    Returns (month_name, year_str) to search.
    On the 1st of the month, Mercury has no data yet for the new month,
    so we look back at the previous month.
    On all other days, we use the current month.
    """
    today = date.today()
    if today.day == 1:
        # Step back to last day of previous month
        target = today - timedelta(days=1)
        log(f"1st of month detected — targeting previous month: {target.strftime('%B %Y')}")
    else:
        target = today
    return target.strftime("%B"), target.strftime("%Y")


def get_latest_shift_date(page, store_name):
    page.wait_for_timeout(4000)
    save_debug(page, store_name, "_sales_day")

    js = """
    () => {
        const tableRows = document.querySelectorAll("#dayResultsTable tbody tr").length
            ? document.querySelectorAll("#dayResultsTable tbody tr")
            : document.querySelectorAll("table tbody tr");
        const rows = Array.from(tableRows);
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
            tableHtml: document.querySelector("#dayResultsTable")
                ? document.querySelector("#dayResultsTable").outerHTML.slice(0, 4000)
                : (document.querySelector("table") ? document.querySelector("table").outerHTML.slice(0, 4000) : null)
        };
    }
    """

    result = page.evaluate(js)
    save_json(f"{store_name}_date_debug.json", result)

    picked = result.get("firstDate")
    if not picked:
        raise Exception(f"No report date found for {store_name}")

    log(f"{store_name}: latest shift date = {picked}")
    return picked


def download_csv_via_browser(page, store_name, shift_date):
    log(f"{store_name}: attempting CSV download for {shift_date}")

    has_download_csv = page.evaluate("() => typeof downloadCSV === 'function'")

    with page.expect_download(timeout=120000) as download_info:
        if has_download_csv:
            log(f"{store_name}: invoking browser downloadCSV('{shift_date}')")
            page.evaluate("(date) => downloadCSV(date)", shift_date)
        else:
            log(f"{store_name}: downloadCSV missing, trying visible CSV/export controls")
            click_first_available(
                page,
                [
                    "button:has-text('CSV')",
                    "a:has-text('CSV')",
                    "text=CSV",
                    "[onclick*='download']",
                    "[onclick*='csv']",
                    "[href*='csv']",
                    "[href*='export']",
                ],
                "CSV download control",
                timeout=15000
            )

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
        log_page_debug_state(page, store_name, "_login_page")
        save_debug(page, store_name, "_login_page")

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.click("#submitButton")

        wait_for_login_success(page, store_name)
        log_page_debug_state(page, store_name, "_after_login")
        save_debug(page, store_name, "_after_login")
        handle_location_selection(page, store_name)

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)
        log_page_debug_state(page, store_name, "_shifts_index")
        save_debug(page, store_name, "_shifts_index")

        open_sales_day_view(page, store_name)

        # Set month/year and fetch results
        month_name, year_str = get_target_month_year()
        log(f"{store_name}: selecting month={month_name} year={year_str}")
        select_option_with_fallback(page, "#searchDayMonth", month_name, store_name)
        select_option_with_fallback(page, "#searchDayYear", year_str, store_name)
        click_get_results(page)
        page.wait_for_timeout(5000)
        wait_for_shift_rows(page, store_name)

        try:
            page.wait_for_function(
                """
                () => {
                    const hasFunction = typeof downloadCSV === 'function';
                    const hasControl = !!(
                        document.querySelector("button[onclick*='download']") ||
                        document.querySelector("button[onclick*='csv']") ||
                        document.querySelector("a[href*='csv']") ||
                        document.querySelector("a[href*='export']") ||
                        Array.from(document.querySelectorAll("button,a,input"))
                            .some((node) => /csv/i.test((node.innerText || node.value || "").trim()))
                    );
                    return hasFunction || hasControl;
                }
                """,
                timeout=30000
            )
        except PlaywrightTimeoutError:
            log_page_debug_state(page, store_name, "_download_control_missing")
            save_debug(page, store_name, "_download_control_missing")
            raise Exception(f"{store_name}: no CSV export control became available")

        shift_date = get_latest_shift_date(page, store_name)
        csv_text = download_csv_via_browser(page, store_name, shift_date)

        return csv_text

    except Exception:
        log_page_debug_state(page, store_name, "_error")
        save_debug(page, store_name, "_error")
        raise

    finally:
        context.close()
        browser.close()


def main():
    stores = [
        ("Texaco", os.environ["STORE_TEXACO_USERNAME"], os.environ["STORE_TEXACO_PASSWORD"]),
        ("Dalton", os.environ["STORE_DALTON_USERNAME"], os.environ["STORE_DALTON_PASSWORD"]),
        ("Rome KS3", os.environ["STORE_ROME_USERNAME"], os.environ["STORE_ROME_PASSWORD"]),
        ("Carnesville", os.environ["STORE_CARNESVILLE_USERNAME"], os.environ["STORE_CARNESVILLE_PASSWORD"]),
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
