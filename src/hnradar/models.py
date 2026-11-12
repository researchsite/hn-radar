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
