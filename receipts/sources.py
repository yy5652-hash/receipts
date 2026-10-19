"""Stage 2: fetch the text of every cited source (URL extract, then title search)."""

import difflib
import re
from collections.abc import Callable
from urllib.parse import urlparse

from .models import FetchedSource, Source
from .search import Searcher, http_status
from .textutil import normalize

TITLE_MATCH = 0.6  # minimum difflib ratio between a search hit and the cited title


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _surname(author: str) -> str:
    return author.split(",")[0].strip() if "," in author else author.split()[-1]


def _slug(url: str | None) -> str:
    path = urlparse(url or "").path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", re.sub(r"\.\w{2,4}$", "", path))


def _title_search(src: Source, searcher: Searcher) -> tuple[dict, str] | None:
    """Best search hit whose title (or URL slug) resembles the cited title, with its text."""
    query = " ".join(filter(None, [
        f'"{src.title}"', _surname(src.authors[0]) if src.authors else None, str(src.year) if src.year else None,
    ]))
    best, best_score = None, TITLE_MATCH
    for hit in searcher.search(query, max_results=5):
        text = (hit.get("raw_content") or hit.get("content") or "").strip()
        score = max(_ratio(hit.get("title") or "", src.title), _ratio(_slug(hit.get("url")), src.title))
        if text and score >= best_score:
            best, best_score = (hit, text), score
    return best


def fetch_sources(
    sources: list[Source],
    searcher: Searcher,
    *,
    status_fn: Callable[[str], int | None] = http_status,
    scholarly_fn: Callable[[str], dict | None] | None = None,
) -> list[FetchedSource]:
    """One FetchedSource per source, same order.

    `scholarly_fn(title)` is an optional last-resort lookup (e.g. Crossref) returning
    {"url", "title", "text"} or None; it is tried only after the title search found nothing.
    """
    urls = {s.id: s.url or (f"https://doi.org/{s.doi}" if s.doi else None) for s in sources}
    pages = searcher.extract(list(dict.fromkeys(u for u in urls.values() if u)))
    out: list[FetchedSource] = []
    for s in sources:
        url = urls[s.id]
        base = dict(source_id=s.id, url=url, title=s.title)
        text = (pages.get(url) or "").strip() if url else ""
        if text:
            out.append(FetchedSource(**base, status="ok", text=text, via="tavily_extract"))
            continue
        if url:
            code = status_fn(url)
            if code in (404, 410) or code is None:
                note = "link did not resolve" if code is None else f"link returned HTTP {code}"
                out.append(FetchedSource(**base, status="dead_link", note=note))
                continue
        if s.title:
            if found := _title_search(s, searcher):
                hit, text = found
                out.append(FetchedSource(source_id=s.id, status="ok", url=hit.get("url") or url,
                                         title=hit.get("title") or s.title, text=text, via="tavily_search"))
                continue
            if scholarly_fn and (rec := scholarly_fn(s.title)) and (rec.get("text") or "").strip():
                out.append(FetchedSource(source_id=s.id, status="ok", url=rec.get("url") or url,
                                         title=rec.get("title") or s.title, text=rec["text"], via="crossref"))
                continue
        note = ("no matching source found by title search" if s.title
                else "no url or title to look up")
        out.append(FetchedSource(**base, status="not_found", note=note))
    return out
