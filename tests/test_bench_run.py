import json
from types import SimpleNamespace

from bench import run
from bench.run import label_rows, load_results, match_claim, run_report
from receipts.config import Settings
from receipts.models import Claim
from receipts.testing import FakeLLM, FakeSearcher

DOC = ("# Briefing: Investment\n\nGlobal investment in renewable energy reached $400 billion in 2022 [1]. "
       "Solar led the total, said the report [1].\n\n## References\n\n[1] Investment trends. https://a.example/p\n")
PAGE = "Global investment in renewable energy reached $400 billion in 2022, up 8% on the previous year. Solar led the total."


def claim(i, start, end):
    return Claim(id=f"c{i}", sentence="x", start=start, end=end, claim="x", source_ids=["s1"])


def test_match_needs_half_of_the_gold_sentence():
    g = {"start": 100, "end": 200}
    assert match_claim(g, [claim(1, 100, 200)]).id == "c1"
    assert match_claim(g, [claim(1, 90, 260)]).id == "c1"  # a longer claim covering it
    assert match_claim(g, [claim(1, 100, 150)]).id == "c1"  # exactly 50 %
    assert match_claim(g, [claim(1, 100, 149)]) is None
    assert match_claim(g, [claim(1, 0, 100), claim(2, 200, 300)]) is None  # touching, not overlapping
    assert match_claim(g, [claim(1, 100, 160), claim(2, 150, 230)]).id == "c1"  # the larger overlap wins (60 vs 50)
    assert match_claim(g, []) is None


def test_label_rows_takes_the_best_verdict_and_marks_missed():
    gold = [{"start": 0, "end": 10, "type": "supported", "gold": "supported"},
            {"start": 20, "end": 30, "type": "number_swap", "gold": "contradicted"},
            {"start": 40, "end": 50, "type": "fabricated", "gold": "source_unavailable"},
            {"start": 60, "end": 70, "type": "overclaim", "gold": "partial"}]
    claims = [claim(1, 0, 10), claim(2, 20, 30), claim(3, 40, 50)]
    v = lambda cid, label, **kw: SimpleNamespace(claim_id=cid, label=label, tier=kw.get("tier", "super"), escalated=kw.get("esc", False), confidence=0.9, quote_found=True)  # noqa: E731
    verdicts = [v("c1", "not_in_source"), v("c1", "supported"), v("c2", "contradicted", tier="ultra", esc=True)]  # c3 has no verdict
    rows = label_rows("r01", gold, claims, verdicts)
    assert [r["predicted"] for r in rows] == ["supported", "contradicted", "missed", "missed"]  # best label across sources
    assert rows[1]["tier"] == "ultra" and rows[1]["escalated"] and rows[1]["quote_found"]
    assert rows[2]["tier"] == "none" and not rows[2]["escalated"] and rows[2]["confidence"] == 0.0
    assert {"report", "type", "gold", "predicted", "tier", "escalated", "confidence", "quote_found"} <= set(rows[0])


def test_run_report_judges_with_a_fresh_llm_per_config_and_adds_shared_cost():
    made = []

    def make_llm():
        made.append(FakeLLM())
        return made[-1]

    start = DOC.index("Global investment")
    end = DOC.index("[1].", start) + 4
    gold = [{"start": start, "end": end, "type": "supported", "gold": "supported"},
            {"start": 0, "end": 5, "type": "fabricated", "gold": "source_unavailable"}]  # no claim there: missed
    settings = Settings(models={"nano": "m-nano", "super": "m-super", "ultra": "m-ultra"})
    rows, runs = run_report("r01", DOC, gold, ["nano", "cascade_nano"], settings, make_llm, FakeSearcher({"https://a.example/p": PAGE}), None)
    assert len(made) == 3  # extraction + one fresh client per configuration
    assert [r["predicted"] for r in rows["nano"]] == ["supported", "missed"]
    assert rows["cascade_nano"][0]["tier"] == "nano" and not rows["cascade_nano"][0]["escalated"]
    for name in ("nano", "cascade_nano"):
        rec = runs[name]
        assert rec["cost_usd"] == rec["extract_cost_usd"] + rec["judge_cost_usd"]
        assert abs(rec["seconds"] - (rec["extract_seconds"] + rec["fetch_seconds"] + rec["judge_seconds"])) < 0.02
        assert rec["claims"] == 2 and rec["gold_items"] == 2
    again = run_report("r01", DOC, gold, ["super"], settings, make_llm, FakeSearcher({"https://a.example/p": PAGE}), None,
                       shared={"extract_cost_usd": 1.0, "extract_seconds": 2.0, "fetch_seconds": 3.0})[1]["super"]
    assert again["cost_usd"] >= 1.0 and again["seconds"] >= 5.0  # a resumed report keeps its recorded shared cost


def test_config_table_matches_the_spec():
    assert list(run.CONFIGS) == ["nano", "super", "ultra", "cascade", "cascade_nano", "routed"]
    assert run.CONFIGS["cascade"] == ("cascade", ("super", "ultra")) and run.CONFIGS["cascade_nano"] == ("cascade", ("nano", "ultra"))


def test_load_results_drops_rows_of_interrupted_pairs(tmp_path):
    (tmp_path / "runs.json").write_text(json.dumps({"r01": {"nano": {"cost_usd": 0.1}}}))
    (tmp_path / "nano.jsonl").write_text(json.dumps({"report": "r01"}) + "\n" + json.dumps({"report": "r02"}) + "\n")
    runs, rows = load_results(tmp_path)
    assert [r["report"] for r in rows["nano"]] == ["r01"] and rows["super"] == [] and "r01" in runs
