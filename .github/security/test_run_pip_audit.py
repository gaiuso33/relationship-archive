import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import run_pip_audit as audit

TODAY = date(2026, 10, 29)


def write(entries: object) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(entries, handle)
    return Path(handle.name)


def entry(**overrides: str) -> dict[str, str]:
    base = {
        "id": "PYSEC-0000-0000",
        "reason": "synthetic example",
        "owner": "maintainer",
        "expires": (TODAY + timedelta(days=30)).isoformat(),
    }
    return {**base, **overrides}


class LoadExceptionsTest(unittest.TestCase):
    def test_empty_list_is_valid(self) -> None:
        self.assertEqual(audit.load_exceptions(write({"exceptions": []}), TODAY), [])

    def test_the_committed_file_is_valid(self) -> None:
        audit.load_exceptions(audit.DEFAULT_EXCEPTIONS, TODAY)

    def test_valid_entry_is_returned(self) -> None:
        entries = audit.load_exceptions(write({"exceptions": [entry()]}), TODAY)
        self.assertEqual(entries[0]["id"], "PYSEC-0000-0000")

    def test_each_missing_or_blank_field_is_rejected(self) -> None:
        for field in audit.REQUIRED_FIELDS:
            for broken in ({field: ""}, {field: "  "}):
                with self.subTest(field=field, broken=broken):
                    with self.assertRaises(audit.ExceptionsError):
                        audit.load_exceptions(write({"exceptions": [entry(**broken)]}), TODAY)
            missing = entry()
            del missing[field]
            with self.subTest(field=field, missing=True), self.assertRaises(audit.ExceptionsError):
                audit.load_exceptions(write({"exceptions": [missing]}), TODAY)

    def test_expired_entry_is_rejected(self) -> None:
        expired = entry(expires=(TODAY - timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(audit.ExceptionsError, "expired"):
            audit.load_exceptions(write({"exceptions": [expired]}), TODAY)

    def test_entry_expiring_today_is_still_valid(self) -> None:
        audit.load_exceptions(write({"exceptions": [entry(expires=TODAY.isoformat())]}), TODAY)

    def test_expiry_too_far_in_the_future_is_rejected(self) -> None:
        far = entry(expires=(TODAY + timedelta(days=audit.MAX_DAYS + 1)).isoformat())
        with self.assertRaisesRegex(audit.ExceptionsError, "more than"):
            audit.load_exceptions(write({"exceptions": [far]}), TODAY)

    def test_invalid_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(audit.ExceptionsError, "YYYY-MM-DD"):
            audit.load_exceptions(write({"exceptions": [entry(expires="soon")]}), TODAY)

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(audit.ExceptionsError, "repeats"):
            audit.load_exceptions(write({"exceptions": [entry(), entry()]}), TODAY)

    def test_wrong_shape_is_rejected(self) -> None:
        for bad in ([], {"exceptions": "x"}, {"exceptions": ["x"]}, {}):
            with self.subTest(bad=bad), self.assertRaises(audit.ExceptionsError):
                audit.load_exceptions(write(bad), TODAY)

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(audit.ExceptionsError):
            audit.load_exceptions(Path("does-not-exist.json"), TODAY)


class BuildCommandTest(unittest.TestCase):
    def test_no_exceptions_means_no_ignore_flags(self) -> None:
        command = audit.build_command(Path("/site"), [])
        self.assertNotIn("--ignore-vuln", command)
        self.assertIn("--skip-editable", command)

    def test_each_exception_becomes_an_ignore_flag(self) -> None:
        command = audit.build_command(Path("/site"), [entry(id="A-1"), entry(id="B-2")])
        self.assertEqual(command.count("--ignore-vuln"), 2)
        self.assertIn("A-1", command)
        self.assertIn("B-2", command)


if __name__ == "__main__":
    unittest.main()
