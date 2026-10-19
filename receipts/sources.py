"""Stage 2: fetch the text of every cited source (URL extract, OpenAlex record, title search)."""

import difflib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .models import FetchedSource, Source
from .scholarly import Scholarly
from .search import Searcher, http_status
from .textutil import clean_web_text, normalize

TITLE_MATCH = 0.8  # minimum difflib ratio between a search hit and the cited title
ACADEMIC_TITLE_MATCH = 0.9  # stricter for papers OpenAlex has no record of: a near title is another paper
SHORT_PAGE = 3000  # a landing page this short is metadata, not the work itself
_ACADEMIC = re.compile(
    r"\b(?:journal|proceedings|transactions|conference|symposium|workshop|vol\.|pp\.|arxiv|preprint|"
    r"neurips|icml|iclr|acl|naacl|emnlp|nature|science|lancet|jama|nejm|advances in|et al)\b"
    r"|\b\d+\(\d+\)|\(\d{4}[a-z]?\)\.",  # 14(2)   Author, A. (2020).
    re.I,
)


def _looks_academic(s: Source) -> bool:
    return bool(s.doi or _ACADEMIC.search(s.raw))


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _surname(author: str) -> str:
    return author.split(",")[0].strip() if "," in author else author.split()[-1]


def _slug(url: str | None) -> str:
    path = urlparse(url or "").path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", re.sub(r"\.\w{2,4}$", "", path))


def _title_search(src: Source, searcher: Searcher, min_ratio: float = TITLE_MATCH) -> tuple[dict, str] | None:
    """Best search hit whose title (or URL slug) resembles the cited title, with its text."""
    query = " ".join(filter(None, [
        f'"{src.title}"', _surname(src.authors[0]) if src.authors else None, str(src.year) if src.year else None,
    ]))
    best, best_score = None, min_ratio
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


def _arxiv_text(s: Source, title: str, scholarly: Scholarly | None, searcher: Searcher) -> FetchedSource | None:
    """The arXiv version of a paper: full text from its PDF when Tavily can read it, else the arXiv abstract."""
    finder = getattr(scholarly, "arxiv", None)
    paper = finder(title) if finder else None
    if not paper:
        return None
    base = dict(source_id=s.id, status="ok", url=paper["abs_url"], title=paper["title"] or title, exists=True)
    if full := clean_web_text(searcher.extract([paper["pdf_url"]]).get(paper["pdf_url"]) or ""):
        return FetchedSource(**base, text=full, via="arxiv", note="checked against the arXiv version")
    if abstract := clean_web_text(paper.get("abstract") or ""):
        return FetchedSource(**base, text=abstract, via="arxiv", coverage="abstract",
                             note="checked against the arXiv abstract")
    return None


def fetch_sources(
    sources: list[Source],
    searcher: Searcher,
    *,
    status_fn: Callable[[str], int | None] = http_status,
    openalex: Scholarly | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> list[FetchedSource]:
    """One FetchedSource per source, same order.

    Order per source: the cited URL (Tavily extract; a dead link ends the search), then the
    OpenAlex record by DOI ("missing" ends it) or by title (full text if open, else the abstract),
    then a Tavily title search. Without `openalex` a DOI is fetched through doi.org instead.
    `on_event` receives {"type": "source", "fetched": ...} (without the text) as each source resolves.
    """
    doi_url = {s.id: f"https://doi.org/{s.doi}" if s.doi else None for s in sources}
    urls = {s.id: s.url or (None if openalex else doi_url[s.id]) for s in sources}
    pages = searcher.extract(list(dict.fromkeys(u for u in urls.values() if u)))

    def resolve(s: Source) -> FetchedSource:
        url = urls[s.id]
        shown = s.url or doi_url[s.id]
        base = dict(source_id=s.id, url=shown, title=s.title)
        text = clean_web_text(pages.get(url) or "") if url else ""
        academic = openalex is not None and _looks_academic(s)
        landing = bool(text) and (len(text) < SHORT_PAGE or "doi.org/" in (url or ""))
        if text and not (landing and academic):
            return FetchedSource(**base, status="ok", text=text, via="tavily_extract", exists=True)
        reachable = bool(text)  # the link answered; a short landing page may still lead to the paper
        if url and not text:
            code = status_fn(url)
            if code in (404, 410) or code is None:
                note = "link did not resolve" if code is None else f"link returned HTTP {code}"
                return FetchedSource(**base, status="dead_link", note=note)
            reachable = code < 500
        rec = None
        if openalex and s.doi:
            rec = openalex.by_doi(s.doi)
            if rec == "missing":
                if not text:
                    return FetchedSource(**base, status="not_found", note="DOI is not registered", exists=False)
                rec = None
        if openalex and rec is None and s.title:
            rec = openalex.by_title(s.title, _surname(s.authors[0]) if s.authors else None, s.year)
        title = (rec or {}).get("title") or s.title
        if rec:
            found = _from_record(s, rec, searcher, shown)
            if found.status == "ok" and found.via == "tavily_extract" and len(found.text) >= SHORT_PAGE:
                return found  # the published version is openly readable
            if title and (paper := _arxiv_text(s, title, openalex, searcher)) and (
                    paper.coverage == "full" or found.status != "ok"):
                return paper
            if found.status == "ok" and found.via == "openalex" and text:  # abstract + whatever the landing page says
                found.text, found.via = f"Abstract: {found.text}\n\n{text}", "tavily_extract+openalex"
            if found.status == "ok" or not text:
                return found
        elif title and academic and (paper := _arxiv_text(s, title, openalex, searcher)):
            return paper
        if text:
            return FetchedSource(**base, status="ok", text=text, via="tavily_extract", exists=True,
                                 note="only the landing page could be read")
        if s.title and (found := _title_search(s, searcher, ACADEMIC_TITLE_MATCH if academic else TITLE_MATCH)):
            hit, text = found
            return FetchedSource(source_id=s.id, status="ok", url=hit.get("url") or shown,
                                 title=hit.get("title") or s.title, text=text, via="tavily_search", exists=True)
        if s.title:
            note = "no record in OpenAlex and no matching web page" if openalex else "no matching source found by title search"
        elif reachable:
            note = "the link answers but its text could not be read"
        else:
            note = "the DOI could not be looked up" if openalex and s.doi else "no url or title to look up"
        exists = True if reachable else (False if s.title else None)  # a title lookup ran: no hit means no record
        return FetchedSource(**base, status="not_found", note=note, exists=exists)

    def resolve_and_emit(s: Source) -> FetchedSource:
        f = resolve(s)
        if on_event:
            on_event({"type": "source", "fetched": f.model_dump(mode="json", exclude={"text"})})
        return f

    with ThreadPoolExecutor(max_workers=6) as pool:  # status checks and lookups are network-bound
        return list(pool.map(resolve_and_emit, sources))
