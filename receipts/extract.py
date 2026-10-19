"""Stage 1: from the report text to Source entries and the claims that cite them."""

import json
import re

from .llm import LLM
from .models import Claim, Source
from .textutil import (
    AUTHOR_YEAR_RE,
    LINK_RE,
    MARKER_RE,
    split_body_and_references,
    split_sentences,
    strip_citations,
)

BATCH_SIZE = 12

PARSE_SOURCES_SYSTEM = """You parse the entries of a report's reference list.
For every entry in the input, extract its bibliographic fields. Copy them from the entry text; \
never invent or guess a field that is not written there (use null / [] instead).
Return ONLY a JSON object of exactly this shape:
{"sources": [{"id": "<the entry id from the input>", "title": "<title of the cited work>" | null, \
"authors": ["<author as written>", ...], "year": <4-digit integer> | null}]}
Return one object per input entry, using the same ids. Do not add entries."""

EXTRACT_CLAIMS_SYSTEM = """You turn citing sentences of a research report into checkable claims.
Each input sentence cites one or more sources. For every sentence write ONE claim: the assertion \
the sentence attributes to its source(s).
Rules:
- Decontextualise: replace pronouns and vague references ("it", "this trend", "the study") with the \
concrete entity, using the sentence's "context" (the previous sentence).
- Keep every number, date, unit and proper name exactly as written. Do not round, convert or add facts.
- Remove citation markers, links and author-year parentheticals from the claim text.
- Exactly one claim per sentence, as a single self-contained sentence.
- "source_ids": the ids of the sources the sentence cites, chosen only from the input "sources" list. \
Start from "marker_source_ids" (already resolved from numeric markers and links). For author-year \
citations such as (Chen et al., 2020), match the author and year to the source entry text. \
Never invent a source id.
Return ONLY a JSON object of exactly this shape:
{"claims": [{"index": <the sentence index from the input>, "claim": "<claim text>", \
"source_ids": ["<source id>", ...]}]}"""

_REF_NUM = re.compile(r"^\s*\[(\d+|\^[\w-]+)\](?::|\s)\s*(.*)$")  # [1] text   [^1]: text   [^curie]: text
_REF_DOTTED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")  # 1. text
_REF_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_BULLET_LEAD = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_URL = re.compile(r"https?://\S+")
_DOI = re.compile(r"\b10\.\d{4,9}/\S+")


def _clean_url(u: str) -> str:
    u = u.rstrip(".,;:*_'\"]>")
    while u.endswith(")") and u.count(")") > u.count("("):
        u = u[:-1].rstrip(".,;:*_'\"]>")
    return u


def _first(rx: re.Pattern, text: str, clean=_clean_url) -> str | None:
    m = rx.search(text)
    return clean(m.group()) if m else None


def _expand(inner: str) -> list[str]:
    """'1, 2' -> ['1','2'];  '3–5' -> ['3','4','5'];  '^1' -> ['^1']."""
    if inner.startswith("^"):
        return [inner]
    out: list[str] = []
    for part in re.split(r"\s*,\s*", inner.strip()):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m and 0 <= int(m[2]) - int(m[1]) <= 50:
            out += [str(i) for i in range(int(m[1]), int(m[2]) + 1)]
        else:
            out.append(part)
    return out


def _starts_plain_entry(line: str, prev: str) -> bool:
    """An unnumbered reference line starts a new entry when the previous one looks finished
    (ends with '.', ')' or a URL/DOI) and this one starts like an author or organisation."""
    finished = bool(re.search(r"(?:[.)]|https?://\S+|10\.\d{4,9}/\S+)\s*$", prev))
    return finished and bool(re.match(r"\s*[A-Z0-9\"“'(]", line))


