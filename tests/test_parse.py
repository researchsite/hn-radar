from hnradar.parse import extract_stack, html_to_text, parse_comment

from conftest import FREEFORM_COMMENT, ONSITE_COMMENT, PIPE_COMMENT


def test_pipe_format():
    job = parse_comment(101, PIPE_COMMENT)
    assert job.comment_id == 101
    assert job.company == "Acme"
    assert job.role == "Senior Backend Engineer"
    assert job.location == "Berlin, Germany"
    assert job.remote is True
    assert job.stack == ["Python", "PostgreSQL"]
    assert job.url == "https://acme.example/jobs"
    assert job.raw_text.startswith("Acme | Senior Backend Engineer")


def test_non_pipe_post_falls_back_gracefully():
    job = parse_comment(102, FREEFORM_COMMENT)
    assert job.company == "Initech"
    assert job.role is None
    assert job.location is None
    assert job.remote is True
    assert job.url is None
    assert "Looking for engineers in Austin" in job.raw_text


def test_onsite_only_is_not_remote():
    job = parse_comment(104, ONSITE_COMMENT)
    assert job.role == "Data Analyst"
    assert job.location == "New York, NY"
    assert job.remote is False
    assert job.stack == ["SQL"]


def test_real_place_beats_onsite_marker():
    job = parse_comment(1, "Shepherd (Series B) | ONSITE | San Francisco, CA")
    assert job.location == "San Francisco, CA"
    assert job.remote is False


def test_multi_city_location_with_hybrid_marker():
    job = parse_comment(1, "CoVar | Durham, NC and McLean, VA | Full-time | HYBRID (3 days in office)")
    assert job.location == "Durham, NC and McLean, VA"
    assert job.remote is None


def test_vague_and_non_engineering_roles():
    assert parse_comment(1, "FutureSearch | Multiple senior roles | REMOTE").role == "Multiple senior roles"
    assert parse_comment(1, "Mastra | Founding Sales (Founding AE) | US Remote").role == "Founding Sales (Founding AE)"


def test_url_is_stripped_from_company():
    job = parse_comment(1, "Don't Use This Code: https://dutc.io | Training | Fully Remote (US-only)")
    assert job.company == "Don't Use This Code"


def test_negated_remote_statements_are_not_remote():
    for text in (
        "Remote work is not available",
        "Remote work isn&#x27;t available for this role",
        "Remote is not possible, sorry",
        "Remote unavailable",
    ):
        assert parse_comment(1, f"Foo | Engineer | Berlin<p>{text}").remote is False, text


def test_company_suffix_survives_comma_in_prose_fallback():
    job = parse_comment(1, "We&#x27;re Acme, Inc. hiring engineers in Berlin")
    assert job.company == "Acme, Inc."
    assert parse_comment(1, "Acme Inc. is hiring engineers").company == "Acme Inc"


def test_remote_unknown_when_not_mentioned():
    assert parse_comment(1, "Foo | Engineer | Berlin").remote is None


def test_hn_links_are_not_job_urls():
    html = 'Foo | Engineer | Berlin<p><a href="https://news.ycombinator.com/item?id=1">x</a>'
    assert parse_comment(1, html).url is None


def test_html_to_text_unescapes_and_splits_paragraphs():
    assert html_to_text("a &amp; b<p>c<br>d <i>e</i>") == "a & b\nc\nd e"


def test_stack_uses_whole_words():
    assert extract_stack("JavaScript, Java and golang; Go to market") == ["JavaScript", "Java", "Go"]
