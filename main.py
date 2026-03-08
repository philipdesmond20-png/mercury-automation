import os
from playwright.sync_api import sync_playwright

BASE_URL = "https://monecloud.aboveo.com"


def log(msg):
    print(msg, flush=True)


def debug_store(playwright, store_name, username, password):

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    try:
        log(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle")

        page.fill('input[name="loginUserName"]', username)
        page.fill('input[name="loginPassword"]', password)

        with page.expect_navigation():
            page.click("#submitButton")

        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle")

        page.wait_for_selector("table", timeout=60000)

        log("Page loaded successfully")

        # Save screenshot
        screenshot_path = f"{store_name}_shifts_page.png"
        page.screenshot(path=screenshot_path, full_page=True)
        log(f"Saved screenshot: {screenshot_path}")

        # Save full HTML
        html_path = f"{store_name}_page.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        log(f"Saved HTML: {html_path}")

        # Save table text snippet
        table_text = page.locator("table").inner_text()

        snippet_path = f"{store_name}_table.txt"
        with open(snippet_path, "w", encoding="utf-8") as f:
            f.write(table_text[:2000])

        log(f"Saved table snippet: {snippet_path}")

    finally:
        context.close()
        browser.close()


def main():

    stores = [
        ("Texaco", os.environ["STORE_TEXACO_USERNAME"], os.environ["STORE_TEXACO_PASSWORD"]),
        ("Dalton", os.environ["STORE_DALTON_USERNAME"], os.environ["STORE_DALTON_PASSWORD"]),
        ("Rome", os.environ["STORE_ROME_USERNAME"], os.environ["STORE_ROME_PASSWORD"]),
    ]

    with sync_playwright() as playwright:
        for store_name, username, password in stores:
            debug_store(playwright, store_name, username, password)


if __name__ == "__main__":
    main()
