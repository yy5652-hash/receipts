"""Stage 3a: find the passages of a source most relevant to a claim (BM25 + number boost)."""

import math
import re
from collections import Counter

from .models import Passage
from .textutil import chunk, numbers

K1, B, NUMBER_BOOST, SPECIFIC_BOOST = 1.5, 0.75, 1.5, 3.0
_STOP = set(
    "a an the of in on at to for and or but by with from as is are was were be been being it its this that "
    "these those which who whom whose has have had do does did not no than then also into over under between "
    "about their there they he she we you i our his her up out so such can could would should may might will "
    "shall if while".split()
)


# Spelled-out units and their symbols count as the same term ("kilometres" in a claim, "km" in the source).
_UNITS = {
    "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km", "km": "km",
    "metre": "m", "metres": "m", "meter": "m", "meters": "m", "m": "m",
    "mile": "mi", "miles": "mi", "mi": "mi", "percent": "percent", "per": "per", "cent": "cent",
    "terawatt": "twh", "twh": "twh", "gigawatt": "gw", "gw": "gw", "megawatt": "mw", "mw": "mw",
    "tonne": "t", "tonnes": "t", "ton": "t", "tons": "t", "kilogram": "kg", "kilograms": "kg", "kg": "kg",
    "billion": "billion", "bn": "billion", "million": "million", "mn": "million",
}
UNIT_TERMS = {"km", "m", "mi", "percent", "twh", "gw", "mw", "t", "kg", "billion", "million"}
UNIT_BOOST = 1.3


def _terms(text: str) -> list[str]:
    """Lower-cased content words with units normalised; bare numbers are left to the number boost."""
    text = text.lower().replace("%", " percent ").replace("per cent", " percent ")
    words = re.findall(r"[a-z]+[a-z0-9]*|\d+[a-z]+", text)  # "12km" -> "12km" -> unit split below
    out = []
    for w in words:
        if w[0].isdigit():
            w = re.sub(r"^\d+", "", w)
        w = _UNITS.get(w, w)
        if w and w not in _STOP:
            out.append(w)
    return out


def _unit_numbers(text: str) -> set[str]:
    """Units that appear right after a number, e.g. '21,196 km' -> {'km'}."""
    found = set()
    for m in re.finditer(r"\d[\d,.]*\s*(%|[a-zA-Z]+)", text):
        u = "percent" if m.group(1) == "%" else _UNITS.get(m.group(1).lower())
        if u in UNIT_TERMS:
            found.add(u)
    return found


def locate(claim: str, text: str, k: int = 5) -> list[Passage]:
    """Top-k chunks of `text` for `claim`; offsets index into `text`."""
    spans = chunk(text)
    if not spans:
        return []
    docs = [Counter(_terms(text[s:e])) for s, e in spans]
    n, avg = len(docs), sum(sum(d.values()) for d in docs) / len(docs)
    df = Counter(t for d in docs for t in d)
    query, claim_nums, claim_units = set(_terms(claim)), numbers(claim), _unit_numbers(claim)
    scored = []
    for (s, e), d in zip(spans, docs):
        dl = sum(d.values())
        score = sum(
            math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * (K1 + 1)
            / (d[t] + K1 * (1 - B + B * dl / avg))
            for t in query if t in d
        )
        shared = claim_nums & numbers(text[s:e])
        if shared:  # a specific figure such as 8,848.86 is a far stronger signal than a word
            score *= SPECIFIC_BOOST if any(_specific(x) for x in shared) else NUMBER_BOOST
        elif claim_units & _unit_numbers(text[s:e]):  # same kind of quantity, possibly a different value
            score *= UNIT_BOOST
        if score > 0:
            scored.append(Passage(text=text[s:e], start=s, end=e, score=round(score, 4)))
    ranked = sorted(scored, key=lambda p: -p.score)
    top = ranked[:k]
    # Keep one slot for the best chunk that shows the claim's figure or the same kind of quantity.
    for want in (lambda p: claim_nums & numbers(p.text), lambda p: claim_units & _unit_numbers(p.text)):
        if (claim_nums or claim_units) and not any(want(p) for p in top):
            if extra := next((p for p in ranked[k:] if want(p)), None):
                top = top[: k - 1] + [extra]
                break
    return top


def _specific(num: str) -> bool:
    """3+ significant digits or a decimal: unlikely to appear by chance."""
    digits = re.sub(r"\D", "", num).lstrip("0")
    return len(digits) >= 3 or "." in num
