import json
from pathlib import Path

import pytest

from receipts import cli
from receipts.config import Settings
from receipts.models import AuditReport, Claim, FetchedSource, Source, Verdict
from receipts.pipeline import audit
from receipts.render import render_markdown
from receipts.testing import FakeLLM, FakeOpenAlex, load_fixtures

FIX = Path(__file__).parent / "fixtures"
DOC = (FIX / "report_basic.md").read_text(encoding="utf-8")
FIXTURES = FIX / "sources.json"


@pytest.fixture(scope="module")
def report():
    searcher, status_fn = load_fixtures(FIXTURES)
    events = []
    rep = audit(DOC, FakeLLM(), searcher, Settings(), on_event=events.append, status_fn=status_fn)
    rep._events = events
    return rep


def label_of(report, prefix):
    (c,) = [c for c in report.claims if c.claim.startswith(prefix)]
    return report.claim_label(c.id)


def test_seeded_labels(report):
    assert label_of(report, "Global investment") == "contradicted"  # $500 vs $400
    assert label_of(report, "Solar module prices") == "supported"
    assert label_of(report, "Heat pumps") == "not_in_source"
    assert label_of(report, "Offshore wind") == "supported"  # found by title search
    assert label_of(report, "Battery storage") == "source_unavailable"  # dead link
    assert label_of(report, "Utility-scale hydrogen") == "source_unavailable"  # not found
    assert label_of(report, "According to the IRENA") == "supported"  # inline link
    assert label_of(report, "Employment in the solar") == "supported"  # author-year
    assert label_of(report, "Solar was the cheapest") == "supported"  # best of two sources


def test_fetch_statuses_and_quotes(report):
    st = {f.source_id: (f.status, f.via) for f in report.fetched}
    assert st["s1"] == ("ok", "tavily_extract") and st["s2"] == ("ok", "tavily_extract")
    assert st["s3"] == ("ok", "tavily_search")
    assert st["s4"][0] == "dead_link" and st["s5"][0] == "not_found"
    for v in report.verdicts:
        if v.label in ("supported", "contradicted"):
            assert v.quote_found and v.quote
        if v.label == "source_unavailable":
            assert v.tier == "none" and not v.quote


def test_cascade_usage_and_summary(report):
    contradicted = [v for v in report.verdicts if v.label == "contradicted"][0]
    assert contradicted.escalated and contradicted.tier == "ultra"
    assert not any(v.escalated for v in report.verdicts if v.label == "supported" and v.claim_id == "c2")
    tiers = {u.tier: u.calls for u in report.usage}
    assert tiers["nano"] == 2 and tiers["super"] >= 6 and tiers["ultra"] >= 2
    sm = report.summary()
    assert sm["counts"] == {"supported": 5, "partial": 0, "contradicted": 1, "not_in_source": 1, "source_unavailable": 2}
    assert (sm["n_claims"], sm["n_sources"], sm["cost_usd"]) == (9, 7, 0.0)


def test_events(report):
    stages = [e["stage"] for e in report._events if e["type"] == "stage"]
    assert stages == ["extract", "fetch", "judge", "done"]
    assert sum(e["type"] == "verdict" for e in report._events) == len(report.verdicts)


def test_render_markdown_has_every_claim(report):
    md = render_markdown(report)
    for c in report.claims:
        assert c.sentence in md
    for badge in ("✅ supported", "❌ contradicted", "⚪ not in source", "🚫 source unavailable"):
        assert badge in md
    assert "Renewable Energy Outlook 2023" in md and "https://example.org/energy-journal/investment-trends" in md
    assert "$400 billion in 2022" in md  # the quote shown for the contradicted claim


def test_claim_label_prefers_best_verdict():
    def v(label, sid):
        return Verdict(claim_id="c1", source_id=sid, label=label)

    rep = AuditReport(document="", mode="cascade", sources=[], claims=[], fetched=[], usage=[], seconds=0,
                      verdicts=[v("source_unavailable", "s1"), v("contradicted", "s2"), v("partial", "s3")])
    assert rep.claim_label("c1") == "partial"
    rep.verdicts = [v("source_unavailable", "s1"), v("not_in_source", "s2")]
    assert rep.claim_label("c1") == "not_in_source"


def test_openalex_is_wired_through_to_fetch_judge_and_render():
    searcher, status_fn = load_fixtures(FIXTURES)
    rec = {"title": "Solar PV module price trends", "doi": "10.5555/iea.solar.2021", "year": 2021, "authors": [], "venue": "",
           "url": "https://publisher.example/closed", "abstract": "Solar PV module prices fell 89% between 2010 and 2020."}
    oa = FakeOpenAlex(dois={"10.5555/iea.solar.2021": rec})
    rep = audit(DOC, FakeLLM(), searcher, Settings(), mode="super", status_fn=status_fn, openalex=oa)
    f2 = next(f for f in rep.fetched if f.source_id == "s2")
    assert (f2.status, f2.via, f2.coverage, f2.exists) == ("ok", "openalex", "abstract", True)
    assert label_of(rep, "Solar module prices") == "supported"
    assert ("doi", "10.5555/iea.solar.2021") in oa.calls and any(c[0] == "title" for c in oa.calls)
    md = render_markdown(rep)
    assert "checked against abstract only" in md
    assert "Record: a record of the source exists, but its text could not be read" in md  # s5: link answers, no text


def test_render_says_whether_an_unreadable_source_exists():
    def md(exists):
        f = FetchedSource(source_id="s1", status="not_found", note="n", exists=exists)
        v = Verdict(claim_id="c1", source_id="s1", label="source_unavailable")
        rep = AuditReport(document="", mode="cascade", sources=[Source(id="s1", raw="r", title="T")], fetched=[f],
                          claims=[Claim(id="c1", sentence="S.", start=0, end=2, claim="S.", source_ids=["s1"])],
                          verdicts=[v], usage=[], seconds=0)
        return render_markdown(rep)

    assert "Record: no record of the source was found" in md(False)
    assert "Record: a record of the source exists, but its text could not be read" in md(True)
    assert "Record:" not in md(None)


def test_unknown_mode_rejected():
    searcher, status_fn = load_fixtures(FIXTURES)
    with pytest.raises(ValueError, match="unknown mode"):
        audit(DOC, FakeLLM(), searcher, Settings(), mode="turbo", status_fn=status_fn)


def test_cli_fake_run_writes_outputs(tmp_path, capsys):
    out_json, out_md = tmp_path / "o" / "a.json", tmp_path / "a.md"
    rc = cli.main(["audit", str(FIX / "report_basic.md"), "--fake", str(FIXTURES), "--mode", "super",
                   "--json", str(out_json), "--md", str(out_md)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Claims: 9" in out and "contradicted 1" in out and "source_unavailable 2" in out
    data = json.loads(out_json.read_text())
    assert len(data["claims"]) == 9 and data["mode"] == "super"
    assert AuditReport.model_validate(data).claim_label("c1") == "contradicted"
    assert "citation audit" in out_md.read_text()


def test_cli_without_keys_names_the_variables(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings())
    assert cli.main(["audit", str(FIX / "report_basic.md")]) == 2
    err = capsys.readouterr().err
    assert "NEBIUS_API_KEY" in err and "TAVILY_API_KEY" in err


def test_cli_missing_file(capsys):
    assert cli.main(["audit", "/no/such/report.md"]) == 2
