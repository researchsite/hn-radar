from pydantic import BaseModel, Field


class Job(BaseModel):
    comment_id: int
    company: str
    role: str | None = None
    location: str | None = None
    remote: bool | None = None
    stack: list[str] = Field(default_factory=list)
    url: str | None = None
    raw_text: str


class ShowHNCandidate(BaseModel):
    story_id: int
    title: str
    points: int
    num_comments: int
    url: str | None = None
    domain_match: bool = False


class ShowHNResult(BaseModel):
    query: str
    domain: str | None = None
    candidates: list[ShowHNCandidate] = Field(default_factory=list)
    total_hits: int
    truncated: bool
    low_confidence: bool


class Language(BaseModel):
    name: str
    size_kb: int


class Repo(BaseModel):
    name: str
    description: str | None = None
    stars: int
    language: str | None = None


class Profile(BaseModel):
    username: str
    languages: list[Language] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    notable_repos: list[Repo] = Field(default_factory=list)


class MatchReason(BaseModel):
    kind: str  # language | topic | repo | remote
    text: str
    evidence: str  # the concrete repo, language or topic from the profile


class MatchJob(BaseModel):
    comment_id: int
    company: str
    role: str | None = None
    remote: bool | None = None
    url: str | None = None


class Match(BaseModel):
    rank: int
    score: int
    job: MatchJob
    reasons: list[MatchReason]


class MatchResult(BaseModel):
    username: str
    generated_at: str
    status: str  # ok | no_strong_matches
    matches: list[Match] = Field(default_factory=list)
