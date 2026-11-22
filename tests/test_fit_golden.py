"""The shared cases for the browser port (site/careers/hn-fit.js) and the Node suite that checks them (S6)."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_fit_golden", ROOT / "site" / "tools" / "make_fit_golden.py")
golden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden)


def test_committed_golden_file_is_what_the_python_reference_makes():
    committed = (ROOT / "tests" / "fixtures" / "fit_golden.json").read_text(encoding="utf-8")
    assert committed == golden.render(), "run: uv run python site/tools/make_fit_golden.py (and commit the result)"


def test_golden_cases_cover_the_edges():
    made = golden.make()
    names = {c["name"] for kind in made.values() for c in kind}
    for wanted in ("empty", "forks_only", "only_five_languages", "no_overlap", "score_is_capped_at_100", "ties_go_to_the_newer_post",
                   "limit_zero", "aliases_golang_k8s_postgres", "language_beats_topic_of_the_same_name"):
        assert wanted in names
    capped = next(c for c in made["score_cases"] if c["name"] == "score_is_capped_at_100")
    assert capped["expected"]["score"] == 100


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed; run the Node suite where it is")
def test_the_javascript_tests_pass():
    files = sorted(str(p) for p in (ROOT / "site" / "tests").glob("*.test.js"))
    assert len(files) >= 2
    run = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1000:]
