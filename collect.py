"""
Mercury POS — Fast Dashboard Data Collector
=============================================
Separate from main.py (Google Sheets fill).
This script ONLY updates data/latest.json for the dashboard.
Uses HTTP requests — no browser, ~15 seconds.
"""

import os, json, re, time, requests
from pathlib import Path
from datetime import datetime

BASE_URL = "https://monecloud.aboveo.com"

STORES = [
    {"name": "Texaco",   "username": os.environ["STORE_TEXACO_USERNAME"],  "password": os.environ["STORE_TEXACO_PASSWORD"]},
    {"name": "Dalton",   "username": os.environ["STORE_DALTON_USERNAME"],  "password": os.environ["STORE_DALTON_PASSWORD"]},
    {"name": "Rome KS3", "username": os.environ["STORE_ROME_USERNAME"],    "password": os.environ["STORE_ROME_PASSWORD"]},
]

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s

def login(session, username, password):
    try:
        session.get(f"{BASE_URL}/shifts/index", timeout=30)
        r = session.post(
            f"{BASE_URL}/user/authenticateLogin/loginForm",
            data={"loginUserName": username, "loginPassword": password},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": f"{BASE_URL}/user/homepage"},
            timeout=30, allow_redirects=True
        )
        if "logout" in r.text.lower() or "loginUserName" not in r.text:
            log(f"  Login OK")
            return True
        r2 = session.get(f"{BASE_URL}/shifts/index", timeout=30)
        if "loginUserName" not in r2.text:
            return True
        log(f"  Login FAILED")
        return False
    except Exception as e:
        log(f"  Login error: {e}")
        return False

def search_days(session, month, year, start_date, end_date):
    try:
        r = session.post(
            f"{BASE_URL}/shifts/searchDays",
            data={"month": month, "year": year, "startDate": start_date, "endDate": end_date},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/shifts/index",
            },
            timeout=30
        )
        return r.text if r.status_code == 200 else None
    except Exception as e:
        log(f"  searchDays error: {e}")
        return None

def parse_search_days(html):
    days = []
    if not html:
        return days

    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        cells = [c for c in cells if c]

        if len(cells) < 3:
            continue

        date_match = re.match(r'(\d{2}/\d{2}/\d{4})', cells[0])
        if not date_match:
            continue

        display_date = date_match.group(1)
        parts = display_date.split('/')
        iso_date = f"{parts[2]}-{parts[0]}-{parts[1]}"

        def safe_float(v):
            try:
                v = v.replace(',', '').replace('$', '').replace('(', '-').replace(')', '').strip()
                return float(v)
            except:
                return 0.0

        # Log raw cells for first row to verify column order
        if len(days) == 0:
            log(f"  Sample row cells: {cells}")

        day = {
            "date":         iso_date,
            "display_date": display_date,
            "total_sales":  safe_float(cells[1]) if len(cells) > 1 else 0,
            "fuel_sales":   safe_float(cells[2]) if len(cells) > 2 else 0,
            "inside_sales": safe_float(cells[3]) if len(cells) > 3 else 0,
            "lottery_net":  safe_float(cells[4]) if len(cells) > 4 else 0,
            "cash_drop":    safe_float(cells[5]) if len(cells) > 5 else 0,
            "over_short":   safe_float(cells[6]) if len(cells) > 6 else 0,
        }
        days.append(day)

    log(f"  Parsed {len(days)} days")
    return days

def collect_store(store):
    log(f"\n{'='*40}\n{store['name']}\n{'='*40}")
    session = make_session()
    if not login(session, store["username"], store["password"]):
        return None

    now = datetime.now()
    html = search_days(session, now.strftime("%B"), now.strftime("%Y"),
                       now.strftime("%m/01/%Y"), now.strftime("%m/%d/%Y"))
    if not html:
        return None

    days = parse_search_days(html)
    days.sort(key=lambda d: d["date"])
    log(f"  {store['name']}: {len(days)} days")
    return days

def main():
    log("Dashboard data collector starting...")
    today = datetime.now().strftime("%Y-%m-%d")
    output = {"generated": today, "stores": {}}

    for store in STORES:
        days = collect_store(store)
        output["stores"][store["name"]] = {"days": days or []}

    Path("data").mkdir(exist_ok=True)
    Path("data/latest.json").write_text(json.dumps(output))
    Path(f"data/{today}.json").write_text(json.dumps(output))

    total = sum(len(v["days"]) for v in output["stores"].values())
    log(f"\nSaved data/latest.json — {total} total days across all stores")

if __name__ == "__main__":
    main()
