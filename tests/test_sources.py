from receipts.models import Source
from receipts.sources import fetch_sources
from receipts.testing import FakeOpenAlex, FakeSearcher

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


def test_fetched_text_is_cleaned_of_markdown():
    md = "Built for the [1889 Exposition Universelle](https://w.test/x) in _Paris_.[[47]]"
    (f,), _ = fetch([src("s1", url="https://a.example/p")], {"https://a.example/p": md})
    assert f.text == "Built for the 1889 Exposition Universelle in Paris."
    hit = dict(HIT, raw_content="Offshore **wind** grew.[1]")
    (f,), _ = fetch([src("s1", title="Offshore wind in Europe: 2020 statistics")], index=[hit])
    assert f.text == "Offshore wind grew."


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


REC = {
    "title": "Attention Is All You Need", "doi": "10.5555/attn", "year": 2017, "authors": ["Ashish Vaswani"],
    "venue": "NeurIPS", "url": "https://arxiv.example/pdf/1706", "abstract": "We propose the Transformer.",
}
ABSTRACT_NOTE = "checked against the abstract; full text is not openly available"


def test_doi_found_reads_the_open_full_text_when_tavily_can():
    oa = FakeOpenAlex(dois={"10.5555/attn": REC})
    (f,), searcher = fetch([src("s1", doi="10.5555/attn")], {REC["url"]: "Full **paper** text.[1]"}, openalex=oa)
    assert (f.status, f.via, f.coverage, f.exists, f.url) == ("ok", "tavily_extract", "full", True, REC["url"])
    assert f.text == "Full paper text." and f.title == REC["title"]
    assert oa.calls == [("doi", "10.5555/attn")]
    assert searcher.extract_calls == [[], [REC["url"]]]  # the DOI itself is not fetched through doi.org


def test_doi_found_falls_back_to_the_abstract():
    oa = FakeOpenAlex(dois={"10.5555/attn": REC})
    (f,), _ = fetch([src("s1", doi="10.5555/attn")], {REC["url"]: None}, openalex=oa)
    assert (f.status, f.via, f.coverage, f.exists) == ("ok", "openalex", "abstract", True)
    assert f.text == "We propose the Transformer." and f.note == ABSTRACT_NOTE and f.url == REC["url"]


def test_doi_missing_is_not_registered():
    oa = FakeOpenAlex()
    (f,), searcher = fetch([src("s1", doi="10.1/fake", title="A real-sounding title")], openalex=oa)
    assert (f.status, f.note, f.exists, f.text) == ("not_found", "DOI is not registered", False, "")
    assert f.url == "https://doi.org/10.1/fake" and searcher.search_calls == [] and oa.calls == [("doi", "10.1/fake")]


def test_title_only_found_in_openalex_before_any_web_search():
    oa = FakeOpenAlex(titles={REC["title"]: REC})
    s = src("s1", title=REC["title"], authors=["Vaswani, A."], year=2017)
    (f,), searcher = fetch([s], {REC["url"]: None}, openalex=oa)
    assert (f.status, f.via, f.coverage, f.exists) == ("ok", "openalex", "abstract", True)
    assert oa.calls == [("title", REC["title"], "Vaswani", 2017)] and searcher.search_calls == []


def test_title_not_in_openalex_falls_back_to_web_search_then_not_found():
    title = "Offshore wind in Europe: 2020 statistics"
    oa = FakeOpenAlex()
    (f,), _ = fetch([src("s1", title=title)], index=[HIT], openalex=oa)
    assert (f.status, f.via, f.exists, f.url) == ("ok", "tavily_search", True, HIT["url"])
    (f,), _ = fetch([src("s1", title=title)], index=[DECOY], openalex=oa)
    assert (f.status, f.exists, f.note) == ("not_found", False, "no record in OpenAlex and no matching web page")


def test_record_without_text_or_abstract_is_not_found_but_exists():
    bare = dict(REC, abstract="")
    oa = FakeOpenAlex(dois={"10.5555/attn": bare})
    (f,), _ = fetch([src("s1", doi="10.5555/attn", title="T")], {REC["url"]: None}, openalex=oa)
    assert (f.status, f.exists, f.text, f.title, f.url) == ("not_found", True, "", REC["title"], REC["url"])
    assert f.note == "record exists in OpenAlex but no text or abstract is available"


def test_doi_lookup_failure_falls_through_to_title_then_reports_unchecked():
    oa = FakeOpenAlex(dois={"10.5555/attn": None}, titles={REC["title"]: REC})
    (f,), _ = fetch([src("s1", doi="10.5555/attn", title=REC["title"])], openalex=oa)
    assert f.status == "ok" and f.coverage == "abstract"
    (f,), _ = fetch([src("s1", doi="10.5555/attn")], openalex=oa)
    assert (f.status, f.exists, f.note) == ("not_found", None, "the DOI could not be looked up")


def test_url_extract_wins_and_dead_link_ends_the_search_even_with_openalex():
    oa = FakeOpenAlex(dois={"10.5555/attn": REC})
    (f,), _ = fetch([src("s1", url="https://a.example/p", doi="10.5555/attn")], {"https://a.example/p": "page"}, openalex=oa)
    assert (f.via, f.exists, f.coverage, oa.calls) == ("tavily_extract", True, "full", [])
    (f,), _ = fetch([src("s1", url="https://gone.example/a", doi="10.5555/attn")], status={"https://gone.example/a": 404}, openalex=oa)
    assert (f.status, f.exists, oa.calls) == ("dead_link", None, [])


def test_blocked_url_continues_to_openalex_and_unreadable_reachable_link_exists():
    oa = FakeOpenAlex(dois={"10.5555/attn": REC})
    (f,), _ = fetch([src("s1", url="https://blocked.example/a", doi="10.5555/attn")], {REC["url"]: None},
                    status={"https://blocked.example/a": 403}, openalex=oa)
    assert (f.status, f.coverage) == ("ok", "abstract")
    (f,), _ = fetch([src("s1", url="https://blocked.example/a")], status={"https://blocked.example/a": 403})
    assert (f.status, f.exists) == ("not_found", True)


def test_exists_is_unchecked_when_there_is_nothing_to_look_up():
    (f,), _ = fetch([src("s1")])
    assert (f.status, f.exists) == ("not_found", None)


def test_order_and_ids_preserved():
    srcs = [src("s1", url="https://a/1"), src("s2"), src("s3", url="https://a/3")]
    out, _ = fetch(srcs, {"https://a/1": "one", "https://a/3": "three"})
    assert [(f.source_id, f.status) for f in out] == [("s1", "ok"), ("s2", "not_found"), ("s3", "ok")]
