import pytest

from hnradar.parse import extract_stack, html_to_text, is_candidate_profile, parse_comment

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


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Stack: Python, Go, Rust, or Zig", True),
        ("We write Go and Kubernetes, not Java", True),
        ("We write Go and run our own database", False),  # a miss is fine
        ("Ready to Go, join us", False),
        ("see https://example.test/?lang=Go,Rust", False),
        ("see https://example.test/?x=Go/Rust&y=Python,Go", False),
        ("Lets Go: apply now", False),
        ("Join the Go/Rust team", True),
        ("Staff Backend Engineer, Go developer positions", True),
        ("SFU in Go, cascading", False),  # a miss is fine
        ("Go for it! Custom Discord apps", False),  # the English verb
        ("We move infrastructure from Go into Elixir", False),  # a miss is fine, a false hit is not
        ("Let's go build things", False),  # lower case
        ("GoLand and Gotham", False),
        ("ago, Gone, Google", False),
        ("", False),
    ],
)
def test_go_is_only_the_language_in_clear_contexts(text, expected):
    assert ("Go" in extract_stack(text)) is expected


def test_golang_and_go_do_not_double_count():
    assert extract_stack("Golang, Go, Rust").count("Go") == 1


# Real posts from the November 2026 thread, shortened (ids in the comments). Profiles first, then real jobs
# that look similar and must stay.
PROFILE_PIPE_FORMAT = (  # 50454490: pipe header, so it even gets a role and remote
    "Hemant Singh | Software Engineer / Backend Engineer | India (REMOTE / ONSITE)\n"
    "I'm a software engineer focused on backend development, primarily working with Java, Spring Boot, and Python.\n"
    "Technical Skills:"
)
PROFILE_INTRO = (  # 50388471
    "I'm Sushant Gangurde, A Software Engineer focused on Full-Stack Development and AI-powered applications.\n"
    "I work with React, Next.js, TypeScript, Node.js, Python, PostgreSQL and MongoDB."
)
PROFILE_INTRO_AND = (  # 50382899
    "I'm Rajneesh Verma, a Full-Stack & AI Engineer currently working with a YC-backed startup.\n"
    "I have hands-on experience building and shipping production-ready applications."
)
PROFILE_RESUME_LABELS = (  # 50403646: the old "Who wants to be hired?" format
    "Location: Estonia Remote:Yes\n"
    "Willing to relocate: No\n"
    "Technologies: React.js,Vue.js, Next.js,Nuxt.js ,TypeScript, SCSS, CSS3, Tailwind CSS, Redux, Zustand\n"
    "Part-time/Freelance: Yes\n"
    "Linkedin: https://www.linkedin.com/in/mustafadalga/\n"
    "Contact info: mustafadalga \\[at\\] gmail \\[dot\\] com\n"
    "Front-end engineer with 7+ years of experience in React/Next.js, Vue/Nuxt.js, and TypeScript."
)
PROFILE_RESUME_LABELS_MORE = {  # the other five resume-label profiles from the thread; contact values removed
    "50344555": (
        "Location: Huntington Beach, CA, USA\nRemote: Yes\nWilling to relocate: No\n"
        "Technologies: C#/.NET, TypeScript, React, PostgreSQL, Linux\nRésumé/CV: https://example.invalid\n"
        "Email: removed\nFractional CTO and contract work: web apps that do real work."
    ),
    "50349800": (
        "Location: Dorset, UK\nRemote: Yes (UK, US and EU time zones)\nWilling to relocate: No\n"
        "Technologies: Ruby on Rails, Python (FastAPI), TypeScript/Node, Go\nRésumé/CV: https://example.invalid\n"
        "Email: removed\nSenior engineer, 15 years. I build complete products on a fixed scope."
    ),
    "50353980": (
        "Location: Andalucía, Spain (CET)\nRemote: Yes, remote only\nWilling to relocate: No (EU citizen)\n"
        "Technologies: TypeScript, React, Next.js, Node.js, Fastify\nRésumé/CV: https://example.invalid\n"
        "Email: removed\nFull-stack engineer, 6+ years on 100% remote teams."
    ),
    "50430411": (
        "Location: Bangalore, India (IST / UTC+5:30)\nRemote: Yes (Worldwide)\n"
        "Willing to relocate: Yes (Worldwide, visa sponsorship required)\n"
        "Technologies: TypeScript, Node.js, Go, Python, React, PostgreSQL\nRésumé/CV: https://example.invalid\n"
        "Email: removed\nSenior Software Engineer (~5 YoE) building real-time AI systems.\nExperience:"
    ),
    "50479091": (
        "Location: India (currently travelling within India; IST, UTC+5:30)\nRemote: Yes, remote only\n"
        "Willing to relocate: No\nTechnologies: React, TypeScript, Python, Kotlin/Spring Boot, GraphQL\n"
        "Résumé/CV: https://example.invalid\nGitHub: https://example.invalid\nEmail: removed\n"
        "Full-stack engineer, ~5 yrs."
    ),
}
JOB_SEEKING_FREELANCER = (  # 50352957: a company looking for a freelancer
    "SEEKING FREELANCER | Devops Engineer | Remote - UK or EU\n"
    "getregistered is a better way to do event registration.\n"
    "We're looking for an experienced devops freelancer to help manage and enhance our GCP + Terraform stack.\n"
    "Please email hn@getregistered.com with LinkedIn / CV / Samples of work."
)
JOB_FOUNDER_INTRO = (  # 50369728
    "Matcha.fm | REMOTE (Worldwide: US / Canada / Europe / UK / LATAM) | Full-time | https://matcha.fm\n"
    "I'm a Canadian repeat founder working on agents that cut the noise in large dataset search.\n"
    "Looking for a great builder (founding software engineer) and a great marketer."
)
JOB_LOOKING_TO_HIRE = (  # 50428062
    "Collabify | Senior Full-Stack Engineer (backend-heavy) | REMOTE (worldwide) | Contract\n"
    "I'm looking to hire our first engineer to own architecture and build product first version.\n"
    "Stack: TypeScript, React/Next.js, Node.js and PostgreSQL."
)
JOB_RECRUITER_INTRO = (  # 50484305
    "*Confidential Healthcare AI Startup | Senior Full Stack Engineer (Frontend-Leaning) | NYC | Hybrid\n"
    "I'm an independent recruiter sourcing for a venture-backed healthcare technology startup.\n"
    "The company has raised $50M+ and is looking for a senior, frontend-focused engineer."
)


