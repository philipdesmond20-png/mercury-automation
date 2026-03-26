"""
Mercury POS — Data Collector v2
=================================
Logs into each store, pulls all data from searchDays (full month),
plus detailed endpoints for each day. Stores everything in SQLite.
"""

import os, json, sqlite3, time, re
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://monecloud.aboveo.com"
DB_PATH  = "mercury.db"

STORES = [
    {"name": "Texaco",   "username": os.environ["STORE_TEXACO_USERNAME"],  "password": os.environ["STORE_TEXACO_PASSWORD"]},
    {"name": "Dalton",   "username": os.environ["STORE_DALTON_USERNAME"],  "password": os.environ["STORE_DALTON_PASSWORD"]},
    {"name": "Rome KS3", "username": os.environ["STORE_ROME_USERNAME"],    "password": os.environ["STORE_ROME_PASSWORD"]},
]

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def money(v):
    if not v: return None
    s = str(v).replace('$','').replace(',','').replace('\xa0','').strip()
    negative = '(' in s
    s = s.replace('(','').replace(')','').strip()
    try:
        val = float(s)
        return -val if negative else val
    except: return None

# ══════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript(Path("schema.sql").read_text())
    conn.commit()
    for s in STORES:
        conn.execute("INSERT OR IGNORE INTO stores (name, username) VALUES (?,?)", (s["name"], s["username"]))
    conn.commit()
    conn.close()

def get_store_id(conn, name):
    return conn.execute("SELECT id FROM stores WHERE name=?", (name,)).fetchone()["id"]

def get_or_create_shift(conn, store_id, shift_date, display_date=None):
    row = conn.execute("SELECT id FROM shifts WHERE store_id=? AND shift_date=?", (store_id, shift_date)).fetchone()
    if row: return row["id"]
    cur = conn.execute("INSERT INTO shifts (store_id, shift_date, display_date) VALUES (?,?,?)", (store_id, shift_date, display_date))
    conn.commit()
    return cur.lastrowid

def save_raw(conn, shift_id, endpoint, text):
    conn.execute("INSERT OR REPLACE INTO raw_responses (shift_id, endpoint, response_text) VALUES (?,?,?)", (shift_id, endpoint, text))

# ══════════════════════════════════════════════════════════════
# PARSERS
# ══════════════════════════════════════════════════════════════

# Column indices in searchDays table
COL = {
    'date':0,'fuel_vol':1,'fuel':2,'grocery':3,'lottery':4,'lottery_payout':5,
    'financial':6,'non_fuel':7,'tax':8,'total':9,'mop_ebt':10,
    'local_credits':11,'local_payments':12,'paid_in_out':13,
    'cash_drop':14,'atm_drop':15,'difference':16
}

def parse_search_days(html):
    """Parse searchDays response — returns list of day dicts."""
    tbody = re.search(r'<tbody>(.*?)</tbody>', html, re.DOTALL)
    if not tbody: return []
    days = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbody.group(1), re.DOTALL):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) < 15: continue
        date_str = cells[COL['date']]
        if not re.match(r'\d{2}/\d{2}/\d{4}', date_str): continue
        # Convert MM/DD/YYYY to YYYY-MM-DD
        parts = date_str.split('/')
        shift_date = f"{parts[2]}-{parts[0]}-{parts[1]}"
        days.append({
            'shift_date':      shift_date,
            'display_date':    date_str,
            'fuel':            money(cells[COL['fuel']]),
            'grocery':         money(cells[COL['grocery']]),
            'lottery':         money(cells[COL['lottery']]),
            'lottery_payout':  money(cells[COL['lottery_payout']]),
            'tax':             money(cells[COL['tax']]),
            'total':           money(cells[COL['total']]),
            'mop_ebt':         money(cells[COL['mop_ebt']]),
            'cash_drop':       money(cells[COL['cash_drop']]),
            'difference':      money(cells[COL['difference']]),
            'paid_in_out':     money(cells[COL['paid_in_out']]),
        })
    return days

def parse_fuel_html(html):
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    data = {}
    for r in rows:
        cells = [re.sub(r'<[^>]+>','',c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)]
        cells = [c for c in cells if c]
        if len(cells) >= 3:
            label = cells[0].upper()
            if 'REGULAR' in label: data['regular_gal'] = money(cells[1]); data['regular_amt'] = money(cells[2])
            elif 'DIESEL'  in label: data['diesel_gal']  = money(cells[1]); data['diesel_amt']  = money(cells[2])
            elif 'PREM'    in label: data['super_gal']   = money(cells[1]); data['super_amt']   = money(cells[2])
            elif 'MID'     in label: data['plus_gal']    = money(cells[1]); data['plus_amt']    = money(cells[2])
            elif 'TOTAL'   in label: data['total_gal']   = money(cells[1]); data['total_amt']   = money(cells[2])
    return data

def parse_card_html(html):
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    data = {}
    for r in rows:
        cells = [re.sub(r'<[^>]+>','',c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)]
        cells = [c for c in cells if c and c != '\xa0']
        if len(cells) >= 3:
            label = cells[0].upper()
            amt = money(cells[2]) or money(cells[1])
            if 'CREDIT'     in label: data['credit']     = amt
            elif 'DEBIT'    in label: data['debit']      = amt
            elif 'MOBILE'   in label: data['mobile']     = amt
            elif 'FOODSTAMP'in label or 'FOOD STAMP' in label: data['food_stamp'] = amt
            elif 'COUPON'   in label: data['coupon']     = amt
            elif 'MAN CRED' in label or 'MANUAL CARD' in label: data['manual_card'] = amt
            elif 'MAN DEBIT'in label or 'MANUAL DEBIT' in label: data['manual_debit'] = amt
            elif 'CASH'     in label: data['cash']       = amt
    return data

