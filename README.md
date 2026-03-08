# Mercury POS Daily Auto-Fill — Setup Guide

## What this does
Every day at 8am (UTC), this script:
1. Logs into each Mercury POS store account
2. Downloads yesterday's CSV report
3. Pastes it into the RAW_CSV tab in your Google Sheet
4. Triggers the Fill Store script automatically

---

## Step 1 — Google Service Account (to write to your sheet)

1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable **Google Sheets API** and **Google Drive API**
4. Go to **IAM & Admin → Service Accounts → Create**
5. Download the JSON key file
6. Open your Google Sheet → Share → paste the service account email → Editor access

---

## Step 2 — Deploy Apps Script as Web App (to auto-trigger fill)

In your Google Sheet Apps Script:
1. Add this function at the bottom of FillFromCSV_Final.gs:

```javascript
function doGet(e) {
  var store = e.parameter.store;
  if (store === 'Texaco')   fillTexaco();
  else if (store === 'Dalton')   fillDalton();
  else if (store === 'Rome KS3') fillRome();
  return ContentService.createTextOutput('done');
}
```

2. Click **Deploy → New deployment → Web App**
3. Execute as: **Me**
4. Who has access: **Anyone**
5. Copy the deployment URL — this is your APPS_SCRIPT_URL

---

## Step 3 — Deploy to Render (free cloud server)

1. Go to https://render.com and sign up free
2. Connect your GitHub account
3. Push this folder to a GitHub repo
4. In Render → New → Cron Job → connect your repo
5. Add all environment variables (see below)
6. Set schedule: `0 8 * * *` (8am UTC daily) — adjust for your timezone

---

## Environment Variables to set in Render

| Variable | Value |
|----------|-------|
| STORE_TEXACO_USERNAME | your texaco username |
| STORE_TEXACO_EMAIL | your texaco email |
| STORE_TEXACO_PASSWORD | your texaco password |
| STORE_DALTON_USERNAME | your dalton username |
| STORE_DALTON_EMAIL | your dalton email |
| STORE_DALTON_PASSWORD | your dalton password |
| STORE_ROME_USERNAME | your rome username |
| STORE_ROME_EMAIL | your rome email |
| STORE_ROME_PASSWORD | your rome password |
| GOOGLE_SHEET_ID | from your sheet URL: docs.google.com/spreadsheets/d/**THIS_PART**/edit |
| GOOGLE_SERVICE_ACCOUNT_JSON | paste the entire contents of the JSON key file |
| APPS_SCRIPT_URL | your Apps Script web app deployment URL |

---

## Timezone note
The schedule `0 8 * * *` runs at 8am UTC.
- UTC+5:30 (India): that's 1:30pm IST
- Adjust the hour to whatever time you want the sheet filled each day

---

## Testing manually
To test without waiting for the schedule:
```bash
pip install -r requirements.txt
# Set all environment variables first, then:
python main.py
```
