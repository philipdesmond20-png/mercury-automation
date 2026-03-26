"""
Mercury POS — Data Collector
==============================
Logs into each store via Playwright, hits all discovered API endpoints,
parses responses, and stores everything in SQLite.

Run: python collect.py
"""

import os
import json
import sqlite3
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL  = "https://monecloud.aboveo.com"
DB_PATH   = "mercury.db"

STORES = [
    {"name": "Texaco",   "username": os.environ["STORE_TEXACO_USERNAME"],  "password": os.environ["STORE_TEXACO_PASSWORD"]},
    {"name": "Dalton",   "username": os.environ["STORE_DALTON_USERNAME"],  "password": os.environ["STORE_DALTON_PASSWORD"]},
    {"name": "Rome KS3", "username": os.environ["STORE_ROME_USERNAME"],    "password": os.environ["STORE_ROME_PASSWORD"]},
]

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ══════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    schema = Path("schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
    # Seed stores
    for s in STORES:
        conn.execute(
            "INSERT OR IGNORE INTO stores (name, username) VALUES (?, ?)",
            (s["name"], s["username"])
        )
    conn.commit()
    conn.close()

def get_store_id(conn, name):
    row = conn.execute("SELECT id FROM stores WHERE name=?", (name,)).fetchone()
    return row["id"] if row else None

def get_or_create_shift(conn, store_id, shift_date, display_date=None):
    row = conn.execute(
        "SELECT id FROM shifts WHERE store_id=? AND shift_date=?",
        (store_id, shift_date)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO shifts (store_id, shift_date, display_date) VALUES (?,?,?)",
        (store_id, shift_date, display_date)
    )
    conn.commit()
    return cur.lastrowid

def save_raw(conn, shift_id, endpoint, text):
    conn.execute(
        "INSERT OR REPLACE INTO raw_responses (shift_id, endpoint, response_text) VALUES (?,?,?)",
        (shift_id, endpoint, text)
    )

# ══════════════════════════════════════════════════════════════════════
# PARSERS
# ══════════════════════════════════════════════════════════════════════

def money(v):
    if v is None: return None
    s = str(v).replace("$","").replace(",","").strip()
    try: return float(s)
    except: return None

def parse_search_days(html):
    """Extract list of shift dates from searchDays response."""
    dates = re.findall(r'(\d{2}/\d{2}/\d{4})', html)
    return list(dict.fromkeys(dates))  # deduplicated, ordered

def parse_fuel(html):
    """Extract fuel summary from getFuelDailySummary response."""
    data = {}
    patterns = {
        "regular_gal": r'Regular.*?(\d+\.?\d*)\s*[Gg]al',
        "regular_amt": r'Regular.*?\$\s*([\d,]+\.?\d*)',
        "diesel_gal":  r'Diesel.*?(\d+\.?\d*)\s*[Gg]al',
        "diesel_amt":  r'Diesel.*?\$\s*([\d,]+\.?\d*)',
        "total_amt":   r'[Tt]otal.*?\$\s*([\d,]+\.?\d*)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, html)
        if m: data[key] = money(m.group(1))
    return data

def parse_financials(html):
    """Extract top-level financials."""
    data = {}
    patterns = {
        "total_sales":   r'[Tt]otal\s+[Ss]ales.*?\$\s*([\d,]+\.?\d*)',
        "fuel_sales":    r'[Ff]uel\s+[Ss]ales.*?\$\s*([\d,]+\.?\d*)',
        "inside_sales":  r'[Ii]nside\s+[Ss]ales.*?\$\s*([\d,]+\.?\d*)',
        "cash_drop":     r'[Cc]ash\s+[Dd]rop.*?\$\s*([\d,]+\.?\d*)',
        "over_short":    r'[Oo]ver.*?[Ss]hort.*?\$\s*(-?[\d,]+\.?\d*)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, html)
        if m: data[key] = money(m.group(1))
    return data

def parse_tenders(html):
    """Extract payment methods from getDailyCardInfo."""
    data = {}
    patterns = {
        "credit":     r'[Cc]redit.*?\$\s*([\d,]+\.?\d*)',
        "debit":      r'[Dd]ebit.*?\$\s*([\d,]+\.?\d*)',
        "mobile":     r'[Mm]obile.*?\$\s*([\d,]+\.?\d*)',
        "food_stamp": r'[Ff]ood\s*[Ss]tamp.*?\$\s*([\d,]+\.?\d*)',
        "coupon":     r'[Cc]oupon.*?\$\s*([\d,]+\.?\d*)',
        "cash":       r'[Cc]ash.*?\$\s*([\d,]+\.?\d*)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, html)
        if m: data[key] = money(m.group(1))
    return data

def parse_lottery(html):
    """Extract lottery data from getLottoSalesDailySummary."""
    data = {}
    patterns = {
        "online_sales":   r'[Oo]nline.*?[Ss]ales.*?\$\s*([\d,]+\.?\d*)',
        "instant_sales":  r'[Ii]nstant.*?[Ss]ales.*?\$\s*([\d,]+\.?\d*)',
        "online_payout":  r'[Oo]nline.*?[Pp]ayout.*?\$\s*([\d,]+\.?\d*)',
        "instant_payout": r'[Ii]nstant.*?[Pp]ayout.*?\$\s*([\d,]+\.?\d*)',
        "total_sales":    r'[Tt]otal.*?[Ss]ales.*?\$\s*([\d,]+\.?\d*)',
        "total_payout":   r'[Tt]otal.*?[Pp]ayout.*?\$\s*([\d,]+\.?\d*)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, html)
        if m: data[key] = money(m.group(1))
    if data.get("total_sales") and data.get("total_payout"):
        data["net"] = round(data["total_sales"] - data["total_payout"], 2)
    return data

def parse_inside_sales(html):
    """Extract inside sales categories and amounts."""
    items = {}
    # Look for table rows with category + amount
    rows = re.findall(
        r'<tr[^>]*>.*?<td[^>]*>([\w\s&/]+)</td>.*?\$([\d,]+\.?\d*)',
        html, re.DOTALL
    )
    for cat, amt in rows:
        cat = cat.strip().upper()
        if cat and len(cat) > 1:
            items[cat] = money(amt)
    return items

# ══════════════════════════════════════════════════════════════════════
# API CALLER
# ══════════════════════════════════════════════════════════════════════

def post_endpoint(page, endpoint, params):
    """POST to a Mercury endpoint using the authenticated browser session."""
    try:
        result = page.evaluate(f"""
            async () => {{
                const resp = await fetch('{BASE_URL}{endpoint}', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                    body: new URLSearchParams({json.dumps(params)})
                }});
                return await resp.text();
            }}
        """)
        return result
    except Exception as e:
        log(f"  Error calling {endpoint}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
# STORE PROCESSOR
# ══════════════════════════════════════════════════════════════════════

def process_store(playwright, store, conn, target_date):
    store_id = get_store_id(conn, store["name"])
    log(f"\n{'='*50}\n{store['name']}\n{'='*50}")

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page    = context.new_page()
    start   = time.time()

    try:
        # Login
        log(f"{store['name']}: logging in...")
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
        page.fill('input[name="loginUserName"]', store["username"])
        page.fill('input[name="loginPassword"]', store["password"])
        with page.expect_navigation(wait_until="networkidle", timeout=60000):
            page.click("#submitButton")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
        log(f"{store['name']}: logged in")

        # Get shift date
        display_date = target_date.strftime("%m/%d/%Y")
        shift_date   = target_date.strftime("%Y-%m-%d")
        month        = target_date.strftime("%B")
        year         = target_date.strftime("%Y")
        start_date   = target_date.strftime("%m/01/%Y")
        end_date     = target_date.strftime("%m/%d/%Y")

        shift_id = get_or_create_shift(conn, store_id, shift_date, display_date)
        log(f"{store['name']}: processing date {display_date} (shift_id={shift_id})")

        endpoints_hit = 0

        # ── 1. Search Days ───────────────────────────────────────────
        html = post_endpoint(page, "/shifts/searchDays", {
            "month": month, "year": year,
            "startDate": start_date, "endDate": end_date
        })
        if html:
            save_raw(conn, shift_id, "searchDays", html)
            endpoints_hit += 1

        # ── 2. Financials Summary ────────────────────────────────────
        html = post_endpoint(page, "/shifts/getFinancialsDailySummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getFinancialsDailySummary", html)
            data = parse_financials(html)
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO financials
                    (shift_id, total_sales, fuel_sales, inside_sales, cash_drop, over_short, raw_json)
                    VALUES (?,?,?,?,?,?,?)
                """, (shift_id, data.get("total_sales"), data.get("fuel_sales"),
                      data.get("inside_sales"), data.get("cash_drop"),
                      data.get("over_short"), json.dumps(data)))
            endpoints_hit += 1

        # ── 3. Fuel ──────────────────────────────────────────────────
        html = post_endpoint(page, "/shifts/getFuelDailySummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getFuelDailySummary", html)
            data = parse_fuel(html)
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO fuel_summary
                    (shift_id, regular_amt, diesel_amt, total_amt, raw_json)
                    VALUES (?,?,?,?,?)
                """, (shift_id, data.get("regular_amt"), data.get("diesel_amt"),
                      data.get("total_amt"), json.dumps(data)))
            endpoints_hit += 1

        # ── 4. Tenders / Card Info ───────────────────────────────────
        html = post_endpoint(page, "/shifts/getDailyCardInfo", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getDailyCardInfo", html)
            data = parse_tenders(html)
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO tenders
                    (shift_id, credit, debit, mobile, food_stamp, coupon, cash, raw_json)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (shift_id, data.get("credit"), data.get("debit"),
                      data.get("mobile"), data.get("food_stamp"),
                      data.get("coupon"), data.get("cash"), json.dumps(data)))
            endpoints_hit += 1

        # ── 5. Lottery ───────────────────────────────────────────────
        html = post_endpoint(page, "/shifts/getLottoSalesDailySummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getLottoSalesDailySummary", html)
            data = parse_lottery(html)
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO lottery
                    (shift_id, online_sales, instant_sales, instant_payout, total_sales, total_payout, net, raw_json)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (shift_id, data.get("online_sales"), data.get("instant_sales"),
                      data.get("instant_payout"), data.get("total_sales"),
                      data.get("total_payout"), data.get("net"), json.dumps(data)))
            endpoints_hit += 1

        # ── 6. Lottery Payout ────────────────────────────────────────
        html = post_endpoint(page, "/shifts/getLottoPayoutSummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getLottoPayoutSummary", html)
            endpoints_hit += 1

        # ── 7. Tax Summary ───────────────────────────────────────────
        html = post_endpoint(page, "/shifts/getDailyTaxSummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getDailyTaxSummary", html)
            endpoints_hit += 1

        # ── 8. Grocery / Inside Sales ────────────────────────────────
        html = post_endpoint(page, "/shifts/getGroceryDailySummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getGroceryDailySummary", html)
            data = parse_inside_sales(html)
            for cat, amt in data.items():
                if amt:
                    conn.execute("""
                        INSERT OR REPLACE INTO inside_sales (shift_id, category, amount)
                        VALUES (?,?,?)
                    """, (shift_id, cat, amt))
            endpoints_hit += 1

        # ── 9. Cash Drops ────────────────────────────────────────────
        html = post_endpoint(page, "/shifts/searchBankDropsWithCashDrops", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "searchBankDropsWithCashDrops", html)
            endpoints_hit += 1

        # ── 10. Wastage ──────────────────────────────────────────────
        html = post_endpoint(page, "/shifts/getDailyWastageSummary", {
            "shiftDate": display_date
        })
        if html:
            save_raw(conn, shift_id, "getDailyWastageSummary", html)
            endpoints_hit += 1

        conn.commit()
        duration = round(time.time() - start, 1)
        conn.execute("""
            INSERT INTO crawler_runs (run_date, store_id, status, endpoints_hit, duration_secs)
            VALUES (?,?,?,?,?)
        """, (shift_date, store_id, "success", endpoints_hit, duration))
        conn.commit()
        log(f"{store['name']}: ✅ done — {endpoints_hit} endpoints, {duration}s")

    except Exception as e:
        log(f"{store['name']}: ❌ error — {e}")
        conn.execute("""
            INSERT INTO crawler_runs (run_date, store_id, status, error_message)
            VALUES (?,?,?,?)
        """, (target_date.strftime("%Y-%m-%d"), store_id, "failed", str(e)))
        conn.commit()

    finally:
        context.close()
        browser.close()


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    target_date = datetime.now() - timedelta(days=1)
    log(f"Collecting data for {target_date.strftime('%Y-%m-%d')}")

    init_db()
    conn = get_db()

    with sync_playwright() as playwright:
        for store in STORES:
            process_store(playwright, store, conn, target_date)

    conn.close()
    log("\n✅ Collection complete")

if __name__ == "__main__":
    main()
