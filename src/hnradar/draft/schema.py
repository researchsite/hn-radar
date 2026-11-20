from pydantic import BaseModel, Field

LINES = 5
MAX_LINE_CHARS = 300


class DraftLine(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_LINE_CHARS)
    comment_ids: list[int] = Field(default_factory=list)  # rule 1: a line about what users said lists its comments


class ModelDraft(BaseModel):
    """The only shape accepted from the model (rule 2): exactly five lines."""

    lines: list[DraftLine] = Field(min_length=LINES, max_length=LINES)


class Evidence(BaseModel):
    comment_id: int
    text: str  # plain text of the cited comment, cut to MAX_EVIDENCE_CHARS
    url: str


class DraftResult(BaseModel):
    username: str
    company: str
    domain: str
    story_id: int
    repo: str
    repo_language: str | None = None
    repo_description: str | None = None  # printed so the person can check what the draft says about the repo
    model: str
    lines: list[DraftLine]
    evidence: list[Evidence]
