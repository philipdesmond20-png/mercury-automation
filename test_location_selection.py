import unittest
from unittest.mock import patch

import main


class FakeLocator:
    def count(self):
        return 0


class FakePage:
    url = "https://monecloud.aboveo.com/user/viewLocations"

    def __init__(self):
        self.waits = []

    def locator(self, _selector):
        return FakeLocator()

    def wait_for_selector(self, selector, **kwargs):
        self.waits.append((selector, kwargs))


class NavigationPage:
    def __init__(self):
        self.url = "https://monecloud.aboveo.com/user/viewLocations"
        self.waited_for = None

    def wait_for_url(self, predicate, **kwargs):
        self.url = "https://monecloud.aboveo.com/user/puzzleMainPage"
        self.waited_for = (predicate, kwargs)
        if not predicate(self.url):
            raise AssertionError("location landing predicate rejected the destination")


class LocationSelectionTests(unittest.TestCase):
    def test_waits_for_the_location_dropdown_after_exact_manage_click(self):
        page = FakePage()

        with (
            patch.object(main, "log_page_debug_state"),
            patch.object(main, "save_location_candidates"),
            patch.object(main, "try_click_named_location", return_value=True),
            patch.object(main, "wait_for_location_landing_page"),
            patch.object(main, "apply_location_dropdown", side_effect=[False, True]),
        ):
            main.handle_location_selection(page, "Texaco")

        self.assertEqual(
            page.waits,
            [("#multipleLocations", {"state": "attached", "timeout": 15000})],
        )

    def test_waits_until_manage_leaves_the_location_chooser(self):
        page = NavigationPage()

        with patch.object(main, "settle_page") as settle:
            main.wait_for_location_landing_page(page, "Texaco")

        predicate, kwargs = page.waited_for
        self.assertTrue(predicate("https://monecloud.aboveo.com/user/puzzleMainPage"))
        self.assertFalse(predicate("https://monecloud.aboveo.com/user/viewLocations"))
        self.assertEqual(kwargs["wait_until"], "domcontentloaded")
        self.assertEqual(kwargs["timeout"], 30000)
        settle.assert_called_once_with(page, timeout=30000)


if __name__ == "__main__":
    unittest.main()
