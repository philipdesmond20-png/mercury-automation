from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

import sync_lottery_shifts
from sync_lottery_shifts import (
    ShiftNotAvailable,
    completed_store_day,
    open_shifts_sync_view,
    save_shift_changes,
    search_shifts_for_date,
)


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


class MissingShiftPage:
    def wait_for_function(self, _expression, **_kwargs):
        raise MissingShiftTimeout()


class MissingShiftTimeout(Exception):
    pass


class FakePlaywrightContext:
    def __enter__(self):
        return object()

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


class SaveLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 1 if self.selector == "button[onclick='doSubmitShiftInfo()']" else 0

    def click(self):
        self.page.clicked.append(self.selector)


class SavePage:
    def __init__(self):
        self.clicked = []

    def locator(self, selector):
        return SaveLocator(self, selector)

    def wait_for_timeout(self, _timeout):
        pass


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

    def test_submits_the_visible_shift_save_button(self):
        page = SavePage()

        with patch.object(sync_lottery_shifts, "settle_page"):
            save_shift_changes(page, "Texaco")

        self.assertEqual(page.clicked, ["button[onclick='doSubmitShiftInfo()']"])

    def test_missing_shift_is_deferred_instead_of_raising_a_generic_failure(self):
        page = MissingShiftPage()

        with (
            patch.object(sync_lottery_shifts, "click_first_available"),
            patch.object(sync_lottery_shifts, "settle_page"),
            patch.object(sync_lottery_shifts, "search_shifts_for_date"),
            patch.object(sync_lottery_shifts, "PlaywrightTimeoutError", MissingShiftTimeout),
            self.assertRaises(ShiftNotAvailable),
        ):
            open_shifts_sync_view(page, "Carnesville", date(2026, 8, 13))

    def test_main_completes_when_a_store_is_deferred(self):
        results = [
            {"store": "Texaco", "status": "updated"},
            {"store": "Dalton", "status": "updated"},
            {"store": "Rome KS3", "status": "updated"},
            {"store": "Carnesville", "status": "deferred", "reason": "shift not available"},
        ]
        env = {
            "STORE_TEXACO_USERNAME": "x",
            "STORE_TEXACO_PASSWORD": "x",
            "STORE_DALTON_USERNAME": "x",
            "STORE_DALTON_PASSWORD": "x",
            "STORE_ROME_USERNAME": "x",
            "STORE_ROME_PASSWORD": "x",
            "STORE_CARNESVILLE_USERNAME": "x",
            "STORE_CARNESVILLE_PASSWORD": "x",
        }

        with (
            patch.dict(sync_lottery_shifts.os.environ, env, clear=True),
            patch.object(sync_lottery_shifts, "sync_playwright", return_value=FakePlaywrightContext()),
            patch.object(sync_lottery_shifts, "run_store", side_effect=results),
            patch.object(sync_lottery_shifts, "log") as log,
        ):
            sync_lottery_shifts.main()

        log.assert_any_call("Lottery shift sync completed with deferred stores:")
        log.assert_any_call("- Carnesville: shift not available")


if __name__ == "__main__":
    unittest.main()
