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

    page.wait_for_function(
        """
        () => Array.from(document.querySelectorAll("[onclick*='openShift']"))
            .some((node) => /\\d{2}\\/\\d{2}\\/\\d{4}/.test((node.innerText || node.textContent || "").trim()))
        """,
        timeout=30000,
    )

    shift_target = page.evaluate(
        """
        () => {
            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                return style.display !== "none" && style.visibility !== "hidden";
            };

            const candidates = Array.from(document.querySelectorAll("[onclick*='openShift']"))
                .map((node) => ({
                    node,
                    text: clean(node.innerText || node.textContent),
                    onclick: node.getAttribute("onclick") || ""
                }))
                .filter((item) => /\\d{2}\\/\\d{2}\\/\\d{4}/.test(item.text));

            const chosen = candidates.find((item) => isVisible(item.node)) || candidates[0] || null;
            if (!chosen) return null;

            return {
                text: chosen.text.slice(0, 200),
                onclick: chosen.onclick
            };
        }
        """
    )

    if not shift_target or not shift_target.get("onclick"):
        raise Exception(f"{store_name}: could not find a clickable shift row on Sales - Shifts")

    log(f"{store_name}: opening shift detail for {shift_target['text']}")
    opened = page.evaluate(
        """
        (onclickText) => {
            const match = onclickText.match(/openShift\\(['"]?([^'")]+)['"]?\\)/i);
            if (match && typeof window.openShift === "function") {
                window.openShift(match[1]);
                return true;
            }

            const node = Array.from(document.querySelectorAll("[onclick*='openShift']"))
                .find((candidate) => (candidate.getAttribute("onclick") || "") === onclickText);

            if (node) {
                node.click();
                return true;
            }

            return false;
        }
        """,
        shift_target["onclick"],
    )

    if not opened:
        raise Exception(f"{store_name}: failed to open shift detail modal")

    page.wait_for_timeout(2500)
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
            timeout=10000,
        )
        page.wait_for_timeout(1500)
    except Exception:
        log(f"{store_name}: Version 2 toggle not found, continuing with current modal view")

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
    clicked = page.evaluate(
        """
        () => {
            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                return (
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    node.offsetWidth > 0 &&
                    node.offsetHeight > 0
                );
            };

            const lotteryTable = document.querySelector("#lotteryTable");
            const modalScope = lotteryTable
                ? lotteryTable.closest(".ui-dialog, .modal, [role='dialog'], .popup, .modal-content") || lotteryTable.parentElement
                : null;

            const candidates = Array.from(
                document.querySelectorAll("button, input[type='button'], input[type='submit'], a")
            ).filter((node) => {
                const label = clean(node.value || node.innerText || node.textContent);
                return /^save$/i.test(label) && isVisible(node) && !node.disabled;
            });

            const target = (modalScope && candidates.find((node) => modalScope.contains(node))) || candidates[0] || null;
            if (!target) {
                return { ok: false, reason: "no visible Save button found", count: candidates.length };
            }

            const label = clean(target.value || target.innerText || target.textContent);
            target.scrollIntoView({ block: "center", inline: "center" });
            target.click();
            target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));

            return {
                ok: true,
                label,
                tag: target.tagName || null,
                count: candidates.length
            };
        }
        """
    )

    if not clicked.get("ok"):
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
    else:
        log(
            f"{store_name}: clicked Save via DOM helper "
            f"({clicked.get('tag')} {clicked.get('label')}, {clicked.get('count')} visible candidates)"
        )

    page.wait_for_timeout(3000)
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
