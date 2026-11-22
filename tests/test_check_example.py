"""The example in site/careers/check.html must be something hnradar itself could produce (Qodo review of PR 59)."""

import json
import re
from pathlib import Path

from hnradar.draft.schema import ModelDraft
from hnradar.draft.write import _check
from hnradar.radar import RadarResult

PAGE = Path(__file__).resolve().parent.parent / "site" / "careers" / "check.html"


def example() -> dict:
    html = PAGE.read_text(encoding="utf-8")
    block = re.search(r'<script type="application/json" id="ck-example-data">(.*?)</script>', html, re.S)
    assert block, "the example data block is missing from check.html"
    return json.loads(block.group(1))


def test_example_is_a_valid_radar_result():
    result = RadarResult.model_validate(example())
    assert [c.rank for c in result.companies] == [1, 2, 3]
    assert [c.draft_status for c in result.companies].count("written") == 1


def test_example_draft_passes_the_tools_own_draft_check():
    drafted = next(c.draft for c in RadarResult.model_validate(example()).companies if c.draft)
    cited = {e.comment_id for e in drafted.evidence}
    _check(ModelDraft.model_validate({"lines": [line.model_dump() for line in drafted.lines]}), cited, drafted.repo, drafted.domain)


def test_example_fit_reasons_use_the_wording_of_the_matcher():
    for c in RadarResult.model_validate(example()).companies:
        for reason in c.fit_reasons:
            assert re.fullmatch(r"Your .+ work matches this job's stack|Your repo .+ is written in .+|Your repos use the topic .+", reason), reason