@pytest.mark.parametrize(
    "text",
    [PROFILE_PIPE_FORMAT, PROFILE_INTRO, PROFILE_INTRO_AND, PROFILE_RESUME_LABELS],
    ids=["pipe-format", "intro", "intro-ampersand-title", "resume-labels"],
)
def test_candidate_profiles_are_detected(text):
    assert is_candidate_profile(text)


@pytest.mark.parametrize("post_id", sorted(PROFILE_RESUME_LABELS_MORE))
def test_every_known_resume_label_profile_is_detected(post_id):
    assert is_candidate_profile(PROFILE_RESUME_LABELS_MORE[post_id])


@pytest.mark.parametrize(
    "text",
    [JOB_SEEKING_FREELANCER, JOB_FOUNDER_INTRO, JOB_LOOKING_TO_HIRE, JOB_RECRUITER_INTRO],
    ids=["seeking-freelancer", "founder-intro", "looking-to-hire", "recruiter-intro"],
)
def test_real_jobs_that_look_like_profiles_stay(text):
    assert not is_candidate_profile(text)


def test_ordinary_jobs_are_not_profiles():
    for html in (PIPE_COMMENT, FREEFORM_COMMENT, ONSITE_COMMENT):
        assert not is_candidate_profile(html_to_text(html))


