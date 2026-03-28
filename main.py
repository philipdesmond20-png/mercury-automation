"""
Mercury POS — Fast HTTP Collector (no browser)
================================================
Uses requests + session cookies instead of Playwright.
Falls back gracefully if login fails.
~15-20 seconds total vs ~5 minutes with Playwright.
"""

import os, io, csv, json, re, time, requests, gspread
from pathlib import Path
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

BASE_URL = "https://monecloud.aboveo.com"
SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"
DB_PATH  = "mercury.db"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

STORES = [
    {"name": "Texaco",   "username": os.environ["STORE_TEXACO_USERNAME"],  "password": os.environ["STORE_TEXACO_PASSWORD"]},
    {"name": "Dalton",   "username": os.environ["STORE_DALTON_USERNAME"],  "password": os.environ["STORE_DALTON_PASSWORD"]},
    {"name": "Rome KS3", "username": os.environ["STORE_ROME_USERNAME"],    "password": os.environ["STORE_ROME_PASSWORD"]},
]

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ══════════════════════════════════════════════════════════════
# HTTP LOGIN
# ══════════════════════════════════════════════════════════════

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s

def login(session, username, password):
    """Login via HTTP POST — returns True if successful."""
    try:
        # First hit the login page to get any cookies/tokens
        r = session.get(f"{BASE_URL}/shifts/index", timeout=30, allow_redirects=True)
        
        # Submit login form
        r = session.post(
            f"{BASE_URL}/user/authenticateLogin/loginForm",
            data={"loginUserName": username, "loginPassword": password},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": f"{BASE_URL}/user/homepage"},
            timeout=30,
            allow_redirects=True
        )
        
        # Check if logged in — look for logout link or shift data
        if "logout" in r.text.lower() or "shifts" in r.url.lower() or r.url != f"{BASE_URL}/user/homepage":
            log(f"  Login successful (status={r.status_code} url={r.url})")
            return True
        
        # Try alternative check — hit shifts/index and see if we get data
        r2 = session.get(f"{BASE_URL}/shifts/index", timeout=30)
        if "loginUserName" not in r2.text:  # not redirected back to login
            log(f"  Login successful via redirect check")
            return True
            
        log(f"  Login failed — redirected back to login page")
        return False
        
    except Exception as e:
        log(f"  Login error: {e}")
        return False

def post_api(session, endpoint, params):
    """POST to Mercury API endpoint."""
    try:
        r = session.post(
            f"{BASE_URL}{endpoint}",
            data=params,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/shifts/index",
            },
            timeout=30
        )
        if r.status_code == 200:
            return r.text
        log(f"  {endpoint} returned {r.status_code}")
        return None
    except Exception as e:
        log(f"  {endpoint} error: {e}")
        return None

# ══════════════════════════════════════════════════════════════
# CSV DOWNLOAD
# ══════════════════════════════════════════════════════════════

def get_latest_shift_date(session):
    """Get the most recent shift date from searchDays."""
    now = datetime.now()
    month = now.strftime("%B")
    year  = now.strftime("%Y")
    start = now.strftime("%m/01/%Y")
    end   = now.strftime("%m/%d/%Y")
    
    html = post_api(session, "/shifts/searchDays", {
        "month": month, "year": year,
        "startDate": start, "endDate": end
    })
    if not html:
        return None
    
    # Parse dates from table
    dates = re.findall(r'(\d{2}/\d{2}/\d{4})', html)
    if dates:
        return dates[0]  # Most recent first
    return None

def download_csv(session, shift_date):
    """Download CSV for a specific date."""
    try:
        r = session.post(
            f"{BASE_URL}/shifts/createDailyPdf",
            data={"shiftDate": shift_date, "csv": "true"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/shifts/index",
            },
            timeout=60
        )
        
        if r.status_code != 200:
            log(f"  CSV download failed: {r.status_code}")
            return None
        
        text = r.text
        
        # Check if it's HTML (error) or CSV
        if "<html" in text.lower() or "<!doctype" in text.lower():
            log(f"  Got HTML instead of CSV")
            return None
        
        if not text.strip():
            log(f"  Empty response")
            return None
            
        return text
        
    except Exception as e:
        log(f"  CSV download error: {e}")
        return None

# ══════════════════════════════════════════════════════════════
# GOOGLE SHEETS
# ══════════════════════════════════════════════════════════════

def get_google_client():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)

