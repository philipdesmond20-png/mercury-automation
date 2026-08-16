import unittest
from unittest.mock import patch

import main


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeAPIError(Exception):
    def __init__(self, status_code):
        self.response = FakeResponse(status_code)


class GoogleSheetsRetryTests(unittest.TestCase):
    def test_retries_transient_google_sheets_errors(self):
        attempts = []

        def upload():
            attempts.append(True)
            if len(attempts) < 3:
                raise FakeAPIError(503)
            return "updated"

        with (
            patch.object(main, "APIError", FakeAPIError),
            patch.object(main.time, "sleep") as sleep,
        ):
            result = main.retry_google_sheets("RAW_CSV upload", upload)

        self.assertEqual(result, "updated")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_does_not_retry_non_transient_google_sheets_errors(self):
        attempts = []

        def upload():
            attempts.append(True)
            raise FakeAPIError(403)

        with (
            patch.object(main, "APIError", FakeAPIError),
            patch.object(main.time, "sleep") as sleep,
            self.assertRaises(FakeAPIError),
        ):
            main.retry_google_sheets("RAW_CSV upload", upload)

        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
