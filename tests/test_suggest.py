"""Find-a-real-source stage: targets, candidates, picking, events. Offline (FakeSearcher / FakeLLM)."""

import pytest

from receipts.config import Settings
from receipts.models import Claim, FetchedSource, Source, Verdict
from receipts.suggest import cited_urls, normalize_url, pick, suggest_sources, suggest_targets
from receipts.testing import FakeLLM, FakeSearcher

CITED = "https://example.org/cited-page"


def claim(i: int, text: str, sids=("s1",)) -> Claim:
    return Claim(id=f"c{i}", sentence=text, start=0, end=len(text), claim=text, source_ids=list(sids))


def verdict(cid: str, label: str, sid="s1", **kw) -> Verdict:
    return Verdict(claim_id=cid, source_id=sid, label=label, tier=kw.pop("tier", "nano"), **kw)


def hit(url: str, title: str, text: str) -> dict:
    return {"url": url, "title": title, "content": text[:80], "raw_content": text}


SOURCES = [Source(id="s1", raw="[1] Cited. " + CITED, url=CITED, title="Cited"),
           Source(id="s2", raw="[2] Other. doi:10.1/x", doi="10.1/x", title="Other")]
FETCHED = [FetchedSource(source_id="s1", status="ok", url=CITED, text="unrelated"),
           FetchedSource(source_id="s2", status="not_found", url="https://doi.org/10.1/x")]


def run(claims, verdicts, index, **settings):
    searcher, llm, events = FakeSearcher({}, index), FakeLLM(), []
    out = suggest_sources(claims, SOURCES, FETCHED, verdicts, searcher, llm, Settings(**settings), on_event=events.append)
    return out, searcher, llm, events


# --- targets -----------------------------------------------------------------------------

def test_targets_are_only_unsupported_claims_in_document_order_and_capped():
    claims = [claim(i, f"Claim number {i}.") for i in range(1, 8)]
    labels = ["supported", "not_in_source", "partial", "source_unavailable", "contradicted", "not_in_source", "supported"]
    verdicts = [verdict(c.id, lab) for c, lab in zip(claims, labels)]
    assert [c.id for c in suggest_targets(claims, verdicts, 8)] == ["c2", "c4", "c6"]
    assert [c.id for c in suggest_targets(claims, verdicts, 2)] == ["c2", "c4"]
    assert suggest_targets(claims, verdicts, 0) == []


def test_best_verdict_decides_the_target_and_no_verdict_counts_as_unavailable():
    two = claim(1, "Cited twice.", ("s1", "s2"))
    verdicts = [verdict("c1", "not_in_source", "s1"), verdict("c1", "supported", "s2"), verdict("c2", "not_in_source")]
    assert [c.id for c in suggest_targets([two, claim(2, "Another.")], verdicts, 8)] == ["c2"]  # c1: one source backs it
    assert [c.id for c in suggest_targets([claim(3, "No verdict.")], [], 8)] == ["c3"]


def test_the_cap_comes_from_the_settings():
    claims = [claim(i, f"Claim number {i}.") for i in range(1, 6)]
    verdicts = [verdict(c.id, "not_in_source") for c in claims]
    out, searcher, _, _ = run(claims, verdicts, [], suggest_max_claims=2)
    assert len(out) == 2 and len(searcher.search_calls) == 2


# --- candidates --------------------------------------------------------------------------

def test_normalize_url():
    assert normalize_url("HTTPS://Example.ORG/a/b/#frag") == "https://example.org/a/b"
    assert normalize_url("https://example.org/a?x=1") == "https://example.org/a?x=1"


def test_cited_urls_cover_written_resolved_and_doi_links():
    c = claim(1, "x", ("s1", "s2"))
    assert cited_urls(c, SOURCES, FETCHED) == {CITED, "https://doi.org/10.1/x"}


TEXT = "Heat pumps outsold gas furnaces in the United States in 2022. Sales rose in every region."


def test_cited_url_is_excluded_and_only_three_readable_hits_are_judged():
    c = claim(1, "Heat pumps outsold gas furnaces in the United States in 2022.")
    index = [hit("HTTPS://Example.org/cited-page/#top", "The cited page itself", TEXT),
             {"url": "https://a.example/empty", "title": "Heat pumps furnaces", "content": "", "raw_content": ""},
             hit("https://a.example/1", "Heat pumps furnaces one", TEXT),
             hit("https://a.example/2", "Heat pumps furnaces two", TEXT),
             hit("https://a.example/3", "Heat pumps furnaces three", TEXT),
             hit("https://a.example/4", "Heat pumps furnaces four", TEXT)]
    out, searcher, llm, _ = run([c], [verdict("c1", "not_in_source")], index)
    (s,) = out
    assert searcher.search_calls == [c.claim]
    assert s.candidates == 3 and sum(t == "judge" for t, _ in llm.calls) == 3
    assert s.url == "https://a.example/1"  # the cited page never competes; ties go to the best-ranked hit


