"""
Mercury POS Daily Auto-Fill
============================
Runs daily on a schedule. For each store:
  1. Logs into Mercury POS (monecloud.aboveo.com)
  2. Downloads yesterday's CSV report
  3. Pastes it into the RAW_CSV tab of Google Sheet
  4. Triggers the Apps Script fill function via Google Apps Script API

SETUP:
  See README.md for full instructions.
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── Store config — set these in environment variables ─────────────────────────
# Each store needs: USERNAME, EMAIL, PASSWORD
# Format: STORE_TEXACO_USERNAME, STORE_TEXACO_EMAIL, STORE_TEXACO_PASSWORD etc.

STORES = [
    {
        'name':     'Texaco',
        'tab':      'Daily Account Sheet - Texaco',
        'username': os.environ.get('STORE_TEXACO_USERNAME'),
        'email':    os.environ.get('STORE_TEXACO_EMAIL'),
        'password': os.environ.get('STORE_TEXACO_PASSWORD'),
    },
    {
        'name':     'Dalton',
        'tab':      'Daily Account Sheet - Dalton',
        'username': os.environ.get('STORE_DALTON_USERNAME'),
        'email':    os.environ.get('STORE_DALTON_EMAIL'),
        'password': os.environ.get('STORE_DALTON_PASSWORD'),
    },
    {
        'name':     'Rome KS3',
        'tab':      'Daily Account Sheet - Rome KS3',
        'username': os.environ.get('STORE_ROME_USERNAME'),
        'email':    os.environ.get('STORE_ROME_EMAIL'),
        'password': os.environ.get('STORE_ROME_PASSWORD'),
    },
]

# ── Google Sheet config ───────────────────────────────────────────────────────
SHEET_ID        = os.environ.get('GOOGLE_SHEET_ID')        # from sheet URL
APPS_SCRIPT_ID  = os.environ.get('APPS_SCRIPT_ID')         # from Apps Script deployment
APPS_SCRIPT_KEY = os.environ.get('APPS_SCRIPT_DEPLOY_KEY') # deployment URL key

# Mercury POS base URL
BASE_URL = 'https://monecloud.aboveo.com'


# ══════════════════════════════════════════════════════════════════════════════
# MERCURY POS — LOGIN + DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

def mercury_login(session, username, email, password):
    """Log into Mercury POS and return True if successful."""
    log.info(f'Logging in as {email}...')

    # Get login page first (for any CSRF tokens/cookies)
    r = session.get(f'{BASE_URL}/user/homepage')
    r.raise_for_status()

    # Submit login form
    payload = {
        'username': username,
        'email':    email,
        'password': password,
    }
    r = session.post(f'{BASE_URL}/user/login', data=payload, allow_redirects=True)
    r.raise_for_status()

    # Check if login succeeded by looking for logout link
    if 'LOGOUT' in r.text.upper() or 'logout' in r.url.lower() or 'homepage' not in r.url.lower():
        log.info('Login successful')
        return True

    log.error('Login failed — check credentials')
    return False


def download_csv(session, date_str):
    """
    Download CSV for a given date.
    date_str format: YYYY-MM-DD (e.g. 2026-03-06)
    Returns CSV text or None.
    """
    log.info(f'Downloading CSV for {date_str}...')

    # This is the exact POST request the yellow CSV button makes
    payload = {
        'shiftDate': date_str,
        'csv':       'true',
    }
    headers = {
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
        'Referer':          f'{BASE_URL}/shifts/index',
    }

    r = session.post(
        f'{BASE_URL}/shifts/createDailyPdf',
        data=payload,
        headers=headers
    )
    r.raise_for_status()

    # Response is JSON containing the CSV data
    try:
        data = r.json()
        # Try common keys the API might use
        for key in ['csv', 'data', 'content', 'file', 'report']:
            if key in data:
                log.info(f'Got CSV data (key: {key}), length: {len(str(data[key]))}')
                return data[key]
        # If it's just a string response
        if isinstance(data, str):
            return data
        # Log what we got so we can debug
        log.warning(f'Unexpected response keys: {list(data.keys())}')
        log.debug(f'Response: {str(data)[:500]}')
        return None
    except Exception:
        # Maybe it returned raw CSV text
        if r.text and ('mercury' in r.text.lower() or ',' in r.text):
            return r.text
        log.error(f'Could not parse response: {r.text[:200]}')
        return None


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS — WRITE CSV TO RAW_CSV TAB
# ══════════════════════════════════════════════════════════════════════════════

def get_sheets_client():
    """Get authenticated gspread client using service account."""
    # Service account JSON stored as environment variable
    sa_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not sa_json:
        raise ValueError('GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set')

    sa_info = json.loads(sa_json)
    scopes  = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]
    creds  = Credentials.from_service_account_info(sa_info, scopes=scopes)
    client = gspread.authorize(creds)
    return client


def write_csv_to_sheet(client, sheet_id, csv_text):
    """Clear RAW_CSV tab and paste the CSV data into it."""
    log.info('Writing CSV to Google Sheet RAW_CSV tab...')

    sh  = client.open_by_key(sheet_id)
    ws  = sh.worksheet('RAW_CSV')

    # Parse CSV into rows
    rows = []
    for line in csv_text.strip().split('\n'):
        # Split by pipe (Mercury POS uses | as delimiter) or comma
        if '|' in line:
            cols = line.split('|')
        else:
            cols = line.split(',')
        rows.append(cols)

    if not rows:
        log.error('No rows parsed from CSV')
        return False

    # Clear existing content
    ws.clear()

    # Write all rows at once
    ws.update('A1', rows)
    log.info(f'Written {len(rows)} rows to RAW_CSV')
    return True


# ══════════════════════════════════════════════════════════════════════════════
# TRIGGER APPS SCRIPT FILL FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def trigger_fill(store_name, apps_script_url):
    """
    Trigger the Apps Script fill function via a Web App deployment.
    The Apps Script needs to be deployed as a Web App (see README).
    """
    if not apps_script_url:
        log.warning('APPS_SCRIPT_URL not set — skipping auto-trigger. Run Fill Store manually.')
        return False

    log.info(f'Triggering fill for {store_name}...')
    try:
        r = requests.get(
            apps_script_url,
            params={'store': store_name},
            timeout=60
        )
        if r.status_code == 200:
            log.info(f'Fill triggered successfully for {store_name}')
            return True
        else:
            log.error(f'Fill trigger failed: {r.status_code} {r.text[:200]}')
            return False
    except Exception as e:
        log.error(f'Fill trigger error: {e}')
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run():
    # Yesterday's date (the report we want)
    yesterday   = datetime.now() - timedelta(days=1)
    date_str    = yesterday.strftime('%Y-%m-%d')  # for API: 2026-03-06
    log.info(f'Running daily automation for date: {date_str}')

    # Google Sheets client (shared across all stores)
    try:
        sheets_client = get_sheets_client()
    except Exception as e:
        log.error(f'Could not connect to Google Sheets: {e}')
        sys.exit(1)

    apps_script_url = os.environ.get('APPS_SCRIPT_URL')

    results = []

    for store in STORES:
        log.info(f'\n{"="*50}')
        log.info(f'Processing store: {store["name"]}')
        log.info(f'{"="*50}')

        # Validate credentials are set
        if not all([store['username'], store['email'], store['password']]):
            log.error(f'Missing credentials for {store["name"]} — skipping')
            results.append({'store': store['name'], 'status': 'SKIPPED — missing credentials'})
            continue

        # Create a fresh session per store (separate login)
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

        try:
            # 1. Login
            if not mercury_login(session, store['username'], store['email'], store['password']):
                results.append({'store': store['name'], 'status': 'FAILED — login error'})
                continue

            # 2. Download CSV
            csv_text = download_csv(session, date_str)
            if not csv_text:
                results.append({'store': store['name'], 'status': 'FAILED — could not download CSV'})
                continue

            # 3. Write to Google Sheet RAW_CSV
            if not write_csv_to_sheet(sheets_client, SHEET_ID, csv_text):
                results.append({'store': store['name'], 'status': 'FAILED — could not write to sheet'})
                continue

            # 4. Trigger Apps Script fill
            trigger_fill(store['name'], apps_script_url)

            results.append({'store': store['name'], 'status': 'SUCCESS'})
            log.info(f'✅ {store["name"]} completed successfully')

            # Small delay between stores
            time.sleep(2)

        except Exception as e:
            log.error(f'Unexpected error for {store["name"]}: {e}')
            results.append({'store': store['name'], 'status': f'ERROR — {str(e)}'})

    # Summary
    log.info(f'\n{"="*50}')
    log.info('DAILY RUN SUMMARY')
    log.info(f'{"="*50}')
    for r in results:
        log.info(f'{r["store"]}: {r["status"]}')


if __name__ == '__main__':
    run()