# ══════════════════════════════════════════════════════════════
# API CALLER
# ══════════════════════════════════════════════════════════════

def post_api(page, endpoint, params):
    try:
        return page.evaluate(f"""
            async () => {{
                const r = await fetch('{BASE_URL}{endpoint}', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                    body: new URLSearchParams({json.dumps(params)})
                }});
                return await r.text();
            }}
        """)
    except Exception as e:
        log(f"  Error {endpoint}: {e}")
        return None

# ══════════════════════════════════════════════════════════════
# STORE PROCESSOR
# ══════════════════════════════════════════════════════════════

def process_store(playwright, store, conn, target_date):
    store_id = get_store_id(conn, store["name"])
    log(f"\n{'='*50}\n{store['name']}\n{'='*50}")

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    start = time.time()

    try:
        # Login
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
        page.fill('input[name="loginUserName"]', store["username"])
        page.fill('input[name="loginPassword"]', store["password"])
        with page.expect_navigation(wait_until="networkidle", timeout=60000):
            page.click("#submitButton")
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
        log(f"{store['name']}: logged in")

        month      = target_date.strftime("%B")
        year       = target_date.strftime("%Y")
        start_date = target_date.strftime("%m/01/%Y")
        end_date   = target_date.strftime("%m/%d/%Y")

        # ── Pull full month via searchDays ──────────────────────────
        html = post_api(page, "/shifts/searchDays", {
            "month": month, "year": year,
            "startDate": start_date, "endDate": end_date
        })

        if not html:
            log(f"{store['name']}: searchDays failed"); return

        days = parse_search_days(html)
        log(f"{store['name']}: found {len(days)} days")

        for day in days:
            shift_id = get_or_create_shift(conn, store_id, day['shift_date'], day['display_date'])
            save_raw(conn, shift_id, "searchDays", html)

            # Save financials from searchDays
            conn.execute("""
                INSERT OR REPLACE INTO financials
                (shift_id, total_sales, fuel_sales, inside_sales, lottery_net, cash_drop, over_short)
                VALUES (?,?,?,?,?,?,?)
            """, (shift_id, day['total'], day['fuel'], day['grocery'],
                  (day['lottery'] or 0) + (day['lottery_payout'] or 0),
                  day['cash_drop'], day['difference']))

        conn.commit()

        # ── Pull detailed data for target date only ─────────────────
        display_date = target_date.strftime("%m/%d/%Y")
        shift_id = get_or_create_shift(conn, store_id,
            target_date.strftime("%Y-%m-%d"), display_date)

        # Fuel detail
        html = post_api(page, "/shifts/getFuelDailySummary", {"shiftDate": display_date})
        if html and '<table' in html:
            save_raw(conn, shift_id, "getFuelDailySummary", html)
            data = parse_fuel_html(html)
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO fuel_summary
                    (shift_id, regular_gal, regular_amt, diesel_gal, diesel_amt, total_gal, total_amt, raw_json)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (shift_id, data.get('regular_gal'), data.get('regular_amt'),
                      data.get('diesel_gal'), data.get('diesel_amt'),
                      data.get('total_gal'), data.get('total_amt'), json.dumps(data)))

        # Card/tender detail
        html = post_api(page, "/shifts/getDailyCardInfo", {"shiftDate": display_date})
        if html and '<table' in html:
            save_raw(conn, shift_id, "getDailyCardInfo", html)
            data = parse_card_html(html)
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO tenders
                    (shift_id, credit, debit, mobile, food_stamp, coupon, manual_card, manual_debit, raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (shift_id, data.get('credit'), data.get('debit'),
                      data.get('mobile'), data.get('food_stamp'), data.get('coupon'),
                      data.get('manual_card'), data.get('manual_debit'), json.dumps(data)))

        conn.commit()
        duration = round(time.time() - start, 1)
        conn.execute("""
            INSERT INTO crawler_runs (run_date, store_id, status, endpoints_hit, duration_secs)
            VALUES (?,?,?,?,?)
        """, (target_date.strftime("%Y-%m-%d"), store_id, "success", len(days)+2, duration))
        conn.commit()
        log(f"{store['name']}: ✅ done — {len(days)} days stored, {duration}s")

    except Exception as e:
        log(f"{store['name']}: ❌ {e}")
        conn.execute("INSERT INTO crawler_runs (run_date, store_id, status, error_message) VALUES (?,?,?,?)",
            (target_date.strftime("%Y-%m-%d"), store_id, "failed", str(e)))
        conn.commit()
    finally:
        context.close()
        browser.close()

def main():
    target_date = datetime.now() - timedelta(days=1)
    log(f"Collecting data for {target_date.strftime('%Y-%m-%d')}")
    init_db()
    conn = get_db()
    with sync_playwright() as pw:
        for store in STORES:
            process_store(pw, store, conn, target_date)
    conn.close()
    log("✅ Done")

if __name__ == "__main__":
    main()
