"""
Mercury POS — API Discovery Crawler
====================================
Logs into each store, visits every page, records all network requests.
Outputs:
  artifacts/screenshots/     — one screenshot per page per store
  artifacts/network/         — all requests captured as JSON
  artifacts/site_map.json    — full structured sitemap
  artifacts/endpoints.csv    — flat list of unique endpoints
"""

import os
import json
import csv
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://monecloud.aboveo.com"

STORES = [
    ("Texaco",   os.environ["STORE_TEXACO_USERNAME"],  os.environ["STORE_TEXACO_PASSWORD"]),
    ("Dalton",   os.environ["STORE_DALTON_USERNAME"],  os.environ["STORE_DALTON_PASSWORD"]),
    ("Rome KS3", os.environ["STORE_ROME_USERNAME"],    os.environ["STORE_ROME_PASSWORD"]),
]

# Pages to visit after login — add more as discovered
NAV_PAGES = [
    ("homepage",      "/user/homepage"),
    ("shifts_index",  "/shifts/index"),
    ("shifts_day",    "/shifts/index"),       # click Sales - Day
    ("shifts_week",   "/shifts/index"),       # click Sales - Week
    ("shifts_month",  "/shifts/index"),       # click Sales - Month
    ("lottery",       "/shifts/index"),       # click Lottery
    ("tenders",       "/shifts/index"),       # click Tenders
    ("exceptions",    "/shifts/index"),       # click Exceptions
    ("transactions",  "/shifts/index"),       # click Transactions
]

# Navigation menu items to click
MENU_CLICKS = [
    "Sales - Day",
    "Sales - Week",
    "Sales - Month",
    "Lottery",
    "Tenders",
    "Exceptions",
    "Transactions",
    "Fuel",
    "Inside",
    "Summary",
]

def setup_dirs():
    for d in ["artifacts/screenshots", "artifacts/network"]:
        Path(d).mkdir(parents=True, exist_ok=True)

def log(msg):
    print(msg, flush=True)

def sanitize(s):
    return s.replace(" ", "_").replace("/", "_").replace(":", "")

def crawl_store(playwright, store_name, username, password, all_requests):
    log(f"\n{'='*50}\nCrawling: {store_name}\n{'='*50}")

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    store_requests = []

    def on_request(request):
        if BASE_URL in request.url:
            entry = {
                "store":    store_name,
                "url":      request.url,
                "method":   request.method,
                "headers":  dict(request.headers),
                "post_data": request.post_data,
            }
            store_requests.append(entry)
            all_requests.append(entry)

    def on_response(response):
        if BASE_URL in response.url:
            # find matching request and attach response info
            for r in reversed(store_requests):
                if r["url"] == response.url and "status" not in r:
                    r["status"] = response.status
                    r["content_type"] = response.headers.get("content-type", "")
                    # capture small responses only
                    try:
                        if response.status == 200 and "json" in r.get("content_type",""):
                            r["response_preview"] = response.text()[:2000]
                    except Exception:
                        pass
                    break

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        # ── Login ────────────────────────────────────────────────────────────
        log(f"{store_name}: navigating to login page...")
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
        page.screenshot(path=f"artifacts/screenshots/{sanitize(store_name)}_00_login.png")

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation(wait_until="networkidle", timeout=60000):
            page.click("#submitButton")

        page.screenshot(path=f"artifacts/screenshots/{sanitize(store_name)}_01_after_login.png")
        log(f"{store_name}: logged in, URL = {page.url}")

        # ── Visit shifts index ────────────────────────────────────────────────
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
        page.screenshot(path=f"artifacts/screenshots/{sanitize(store_name)}_02_shifts_index.png")

        # Save page HTML for analysis
        html = page.content()
        Path(f"artifacts/network/{sanitize(store_name)}_shifts_index.html").write_text(html, encoding="utf-8")

        # ── Click each menu item ──────────────────────────────────────────────
        for i, menu_item in enumerate(MENU_CLICKS):
            try:
                log(f"{store_name}: clicking '{menu_item}'...")
                # Try different selector strategies
                clicked = False
                for selector in [
                    f"text={menu_item}",
                    f"a:has-text('{menu_item}')",
                    f"li:has-text('{menu_item}')",
                    f"[href*='{menu_item.lower().replace(' ','')}']",
                ]:
                    try:
                        if page.locator(selector).count() > 0:
                            page.locator(selector).first.click()
                            page.wait_for_timeout(3000)
                            clicked = True
                            break
                    except Exception:
                        continue

                if clicked:
                    page.screenshot(
                        path=f"artifacts/screenshots/{sanitize(store_name)}_{i+3:02d}_{sanitize(menu_item)}.png"
                    )
                    log(f"{store_name}: '{menu_item}' — captured, URL = {page.url}")
                else:
                    log(f"{store_name}: '{menu_item}' — not found on page")

            except Exception as e:
                log(f"{store_name}: '{menu_item}' — error: {e}")

        # ── Try to trigger a CSV download ─────────────────────────────────────
        try:
            log(f"{store_name}: attempting CSV download trigger...")
            page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            # Check if downloadCSV function exists
            has_dl = page.evaluate("() => typeof downloadCSV === 'function'")
            log(f"{store_name}: downloadCSV available = {has_dl}")

            # Also check for other download functions
            funcs = page.evaluate("""
                () => Object.keys(window).filter(k =>
                    typeof window[k] === 'function' &&
                    (k.toLowerCase().includes('download') ||
                     k.toLowerCase().includes('export') ||
                     k.toLowerCase().includes('csv') ||
                     k.toLowerCase().includes('pdf'))
                )
            """)
            log(f"{store_name}: download-related JS functions = {funcs}")

        except Exception as e:
            log(f"{store_name}: CSV probe error: {e}")

        # Save all requests for this store
        out_path = f"artifacts/network/{sanitize(store_name)}_requests.json"
        Path(out_path).write_text(json.dumps(store_requests, indent=2), encoding="utf-8")
        log(f"{store_name}: saved {len(store_requests)} requests to {out_path}")

    except Exception as e:
        log(f"{store_name}: fatal error — {e}")
        page.screenshot(path=f"artifacts/screenshots/{sanitize(store_name)}_ERROR.png")

    finally:
        context.close()
        browser.close()

    return store_requests


