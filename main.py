def login_and_download_first_report(playwright, store_name: str, username: str, password: str) -> str:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        print(f"Running {store_name}")

        page.goto(f"{BASE_URL}/user/homepage", wait_until="networkidle", timeout=120000)

        # Login
        page.locator('input[name="loginUserName"]').fill(username)
        page.locator('input[name="loginPassword"]').fill(password)

        with page.expect_navigation(wait_until="networkidle", timeout=120000):
            page.locator("#submitButton").click()

        # Open Sales - Day
        page.goto(f"{BASE_URL}/shifts/index", wait_until="networkidle", timeout=120000)

        # Wait for rows to appear
        page.wait_for_selector("table tr", timeout=120000)

        rows = page.locator("table tr")
        row_count = rows.count()

        target_row = None

        # Find first actual data row whose first cell looks like a date
        import re
        for i in range(row_count):
            row = rows.nth(i)
            cells = row.locator("td")
            if cells.count() < 2:
                continue

            first_text = cells.nth(0).inner_text().strip()
            if re.match(r"\d{2}/\d{2}/\d{4}", first_text):
                target_row = row
                print(f"{store_name} first report row date: {first_text}")
                break

        if target_row is None:
            page.screenshot(path=f"{store_name}_no_row_found.png", full_page=True)
            raise Exception(f"Could not find first report row for {store_name}")

        report_cell = target_row.locator("td").last
        links = report_cell.locator("a")
        link_count = links.count()

        if link_count < 3:
            page.screenshot(path=f"{store_name}_report_cell_issue.png", full_page=True)
            raise Exception(f"Expected 3 report links in first row for {store_name}, found {link_count}")

        # 3rd icon = yellow CSV
        csv_link = links.nth(2)

        with page.expect_download(timeout=120000) as download_info:
            csv_link.click()

        download = download_info.value
        path = download.path()

        if not path:
            raise Exception(f"Download path not available for {store_name}")

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            csv_text = f.read()

        if "<html" in csv_text.lower() or "<!doctype html" in csv_text.lower():
            raise Exception(f"Downloaded HTML instead of CSV for {store_name}")

        print(f"Completed download for {store_name}")
        return csv_text

    finally:
        context.close()
        browser.close()
