"""
Mercury POS — Shift Lottery Sync
================================
Copies M1/State Lottery values into POS Amount inputs for the
Online / Scratch / Payout rows on the Sales - Shifts screen, then saves.
"""

import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from main import (
    BASE_URL,
    log,
    log_page_debug_state,
    save_debug,
    wait_for_login_success,
    handle_location_selection,
    click_first_available,
    settle_page,
)


ROW_LABELS = ("Online", "Scratch", "Payout")


def parse_currency_value(raw_text):
    text = (raw_text or "").strip()
    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace("£", "").replace("$", "")
    text = text.replace("(", "-").replace(")", "")
    text = text.strip()

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return match.group(0) if match else None


def open_shifts_sync_view(page, store_name):
    click_first_available(
        page,
        [
            "text=Sales - Shifts",
            "a:has-text('Sales - Shifts')",
            "button:has-text('Sales - Shifts')",
            "li:has-text('Sales - Shifts')",
        ],
        "Sales - Shifts",
        timeout=20000,
    )
    settle_page(page, timeout=30000)

    click_first_available(
        page,
        [
            "text=Sales - Current Shift (Ruby)",
            "a:has-text('Sales - Current Shift (Ruby)')",
            "button:has-text('Sales - Current Shift (Ruby)')",
            "li:has-text('Sales - Current Shift (Ruby)')",
            "text=Sales - Current Shift",
        ],
        "Sales - Current Shift (Ruby)",
        timeout=20000,
    )
    settle_page(page, timeout=30000)

    try:
        click_first_available(
            page,
            [
                "text=Version 2",
                "button:has-text('Version 2')",
                "a:has-text('Version 2')",
            ],
            "Version 2",
            timeout=15000,
        )
        settle_page(page, timeout=15000)
    except Exception:
        log(f"{store_name}: Version 2 toggle not found, continuing with current view")

    page.wait_for_function(
        """
        () => {
            const table = document.querySelector("#lotteryTable");
            if (!table) return false;
            const text = (table.innerText || table.textContent || "");
            return /Online/i.test(text) && /Scratch/i.test(text) && /Payout/i.test(text);
        }
        """,
        timeout=30000,
    )


def sync_lottery_rows(page, store_name):
    result = page.evaluate(
        """
        (labels) => {
            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const parseValue = (value) => {
                const cleaned = clean(value)
                    .replace(/,/g, "")
                    .replace(/[£$]/g, "")
                    .replace(/\\(([^)]+)\\)/, "-$1");
                const match = cleaned.match(/-?\\d+(?:\\.\\d+)?/);
                return match ? match[0] : null;
            };

            const table = document.querySelector("#lotteryTable");
            if (!table) {
                return { ok: false, error: "lotteryTable not found" };
            }

            const rows = Array.from(table.querySelectorAll("tbody tr"));
            const updates = [];

            for (const label of labels) {
                const row = rows.find((candidate) => {
                    const firstCell = candidate.querySelector("td");
                    return firstCell && clean(firstCell.innerText || firstCell.textContent).toLowerCase() === label.toLowerCase();
                });

                if (!row) {
                    updates.push({ label, status: "missing-row" });
                    continue;
                }

                const cells = row.querySelectorAll("td");
                const posAmountInput = cells[2] ? cells[2].querySelector("input") : null;
                const m1Cell = cells[3] || null;

                const sourceText = m1Cell ? clean(m1Cell.innerText || m1Cell.textContent) : "";
                const parsed = parseValue(sourceText);

                if (!posAmountInput) {
                    updates.push({ label, status: "missing-input", sourceText });
                    continue;
                }

                if (parsed === null) {
                    updates.push({ label, status: "missing-source", sourceText });
                    continue;
                }

                posAmountInput.focus();
                posAmountInput.value = parsed;
                posAmountInput.dispatchEvent(new Event("input", { bubbles: true }));
                posAmountInput.dispatchEvent(new Event("change", { bubbles: true }));
                posAmountInput.dispatchEvent(new Event("blur", { bubbles: true }));

                updates.push({ label, status: "updated", sourceText, value: parsed });
            }

            return { ok: true, updates };
        }
        """,
        list(ROW_LABELS),
    )

    if not result.get("ok"):
        raise Exception(f"{store_name}: {result.get('error', 'lottery sync failed')}")

    missing = [item for item in result["updates"] if item["status"] != "updated"]
    if missing:
        raise Exception(f"{store_name}: some lottery rows were not updated: {missing}")

    log(f"{store_name}: lottery rows synced: {result['updates']}")


def save_shift_changes(page, store_name):
    click_first_available(
        page,
        [
            "button:has-text('Save')",
            "input[type='button'][value='Save']",
            "input[type='submit'][value='Save']",
            "text=Save",
        ],
        "Save",
        timeout=20000,
    )
    settle_page(page, timeout=30000)


def run_store(playwright, store_name, username, password):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    try:
        log(f"Running lottery shift sync for {store_name}")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)
        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.click("#submitButton")

        wait_for_login_success(page, store_name)
        handle_location_selection(page, store_name)

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)
        open_shifts_sync_view(page, store_name)
        sync_lottery_rows(page, store_name)
        save_shift_changes(page, store_name)

        log_page_debug_state(page, store_name, "_lottery_shift_sync_done")
        save_debug(page, store_name, "_lottery_shift_sync_done")
    except Exception:
        log_page_debug_state(page, store_name, "_lottery_shift_sync_error")
        save_debug(page, store_name, "_lottery_shift_sync_error")
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

    with sync_playwright() as playwright:
        for store_name, username, password in stores:
            run_store(playwright, store_name, username, password)

    log("Lottery shift sync completed successfully")


if __name__ == "__main__":
    main()
