from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_ITEMS = 30  # a small model can loop and repeat one item for minutes; a real thread has far fewer themes


class PulseItem(BaseModel):
    kind: Literal["pro", "bug", "ask"]
    summary: str = Field(min_length=1, max_length=500)
    comment_ids: list[int] = Field(min_length=1)  # rule 1: no item without the comments it came from


class ModelReply(BaseModel):
    """The only shape accepted from the model (rule 2)."""

    items: list[PulseItem] = Field(max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _no_repeats(self):
        seen = set()
        for item in self.items:
            key = (item.kind, " ".join(item.summary.lower().split()))
            if key in seen:
                raise ValueError("the same item is listed more than once")
            seen.add(key)
        return self


class PulseResult(BaseModel):
    story_id: int
    title: str
    model: str
    comments_used: int  # sent to the model
    comments_total: int  # top-level comments with text; used < total means the thread was cut (rule 6)
    items: list[PulseItem] = Field(default_factory=list)
