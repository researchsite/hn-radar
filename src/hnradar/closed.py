"""Find the reply in which the poster of a job says the job is closed. Pure functions, no I/O."""

import re
from bisect import bisect_left, bisect_right

from hnradar.parse import html_to_text

_NOUN = r"(?:applications?|positions?|roles?|jobs?|postings?|listings?|search|hiring)"
_JOB_NOUN = r"(?:positions?|roles?|jobs?|openings?|vacanc(?:y|ies))"
# A phrase that is only a closing notice when nothing qualifies it ("We are not hiring." but not
# "We are not hiring juniors" or "Please don't apply, we only want seniors"). Only a full stop, "!" or the
# end of the reply closes the phrase; a comma or semicolon means more follows, which is not read.
_END = r"(?=\s*(?:[.!]|$))"  # no comma: "Don't apply, unless you're in Berlin" goes on
# What may follow "no longer hiring" / "not accepting applications" and still be the same notice. Anything
# else ("no longer hiring juniors", "not accepting applications from recruiters") is a narrower statement.
_TAIL = (
    r"(?:\s+(?:applications?|applicants|new|any\s?more|currently|right now|at (?:this|the) (?:time|moment)"
    r"|for\s+(?:this|the|these|any)\s+(?:\w+\s+)?" + _JOB_NOUN + r"))*"
)
_CLOSING = re.compile(
    r"\bno longer\s+(?:hiring|accepting|taking|considering|open)" + _TAIL + _END +
    r"|\bnot\s+(?:currently\s+|any\s?more\s+)?(?:accepting|taking)\s+(?:any\s+)?(?:new\s+)?applications?" + _TAIL + _END +
    r"|\b" + _NOUN + r"\s+(?:(?:is|are|has|have|was|were)\s+)?(?:(?:now|been|already)\s+)*(?:closed|filled)\b"
    r"(?!\s+(?:to|from|outside|except|unless|if)\b)"
    r"|\bwe(?:['’]ve|\s+have|\s+had)?\s+(?:now\s+)?filled\s+(?:this|the|these|all|our)\s+(?:\w+\s+)?" + _JOB_NOUN +
    r"\b(?!\s+(?:of|for|as|in|at|on|by|with|last|before|earlier)\b)"
    r"|\bhiring\s+(?:has|have|is)\s+(?:now\s+)?(?:stopped|ended|finished|closed)\b"
    r"|\bwe(?:['’]re|\s+are)\s+not\s+(?:currently\s+|actively\s+)?hiring(?:\s+(?:any\s?more|now|right now|at the moment))?" + _END +
    r"|\bwe\s+(?:aren['’]t|are\s+not)\s+hiring" + _END +
    r"|\b(?:don['’]t|do not|should not|shouldn['’]t)\s+apply(?:\s+(?:any\s?more|now))?" + _END,
    re.I,
)
# A condition or a question word earlier in the sentence turns a match into "what if" ("if we hire someone,
# the position is filled"). A missed notice is fine; a job wrongly marked closed is not.
_CONDITION = re.compile(r"\b(?:if|once|when|until|unless|whether)\b", re.I)
# A negation only reaches to the end of its own clause, so "The position isn't filled yet, but applications
# are closed" still counts: the later clause is a plain statement.
_NEGATION = re.compile(r"\b(?:not|never|no|neither|nor|none)\b|n['’]t\b", re.I)
_CLAUSE_BREAK = re.compile(r"[,;:—–]|\b(?:but|however|though|although)\b", re.I)
# An exception after the phrase takes it back: "Position filled, unless you are a senior."
_QUALIFIER = re.compile(r"\b(?:unless|except|if|apart from|besides|only)\b", re.I)
# The rest of the reply can take it back: "some positions are filled, but we're still hiring".
_STILL_OPEN = re.compile(
    r"\bstill\s+(?:hiring|open|accepting|taking|looking\s+for|recruiting)\b"
    r"|\bwe(?:['’]re|\s+are)\s+(?:also\s+)?(?:hiring|looking\s+for|recruiting)\b"  # "filled backend, we're hiring for frontend"
    r"|\b(?:remains?|remained)\s+open\b|\b(?:is|are)\s+(?:still\s+|also\s+)?open\b",  # "applications remain open"
    re.I,
)
# Someone else's question, quoted or in brackets, is not the poster's statement: We got: "Is it filled?", but ...
_QUOTED_QUESTION = re.compile(r'"[^"]*\?\s*"|“[^”]*\?\s*”|\([^()]*\?\s*\)')
_HYPHEN_COMPOUND = re.compile(r"-\w")  # "closed-source", "closed-loop": the word is part of a compound
_QUOTE_END = " \t\"'“”‘’)]}»"
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")


def _starts(pattern: re.Pattern, sentence: str) -> list[int]:
    return [m.start() for m in pattern.finditer(sentence)]


def says_closed(text: str) -> bool:
    text = _QUOTED_QUESTION.sub(" ", text)
    if _STILL_OPEN.search(text):  # the whole reply: "We filled the backend role. We're hiring for frontend."
        return False
    for sentence in _SENTENCE.split(text):
        if sentence.rstrip(_QUOTE_END).endswith("?"):
            continue
        # Positions are collected once per sentence and searched by bisect, so a long reply with many
        # rejected matches stays linear instead of rescanning the text before every match.
        conditions, negations, qualifiers = (_starts(p, sentence) for p in (_CONDITION, _NEGATION, _QUALIFIER))
        break_ends = [m.end() for m in _CLAUSE_BREAK.finditer(sentence)]
        for match in _CLOSING.finditer(sentence):  # every phrase, so a later plain statement still counts
            if _HYPHEN_COMPOUND.match(sentence, match.end()):
                continue
            if conditions and conditions[0] < match.start():
                continue
            at = bisect_right(break_ends, match.start())
            clause_start = break_ends[at - 1] if at else 0
            n = bisect_left(negations, clause_start)
            if n < len(negations) and negations[n] < match.start():
                continue
            q = bisect_left(qualifiers, match.end())
            if q < len(qualifiers):
                continue
            return True
    return False


def closing_reply_id(comment: dict) -> int | None:
    """Id of the first reply, in thread order, that the poster wrote and that says the job is closed.

    Replies by anyone else are ignored, however they are worded. Replies are read at any depth, but only
    those whose author is the poster count. Without a poster name nothing can be verified, so the result
    is None, which means "no evidence", not "open".
    """
    poster = comment.get("author")
    if not isinstance(poster, str) or not poster:
        return None
    poster = poster.casefold()

    stack = list(reversed(_children(comment)))  # a stack, not recursion: a hostile thread can nest deeply
    while stack:
        reply = stack.pop()
        stack.extend(reversed(_children(reply)))
        author, text, reply_id = reply.get("author"), reply.get("text"), reply.get("id")
        if not isinstance(author, str) or author.casefold() != poster:
            continue
        if not isinstance(text, str) or not text or isinstance(reply_id, bool) or not isinstance(reply_id, int):
            continue  # deleted or dead (no text), or no id to cite
        if says_closed(html_to_text(text)):
            return reply_id
    return None


def _children(node: dict) -> list[dict]:
    children = node.get("children")
    return [c for c in children if isinstance(c, dict)] if isinstance(children, list) else []
