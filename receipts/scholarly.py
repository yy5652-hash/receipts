"""Scholarly lookup via OpenAlex (free, no key): a DOI or a title -> bibliographic record with abstract."""

import difflib
import re
import time
from typing import Literal, Protocol
from urllib.parse import quote

import httpx

from .search import DiskCache
from .textutil import normalize

OPENALEX_URL = "https://api.openalex.org"
TITLE_MATCH = 0.85  # minimum normalised-title ratio for by_title to accept a record
YEAR_SLACK = 1  # a cited year may be off by one (online-first vs issue year)
_MISS = object()


class Scholarly(Protocol):
    def by_doi(self, doi: str) -> dict | None | Literal["missing"]:
        """The record, "missing" if the DOI is not registered, None if it could not be looked up."""
        ...

    def by_title(self, title: str, first_author: str | None = None, year: int | None = None) -> dict | None:
        """The record whose title matches, or None."""
        ...


def _abstract(index: dict | None) -> str:
    """Rebuild the abstract from OpenAlex's {word: [positions]} inverted index."""
    words = {pos: word for word, positions in (index or {}).items() for pos in positions}
    return " ".join(words[p] for p in sorted(words))


def _record(w: dict) -> dict:
    """The fields Receipts uses from one OpenAlex work."""
    doi = (w.get("doi") or "").removeprefix("https://doi.org/")
    best = w.get("best_oa_location") or {}
    url = best.get("pdf_url") or best.get("landing_page_url") or (w.get("open_access") or {}).get("oa_url")
    return {
        "title": w.get("title") or w.get("display_name") or "",
        "doi": doi,
        "year": w.get("publication_year"),
        "authors": [n for a in w.get("authorships") or [] if (n := (a.get("author") or {}).get("display_name"))],
        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
        "url": url or (f"https://doi.org/{doi}" if doi else ""),
        "abstract": _abstract(w.get("abstract_inverted_index")),
    }


class OpenAlex:
    """OpenAlex REST client. Every answer is cached, so each DOI / title is looked up once."""

    def __init__(self, cache: DiskCache | None = None, client: httpx.Client | None = None):
        self.cache = cache
        self.client = client or httpx.Client(timeout=20)

    def _get(self, path: str, params: dict | None = None) -> httpx.Response | None:
        """GET with backoff on 429/5xx/transport errors; any other status is returned as is."""
        for attempt in range(3):
            try:
                r = self.client.get(f"{OPENALEX_URL}{path}", params=params)
            except httpx.TransportError:
                r = None
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                return r
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        return None

    def by_doi(self, doi: str) -> dict | None | Literal["missing"]:
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
        key = f"openalex:doi:{doi.lower()}"
        hit = self.cache.get(key, _MISS) if self.cache else _MISS
        if hit is not _MISS:
            return hit
        r = self._get(f"/works/doi:{quote(doi, safe='/:;()')}")
        if r is None or r.status_code not in (200, 404):  # transient or unexpected: do not cache
            return None
        out = _record(r.json()) if r.status_code == 200 else "missing"
        if self.cache:
            self.cache.set(key, out)
        return out

    def by_title(self, title: str, first_author: str | None = None, year: int | None = None) -> dict | None:
        key = f"openalex:title:{title}"
        records = self.cache.get(key, _MISS) if self.cache else _MISS
        if records is _MISS:
            query = re.sub(r"[^\w\s'-]", " ", title)  # OpenAlex rejects wildcards such as ? and *
            r = self._get("/works", {"search": " ".join(query.split()), "per-page": 5})
            if r is None or r.status_code != 200:
                return None
            records = [_record(w) for w in r.json().get("results", [])]
            if self.cache:
                self.cache.set(key, records)
        scored = []
        for rec in records:
            if year is not None and (rec["year"] is None or abs(rec["year"] - year) > YEAR_SLACK):
                continue
            ratio = difflib.SequenceMatcher(None, normalize(title), normalize(rec["title"])).ratio()
            if ratio >= TITLE_MATCH:  # the cited first author breaks ties between equally close titles
                by_author = bool(first_author) and first_author.lower() in " ".join(rec["authors"]).lower()
                scored.append((ratio, by_author, rec))
        best = max(scored, key=lambda t: t[:2], default=None)
        return best[2] if best else None
