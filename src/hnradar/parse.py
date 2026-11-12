"""Turn an HN "Who is hiring?" comment (HTML) into a Job. Pure functions, no I/O."""

import re
from html import unescape
from urllib.parse import urlparse

from hnradar.models import Job

_BREAK = re.compile(r"<\s*(?:p|br)\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href\s*=\s*"([^"]+)"', re.I)

_ROLE = re.compile(
    r"\b(?:engineer\w*|developer\w*|designer|scientist|analyst|manager|architect|"
    r"devops|sre|backend|back-end|frontend|front-end|full[- ]?stack|lead|intern|"
    r"cto|founder|head of|recruiter|product|researcher|administrator|consultant|"
    r"sales|executive|staff|officer|chief|director|vp|marketing|writer|technician|"
    r"(?:multiple|several)(?:\s+senior)?\s+(?:roles|positions))\b",
    re.I,
)
_LOCATION_KEYWORD = re.compile(r"\b(?:remote|on-?site|hybrid)\b", re.I)
_ONSITE = re.compile(r"\b(?:on-?site|in[- ]office|in[- ]person)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_CITY_COMMA = re.compile(r"^[A-Z][A-Za-z.' -]+,\s*[A-Za-z]")
_PLACES = re.compile(
    r"\b(?:SF|NYC|LA|London|Berlin|Paris|Toronto|Amsterdam|Boston|Seattle|Austin|"
    r"Zurich|Dublin|Tokyo|Singapore|Sydney|Hong Kong|San Francisco|New York|Bay Area|"
    r"Europe|EU|US|USA|UK|Canada|Germany|India|Japan|Switzerland|Netherlands)\b"
)
_URL = re.compile(r"https?://\S+")
_LEADING_FILLER = re.compile(r"^(?:we['’]re|we are|about|at)\s+", re.I)
_COMPANY_END = re.compile(r"\s+(?:-|–|—|is hiring|are hiring)(?=\s|$)|[,.:;(]")

_NOT_REMOTE = re.compile(
    r"\b(?:no|not|without)\s+remote\b|\bon-?site only\b|"
    r"\b(?:don't|do not|doesn't)\s+(?:do|offer|support|allow)\s+remote\b|"
    r"\bremote(?:\s+work)?\s+(?:(?:is|are)\s+)?(?:not|isn['’]t|aren['’]t)\s+"
    r"(?:available|possible|offered|supported|an option)\b|"
    r"\bremote(?:\s+work)?\s+(?:is\s+)?unavailable\b",
    re.I,
)
_COMPANY_SUFFIX = re.compile(r",?\s+(?:Inc|LLC|Ltd|Corp|GmbH|Co)\b\.?")

_STACK = {
    "python": "Python", "typescript": "TypeScript", "javascript": "JavaScript",
    "rust": "Rust", "golang": "Go", "java": "Java", "kotlin": "Kotlin",
    "swift": "Swift", "scala": "Scala", "elixir": "Elixir", "ruby": "Ruby",
    "rails": "Rails", "php": "PHP", "c++": "C++", "c#": "C#",
    "react": "React", "vue": "Vue", "angular": "Angular", "svelte": "Svelte",
    "next.js": "Next.js", "node.js": "Node.js", "nodejs": "Node.js",
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "mysql": "MySQL",
    "mongodb": "MongoDB", "redis": "Redis", "kafka": "Kafka", "sql": "SQL",
    "graphql": "GraphQL", "aws": "AWS", "gcp": "GCP", "azure": "Azure",
    "docker": "Docker", "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "terraform": "Terraform", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
}
_STACK_RE = re.compile(
    r"(?<![\w+#])("
    + "|".join(re.escape(k) for k in sorted(_STACK, key=len, reverse=True))
    + r")(?![\w+#]|\.\w)",
    re.I,
)


def html_to_text(html: str) -> str:
    text = _TAG.sub("", _BREAK.sub("\n", html))
    lines = (line.strip() for line in unescape(text).splitlines())
    return "\n".join(line for line in lines if line)


def extract_stack(text: str) -> list[str]:
    found = (_STACK[m.group(1).lower()] for m in _STACK_RE.finditer(text))
    return list(dict.fromkeys(found))


def extract_url(html: str) -> str | None:
    for href in _HREF.findall(html):
        href = unescape(href)
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https") and parsed.hostname != "news.ycombinator.com":
            return href
    return None


def _extract_remote(text: str, first_line: str) -> bool | None:
    if _NOT_REMOTE.search(text):
        return False
    if re.search(r"\bremote\b", text, re.I):
        return True
    # Headers like "Acme | ONSITE | Berlin" say it outright; hybrid stays unknown.
    if _ONSITE.search(first_line) and not _HYBRID.search(first_line):
        return False
    return None


def _is_place(segment: str) -> bool:
    if extract_stack(segment):
        return False
    return bool(_CITY_COMMA.match(segment) or _PLACES.search(segment))


def _parse_pipe_line(segments: list[str]) -> tuple[str, str | None, str | None]:
    company, rest = segments[0], segments[1:]
    role = next((s for s in rest if _ROLE.search(s)), None)
    candidates = [s for s in rest if s != role]
    # A real place beats a bare "ONSITE"/"HYBRID" marker, which often precedes the city.
    location = next((s for s in candidates if _is_place(s)), None) or next(
        (s for s in candidates if _LOCATION_KEYWORD.search(s)), None
    )
    return company, role, location


def _clean_company(name: str) -> str:
    return _URL.sub("", name).strip(" :-–—*").strip()[:80] or "Unknown"


def _guess_company(first_line: str) -> str:
    line = _LEADING_FILLER.sub("", first_line)
    name = _COMPANY_END.split(line, maxsplit=1)[0]
    # The cut can land on the comma in "Acme, Inc."; keep the legal suffix.
    suffix = _COMPANY_SUFFIX.match(line, len(name))
    return _clean_company(name + (suffix.group(0) if suffix else ""))


def parse_comment(comment_id: int, html: str) -> Job:
    text = html_to_text(html)
    first_line = text.split("\n", 1)[0] if text else ""
    segments = [s.strip() for s in first_line.split("|") if s.strip()]

    if len(segments) >= 2:
        company, role, location = _parse_pipe_line(segments)
        company = _clean_company(company)
    else:
        company, role, location = _guess_company(first_line), None, None

    return Job(
        comment_id=comment_id,
        company=company,
        role=role,
        location=location,
        remote=_extract_remote(text, first_line),
        stack=extract_stack(text),
        url=extract_url(html),
        raw_text=text,
    )