# --- picking -----------------------------------------------------------------------------

def v(label, conf, found=True, quote="q"):
    return Verdict(claim_id="c1", source_id="w", label=label, confidence=conf, quote=quote, quote_found=found, tier="nano")


def src(name):
    return Source(id=name, raw=name, url=f"https://{name}.example")


def test_supported_beats_contradicted_even_with_lower_confidence():
    judged = [(v("contradicted", 0.95), src("a")), (v("supported", 0.6), src("b"))]
    assert pick(judged)[1].id == "b"


def test_highest_confidence_wins_and_ties_go_to_the_better_ranked_hit():
    judged = [(v("supported", 0.7), src("a")), (v("supported", 0.9), src("b")), (v("supported", 0.9), src("c"))]
    assert pick(judged)[1].id == "b"
    assert pick([(v("contradicted", 0.6), src("a")), (v("contradicted", 0.8), src("b"))])[1].id == "b"


def test_a_verdict_needs_a_verified_quote():
    judged = [(v("supported", 0.9, found=False), src("a")), (v("contradicted", 0.8), src("b"))]
    assert pick(judged)[1].id == "b"  # the unverified 'supported' does not count
    assert pick([(v("supported", 0.9, found=False), src("a"))]) is None


def test_nothing_qualifies():
    assert pick([]) is None
    assert pick([(v("partial", 0.9), src("a")), (v("not_in_source", 0.9, quote=""), src("b"))]) is None


# --- end to end with the fakes -----------------------------------------------------------

CLAIM_SUP = "Heat pumps outsold gas furnaces in the United States in 2022."
CLAIM_CON = "Battery storage additions doubled in 2021."
CLAIM_NONE = "Utility-scale hydrogen projects reached 3.5 GW of announced capacity by 2022."
INDEX = [
    hit("https://hvac.example/heat-pumps", "US heat pump sales", TEXT),
    hit("https://grid.example/storage", "Storage additions in 2022",
        "Battery storage additions doubled in 2022. Grid-scale projects led the growth."),
    hit("https://decoy.example/h2", "Hydrogen overview", "A general overview of national policy goals."),
]


def test_each_target_gets_a_suggestion_with_its_status_and_an_event():
    claims = [claim(1, CLAIM_SUP), claim(2, "Solar was cheap."), claim(3, CLAIM_CON), claim(4, CLAIM_NONE)]
    verdicts = [verdict("c1", "not_in_source"), verdict("c2", "supported"), verdict("c3", "source_unavailable"),
                verdict("c4", "not_in_source")]
    out, searcher, llm, events = run(claims, verdicts, INDEX)
    assert [(s.claim_id, s.status) for s in out] == [("c1", "supported"), ("c3", "contradicted"), ("c4", "none")]
    sup, con, none = out
    assert sup.url == "https://hvac.example/heat-pumps" and sup.title == "US heat pump sales"
    assert sup.quote_found and sup.quote.startswith("Heat pumps outsold") and sup.rationale
    assert (sup.tier, sup.model, sup.query) == ("nano", Settings().models["nano"], CLAIM_SUP)
    assert con.url == "https://grid.example/storage" and "doubled in 2022" in con.quote
    assert none.url is None and none.quote == "" and none.candidates >= 1  # pages were read and none said it
    assert {e["type"] for e in events} == {"suggestion"}
    assert sorted(e["suggestion"]["claim_id"] for e in events) == ["c1", "c3", "c4"]
    assert all(t == "nano" for _, t in llm.calls)


def test_the_judge_tier_is_configurable():
    out, _, llm, _ = run([claim(1, CLAIM_SUP)], [verdict("c1", "not_in_source")], INDEX, suggest_tier="super")
    assert out[0].tier == "super" and {t for _, t in llm.calls} == {"super"}


def test_a_failing_search_leaves_that_claim_without_a_suggestion():
    class Flaky(FakeSearcher):
        def search(self, query, max_results=5):
            if "Battery" in query:
                raise RuntimeError("boom")
            return super().search(query, max_results)

    claims = [claim(1, CLAIM_SUP), claim(2, CLAIM_CON)]
    verdicts = [verdict("c1", "not_in_source"), verdict("c2", "not_in_source")]
    out = suggest_sources(claims, SOURCES, FETCHED, verdicts, Flaky({}, INDEX), FakeLLM(), Settings())
    assert [s.claim_id for s in out] == ["c1"]


def test_no_hits_means_none_without_a_model_call():
    out, _, llm, _ = run([claim(1, CLAIM_SUP)], [verdict("c1", "not_in_source")], [])
    assert out[0].status == "none" and out[0].candidates == 0 and llm.calls == []


@pytest.mark.parametrize("n", [1, 5])
def test_long_claims_are_cut_to_the_search_limit(n):
    long = " ".join(["word"] * (400 * n))
    out, searcher, _, _ = run([claim(1, long)], [verdict("c1", "not_in_source")], [])
    assert len(searcher.search_calls[0]) <= 400
