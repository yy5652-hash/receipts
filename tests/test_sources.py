from receipts.models import Source
from receipts.sources import fetch_sources
from receipts.testing import FakeSearcher

HIT = {
    "url": "https://news.example/wind-2020",
    "title": "Offshore wind in Europe: 2020 statistics",
    "content": "short snippet",
    "raw_content": "Full text about offshore wind.",
}
DECOY = {"url": "https://x.example/other", "title": "Cooking with cast iron", "content": "Wind of change recipe", "raw_content": None}


def src(id, **kw):
    return Source(id=id, raw=kw.pop("raw", id), **kw)


def fetch(sources, pages=None, index=None, status=None, **kw):
    searcher = FakeSearcher(pages or {}, index or [])
    st = status or {}
    return fetch_sources(sources, searcher, status_fn=lambda u: st.get(u), **kw), searcher


def test_ok_via_extract_is_batched():
    (f,), searcher = fetch([src("s1", url="https://a.example/p", title="T")], {"https://a.example/p": "page text"})
    assert (f.status, f.via, f.text, f.url) == ("ok", "tavily_extract", "page text", "https://a.example/p")
    assert searcher.extract_calls == [["https://a.example/p"]] and searcher.search_calls == []


def test_doi_without_url_resolves_via_doi_org():
    (f,), searcher = fetch([src("s1", doi="10.1/x")], {"https://doi.org/10.1/x": "abstract"})
    assert f.status == "ok" and f.url == "https://doi.org/10.1/x"


def test_dead_link_on_404_410_and_dns_failure():
    for code in (404, 410, None):
        (f,), _ = fetch([src("s1", url="https://gone.example/a", title="Some title")],
                        {"https://gone.example/a": None}, status={"https://gone.example/a": code})
        assert f.status == "dead_link" and f.text == "", code


def test_blocked_page_falls_through_to_title_search():
    s = src("s1", url="https://blocked.example/a", title="Offshore wind in Europe: 2020 statistics",
            authors=["Smith, J."], year=2021)
    (f,), searcher = fetch([s], {"https://blocked.example/a": None}, [DECOY, HIT], status={"https://blocked.example/a": 403})
    assert (f.status, f.via, f.url) == ("ok", "tavily_search", HIT["url"])
    assert f.text == HIT["raw_content"]  # raw_content preferred over the snippet
    assert searcher.search_calls == ['"Offshore wind in Europe: 2020 statistics" Smith 2021']


def test_title_only_uses_snippet_when_no_raw_content():
    hit = dict(HIT, raw_content=None)
    (f,), _ = fetch([src("s1", title="Offshore wind in Europe: 2020 statistics")], index=[hit])
    assert f.status == "ok" and f.text == "short snippet"


def test_title_search_rejects_dissimilar_hits():
    (f,), _ = fetch([src("s1", title="Offshore wind in Europe: 2020 statistics")], index=[DECOY])
    assert f.status == "not_found" and f.note == "no matching source found by title search"


def test_url_slug_can_match_title():
    hit = {"url": "https://x.example/offshore-wind-in-europe-2020-statistics", "title": "Home", "content": "offshore wind body text"}
    (f,), _ = fetch([src("s1", title="Offshore wind in Europe: 2020 statistics")], index=[hit])
    assert f.status == "ok" and f.text == "offshore wind body text"


def test_no_url_no_title_is_not_found():
    (f,), searcher = fetch([src("s1")])
    assert f.status == "not_found" and searcher.search_calls == []


def test_scholarly_hook_is_a_last_resort():
    s = src("s1", title="A paper nobody indexed")
    rec = {"url": "https://doi.org/10.9/z", "title": "A paper nobody indexed", "text": "abstract text"}
    (f,), _ = fetch([s], scholarly_fn=lambda t: rec)
    assert (f.status, f.via, f.text) == ("ok", "crossref", "abstract text")
    (f,), _ = fetch([s], scholarly_fn=lambda t: None)
    assert f.status == "not_found"


def test_order_and_ids_preserved():
    srcs = [src("s1", url="https://a/1"), src("s2"), src("s3", url="https://a/3")]
    out, _ = fetch(srcs, {"https://a/1": "one", "https://a/3": "three"})
    assert [(f.source_id, f.status) for f in out] == [("s1", "ok"), ("s2", "not_found"), ("s3", "ok")]
