"""The pages in site/careers/ and the documented publish layout must agree (Qodo, PR 66)."""

import re
from pathlib import Path

CAREERS = Path(__file__).resolve().parent.parent / "site" / "careers"
README = (Path(__file__).resolve().parent.parent / "site" / "README.md").read_text(encoding="utf-8")
LOCAL = re.compile(r'(?:src|href)="\./([\w.-]+\.(?:js|css))"')


def referenced():
    found = {}
    for page in sorted(CAREERS.glob("*.html")):
        for name in LOCAL.findall(page.read_text(encoding="utf-8")):
            found.setdefault(name, []).append(page.name)
    return found


def test_every_local_script_and_style_a_page_loads_exists():
    for name, pages in referenced().items():
        assert (CAREERS / name).is_file(), f"{pages} load ./{name}, which is not in site/careers/"


def test_every_local_script_and_style_a_page_loads_is_in_the_publish_table():
    """site/README.md lists where each shared file goes on the live site; a page that loads a file the table omits breaks there."""
    table = next(line for line in README.splitlines() if line.startswith("| `hn.css`"))
    for name in referenced():
        assert f"`{name}`" in table and f"`includes/rw-{name}`" in table, f"{name} is loaded by a page but missing from the publish table in site/README.md"


def test_every_data_file_the_page_loads_is_published():
    """check.html reads data files by name; site/README.md must list them in the publish allowlist (and the private script has them)."""
    page = (CAREERS / "check.html").read_text(encoding="utf-8")
    files = set(re.findall(r"loadData\('([\w.-]+\.json)'", page))
    assert {"jobs.json", "signals.json"} <= files
    for name in files:
        assert f"`{name}`" in README, f"{name} is read by check.html but not named in site/README.md"


def test_every_node_test_file_is_in_the_documented_command():
    """PowerShell and Node 20 do not expand a glob, so the docs name each file; a new test file must be added to them."""
    root = CAREERS.parent.parent
    docs = {name: (root / name).read_text(encoding="utf-8") for name in ("AGENTS.md", "README.md", "site/README.md")}
    for test_file in sorted((CAREERS.parent / "tests").glob("*.test.js")):
        for name, text in docs.items():
            assert f"site/tests/{test_file.name}" in text, f"{test_file.name} is not in the node --test command in {name}"
