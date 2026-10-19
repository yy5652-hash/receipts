import json

import pytest

from bench import report
from bench.report import main, summarise


def row(rep, typ, gold, pred, qf=False, esc=False):
    return {"report": rep, "start": 0, "type": typ, "gold": gold, "predicted": pred, "tier": "super", "escalated": esc,
            "confidence": 0.9, "quote_found": qf}


ROWS = [
    row("r01", "supported", "supported", "supported", qf=True),
    row("r01", "supported", "supported", "contradicted", qf=True, esc=True),  # a false alarm
    row("r01", "supported", "supported", "missed"),
    row("r01", "number_swap", "contradicted", "contradicted", qf=True, esc=True),
    row("r02", "overclaim", "partial", "supported"),  # not detected, unverifiable quote
    row("r02", "misattribution", "not_in_source", "not_in_source"),
    row("r02", "fabricated", "source_unavailable", "source_unavailable"),
    row("r02", "dead_link", "source_unavailable", "missed"),
    row("r02", "entity_swap", "contradicted", "partial", qf=True),
]
RUNS = {"r01": {"cost_usd": 0.01, "seconds": 10.0}, "r02": {"cost_usd": 0.03, "seconds": 30.0}}


def test_summarise_by_hand():
    s = summarise("cascade", ROWS, RUNS)
    assert (s["problem_recall"]["k"], s["problem_recall"]["n"]) == (4, 6)  # contradicted, not_in_source, unavailable, partial
    assert (s["false_alarm"]["k"], s["false_alarm"]["n"]) == (1, 3)  # a missed supported item raises no alarm
    assert (s["supported_pass"]["k"], s["supported_pass"]["n"]) == (1, 3)
    assert s["macro_accuracy"] == pytest.approx((4 / 6 + 2 / 3) / 2)
    assert s["per_type"]["number_swap"]["rate"] == 1 and s["per_type"]["overclaim"]["rate"] == 0
    assert s["per_type"]["dead_link"] == {"rate": 0.0, "k": 0, "n": 1}  # missed = not detected
    assert s["per_type"]["entity_swap"]["rate"] == 1  # partial still counts as a problem
    assert (s["label_accuracy"]["k"], s["label_accuracy"]["n"]) == (5, 9)  # misstated = partial or contradicted
    assert (s["evidence_found"]["k"], s["evidence_found"]["n"]) == (4, 5)  # supported/partial/contradicted predictions
    assert s["cost_per_100_claims_usd"] == pytest.approx(100 * 0.04 / 9)
    assert s["median_seconds_per_report"] == 20.0
    assert (s["escalated"]["k"], s["escalated"]["n"]) == (2, 7)  # answered rows only
    assert (s["extraction_coverage"]["k"], s["extraction_coverage"]["n"]) == (7, 9)
    assert summarise("nano", ROWS, RUNS)["escalated"] is None  # only cascades escalate


def write_files(root):
    (root / "results").mkdir()
    (root / "data").mkdir()
    (root / "results" / "runs.json").write_text(json.dumps({r: {"cascade": v, "nano": v} for r, v in RUNS.items()}))
    for name in ("cascade", "nano"):
        (root / "results" / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    gold = {"version": "ReceiptsBench v1", "generator": "gen-model", "seed": 7, "built_at": "2026-10-20T00:00:00+00:00",
            "reports": {"r01": [{"type": t} for t in ("supported", "number_swap")], "r02": [{"type": "fabricated"}]}}
    (root / "data" / "gold.json").write_text(json.dumps(gold))
    (root / "data" / "sources_used.json").write_text(json.dumps([{"id": "a", "kind": "arxiv"}, {"id": "b", "kind": "wikipedia"}, {"id": "c", "kind": "arxiv"}]))
    (root / "data" / "generation_cost.json").write_text(json.dumps({"prompt_tokens": 1000, "completion_tokens": 500, "usd": 0.0005}))


def test_main_writes_markdown_and_summary_from_the_result_files(tmp_path):
    write_files(tmp_path)
    assert main(tmp_path) == 0
    summary = json.loads((tmp_path / "results" / "summary.json").read_text())
    assert list(summary["configs"]) == ["nano", "cascade"] and summary["dataset"]["by_type"]["number_swap"] == 1
    assert summary["configs"]["cascade"]["problem_recall"]["k"] == 4
    md = (tmp_path / "RESULTS.md").read_text()
    assert "| cascade | 67% (4/6) | 33% (1/3) | 66.7% | 56% (5/9) | 80% (4/5) | $0.4444 | 20 | 29% (2/7) | 78% (7/9) |" in md
    assert "2 arxiv, 1 wikipedia" in md and "gen-model" in md and "1,000 input and 500 output tokens" in md and "seed 7" in md
    assert "number_swap | entity_swap" in md and "bench/build.py --dry-run" in md


def test_main_without_results_fails_cleanly(tmp_path):
    (tmp_path / "results").mkdir()
    assert main(tmp_path) == 1


def test_svg_is_transparent_with_one_labelled_point_per_config(tmp_path):
    pytest.importorskip("matplotlib")
    summary = {"nano": summarise("nano", ROWS, RUNS), "cascade": summarise("cascade", ROWS, RUNS)}
    assert report.plot_svg(summary, tmp_path / "x.svg")
    svg = (tmp_path / "x.svg").read_text()
    assert "<svg" in svg and "nano" in svg and "cascade" in svg and "cost per 100 claims" in svg
