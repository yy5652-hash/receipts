"""Stage 2: fetch the text of every cited source (URL extract, OpenAlex record, title search)."""

import difflib
import re
from collections.abc import Callable
from urllib.parse import urlparse

from .models import FetchedSource, Source
from .scholarly import Scholarly
from .search import Searcher, http_status
from .textutil import clean_web_text, normalize

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
        text = clean_web_text(hit.get("raw_content") or hit.get("content") or "")
        score = max(_ratio(hit.get("title") or "", src.title), _ratio(_slug(hit.get("url")), src.title))
        if text and score >= best_score:
            best, best_score = (hit, text), score
    return best


ABSTRACT_NOTE = "checked against the abstract; full text is not openly available"


def _from_record(s: Source, rec: dict, searcher: Searcher, fallback_url: str | None) -> FetchedSource:
    """A source whose OpenAlex record exists: its open full text if Tavily can read it, else its abstract."""
    url = rec.get("url") or fallback_url
    base = dict(source_id=s.id, url=url, title=rec.get("title") or s.title, exists=True)
    if rec.get("url") and (text := clean_web_text(searcher.extract([rec["url"]]).get(rec["url"]) or "")):
        return FetchedSource(**base, status="ok", text=text, via="tavily_extract")
    if abstract := clean_web_text(rec.get("abstract") or ""):
        return FetchedSource(**base, status="ok", text=abstract, via="openalex", coverage="abstract", note=ABSTRACT_NOTE)
    return FetchedSource(**base, status="not_found", via="openalex",
                         note="record exists in OpenAlex but no text or abstract is available")


def fetch_sources(
    sources: list[Source],
    searcher: Searcher,
    *,
    status_fn: Callable[[str], int | None] = http_status,
    openalex: Scholarly | None = None,
) -> list[FetchedSource]:
    """One FetchedSource per source, same order.

    Order per source: the cited URL (Tavily extract; a dead link ends the search), then the
    OpenAlex record by DOI ("missing" ends it) or by title (full text if open, else the abstract),
    then a Tavily title search. Without `openalex` a DOI is fetched through doi.org instead.
    """
    doi_url = {s.id: f"https://doi.org/{s.doi}" if s.doi else None for s in sources}
    urls = {s.id: s.url or (None if openalex else doi_url[s.id]) for s in sources}
    pages = searcher.extract(list(dict.fromkeys(u for u in urls.values() if u)))
    out: list[FetchedSource] = []
    for s in sources:
        url = urls[s.id]
        shown = s.url or doi_url[s.id]
        base = dict(source_id=s.id, url=shown, title=s.title)
        text = clean_web_text(pages.get(url) or "") if url else ""
        if text:
            out.append(FetchedSource(**base, status="ok", text=text, via="tavily_extract", exists=True))
            continue
        reachable = False  # the link answered, we just could not read it
        if url:
            code = status_fn(url)
            if code in (404, 410) or code is None:
                note = "link did not resolve" if code is None else f"link returned HTTP {code}"
                out.append(FetchedSource(**base, status="dead_link", note=note))
                continue
            reachable = code < 500
        rec = None
        if openalex and s.doi:
            rec = openalex.by_doi(s.doi)
            if rec == "missing":
                out.append(FetchedSource(**base, status="not_found", note="DOI is not registered", exists=False))
                continue
        if openalex and rec is None and s.title:
            rec = openalex.by_title(s.title, _surname(s.authors[0]) if s.authors else None, s.year)
        if rec:
            out.append(_from_record(s, rec, searcher, shown))
            continue
        if s.title and (found := _title_search(s, searcher)):
            hit, text = found
            out.append(FetchedSource(source_id=s.id, status="ok", url=hit.get("url") or shown,
                                     title=hit.get("title") or s.title, text=text, via="tavily_search", exists=True))
            continue
        if s.title:
            note = "no record in OpenAlex and no matching web page" if openalex else "no matching source found by title search"
        elif reachable:
            note = "the link answers but its text could not be read"
        else:
            note = "the DOI could not be looked up" if openalex and s.doi else "no url or title to look up"
        exists = True if reachable else (False if s.title else None)  # a title lookup ran: no hit means no record
        out.append(FetchedSource(**base, status="not_found", note=note, exists=exists))
    return out
