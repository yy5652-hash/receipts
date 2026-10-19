import json

import pytest

from bench import report
from bench.report import main, summarise


def row(rep, typ, gold, pred, qf=False, esc=False, start=0):
    return {"report": rep, "start": start, "type": typ, "gold": gold, "predicted": pred, "tier": "super", "escalated": esc,
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
    assert summarise("nano", ROWS, RUNS)["escalated"] is None  # single-tier configurations never escalate
    assert summarise("routed", ROWS, RUNS)["escalated"]["k"] == 2  # routed is two-tier
    assert summarise("cascade", ROWS, RUNS, audited=10)["cost_per_100_claims_usd"] == pytest.approx(100 * 0.04 / 10)


# A tiny benchmark: r01 is a development report, r11 a test report. (r01, 20) is excluded by the label audit.
GOLD = {"r01": [(0, "supported", "supported"), (10, "number_swap", "contradicted"), (20, "overclaim", "partial")],
        "r11": [(0, "supported", "supported"), (10, "overclaim", "partial"), (20, "fabricated", "source_unavailable")]}
PERFECT = {"r01": ["supported", "contradicted", "partial"], "r11": ["supported", "partial", "source_unavailable"]}
# nano: misses the excluded overclaim (r01, 20), false-alarms on (r11, 0), misses the overclaim (r11, 10)
NANO = {"r01": ["supported", "contradicted", "supported"], "r11": ["contradicted", "supported", "source_unavailable"]}
NANO_V1 = {"r01": ["supported", "supported", "supported"], "r11": ["supported", "supported", "source_unavailable"]}
ULTRA_V1 = {"r01": PERFECT["r01"], "r11": ["supported", "supported", "source_unavailable"]}
AUDIT = {"version": "ReceiptsBench v1.1", "audited": "2026-10-19", "method": "Every overclaim was read against its source.",
         "excluded": [{"report": "r01", "start": 20, "reason": "True as stated | really."}]}


def write_results(directory, preds_by_config):
    directory.mkdir(parents=True)
    runs = {}
    for name, preds in preds_by_config.items():
        rows = []
        for rep, items in GOLD.items():
            for (start, typ, gold), pred in zip(items, preds[rep]):
                rows.append(row(rep, typ, gold, pred, qf=pred in report.CHECKED, esc=name in report.TWO_TIER and pred == "partial", start=start))
            runs.setdefault(rep, {})[name] = {"cost_usd": 0.03, "seconds": 20.0 if rep == "r01" else 40.0}
        (directory / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (directory / "runs.json").write_text(json.dumps(runs))


def make_root(tmp_path, results, baseline=None):
    (tmp_path / "data").mkdir()
    gold = {"version": "ReceiptsBench v1", "generator": "gen-model", "seed": 7, "built_at": "2026-10-20T00:00:00+00:00",
            "reports": {rep: [{"start": s, "type": t, "gold": g, "sentence": f"Sentence {rep}/{s} " + "word " * 40 + "[1]."}
                              for s, t, g in items] for rep, items in GOLD.items()}}
    (tmp_path / "data" / "gold.json").write_text(json.dumps(gold))
    (tmp_path / "data" / "label_audit.json").write_text(json.dumps(AUDIT))
    (tmp_path / "data" / "sources_used.json").write_text(json.dumps([{"id": "a", "kind": "arxiv"}, {"id": "b", "kind": "wikipedia"}, {"id": "c", "kind": "arxiv"}]))
    (tmp_path / "data" / "generation_cost.json").write_text(json.dumps({"prompt_tokens": 1000, "completion_tokens": 500, "usd": 0.0005}))
    write_results(tmp_path / "results", results)
    if baseline:
        write_results(tmp_path / "base", baseline)
    return tmp_path


def run(root, *extra):
    assert main(["--results", "results", "--baseline", "base", *extra], root=root) == 0
    return json.loads((root / "results" / "summary.json").read_text()), (root / "RESULTS.md").read_text()


def section(md, title):
    body = md.split(f"## {title}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def test_label_audit_exclusion_is_applied_to_scores_and_counts(tmp_path):
    root = make_root(tmp_path, {"nano": NANO}, {"nano": NANO_V1})
    summary, md = run(root)
    s = summary["configs"]["nano"]["all"]
    assert s["items"] == 5  # 6 gold items, one excluded
    assert (s["problem_recall"]["k"], s["problem_recall"]["n"]) == (2, 3)  # the missed excluded overclaim does not count
    assert (s["false_alarm"]["k"], s["false_alarm"]["n"]) == (1, 2)
    assert s["per_type"]["overclaim"] == {"rate": 0.0, "k": 0, "n": 1}
    assert s["cost_per_100_claims_usd"] == pytest.approx(100 * 0.06 / 6)  # excluded items were audited, so they were paid for
    assert summary["dataset"]["items"] == 6 and summary["dataset"]["scored_items"] == 5
    assert summary["dataset"]["by_type"]["overclaim"] == 1 and summary["dataset"]["by_type_all"]["overclaim"] == 2
    assert summary["excluded"] == [{"report": "r01", "start": 20}]
    assert "| 5 | 67% (2/3) | 50% (1/2) |" in section(md, "All reports")
    assert "excluded 1 of them, so 5 items are scored" in md
    # the baseline is not scored on the excluded item either
    assert summary["prompt_comparison"]["nano"]["items"] == 5


def test_dev_and_test_split_numbers(tmp_path):
    root = make_root(tmp_path, {"nano": NANO})
    summary, md = run(root)
    dev, test = summary["configs"]["nano"]["dev"], summary["configs"]["nano"]["test"]
    assert dev["items"] == 2 and dev["macro_accuracy"] == 1.0  # the excluded overclaim is gone from r01
    assert (dev["problem_recall"]["k"], dev["problem_recall"]["n"]) == (1, 1)
    assert test["items"] == 3 and test["macro_accuracy"] == pytest.approx((1 / 2 + 0) / 2)
    assert (test["problem_recall"]["k"], test["problem_recall"]["n"]) == (1, 2)
    assert "| 100.0% | 100% (1/1) | 25.0% | 50% (1/2) |" in section(md, "Development and test reports")


def test_prompt_table_lists_only_configs_in_both_directories(tmp_path):
    root = make_root(tmp_path, {"ultra": PERFECT, "nano": NANO, "routed": NANO},
                     {"ultra": ULTRA_V1, "nano": NANO_V1, "cascade": PERFECT})
    summary, md = run(root)
    assert list(summary["prompt_comparison"]) == ["ultra", "nano"]
    table = section(md, "Judge prompt v1 → v2")
    assert "(`ultra`)" in table and "(`nano`)" in table and "`routed`" not in table and "`cascade`" not in table
    assert "scope or certainty the source does not state" in table
    # ultra on the 5 scored items: the v1 prompt misses the r11 overclaim (recall 2/3), the v2 prompt catches everything
    assert "| Nemotron 3 Ultra (`ultra`) | 5 | 83.3% | 100.0% | 0% (0/1) | 100% (1/1) |" in table
    assert "| Nemotron 3 Nano (`nano`) | 5 | 66.7% | 58.3% | 0% (0/1) | 0% (0/1) |" in table


def test_configs_follow_the_display_order_whatever_the_file_order(tmp_path):
    preds = {name: PERFECT for name in ("cascade", "nano", "routed", "ultra", "cascade_nano", "super")}
    root = make_root(tmp_path, preds)
    summary, md = run(root)
    order = ["ultra", "super", "nano", "routed", "cascade_nano", "cascade"]
    assert list(summary["configs"]) == order == report.CONFIGS
    table = section(md, "All reports")
    positions = [table.index(f"(`{n}`)") for n in order]
    assert positions == sorted(positions)
    for n in order:
        assert f"{report.NAMES[n]} (`{n}`)" in table
    assert report.NAMES["routed"] == "Nano → Ultra, re-check unbacked 'supported'"
    assert report.NAMES["cascade"] == "Super → Ultra, re-check doubts"
    assert "re-checks only 'supported' verdicts whose quote does not show the claim's figures or absolute words" in md


def test_works_with_missing_configs_and_no_baseline(tmp_path):
    root = make_root(tmp_path, {"cascade": PERFECT})
    assert main(["--results", "results", "--baseline", "absent"], root=root) == 0
    summary = json.loads((root / "results" / "summary.json").read_text())
    md = (root / "RESULTS.md").read_text()
    assert list(summary["configs"]) == ["cascade"] and summary["prompt_comparison"] == {}
    assert "## Judge prompt v1 → v2" not in md and "(`nano`)" not in md
    assert "| 5 | 100% (3/3) | 0% (0/2) | 100.0% |" in section(md, "All reports")
    assert "| 100.0% | 100% (1/1) | 100.0% | 100% (2/2) |" in section(md, "Development and test reports")


def test_missing_split_shows_dashes(tmp_path):
    root = make_root(tmp_path, {"nano": NANO})
    (root / "results" / "nano.jsonl").write_text("".join(  # only the test report r11 was run
        x + "\n" for x in (root / "results" / "nano.jsonl").read_text().splitlines() if '"r11"' in x))
    summary, md = run(root)
    assert summary["configs"]["nano"]["dev"] is None
    assert "| Nemotron 3 Nano (`nano`) | - | - | 25.0% | 50% (1/2) |" in md


def test_label_audit_section_lists_the_excluded_sentence_and_reason(tmp_path):
    root = make_root(tmp_path, {"nano": NANO})
    _, md = run(root)
    audit = section(md, "Label audit")
    assert AUDIT["method"] in audit
    line = next(x for x in audit.splitlines() if x.startswith("| r01 |"))
    sentence = line.split("|")[2].strip()
    assert sentence.startswith("Sentence r01/20 word") and sentence.endswith("…") and len(sentence) <= 125
    assert "True as stated / really." in line  # a pipe in the reason does not break the table


def test_default_markdown_path_svg_embed_and_baseline_untouched(tmp_path):
    pytest.importorskip("matplotlib")
    root = make_root(tmp_path, {"nano": NANO}, {"nano": NANO_V1})
    before = sorted(p.name for p in (root / "base").iterdir())
    _, md = run(root)
    assert (root / "results" / "cost_vs_quality.svg").exists() and "![" in md and "(results/cost_vs_quality.svg)" in md
    assert sorted(p.name for p in (root / "base").iterdir()) == before  # the baseline is only read
    out = tmp_path / "out" / "R.md"
    out.parent.mkdir()
    assert main(["--results", str(root / "results"), "--baseline", "base", "--md", "out/R.md"], root=root) == 0
    assert "(../results/cost_vs_quality.svg)" in out.read_text()


def test_main_writes_markdown_and_summary_from_the_result_files(tmp_path):
    root = make_root(tmp_path, {"cascade": NANO, "nano": PERFECT})
    summary, md = run(root)
    assert summary["dataset"]["by_type"]["number_swap"] == 1 and summary["benchmark"] == "ReceiptsBench v1"
    assert "2 arxiv, 1 wikipedia" in md and "gen-model" in md and "1,000 input and 500 output tokens" in md and "seed 7" in md
    assert "number_swap | entity_swap" in md and "bench/build.py --dry-run" in md
    assert "bench/run.py --out results_final" in md and "report.py --results results_final --baseline results" in md
    for part in ("All reports", "By problem type", "Development and test reports", "Label audit", "Definitions", "Dataset", "Reproduce"):
        assert f"## {part}\n" in md
    assert md.startswith("# ReceiptsBench results")


def test_main_without_results_fails_cleanly(tmp_path):
    (tmp_path / "results").mkdir()
    assert main(["--results", "results"], root=tmp_path) == 1


def test_svg_is_transparent_with_one_labelled_point_per_config(tmp_path):
    pytest.importorskip("matplotlib")
    summary = {"nano": summarise("nano", ROWS, RUNS), "cascade": summarise("cascade", ROWS, RUNS)}
    assert report.plot_svg(summary, tmp_path / "x.svg")
    svg = (tmp_path / "x.svg").read_text()
    assert "<svg" in svg and "Nemotron 3 Nano" in svg and "Super → Ultra, re-check doubts" in svg and "cost per 100 claims" in svg
