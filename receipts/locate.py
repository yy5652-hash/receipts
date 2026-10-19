"""Stage 3a: find the passages of a source most relevant to a claim (BM25 + number boost)."""

import math
import re
from collections import Counter

from .models import Passage
from .textutil import chunk, numbers

K1, B, NUMBER_BOOST = 1.5, 0.75, 1.5
_STOP = set(
    "a an the of in on at to for and or but by with from as is are was were be been being it its this that "
    "these those which who whom whose has have had do does did not no than then also into over under between "
    "about their there they he she we you i our his her up out so such can could would should may might will "
    "shall if while".split()
)


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]


def locate(claim: str, text: str, k: int = 3) -> list[Passage]:
    """Top-k chunks of `text` for `claim`; offsets index into `text`."""
    spans = chunk(text)
    if not spans:
        return []
    docs = [Counter(_terms(text[s:e])) for s, e in spans]
    n, avg = len(docs), sum(sum(d.values()) for d in docs) / len(docs)
    df = Counter(t for d in docs for t in d)
    query, claim_nums = set(_terms(claim)), numbers(claim)
    scored = []
    for (s, e), d in zip(spans, docs):
        dl = sum(d.values())
        score = sum(
            math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * (K1 + 1)
            / (d[t] + K1 * (1 - B + B * dl / avg))
            for t in query if t in d
        )
        if claim_nums & numbers(text[s:e]):
            score *= NUMBER_BOOST
        if score > 0:
            scored.append(Passage(text=text[s:e], start=s, end=e, score=round(score, 4)))
    return sorted(scored, key=lambda p: -p.score)[:k]