def parse_csv_text(csv_text):
    attempts = [
        csv.reader(io.StringIO(csv_text)),
        csv.reader(io.StringIO(csv_text), delimiter="\t"),
        csv.reader(io.StringIO(csv_text), delimiter=";"),
    ]
    best_rows, best_width = [], 0
    for reader in attempts:
        rows = list(reader)
        width = max((len(r) for r in rows), default=0)
        if width > best_width:
            best_rows, best_width = rows, width
    if not best_rows:
        return [[""]]
    for r in best_rows:
        if len(r) < best_width:
            r.extend([""] * (best_width - len(r)))
    return best_rows

def build_store_block(store_name, csv_text):
    rows = parse_csv_text(csv_text)
    block = [["STORE", store_name], [""]]
    block.extend(rows)
    block.extend([[""], [""]])
    return block

def upload_to_sheets(all_rows):
    log("Uploading to Google Sheets RAW_CSV...")
    gc = get_google_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("RAW_CSV")
    ws.clear()
    ws.update("A1", all_rows)
    log("RAW_CSV updated")

def trigger_fill(store_name):
    url = os.environ.get("APPS_SCRIPT_URL")
    if not url:
        log(f"  APPS_SCRIPT_URL not set — skipping fill for {store_name}")
        return
    log(f"Triggering fill for {store_name}...")
    try:
        r = requests.get(url, params={"store": store_name}, timeout=60)
        log(f"  Fill response: {r.status_code}")
    except Exception as e:
        log(f"  Fill error: {e}")

# ══════════════════════════════════════════════════════════════
# DATA EXPORT FOR DASHBOARD
# ══════════════════════════════════════════════════════════════

def export_json():
    """Export all collected data to data/latest.json for the dashboard."""
    try:
        import sqlite3
        Path("data").mkdir(exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        output = {"generated": today, "stores": {}}

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        for store_name in ["Texaco", "Dalton", "Rome KS3"]:
            store = conn.execute("SELECT id FROM stores WHERE name=?", (store_name,)).fetchone()
            if not store: continue
            rows = conn.execute("""
                SELECT s.shift_date, s.display_date,
                       f.total_sales, f.fuel_sales, f.inside_sales,
                       f.lottery_net, f.cash_drop, f.over_short
                FROM financials f JOIN shifts s ON f.shift_id=s.id
                WHERE s.store_id=? ORDER BY s.shift_date
            """, (store["id"],)).fetchall()
            output["stores"][store_name] = {"days": [dict(r) for r in rows]}

        conn.close()
        Path("data/latest.json").write_text(json.dumps(output))
        Path(f"data/{today}.json").write_text(json.dumps(output))
        log(f"Exported data/latest.json ({sum(len(v['days']) for v in output['stores'].values())} total days)")
    except Exception as e:
        log(f"Export error: {e}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    log("Starting Mercury POS fast HTTP collection...")
    combined_rows = []
    success_stores = []
    failed_stores  = []

    for store in STORES:
        log(f"\n{'='*40}\n{store['name']}\n{'='*40}")
        start = time.time()
        
        session = make_session()
        
        # Login
        if not login(session, store["username"], store["password"]):
            log(f"{store['name']}: login failed")
            failed_stores.append(store["name"])
            continue
        
        # Get latest shift date
        shift_date = get_latest_shift_date(session)
        if not shift_date:
            log(f"{store['name']}: could not get shift date")
            failed_stores.append(store["name"])
            continue
        log(f"{store['name']}: latest shift = {shift_date}")
        
        # Download CSV
        csv_text = download_csv(session, shift_date)
        if not csv_text:
            log(f"{store['name']}: CSV download failed")
            failed_stores.append(store["name"])
            continue
        
        combined_rows.extend(build_store_block(store["name"], csv_text))
        success_stores.append(store["name"])
        log(f"{store['name']}: ✅ done in {round(time.time()-start, 1)}s")

    if combined_rows:
        upload_to_sheets(combined_rows)
        apps_url = os.environ.get("APPS_SCRIPT_URL")
        if apps_url:
            for name in success_stores:
                trigger_fill(name)

    # Always try to export JSON for dashboard
    export_json()

    log(f"\n{'='*40}")
    log(f"SUCCESS: {success_stores}")
    log(f"FAILED:  {failed_stores}")
    
    if failed_stores and not success_stores:
        raise Exception("All stores failed — check credentials")

if __name__ == "__main__":
    main()
