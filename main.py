"""
Mercury POS — Fast HTTP Collector (no browser)
================================================
Logs in via HTTP, pulls searchDays data for current month,
writes directly to data/latest.json — no database needed.
~15-20 seconds total.
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
            log(f"  Login OK (url={r.url})")
            return True
        r2 = session.get(f"{BASE_URL}/shifts/index", timeout=30)
        if "loginUserName" not in r2.text:
            log(f"  Login OK via redirect check")
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
        if r.status_code == 200:
            return r.text
        log(f"  searchDays returned {r.status_code}")
        return None
    except Exception as e:
        log(f"  searchDays error: {e}")
        return None

def parse_search_days(html):
    """
    Parse the searchDays HTML response into a list of day dicts.
    Tries to extract date, total_sales, fuel_sales, inside_sales,
    lottery_net, cash_drop, over_short from table rows.
    """
    days = []
    if not html:
        return days

    # Find all table rows
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip().replace(',', '').replace('$', '') for c in cells]
        cells = [c for c in cells if c]  # remove empty
        
        if len(cells) < 3:
            continue
        
        # First cell should be a date MM/DD/YYYY
        date_match = re.match(r'(\d{2}/\d{2}/\d{4})', cells[0])
        if not date_match:
            continue
        
        display_date = date_match.group(1)
        # Convert MM/DD/YYYY to YYYY-MM-DD
        parts = display_date.split('/')
        iso_date = f"{parts[2]}-{parts[0]}-{parts[1]}"
        
        def safe_float(v):
            try:
                v = v.replace('(', '-').replace(')', '').strip()
                return float(v)
            except:
                return 0.0
        
        # Column order varies — try to map by position
        # Typical: Date, TotalSales, FuelSales, InsideSales, LotteryNet, CashDrop, Difference
        day = {
            "date": iso_date,
            "display_date": display_date,
            "total_sales": safe_float(cells[1]) if len(cells) > 1 else 0,
            "fuel_sales":  safe_float(cells[2]) if len(cells) > 2 else 0,
            "inside_sales":safe_float(cells[3]) if len(cells) > 3 else 0,
            "lottery_net": safe_float(cells[4]) if len(cells) > 4 else 0,
            "cash_drop":   safe_float(cells[5]) if len(cells) > 5 else 0,
            "over_short":  safe_float(cells[6]) if len(cells) > 6 else 0,
        }
        days.append(day)
    
    log(f"  Parsed {len(days)} days from HTML")
    return days

def collect_store(store):
    log(f"\n{'='*40}\n{store['name']}\n{'='*40}")
    start_time = time.time()
    
    session = make_session()
    
    if not login(session, store["username"], store["password"]):
        return None
    
    now = datetime.now()
    month     = now.strftime("%B")
    year      = now.strftime("%Y")
    start_dt  = now.strftime("%m/01/%Y")
    end_dt    = now.strftime("%m/%d/%Y")
    
    log(f"  Fetching {month} {year} ({start_dt} - {end_dt})")
    html = search_days(session, month, year, start_dt, end_dt)
    
    if not html:
        log(f"  No data returned")
        return None
    
    days = parse_search_days(html)
    
    # Also try last month if we're early in the month
    if now.day <= 5:
        import calendar
        last = now.replace(day=1) - __import__('datetime').timedelta(days=1)
        lm_html = search_days(session, last.strftime("%B"), last.strftime("%Y"),
                              last.strftime("%m/01/%Y"), last.strftime("%m/%d/%Y"))
        if lm_html:
            last_days = parse_search_days(lm_html)
            days = last_days + days
    
    days.sort(key=lambda d: d["date"])
    log(f"  {store['name']}: {len(days)} days collected in {round(time.time()-start_time,1)}s")
    return days

def main():
    log("Mercury POS Fast HTTP Collector starting...")
    today = datetime.now().strftime("%Y-%m-%d")
    output = {"generated": today, "stores": {}}
    
    success, failed = [], []
    
    for store in STORES:
        days = collect_store(store)
        if days is not None:
            output["stores"][store["name"]] = {"days": days}
            success.append(store["name"])
        else:
            output["stores"][store["name"]] = {"days": []}
            failed.append(store["name"])
    
    # Save to data/
    Path("data").mkdir(exist_ok=True)
    Path("data/latest.json").write_text(json.dumps(output))
    Path(f"data/{today}.json").write_text(json.dumps(output))
    
    total_days = sum(len(v["days"]) for v in output["stores"].values())
    log(f"\nExported data/latest.json — {total_days} total days")
    log(f"SUCCESS: {success}")
    log(f"FAILED:  {failed}")
    
    if failed and not success:
        raise Exception("All stores failed")

if __name__ == "__main__":
    main()
