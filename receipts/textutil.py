"""Text helpers: sentence spans, body/reference split, quote matching, chunking."""

import difflib
import re
import string
import unicodedata

# --- sentence splitting -----------------------------------------------------

# Abbreviations whose period never ends a sentence / may end one (only before a sentence starter).
_NEVER = r"e\.g|i\.e|dr|mr|mrs|ms|prof|st|vs|cf|fig|figs|eq|vol|approx"
_MAYBE = r"et\s+al|etc|u\.s|u\.k|e\.u|ph\.d|a\.m|p\.m|inc|ltd|jr|sr"
_NEVER_RE = re.compile(rf"(?:^|[\s(\[\"'“])(?:{_NEVER})\.$", re.I)
_MAYBE_RE = re.compile(rf"(?:^|[\s(\[\"'“])(?:{_MAYBE})\.$", re.I)
_INITIAL_RE = re.compile(r"(?:^|[\s(\[])[A-Z]\.$")
_STARTERS = {
    "the", "a", "an", "in", "it", "this", "that", "these", "those", "they", "we", "he", "she",
    "however", "but", "and", "for", "on", "at", "as", "if", "its", "their", "our", "there", "then",
    "such", "while", "although", "when", "other", "some", "many", "most", "researchers",
}
# Terminator run plus any closers / citation markers glued to it: `.[1]`, `.)`, `."`, `.**`.
_TERM_RE = re.compile(
    r"""[.!?]+(?:["'”’)\]*_]|\[\^?\d+(?:\s*[,–-]\s*\d+)*\]|\[\^[^\]\s]+\])*"""
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[\^[^\]\s]+\]:)\s+")
_BULLET_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[\^[^\]\s]+\]:)\s+")


def _is_abbrev(prefix: str, rest: str) -> bool:
    """Is the period closing `prefix` an abbreviation dot rather than a sentence end?"""
    if _NEVER_RE.search(prefix) or _INITIAL_RE.search(prefix):
        return True
    if _MAYBE_RE.search(prefix):
        m = re.match(r"\w+", rest)
        return not (m and m.group().lower() in _STARTERS)
    return False


def _skip_bracket(text: str, i: int, e: int) -> int:
    """Jump over `[...]` (and a directly following `(...)`) so dots inside links never split."""
    j = text.find("]", i, min(e, i + 300))
    if j == -1:
        return i + 1
    if j + 1 < e and text[j + 1] == "(":
        k = text.find(")", j, min(e, j + 500))
        if k != -1:
            return k + 1
    return j + 1


