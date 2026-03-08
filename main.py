import os
import csv
import io
import json
import requests
import gspread
from google.oauth2.service_account import Credentials

BASE_URL = "https://monecloud.aboveo.com"
SHEET_ID = "1syVhnG43KjivTIMy7GMfH1YNgbTJhnbw_a3D54GH6kU"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_client():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)

    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=SCOPES
    )

    return gspread.authorize(creds)


def login(session, username, password):

    url = BASE_URL + "/user/authenticateLogin/loginForm"

    payload = {
        "loginUserName": username,
        "loginPassword": password
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE_URL + "/user/homepage"
    }

    r = session.post(url, data=payload, headers=headers)

    if "loginUserName" in r.text:
        raise Exception("Login failed")


def get_latest_shift_date(session):

    url = BASE_URL + "/shifts/searchDays"

    r = session.get(url)

    data = r.json()

    # first row is latest report
    latest = data[0]["shiftDate"]

    return latest


def download_csv(session, shift_date):

    url = BASE_URL + "/shifts/createDailyPdf"

    payload = {
        "shiftDate": shift_date,
        "csv": "true"
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": BASE_URL + "/user/homepage",
        "User-Agent": "Mozilla/5.0"
    }

    r = session.post(url, data=payload, headers=headers)

    if "<html" in r.text.lower():
        raise Exception("CSV export failed")

    return r.text


def upload_to_sheet(csv_text):

    client = get_google_client()

    sheet = client.open_by_key(SHEET_ID)

    ws = sheet.worksheet("RAW_CSV")

    ws.clear()

    reader = csv.reader(io.StringIO(csv_text))

    rows = list(reader)

    ws.update("A1", rows)


def run_store(name, username, password):

    print(f"Running {name}")

    session = requests.Session()

    login(session, username, password)

    shift_date = get_latest_shift_date(session)

    print("Latest shift:", shift_date)

    csv_text = download_csv(session, shift_date)

    upload_to_sheet(csv_text)

    print(f"{name} finished")


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
