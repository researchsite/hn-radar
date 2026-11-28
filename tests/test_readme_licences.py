"""The README's "Third-party licences" table must name every package uv.lock installs (Qodo, PR 69)."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def licence_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("## Third-party licences")
    return text[start : text.index("\n## ", start + 1)]


def locked_packages():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return sorted(p["name"] for p in lock["package"] if p["name"] != "hnradar")


def table_rows():
    return [line for line in licence_section().splitlines() if line.startswith("| `")]


def test_every_locked_package_has_a_licence_row():
    rows = "\n".join(table_rows())  # prose does not count: the name must sit in a table row
    missing = [name for name in locked_packages() if f"`{name}`" not in rows]
    assert not missing, f"packages in uv.lock with no entry in the README licence table: {missing}"


def test_the_licence_table_names_no_package_that_is_not_locked():
    """A row for a removed package would be a claim about something that is no longer installed."""
    locked = set(locked_packages())
    named = {name for row in table_rows() for name in row.split("|")[1].split("`")[1::2]}
    assert named <= locked, f"README licence table names packages that are not in uv.lock: {sorted(named - locked)}"


def test_the_lock_is_read_and_is_not_empty():
    assert len(locked_packages()) >= 10 and "certifi" in locked_packages()