@pytest.mark.parametrize(
    "text",
    [
        "I'm looking for a developer to help build Acme's product.",
        "I'm a founder looking for a developer to join the build.",
        "I am seeking an engineer for a six-month contract.",
        "Hi, I'm Max and I need a designer for our launch.",
    ],
)
def test_first_person_request_for_a_role_is_a_job(text):
    assert not is_candidate_profile(text)


def test_profile_that_is_looking_for_work_is_still_detected():
    assert is_candidate_profile("I'm a software engineer looking for a new role in Berlin.")


def test_intro_with_a_hiring_cue_is_kept():
    text = "I'm a software engineer and we're hiring two more backend engineers for our team."
    assert not is_candidate_profile(text)


def test_resume_labels_need_the_relocate_line():
    # A job written as "Key: value" lines with Location, Technologies and Email is still a job.
    text = "Company: Acme\nLocation: Berlin\nTechnologies: Python, Go\nEmail: jobs@acme.example"
    assert not is_candidate_profile(text)


@pytest.mark.parametrize("text", ["", "\n", "   ", "https://example.com"])
def test_empty_and_link_only_posts_do_not_crash(text):
    assert not is_candidate_profile(text)


# ---- issue 61: gaps Qodo found on the data refresh ----

JOB_SEEKING_WORK_HEADER = (  # 50495944: a person offering themselves, in the hiring thread
    "SEEKING FREELANCE - Egypt (UTC+2, full EU/UK overlap) - Remote\n"
    "Senior Full-Stack & Creative Frontend Engineer (4+ yrs). I architect Next.js/TypeScript and Node systems.\n"
    "Portfolio: https://example.com"
)


@pytest.mark.parametrize(
    "text",
    [JOB_SEEKING_WORK_HEADER, "SEEKING WORK | Remote | Python developer\nI build APIs.", "Seeking employment - Berlin\nBackend engineer, 6 yrs."],
    ids=["seeking-freelance-header", "seeking-work-pipe", "seeking-employment"],
)
def test_a_seeking_work_header_is_a_candidate_profile(text):
    assert is_candidate_profile(text)


@pytest.mark.parametrize(
    "text",
    [
        "Seeking freelance developers for a six-week build\nWe are a small studio.",
        "SEEKING FREELANCE DEVOPS ENGINEERS | Remote\nOur team runs on GCP.",
        "Seeking work-from-home friendly engineers | Acme\nWe're hiring.",
        "SEEKING FREELANCE - Egypt | Acme is hiring a freelance designer\nWe are hiring.",
        "Seeking a senior engineer | Acme | Berlin",
    ],
    ids=["plural-role", "plural-role-caps", "work-from-home", "hire-cue-in-post", "seeking-a-role"],
)
def test_hiring_posts_that_start_with_seeking_stay(text):
    assert not is_candidate_profile(text)


@pytest.mark.parametrize(
    "first_line, role",
    [
        ("Bechtel | Penetration Tester | Remote or onsite (US) | Full-time", "Penetration Tester"),
        ("Native | Founding Builder-in-Residence | San Francisco or London | REMOTE or ONSITE", "Founding Builder-in-Residence"),
        ("Genentech | 2027 Interns | PD Data Sciences | ONSITE (South San Francisco, CA USA)", "2027 Interns"),
        ("Acme | QA Tester | Berlin", "QA Tester"),
    ],
)
def test_role_titles_that_the_word_list_used_to_miss(first_line, role):
    assert parse_comment(1, first_line).role == role


def test_remotely_counts_as_remote():
    assert parse_comment(1, "New Product | Full Stack Engineer | Meet In The Bay Area / Work Remotely").remote is True


def test_remote_work_not_available_is_still_not_remote():
    assert parse_comment(1, "Foo | Engineer | Berlin<p>Remote work is not available").remote is False
