import requests
import os
import datetime
import gspread
from google.oauth2.service_account import Credentials

BASE_URL = "https://monecloud.aboveo.com"

SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_google_client():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds = Credentials.from_service_account_info(eval(creds_json), scopes=SCOPES)
    return gspread.authorize(creds)

def login(session, username, password):
    url = BASE_URL + "/user/authenticateLogin/loginForm"

    payload = {
        "username": username,
        "password": password
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0"
    }

    r = session.post(url, data=payload, headers=headers)

    if r.status_code not in [200, 302]:
        raise Exception("Login failed")

def download_csv(session):
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    url = BASE_URL + "/shifts/createDailyPdf"

    payload = {
        "shiftDate": yesterday,
        "csv": True
    }

    r = session.post(url, data=payload)

    return r.text

def upload_to_sheet(data):
    client = get_google_client()
    sheet = client.open_by_key(SHEET_ID)
    raw = sheet.worksheet("RAW_CSV")

    raw.clear()

    rows = [row.split(",") for row in data.splitlines()]
    raw.update(rows)

def run_store(name, username, password):
    print("Running", name)

    session = requests.Session()

    login(session, username, password)

    csv_data = download_csv(session)

    upload_to_sheet(csv_data)

    requests.get(os.environ["APPS_SCRIPT_URL"] + "?store=" + name)

def main():
    run_store(
        "Texaco",
        os.environ["STORE_TEXACO_USERNAME"],
        os.environ["STORE_TEXACO_PASSWORD"]
    )

    run_store(
        "Dalton",
        os.environ["STORE_DALTON_USERNAME"],
        os.environ["STORE_DALTON_PASSWORD"]
    )

    run_store(
        "Rome KS3",
        os.environ["STORE_ROME_USERNAME"],
        os.environ["STORE_ROME_PASSWORD"]
    )

if __name__ == "__main__":
    main()