def build_endpoint_map(all_requests):
    """Deduplicate and categorize all captured endpoints."""
    seen = {}
    for r in all_requests:
        key = f"{r['method']}:{r['url'].split('?')[0]}"
        if key not in seen:
            seen[key] = {
                "method":       r["method"],
                "url":          r["url"].split("?")[0],
                "full_url":     r["url"],
                "stores_seen":  [r["store"]],
                "post_data":    r.get("post_data"),
                "status":       r.get("status"),
                "content_type": r.get("content_type", ""),
                "category":     categorize(r["url"]),
            }
        else:
            if r["store"] not in seen[key]["stores_seen"]:
                seen[key]["stores_seen"].append(r["store"])

    return list(seen.values())


def categorize(url):
    url_lower = url.lower()
    if "login" in url_lower or "logout" in url_lower or "user" in url_lower:
        return "auth"
    if "shift" in url_lower:
        return "shifts"
    if "lottery" in url_lower:
        return "lottery"
    if "fuel" in url_lower:
        return "fuel"
    if "tender" in url_lower:
        return "tenders"
    if "exception" in url_lower:
        return "exceptions"
    if "transaction" in url_lower:
        return "transactions"
    if "pdf" in url_lower or "csv" in url_lower or "export" in url_lower or "download" in url_lower:
        return "export"
    if "inside" in url_lower or "inventory" in url_lower:
        return "inventory"
    if any(x in url_lower for x in ["css","js","png","jpg","gif","woff","ico"]):
        return "static"
    return "other"


def save_outputs(endpoint_map):
    # site_map.json
    Path("artifacts/site_map.json").write_text(
        json.dumps(endpoint_map, indent=2), encoding="utf-8"
    )
    log(f"Saved artifacts/site_map.json ({len(endpoint_map)} unique endpoints)")

    # endpoints.csv — exclude static assets
    api_endpoints = [e for e in endpoint_map if e["category"] != "static"]
    with open("artifacts/endpoints.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "method","url","category","status","content_type","stores_seen","post_data"
        ])
        writer.writeheader()
        for e in api_endpoints:
            writer.writerow({
                "method":       e["method"],
                "url":          e["url"],
                "category":     e["category"],
                "status":       e.get("status",""),
                "content_type": e.get("content_type",""),
                "stores_seen":  ",".join(e.get("stores_seen",[])),
                "post_data":    e.get("post_data",""),
            })
    log(f"Saved artifacts/endpoints.csv ({len(api_endpoints)} API endpoints)")

    # Print summary
    from collections import Counter
    cats = Counter(e["category"] for e in api_endpoints)
    log("\n=== ENDPOINT SUMMARY ===")
    for cat, count in sorted(cats.items()):
        log(f"  {cat:20s} {count}")


def main():
    setup_dirs()
    all_requests = []

    with sync_playwright() as playwright:
        for store_name, username, password in STORES:
            crawl_store(playwright, store_name, username, password, all_requests)

    endpoint_map = build_endpoint_map(all_requests)
    save_outputs(endpoint_map)
    log("\nDiscovery complete!")


if __name__ == "__main__":
    main()