def _reference_entries(refs: str, numeric_body: bool) -> list[tuple[str | None, str]]:
    """(marker, raw text) per entry of the reference list."""
    entries: list[list] = []
    prev_blank = True
    bullets = 0
    for line in refs.splitlines():
        if not line.strip():
            prev_blank = True
            continue
        if line.lstrip().startswith("#"):
            prev_blank = True
            continue
        marker, text, new = None, line.strip(), True
        if m := _REF_NUM.match(line) or _REF_DOTTED.match(line):
            marker, text = m.group(1), m.group(2).strip()
        elif m := _REF_BULLET.match(line):
            bullets += 1
            marker, text = (None if numeric_body else f"b{bullets}"), m.group(1).strip()
        elif prev_blank or not entries or _starts_plain_entry(line, entries[-1][1]):
            pass  # unnumbered entry (APA, Chicago...): one per paragraph or per complete line
        else:
            new = False
        if new:
            entries.append([marker, text])
        elif entries and not prev_blank:
            entries[-1][1] += " " + line.strip()
        prev_blank = False
    return [(m, t) for m, t in entries if t]


def _merge_parsed(sources: list[Source], parsed: dict) -> None:
    by_id = {s.id: s for s in sources}
    for item in parsed.get("sources", []) if isinstance(parsed, dict) else []:
        s = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if s is None:
            continue
        title = item.get("title")
        if s.title is None and isinstance(title, str) and title.strip():
            s.title = title.strip()
        authors = item.get("authors")
        if not s.authors and isinstance(authors, list):
            s.authors = [a for a in authors if isinstance(a, str) and a.strip()]
        year = item.get("year")
        if s.year is None and re.fullmatch(r"(?:19|20)\d{2}", str(year or "")):
            s.year = int(year)


