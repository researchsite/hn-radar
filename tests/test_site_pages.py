"""The pages in site/careers/ and the documented publish layout must agree (Qodo, PR 66)."""

import re
import unicodedata
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


def invisible_characters(text):
    """(code point, line, column) of every format or control character except tab, line feed and carriage return.
    Lines are counted on LF only: str.splitlines() also breaks on U+000B, U+000C, U+001C-1E and U+0085 and would drop them (Qodo)."""
    found, line, col = [], 1, 0
    for ch in text:
        col += 1
        if ch == "\n":
            line, col = line + 1, 0
        elif ch not in "\t\r" and unicodedata.category(ch) in ("Cf", "Cc"):
            found.append((ord(ch), line, col))
    return found


def test_the_invisible_character_check_sees_every_control_and_format_character():
    """Every Unicode code point: exactly the Cc and Cf ones (minus tab, LF, CR) are found, nothing else."""
    codes = [c for c in range(0x110000) if c != 0x0A]  # LF only moves the line counter
    found = invisible_characters("".join(map(chr, codes)))
    expected = {c for c in codes if c not in (0x09, 0x0D) and unicodedata.category(chr(c)) in ("Cc", "Cf")}
    assert {code for code, _, _ in found} == expected and len(found) == len(expected)
    assert {0x000B, 0x000C, 0x001C, 0x001E, 0x0085, 0x200B, 0x202E, 0x2060, 0xFEFF} <= expected  # the ones named in the ticket


def test_the_invisible_character_check_on_small_inputs():
    assert invisible_characters("") == []  # empty input
    assert invisible_characters("a\nb" + chr(0x202E)) == [(0x202E, 2, 2)]  # the line counts follow LF only
    assert invisible_characters("tab\there\r\nnext line\n") == []  # a tab and CRLF line endings are allowed
    assert invisible_characters(chr(0x200B) + chr(0x200B)) == [(0x200B, 1, 1), (0x200B, 1, 2)]  # neighbours are both reported


def test_site_scripts_and_styles_hold_no_invisible_characters():
    """Arnica (Trojan Source): a bidirectional or zero-width character typed into a source file is invisible to a
    reviewer. hn.js once held seven of them inside its own sanitizer pattern; they are written as \\u escapes now."""
    for path in sorted([*CAREERS.glob("*.js"), *CAREERS.glob("*.css")]):
        bad = invisible_characters(path.read_bytes().decode("utf-8"))
        assert not bad, f"{path.name} contains raw invisible characters, write them as escapes: {[(f'U+{c:04X}', l, k) for c, l, k in bad[:5]]}"


def sanitizer_pattern():
    match = re.search(r"var BAD = /(\[.*?\])/g;", (CAREERS / "hn.js").read_text(encoding="utf-8"))
    assert match, "hn.js no longer defines var BAD = /[...]/g"
    return re.compile(match.group(1))


def test_the_page_sanitizer_still_removes_every_class_of_character_it_names():
    """The escape fix must not change what is removed: each end of each range matches, and the neighbours outside do not."""
    pattern = sanitizer_pattern()
    removed = [0x0000, 0x0008, 0x000B, 0x001F, 0x007F, 0x009F, 0x200B, 0x200F, 0x202A, 0x202E, 0x2060, 0x206F, 0xFEFF]
    kept = [ord("a"), ord("Z"), ord("5"), ord("é"), 0x0009, 0x000A, 0x0020, 0x00A0, 0x200A, 0x2010, 0x2029, 0x202F, 0x205F, 0x2070, 0xFEFE, 0xFF00]
    for code in removed:  # code points, not a split string: str.split() would silently drop the whitespace ones
        assert pattern.fullmatch(chr(code)), f"U+{code:04X} should be removed"
    for code in kept:
        assert not pattern.fullmatch(chr(code)), f"U+{code:04X} should be kept"


def test_the_page_sanitizer_on_whole_strings():
    pattern = sanitizer_pattern()
    zero_width, rlo = chr(0x200B), chr(0x202E)
    assert pattern.sub(" ", "") == ""  # empty input
    assert pattern.sub(" ", "plain text, tab\t and newline\n kept") == "plain text, tab\t and newline\n kept"
    assert pattern.sub(" ", "a" + rlo + "b" + zero_width + "c") == "a b c"  # each character becomes one space
    assert pattern.sub(" ", rlo + rlo) == "  "  # neighbours are replaced one by one


def test_every_place_that_lists_the_published_data_files_names_signals_json():
    """Qodo (PR 67): the allowlist is described in more than one paragraph; each one must name every data file the page reads."""
    paragraphs = [p for p in README.split("\n") if "allowlist" in p and "showhn-index.json" in p]
    assert len(paragraphs) >= 2
    for p in paragraphs:
        assert "`signals.json`" in p, "a paragraph of site/README.md lists the published data files without signals.json"
