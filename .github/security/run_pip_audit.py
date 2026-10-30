"""Run pip-audit and apply only well-formed, unexpired exceptions.

pip-audit fails on any known vulnerability. A vulnerability may be accepted for a
limited time by adding an entry to `pip-audit-exceptions.json`:

    {"exceptions": [
      {"id": "PYSEC-0000-0000", "reason": "why it does not apply",
       "owner": "who is responsible", "expires": "YYYY-MM-DD"}
    ]}

Every field is required. `expires` must be strictly after today (an entry that expires
today is already rejected) and at most MAX_DAYS away. A failing entry fails the job, so an
exception can never silently become permanent.
Standard library only.
"""

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REQUIRED_FIELDS = ("id", "reason", "owner", "expires")
MAX_DAYS = 180
WARN_DAYS = 14
DEFAULT_EXCEPTIONS = Path(__file__).with_name("pip-audit-exceptions.json")


class ExceptionsError(Exception):
    """The exceptions file is malformed or contains an invalid, expired or too-distant entry."""


def load_exceptions(path: Path, today: date) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExceptionsError(f"cannot read {path.name}: {error}") from error
    entries = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ExceptionsError('the file must contain {"exceptions": [ ... ]}')

    seen: set[str] = set()
    for position, entry in enumerate(entries, start=1):
        label = f"exception #{position}"
        if not isinstance(entry, dict):
            raise ExceptionsError(f"{label} must be an object")
        for field in REQUIRED_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ExceptionsError(f"{label} is missing a non-empty '{field}'")
        if entry["id"] in seen:
            raise ExceptionsError(f"{label} repeats id {entry['id']}")
        seen.add(entry["id"])
        try:
            expires = date.fromisoformat(entry["expires"])
        except ValueError:
            raise ExceptionsError(f"{label} has an invalid 'expires' (use YYYY-MM-DD)") from None
        if expires <= today:
            raise ExceptionsError(
                f"{label} ({entry['id']}) is not in the future (expires {expires}): "
                "fix the vulnerability, or renew the exception with a new rationale"
            )
        if expires > today + timedelta(days=MAX_DAYS):
            raise ExceptionsError(f"{label} expires more than {MAX_DAYS} days from today")
    return entries


def build_command(site_packages: Path, entries: list[dict[str, str]]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--path",
        str(site_packages),
        "--skip-editable",
        "--progress-spinner",
        "off",
    ]
    for entry in entries:
        command += ["--ignore-vuln", entry["id"]]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", required=True, type=Path, help="site-packages to audit")
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTIONS)
    args = parser.parse_args(argv)

    today = date.today()
    try:
        entries = load_exceptions(args.exceptions, today)
    except ExceptionsError as error:
        print(f"::error title=pip-audit exceptions::{error}")
        return 2

    for entry in entries:
        expires = date.fromisoformat(entry["expires"])
        if expires <= today + timedelta(days=WARN_DAYS):
            print(f"::warning title=pip-audit exception expiring::{entry['id']} expires {expires}")
        print(f"Accepted exception {entry['id']} (owner: {entry['owner']}, expires {expires})")

    status = subprocess.run(build_command(args.path, entries), check=False).returncode
    if status != 0:
        print(f"::error title=pip-audit::pip-audit exited with status {status}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
