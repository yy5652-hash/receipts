"""Stage 4: for a claim its cited source did not back, search the web for a page that does (or does not) say it."""

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from .config import Settings
from .judge import judge_one
from .llm import LLM
from .models import Claim, FetchedSource, Source, Suggestion, Verdict, best_label
from .search import Searcher
from .sources import page_title
from .textutil import clean_web_text

log = logging.getLogger("receipts.suggest")

TARGET_LABELS = ("not_in_source", "source_unavailable")  # claim-level labels that get a web search
SEARCH_RESULTS = 8  # hits requested per claim (some are skipped below)
JUDGED = 3  # hits with readable text that are judged per claim
# Not citable as evidence: user posts, social media and Q&A sites.
SKIP_HOSTS = ("medium.com", "reddit.com", "quora.com", "linkedin.com", "facebook.com", "x.com", "twitter.com",
              "pinterest.com", "youtube.com", "tiktok.com", "instagram.com", "substack.com", "github.com",
              "scribd.com", "slideshare.net", "coursehero.com", "studocu.com")
# Primary and official sources first, then encyclopedias, then everything else.
PRIMARY_HOSTS = ("arxiv.org", "doi.org", "who.int", "un.org", "oecd.org", "worldbank.org", "imf.org", "iea.org",
                 "nasa.gov", "noaa.gov", "nature.com", "science.org", "sciencedirect.com", "springer.com", "wiley.com",
                 "ncbi.nlm.nih.gov", "nih.gov", "acm.org", "ieee.org", "aclanthology.org", "neurips.cc", "openreview.net",
                 "ourworldindata.org", "europa.eu", "cdc.gov", "nber.org")
MAX_QUERY = 400  # Tavily rejects longer queries
TARGET_WORKERS = 4  # claims searched in parallel


def normalize_url(url: str) -> str:
    """Scheme and host lower-cased, fragment and trailing slash dropped: the form two links to one page share."""
    p = urlsplit(url.strip())
    path = p.path.rstrip("/")
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def suggest_targets(claims: list[Claim], verdicts: list[Verdict], limit: int) -> list[Claim]:
    """The claims whose own source did not back them (not in source / unavailable), in document order, capped."""
    return [c for c in claims if best_label(verdicts, c.id) in TARGET_LABELS][: max(0, limit)]


def cited_urls(claim: Claim, sources: list[Source], fetched: list[FetchedSource]) -> set[str]:
    """Every URL the claim already cites (as written, as resolved, and through its DOI), normalised."""
    src = {s.id: s for s in sources}
    fet = {f.source_id: f for f in fetched}
    urls: set[str] = set()
    for sid in claim.source_ids:
        s, f = src.get(sid), fet.get(sid)
        urls |= {u for u in (s.url if s else None, f.url if f else None) if u}
        if s and s.doi:
            urls.add(f"https://doi.org/{s.doi}")
    return {normalize_url(u) for u in urls}


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def source_rank(url: str | None) -> int:
    """0 primary or official (journals, preprints, .gov, .edu, agencies), 1 encyclopedia, 2 anything else."""
    host = _host(url or "")
    if _matches(host, PRIMARY_HOSTS) or any(p in host.split(".") for p in ("gov", "edu", "ac")) or host.endswith(".int"):
        return 0
    return 1 if host.endswith("wikipedia.org") else 2


def _query(claim: Claim) -> str:
    q = " ".join(claim.claim.split())
    return q if len(q) <= MAX_QUERY else q[:MAX_QUERY].rsplit(" ", 1)[0]


def _candidates(hits: list[dict], exclude: set[str]) -> list[tuple[Source, FetchedSource]]:
    """The first few hits that are not already cited and have text, each as a Source / FetchedSource pair."""
    out: list[tuple[Source, FetchedSource]] = []
    for hit in hits:
        url = hit.get("url")
        if not url or normalize_url(url) in exclude or _matches(_host(url), SKIP_HOSTS):
            continue
        text = clean_web_text(hit.get("raw_content") or hit.get("content") or "")
        if not text:
            continue
        i = len(out) + 1
        title = (hit.get("title") or "").strip() or page_title(text, url)
        source = Source(id=f"w{i}", raw=f"{title}. {url}" if title else url, url=url, title=title)
        fetched = FetchedSource(source_id=source.id, status="ok", url=url, title=title, text=text,
                                via="tavily_search", exists=True)
        out.append((source, fetched))
        if len(out) == JUDGED:
            break
    return out


def pick(judged: list[tuple[Verdict, Source]]) -> tuple[Verdict, Source] | None:
    """A supported verdict with a verified quote, preferring primary sources, then higher confidence; failing that,
    the best contradicted one. Ties go to the higher-ranked search hit (the list is in rank order)."""
    for label in ("supported", "contradicted"):
        ok = [(v, s) for v, s in judged if v.label == label and v.quote_found]
        if ok:
            return min(ok, key=lambda vs: (source_rank(vs[1].url), -vs[0].confidence))  # min keeps the first of equals
    return None


def suggest_one(claim: Claim, cited: set[str], searcher: Searcher, llm: LLM, settings: Settings) -> Suggestion:
    tier = settings.suggest_tier
    model = settings.models[tier]
    query = _query(claim)
    cands = _candidates(searcher.search(query, max_results=SEARCH_RESULTS), cited)
    base = dict(claim_id=claim.id, tier=tier, model=model, query=query, candidates=len(cands))
    if not cands:
        return Suggestion(**base, status="none", rationale="The web search returned no readable page to check.")
    with ThreadPoolExecutor(max_workers=len(cands)) as pool:
        judged = list(pool.map(lambda sf: (judge_one(claim, sf[0], sf[1], llm, tier, model=model), sf[0]), cands))
    best = pick(judged)
    if best is None:
        return Suggestion(**base, status="none",
                          rationale=f"None of the {len(cands)} pages checked states the claim or says otherwise.")
    v, s = best
    return Suggestion(**base, status="supported" if v.label == "supported" else "contradicted", url=s.url, title=s.title,
                      quote=v.quote, quote_found=v.quote_found, rationale=v.rationale)


def suggest_sources(
    claims: list[Claim],
    sources: list[Source],
    fetched: list[FetchedSource],
    verdicts: list[Verdict],
    searcher: Searcher,
    llm: LLM,
    settings: Settings,
    on_event: Callable[[dict], None] | None = None,
) -> list[Suggestion]:
    """One Suggestion per target claim, in document order.

    Best effort: a search or model failure for one claim leaves that claim without a suggestion
    instead of losing the audit that already finished.
    """
    targets = suggest_targets(claims, verdicts, settings.suggest_max_claims)
    lock = threading.Lock()

    def run(claim: Claim) -> Suggestion | None:
        try:
            s = suggest_one(claim, cited_urls(claim, sources, fetched), searcher, llm, settings)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("no suggestion for %s: %s", claim.id, type(exc).__name__)
            return None
        if on_event:
            with lock:
                on_event({"type": "suggestion", "suggestion": s.model_dump(mode="json")})
        return s

    with ThreadPoolExecutor(max_workers=TARGET_WORKERS) as pool:
        return [s for s in pool.map(run, targets) if s is not None]