def extract(doc: str, llm: LLM) -> tuple[list[Source], list[Claim]]:
    body_end, refs_start = split_body_and_references(doc)
    if refs_start is None and (fn := re.search(r"^\[\^\w+\]:", doc, re.M)):  # footnotes without a heading
        body_end = refs_start = fn.start()
    body = doc[:body_end]
    numeric_body = bool(MARKER_RE.search(body))

    # 1. reference entries (regex fields), then inline links
    sources: list[Source] = []
    marker_to_id: dict[str, str] = {}
    for marker, raw in _reference_entries(doc[refs_start:] if refs_start is not None else "", numeric_body):
        s = Source(id=f"s{len(sources) + 1}", marker=marker, raw=raw,
                   url=_first(_URL, raw), doi=_first(_DOI, raw, lambda d: d.rstrip(".,;:)>]")))
        sources.append(s)
        if marker:
            marker_to_id.setdefault(marker, s.id)
    n_refs = len(sources)
    url_to_id = {s.url: s.id for s in sources if s.url}
    for m in LINK_RE.finditer(body):
        url = _clean_url(m.group(2))
        if url not in url_to_id:
            s = Source(id=f"s{len(sources) + 1}", raw=m.group(0), url=url, title=m.group(1).strip())
            sources.append(s)
            url_to_id[url] = s.id

    # 2. fill title / authors / year of the reference entries with one nano call
    if n_refs:
        payload = {"entries": [{"id": s.id, "raw": s.raw} for s in sources[:n_refs]]}
        try:
            _merge_parsed(sources, llm.complete_json(
                task="parse_sources", tier="nano", system=PARSE_SOURCES_SYSTEM,
                user=json.dumps(payload, ensure_ascii=False), payload=payload, think=False))
        except ValueError:  # unparseable model output: keep the regex fields
            pass

    # 3. candidate sentences with their deterministic marker -> source mapping
    spans = split_sentences(body)
    cands: list[dict] = []
    for i, (s, e) in enumerate(spans):
        text = body[s:e]
        if text.lstrip().startswith("#"):
            continue
        ids: list[str] = []
        for m in MARKER_RE.finditer(text):
            ids += [marker_to_id[k] for k in _expand(m.group(1)) if k in marker_to_id]
        ids += [url_to_id[_clean_url(m.group(2))] for m in LINK_RE.finditer(text)]
        ids = list(dict.fromkeys(ids))
        author_year = bool(AUTHOR_YEAR_RE.search(text))
        if not ids and not author_year:
            continue
        lead = _BULLET_LEAD.match(text)
        if lead:
            s += lead.end()
        prev = spans[i - 1] if i else None
        cands.append({"start": s, "end": e, "ids": ids, "author_year": author_year,
                      "context": body[prev[0]:prev[1]].strip() if prev else ""})

    # 4. claims from the LLM in batches, validated against the deterministic mapping
    known = {s.id for s in sources}
    src_payload = [{"id": s.id, "raw": s.raw} for s in sources]
    claims: list[Claim] = []
    contexts: list[str] = []
    for b in range(0, len(cands), BATCH_SIZE):
        batch = cands[b : b + BATCH_SIZE]
        payload = {
            "sentences": [{"index": b + j, "text": doc[c["start"]:c["end"]], "context": c["context"],
                           "marker_source_ids": c["ids"]} for j, c in enumerate(batch)],
            "sources": src_payload,
        }
        try:
            out = llm.complete_json(task="extract_claims", tier="nano", system=EXTRACT_CLAIMS_SYSTEM,
                                    user=json.dumps(payload, ensure_ascii=False), payload=payload, think=False)
        except ValueError:
            out = {}
        returned = {}
        for item in out.get("claims", []) if isinstance(out, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                returned.setdefault(item["index"], item)
        for j, c in enumerate(batch):
            item = returned.get(b + j, {})
            sentence = doc[c["start"]:c["end"]]
            llm_ids = [i for i in item.get("source_ids") or [] if isinstance(i, str) and i in known]
            ids = list(c["ids"])
            if c["author_year"] or not ids:  # the LLM resolves author-year citations
                ids += [i for i in llm_ids if i not in ids]
            if not ids:
                continue
            text = item.get("claim")
            text = text.strip() if isinstance(text, str) and text.strip() else strip_citations(sentence)
            claims.append(Claim(id=f"c{len(claims) + 1}", sentence=sentence, start=c["start"], end=c["end"],
                                claim=text, source_ids=list(dict.fromkeys(ids))))
            contexts.append(c["context"])
    _repair_vague(claims, contexts, llm)
    return sources, claims


# A claim that still leans on its neighbours ("It reached...", "this architecture") cannot be checked alone.
VAGUE = re.compile(
    r"^(?:it|its|they|their|them|this|these|those|that|she|he|his|her|such|the (?:study|report|paper|authors|"
    r"survey|model|company|architecture|former|latter))\b"
    r"|\b(?:this|these|those) (?:architecture|trend|model|study|approach|method|figure|number|technology|"
    r"policy|report|paper|system|company|growth|increase)\b",
    re.I,
)


def _repair_vague(claims: list[Claim], contexts: list[str], llm: LLM) -> None:
    """Nano extracts every claim; the few it leaves vague are rewritten once by Super without reasoning."""
    todo = [i for i, c in enumerate(claims) if VAGUE.search(c.claim)]
    if not todo:
        return
    payload = {
        "sentences": [{"index": i, "text": claims[i].sentence, "context": contexts[i],
                       "marker_source_ids": claims[i].source_ids} for i in todo],
        "sources": [],
    }
    try:
        out = llm.complete_json(task="extract_claims", tier="super", system=EXTRACT_CLAIMS_SYSTEM,
                                user=json.dumps(payload, ensure_ascii=False), payload=payload, think=False)
    except ValueError:
        return
    for item in out.get("claims", []) if isinstance(out, dict) else []:
        i, text = (item.get("index"), item.get("claim")) if isinstance(item, dict) else (None, None)
        if i in todo and isinstance(text, str) and text.strip() and not VAGUE.search(text):
            claims[i].claim = text.strip()
