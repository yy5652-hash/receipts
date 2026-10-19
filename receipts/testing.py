"""Offline fakes: a deterministic LLM and a searcher backed by a JSON fixture. No network."""

import json
import re
import threading
from pathlib import Path
from typing import Literal

from .models import Usage
from .textutil import AUTHOR_YEAR_RE, numbers, split_sentences, strip_citations

_STOP = set(
    "a an the of in on at to for and or but by with from as is are was were be been it its this that "
    "these those which who has have had not no than then also into over under between about".split()
)


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) > 2 and w not in _STOP:
            for suf in ("ing", "ed", "s"):  # crude stemming: plural / tense variants still overlap
                if len(w) > len(suf) + 3 and w.endswith(suf) and not w.endswith("ss"):
                    w = w[: -len(suf)]
                    break
            out.add(w)
    return out


class FakeSearcher:
    """Searcher over fixtures: `pages` maps url -> text (None = extraction fails)."""

    def __init__(self, fixtures: dict[str, str | None], search_index: list[dict] | None = None):
        self.fixtures = fixtures
        self.search_index = search_index or []
        self.extract_calls: list[list[str]] = []
        self.search_calls: list[str] = []

    def extract(self, urls: list[str]) -> dict[str, str | None]:
        self.extract_calls.append(list(urls))
        return {u: self.fixtures.get(u) for u in urls}

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        self.search_calls.append(query)
        q = _tokens(query)
        scored = []
        for r in self.search_index:
            score = len(q & _tokens(f"{r.get('title', '')} {r.get('content', '')}"))
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [
            {"url": r.get("url"), "title": r.get("title"), "content": r.get("content", ""),
             "raw_content": r.get("raw_content")}
            for _, r in scored[:max_results]
        ]


class FakeOpenAlex:
    """Scholarly lookup over fixtures: `dois` maps DOI -> record (None = lookup failed; unknown DOIs are "missing"),
    `titles` maps title -> record."""

    def __init__(self, dois: dict[str, dict | None] | None = None, titles: dict[str, dict] | None = None,
                 arxiv_titles: dict[str, dict] | None = None):
        self.dois = dois or {}
        self.titles = titles or {}
        self.arxiv_titles = arxiv_titles or {}
        self.calls: list[tuple] = []

    def by_doi(self, doi: str) -> dict | None | Literal["missing"]:
        self.calls.append(("doi", doi))
        return self.dois.get(doi, "missing")

    def by_title(self, title: str, first_author: str | None = None, year: int | None = None) -> dict | None:
        self.calls.append(("title", title, first_author, year))
        return self.titles.get(title)

    def arxiv(self, title: str) -> dict | None:
        self.calls.append(("arxiv", title))
        return self.arxiv_titles.get(title)


def load_fixtures(path: str | Path):
    """Read a fixtures JSON ({"pages", "search_index", "status"}) -> (FakeSearcher, status_fn)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    status = data.get("status", {})
    return FakeSearcher(data.get("pages", {}), data.get("search_index", [])), lambda url: status.get(url)


class FakeLLM:
    """Deterministic stand-in for the LLM, driven by the structured `payload`."""

    def __init__(self):
        self.usage: dict[str, Usage] = {}
        self.calls: list[tuple[str, str]] = []  # (task, tier)
        self._lock = threading.Lock()

    def complete_json(self, *, task: str, tier: str, system: str, user: str, payload: dict,
                      think: bool | None = None) -> dict:
        out = getattr(self, f"_{task}")(payload)
        with self._lock:
            self.calls.append((task, tier))
            u = self.usage.setdefault(tier, Usage(tier=tier, model=f"fake-{tier}"))
            u.calls += 1
            u.prompt_tokens += len(system + user) // 4
            u.completion_tokens += len(json.dumps(out)) // 4
        return out

    # parse_sources -------------------------------------------------------
    def _parse_sources(self, payload: dict) -> dict:
        res = []
        for e in payload["entries"]:
            text = re.sub(r"^\s*(?:\[\^?\d+\]:?|\d+[.)]|[-*+])\s*", "", e["raw"])
            quoted = re.search(r'["“](.+?)[,.]?["”]', text)
            year = re.search(r"\b(?:19|20)\d{2}\b", text.replace(quoted.group(0), " ") if quoted else text)
            title = None
            if quoted:
                title = quoted.group(1).strip()
            elif year:
                m = re.match(r"\)?\.?\s*([^.]+)", text[year.end():])
                title = m.group(1).strip() if m else None
            cut = min([i for i in (text.find("("), quoted.start() if quoted else -1) if i >= 0] or [0])
            names = re.sub(r"[\s(,]+$", "", text[:cut])
            authors = [a.strip(" ,") for a in re.split(r"\s*&\s*|\s+and\s+", names) if a.strip(" ,")]
            authors = [a[:-1] if a.endswith(".") and len(a.split()[-1]) > 2 else a for a in authors]
            res.append({"id": e["id"], "title": title, "authors": authors, "year": int(year.group()) if year else None})
        return {"sources": res}

    # extract_claims ------------------------------------------------------
    def _extract_claims(self, payload: dict) -> dict:
        claims = []
        for s in payload["sentences"]:
            ids = list(s.get("marker_source_ids") or [])
            if not ids:
                for m in AUTHOR_YEAR_RE.finditer(s["text"]):
                    for part in m.group()[1:-1].split(";"):
                        name = re.match(r"\s*([A-Z][\w'’\-]+)", part)
                        year = re.search(r"(?:19|20)\d{2}", part)
                        for src in payload["sources"]:
                            if name and year and name.group(1) in src["raw"] and year.group() in src["raw"]:
                                ids.append(src["id"])
            claims.append({"index": s["index"], "claim": strip_citations(s["text"]), "source_ids": ids})
        return {"claims": claims}

    # judge ---------------------------------------------------------------
    def _judge(self, payload: dict) -> dict:
        claim = payload["claim"]
        ctoks, cnums = _tokens(claim), numbers(claim)
        best, best_overlap = "", 0.0
        for passage in payload["passages"]:
            for s, e in split_sentences(passage):
                sent = passage[s:e].strip()
                overlap = len(ctoks & _tokens(sent)) / max(1, len(ctoks))
                if overlap > best_overlap:
                    best, best_overlap = sent, overlap
        snums = numbers(best)
        if best_overlap >= 0.6 and cnums <= snums:
            label, conf = "supported", 0.9
        elif best_overlap >= 0.6 and snums and not cnums <= snums:
            label, conf = "contradicted", 0.8
        elif best_overlap >= 0.35:
            label, conf = "partial", 0.6
        else:
            label, conf = "not_in_source", 0.7
        return {
            "label": label,
            "confidence": conf,
            "quote": "" if label == "not_in_source" else best,
            "rationale": f"token overlap {best_overlap:.2f} with the closest source sentence",
        }
