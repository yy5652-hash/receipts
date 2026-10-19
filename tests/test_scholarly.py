import httpx
import pytest

from receipts.scholarly import OpenAlex
from receipts.search import DiskCache

WORK = {
    "title": "Attention Is All You Need",
    "doi": "https://doi.org/10.5555/attn",
    "publication_year": 2017,
    "authorships": [{"author": {"display_name": "Ashish Vaswani"}}, {"author": {"display_name": "Noam Shazeer"}}],
    "primary_location": {"source": {"display_name": "NeurIPS"}},
    "best_oa_location": {"pdf_url": "https://arxiv.example/pdf/1706", "landing_page_url": "https://arxiv.example/abs/1706"},
    "abstract_inverted_index": {"The": [0], "dominant": [1], "models": [2, 5], "are": [3], "simple": [4], "networks.": [6]},
}


def oa(tmp_path, handler, cache=True):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAlex(DiskCache(tmp_path) if cache else None, client=client)


def search_handler(*works, seen=None):
    def handler(req):
        if seen is not None:
            seen.append(req)
        return httpx.Response(200, json={"results": list(works)})

    return handler


def test_by_doi_found_rebuilds_abstract_and_record(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=WORK)

    rec = oa(tmp_path, handler).by_doi("https://doi.org/10.5555/attn")
    assert rec == {
        "title": "Attention Is All You Need", "doi": "10.5555/attn", "year": 2017,
        "authors": ["Ashish Vaswani", "Noam Shazeer"], "venue": "NeurIPS",
        "url": "https://arxiv.example/pdf/1706", "abstract": "The dominant models are simple models networks.",
    }
    (req,) = seen
    assert str(req.url) == "https://api.openalex.org/works/doi:10.5555/attn" and "mailto" not in str(req.url)


def test_by_doi_url_falls_back_to_landing_then_doi_and_empty_abstract(tmp_path):
    work = dict(WORK, best_oa_location={"landing_page_url": "https://x.example/land"}, abstract_inverted_index=None)
    rec = oa(tmp_path, lambda r: httpx.Response(200, json=work)).by_doi("10.5555/attn")
    assert rec["url"] == "https://x.example/land" and rec["abstract"] == ""
    bare = dict(WORK, best_oa_location=None)
    assert oa(tmp_path / "b", lambda r: httpx.Response(200, json=bare)).by_doi("10.5555/attn")["url"] == "https://doi.org/10.5555/attn"


def test_by_doi_404_is_missing_and_cached(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(404, json={"error": "Not found"})

    client = oa(tmp_path, handler)
    assert client.by_doi("10.1/nope") == "missing"
    assert client.by_doi("10.1/NOPE") == "missing" and len(n) == 1


def test_retries_on_429_then_gives_up_without_caching(tmp_path, monkeypatch):
    monkeypatch.setattr("receipts.scholarly.time.sleep", lambda s: None)
    codes = iter([429, 503, 200])
    assert oa(tmp_path, lambda r: httpx.Response(next(codes), json=WORK)).by_doi("10.5555/attn")["year"] == 2017
    n = []

    def always_busy(req):
        n.append(1)
        return httpx.Response(503)

    client = oa(tmp_path / "x", always_busy)
    assert client.by_doi("10.1/y") is None and len(n) == 3  # 1 try + 2 retries
    assert client.by_doi("10.1/y") is None and len(n) == 6  # a failure is not cached


def test_by_title_accepts_close_match(tmp_path):
    seen = []
    rec = oa(tmp_path, search_handler(WORK, seen=seen)).by_title("Attention is all you need", "Vaswani", 2017)
    assert rec["doi"] == "10.5555/attn"
    (req,) = seen
    assert req.url.params["search"] == "Attention is all you need" and req.url.params["per-page"] == "5"
    assert "mailto" not in str(req.url)


def test_by_title_rejects_near_miss(tmp_path):
    other = dict(WORK, title="Attention Is All You Need: A Survey of Transformer Variants in Practice")
    assert oa(tmp_path, search_handler(other)).by_title("Attention is all you need") is None
    assert oa(tmp_path / "e", search_handler()).by_title("Attention is all you need") is None


def test_by_title_rejects_year_mismatch_but_allows_one_year_slack(tmp_path):
    client = oa(tmp_path, search_handler(WORK))
    assert client.by_title("Attention Is All You Need", year=2012) is None
    assert client.by_title("Attention Is All You Need", year=2018)["year"] == 2017
    assert client.by_title("Attention Is All You Need", year=None)["year"] == 2017


def test_by_title_picks_the_best_ratio_and_author_breaks_ties(tmp_path):
    a = dict(WORK, title="Deep learning for solar forecasting", doi=None, authorships=[{"author": {"display_name": "Ann Lee"}}])
    b = dict(a, title="Deep learning for solar forecasting.", authorships=[{"author": {"display_name": "Bo Chen"}}])
    c = dict(a, title="Deep learning for solar forecasting systems")
    got = oa(tmp_path, search_handler(c, a, b)).by_title("Deep learning for solar forecasting", first_author="Chen")
    assert got["authors"] == ["Bo Chen"]


def test_by_title_is_cached_and_works_without_cache(tmp_path):
    seen = []
    client = oa(tmp_path, search_handler(WORK, seen=seen))
    client.by_title("Attention Is All You Need")
    client.by_title("Attention Is All You Need", year=2017)
    assert len(seen) == 1
    assert oa(None, search_handler(WORK), cache=False).by_title("Attention Is All You Need") is not None


def test_transport_error_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("receipts.scholarly.time.sleep", lambda s: None)

    def boom(req):
        raise httpx.ConnectError("offline")

    client = oa(tmp_path, boom)
    assert client.by_doi("10.1/z") is None and client.by_title("Anything") is None


@pytest.mark.parametrize("raw", ["doi:10.5555/attn", "http://dx.doi.org/10.5555/attn", " 10.5555/attn "])
def test_doi_prefixes_are_stripped(tmp_path, raw):
    seen = []
    oa(tmp_path, lambda r: seen.append(r.url.path) or httpx.Response(404)).by_doi(raw)
    assert seen == ["/works/doi:10.5555/attn"]


def test_by_title_strips_wildcard_characters():
    seen = {}

    def handler(request):
        seen["search"] = request.url.params["search"]
        return httpx.Response(200, json={"results": []})

    oa = OpenAlex(None, client=httpx.Client(transport=httpx.MockTransport(handler)))
    oa.by_title("Power Hungry Processing: Watts Driving the Cost of AI Deployment?")
    assert "?" not in seen["search"] and ":" not in seen["search"]
    assert seen["search"] == "Power Hungry Processing Watts Driving the Cost of AI Deployment"
