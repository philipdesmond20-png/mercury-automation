from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

import sync_lottery_shifts
from sync_lottery_shifts import completed_store_day, open_shifts_sync_view, search_shifts_for_date


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 1

    def click(self):
        self.page.clicked.append(self.selector)


class FakePage:
    def __init__(self):
        self.waited_for = []
        self.selected = []
        self.clicked = []

    def wait_for_selector(self, selector, timeout):
        self.waited_for.append((selector, timeout))

    def select_option(self, selector, **kwargs):
        self.selected.append((selector, kwargs))

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _timeout):
        pass


class StopAfterWait(Exception):
    pass


class WaitForFunctionPage:
    def __init__(self):
        self.calls = []

    def wait_for_function(self, expression, **kwargs):
        self.calls.append((expression, kwargs))
        raise StopAfterWait()


class CompletedStoreDayTests(unittest.TestCase):
    def test_uses_the_previous_calendar_day_in_new_york(self):
        now = datetime(2026, 8, 12, 11, 30, tzinfo=timezone.utc)
        self.assertEqual(str(completed_store_day(now)), "2026-08-11")

    def test_handles_the_first_day_of_a_month(self):
        now = datetime(2026, 8, 1, 11, 30, tzinfo=timezone.utc)
        self.assertEqual(str(completed_store_day(now)), "2026-07-31")

    def test_searches_the_completed_day_month_then_requests_results(self):
        page = FakePage()

        search_shifts_for_date(page, "Texaco", date(2026, 7, 31))

        self.assertEqual(
            page.selected,
            [
                ("#searchShiftMonth", {"label": "July"}),
                ("#searchShiftYear", {"value": "2026"}),
            ],
        )
        self.assertEqual(page.clicked, ["button[onclick='doSearchShifts()']"])
        self.assertEqual(
            [selector for selector, _timeout in page.waited_for],
            ["#searchShiftMonth", "#searchShiftYear"],
        )

    def test_passes_target_dates_with_the_supported_wait_for_function_argument(self):
        page = WaitForFunctionPage()

        with (
            patch.object(sync_lottery_shifts, "click_first_available"),
            patch.object(sync_lottery_shifts, "settle_page"),
            patch.object(sync_lottery_shifts, "search_shifts_for_date"),
            self.assertRaises(StopAfterWait),
        ):
            open_shifts_sync_view(page, "Texaco", date(2026, 8, 11))

        self.assertEqual(page.calls[0][1]["arg"], ["08/11/2026", "8/11/2026"])


if __name__ == "__main__":
    unittest.main()
