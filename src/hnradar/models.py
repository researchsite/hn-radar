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