def _split_block(text: str, s: int, e: int, scan_from: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start, i = s, scan_from
    while i < e:
        ch = text[i]
        if ch == "[":
            i = _skip_bracket(text, i, e)
            continue
        if ch in ".!?":
            end = _TERM_RE.match(text, i, e).end()
            k = end
            while k < e and text[k].isspace():
                k += 1
            if end < k < e and not text[k].islower():
                if not (ch == "." and _is_abbrev(text[start : i + 1], text[k:e])):
                    spans.append((start, end))
                    start = i = k
                    continue
            i = end
            continue
        i += 1
    if start < e:
        spans.append((start, e))
    return spans


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Char spans of sentences. Headings and bullets start their own units."""
    spans: list[tuple[int, int]] = []
    blocks: list[tuple[int, int, int, bool]] = []  # start, end, scan_from, is_heading
    cur: list[int] | None = None  # [start, end, scan_from]

    def flush():
        nonlocal cur
        if cur:
            blocks.append((cur[0], cur[1], cur[2], False))
        cur = None

    pos = 0
    for line in text.splitlines(keepends=True):
        ls, le = pos, pos + len(line.rstrip())
        pos += len(line)
        if not line.strip():
            flush()
        elif _HEADING_RE.match(line):
            flush()
            blocks.append((ls + len(line) - len(line.lstrip()), le, le, True))
        elif _BULLET_RE.match(line):
            flush()
            m = _BULLET_MARK_RE.match(line)
            cur = [ls + len(line) - len(line.lstrip()), le, ls + (m.end() if m else 0)]
        elif cur is None:
            cur = [ls + len(line) - len(line.lstrip()), le, ls + len(line) - len(line.lstrip())]
        else:
            cur[1] = le
    flush()
    for s, e, scan_from, is_heading in blocks:
        spans.extend([(s, e)] if is_heading else _split_block(text, s, e, scan_from))
    spans.sort()
    return spans


# --- body / references ------------------------------------------------------

_REF_HEADING_RE = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*"
    r"(?:references|sources|bibliography|works cited|citations|notes)"
    r"[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*:?[ \t]*$",
    re.I | re.M,
)


def split_body_and_references(doc: str) -> tuple[int, int | None]:
    """(body_end, refs_start); the last references-style heading wins."""
    last = None
    for m in _REF_HEADING_RE.finditer(doc):
        last = m
    if last is None:
        return len(doc), None
    refs_start = last.end()
    if refs_start < len(doc) and doc[refs_start] == "\n":
        refs_start += 1
    return last.start(), refs_start


# --- normalisation and quote matching ----------------------------------------

_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
                         "‘": "'", "’": "'", "‚": "'", "‛": "'", "`": "'"})
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−"})
_STRIP = string.punctuation + string.whitespace + "…"


def normalize(s: str) -> str:
    """Lowercase, unify quotes/dashes, collapse whitespace, strip punctuation at the ends."""
    s = unicodedata.normalize("NFKC", s).translate(_QUOTES).translate(_DASHES)
    s = re.sub(r"[​-‍﻿­]", "", s).lower()
    return re.sub(r"\s+", " ", s).strip(_STRIP)


def contains_quote(haystack: str, quote: str) -> bool:
    """Normalised containment; quotes of >= 20 chars may also match fuzzily (ratio >= 0.9)."""
    nq = normalize(quote)
    if not nq:
        return False
    nh = normalize(haystack)
    if nq in nh:
        return True
    if len(nq) < 20:
        return False
    words, qn = nh.split(" "), len(nq.split(" "))
    for n in {max(1, qn - 1), qn, qn + 1}:
        for i in range(max(1, len(words) - n + 1)):
            win = " ".join(words[i : i + n])
            sm = difflib.SequenceMatcher(None, win, nq, autojunk=False)
            if sm.real_quick_ratio() >= 0.9 and sm.quick_ratio() >= 0.9 and sm.ratio() >= 0.9:
                return True
    return False


# --- chunking ----------------------------------------------------------------


def _paragraphs(text: str) -> list[tuple[int, int]]:
    out, pos = [], 0
    for m in list(re.finditer(r"\n[ \t]*\n", text)) + [re.search(r"\Z", text)]:
        seg = text[pos : m.start()]
        lead = len(seg) - len(seg.lstrip())
        if seg.strip():
            out.append((pos + lead, pos + len(seg.rstrip())))
        pos = m.end()
    return out


def _pieces(text: str, s: int, e: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Split one oversize paragraph at whitespace into overlapping pieces."""
    out, pos = [], s
    while pos < e:
        end = min(pos + size, e)
        if end < e:
            cut = max(text.rfind(" ", pos + size // 2, end), text.rfind("\n", pos + size // 2, end))
            end = cut if cut > pos else end
        out.append((pos, end))
        if end >= e:
            break
        pos = max(end - overlap, pos + 1)
        while pos < end and not text[pos - 1].isspace():
            pos += 1
    return out


def chunk(text: str, size: int = 700, overlap: int = 150) -> list[tuple[int, int]]:
    """Paragraph-aware spans of about `size` chars, consecutive spans overlapping by ~`overlap`."""
    units: list[tuple[int, int]] = []
    for s, e in _paragraphs(text):
        units.extend([(s, e)] if e - s <= size else _pieces(text, s, e, size, overlap))
    chunks, i, n = [], 0, len(units)
    while i < n:
        j = i
        while j + 1 < n and units[j + 1][1] - units[i][0] <= size:
            j += 1
        chunks.append((units[i][0], units[j][1]))
        if j >= n - 1:
            break
        k = j + 1
        while k - 1 > i and units[j][1] - units[k - 1][0] <= overlap:
            k -= 1
        i = k
    return chunks


# --- citations and numbers ---------------------------------------------------

MARKER_RE = re.compile(r"\[(\^?\d+(?:\s*[,–-]\s*\d+)*)\]")  # [1]  [1, 2]  [3–5]  [^1]
LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")  # [text](url)
_AY_ONE = (
    r"(?!(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Q[1-4]|In|By|Since|Fiscal|FY)\b)[A-Z][\w'’\-]+(?:\s+(?:et\s+al\.?|and|&)\s*(?:[A-Z][\w'’\-]+)?)*"
    r",?\s+(?:19|20)\d{2}[a-z]?"
)
# Author-year parenthetical: (Chen, 2020)  (Chen et al. 2020)  (Smith & Lee, 2021; Wu, 2019)
AUTHOR_YEAR_RE = re.compile(rf"\(\s*{_AY_ONE}(?:\s*;\s*{_AY_ONE})*\s*\)")
_LEAD_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")


def strip_citations(s: str) -> str:
    """Sentence without citation markers / author-year parentheticals; links keep their anchor text."""
    s = LINK_RE.sub(r"\1", s)
    s = re.sub(r"\s*" + MARKER_RE.pattern, "", s)
    s = re.sub(r"\s*" + AUTHOR_YEAR_RE.pattern, "", s)
    s = _LEAD_RE.sub("", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def numbers(text: str) -> set[str]:
    """Numeric tokens in text; thousands separators removed (1,200 -> 1200)."""
    out = set()
    for tok in re.findall(r"\d+(?:[.,]\d+)*", text):
        out.add(tok.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", tok) else tok)
    return out
