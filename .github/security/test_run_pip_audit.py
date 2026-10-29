import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import run_pip_audit as audit


def write(entries: object) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(entries, handle)
    return Path(handle.name)


def entry(expires: date, **overrides: str) -> dict[str, str]:
    base = {
        "id": "PYSEC-0000-0000",
        "reason": "synthetic example",
        "owner": "maintainer",
        "expires": expires.isoformat(),
    }
    return {**base, **overrides}


def load(entries: list[dict[str, str]], today: date) -> list[dict[str, str]]:
    return audit.load_exceptions(write({"exceptions": entries}), today)


class LoadExceptionsTest(unittest.TestCase):
    """Every test reads the current date once, so results do not depend on when they run."""

    def test_empty_list_is_valid(self) -> None:
        self.assertEqual(load([], date.today()), [])

    def test_the_committed_file_is_valid_today(self) -> None:
        audit.load_exceptions(audit.DEFAULT_EXCEPTIONS, date.today())

    def test_valid_entry_is_returned(self) -> None:
        today = date.today()
        entries = load([entry(today + timedelta(days=30))], today)
        self.assertEqual(entries[0]["id"], "PYSEC-0000-0000")

    def test_each_missing_or_blank_field_is_rejected(self) -> None:
        today = date.today()
        future = today + timedelta(days=30)
        for field in audit.REQUIRED_FIELDS:
            for broken in ({field: ""}, {field: "  "}):
                with self.subTest(field=field, broken=broken):
                    with self.assertRaises(audit.ExceptionsError):
                        load([{**entry(future), **broken}], today)
            missing = entry(future)
            del missing[field]
            with self.subTest(field=field, missing=True), self.assertRaises(audit.ExceptionsError):
                load([missing], today)

    def test_invalid_date_is_rejected(self) -> None:
        today = date.today()
        bad = {**entry(today), "expires": "soon"}
        with self.assertRaisesRegex(audit.ExceptionsError, "YYYY-MM-DD"):
            load([bad], today)

    def test_duplicate_ids_are_rejected(self) -> None:
        today = date.today()
        future = today + timedelta(days=30)
        with self.assertRaisesRegex(audit.ExceptionsError, "repeats"):
            load([entry(future), entry(future)], today)

    def test_wrong_shape_is_rejected(self) -> None:
        for bad in ([], {"exceptions": "x"}, {"exceptions": ["x"]}, {}):
            with self.subTest(bad=bad), self.assertRaises(audit.ExceptionsError):
                audit.load_exceptions(write(bad), date.today())

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(audit.ExceptionsError):
            audit.load_exceptions(Path("does-not-exist.json"), date.today())


class ExpiryWindowTest(unittest.TestCase):
    """An exception is valid only if today < expires <= today + MAX_DAYS."""

    def assert_rejected(self, expires: date, today: date, pattern: str) -> None:
        with self.assertRaisesRegex(audit.ExceptionsError, pattern):
            load([entry(expires)], today)

    def test_yesterday_is_rejected(self) -> None:
        today = date.today()
        self.assert_rejected(today - timedelta(days=1), today, "not in the future")

    def test_today_is_rejected(self) -> None:
        today = date.today()
        self.assert_rejected(today, today, "not in the future")

    def test_tomorrow_is_accepted(self) -> None:
        today = date.today()
        self.assertEqual(len(load([entry(today + timedelta(days=1))], today)), 1)

    def test_last_allowed_day_is_accepted(self) -> None:
        today = date.today()
        expires = today + timedelta(days=audit.MAX_DAYS)
        self.assertEqual(len(load([entry(expires)], today)), 1)

    def test_one_day_beyond_the_limit_is_rejected(self) -> None:
        today = date.today()
        expires = today + timedelta(days=audit.MAX_DAYS + 1)
        self.assert_rejected(expires, today, "more than")

    def test_the_window_moves_with_the_supplied_date(self) -> None:
        # The same entry is valid on one date and rejected on a later one, which is what
        # stops a hard-coded date from making the committed file fail in the future.
        expires = date(2030, 6, 30)
        self.assertEqual(len(load([entry(expires)], date(2030, 1, 1))), 1)
        self.assert_rejected(expires, date(2030, 6, 30), "not in the future")
        self.assert_rejected(expires, date(2029, 1, 1), "more than")

    def test_main_fails_without_running_pip_audit_for_an_entry_expiring_today(self) -> None:
        path = write({"exceptions": [entry(date.today())]})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = audit.main(["--path", "unused", "--exceptions", str(path)])
        self.assertEqual(status, 2)
        self.assertIn("::error title=pip-audit exceptions::", output.getvalue())


class BuildCommandTest(unittest.TestCase):
    def test_no_exceptions_means_no_ignore_flags(self) -> None:
        command = audit.build_command(Path("/site"), [])
        self.assertNotIn("--ignore-vuln", command)
        self.assertIn("--skip-editable", command)

    def test_each_exception_becomes_an_ignore_flag(self) -> None:
        future = date.today() + timedelta(days=30)
        command = audit.build_command(
            Path("/site"), [entry(future, id="A-1"), entry(future, id="B-2")]
        )
        self.assertEqual(command.count("--ignore-vuln"), 2)
        self.assertIn("A-1", command)
        self.assertIn("B-2", command)


if __name__ == "__main__":
    unittest.main()
