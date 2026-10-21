"""bench/suggest_eval.py: items, URL matching, one item through the fakes, metrics. Offline."""

import json

from bench import suggest_eval as se
from receipts.config import Settings
from receipts.testing import FakeLLM, FakeSearcher

PAGE = "# Scaled attention\n\nThe Transformer model uses scaled dot-product attention as its attention mechanism. It was introduced in 2017."
INDEX = [{"url": "https://arxiv.org/abs/1706.03762", "title": "Attention is all you need", "content": PAGE[:60], "raw_content": PAGE},
         {"url": "https://wiki.example/Three_Gorges_Dam", "title": "Dam", "content": "The cited page.", "raw_content": "The cited page about a dam."}]
GOLD = {"reports": {
    "r01": [
        {"start": 10, "type": "supported", "sentence": "S [1].", "source_url": "u", "origin_url": "u", "original": ""},
        {"start": 20, "type": "misattribution", "sentence": "The Transformer model uses scaled dot-product attention as its attention mechanism [1].",
         "source_url": "https://wiki.example/Three_Gorges_Dam", "origin_url": "https://arxiv.org/pdf/1706.03762",
         "original": "The Transformer model uses scaled dot-product attention as its attention mechanism."},
        {"start": 30, "type": "fabricated", "sentence": "Reefs resist heat in the north [4].", "source_url": "", "origin_url": "", "original": ""},
    ],
    "r02": [{"start": 5, "type": "dead_link", "sentence": "x [2].", "source_url": "https://d.example/x/2024-update", "origin_url": "https://d.example/x", "original": "A claim."},
            {"start": 7, "type": "misattribution", "sentence": "y [1].", "source_url": "", "origin_url": "", "original": "Another claim."}]}}


def test_items_cover_the_three_types_skip_the_audit_list_and_strip_the_marker():
    items = se.load_items(GOLD, excluded={("r02", 7)})
    assert [(i["report"], i["start"], i["type"]) for i in items] == [
        ("r01", 20, "misattribution"), ("r01", 30, "fabricated"), ("r02", 5, "dead_link")]
    assert items[1]["claim"] == "Reefs resist heat in the north."  # no original: the sentence without its marker
    assert items[0]["claim"].endswith("attention mechanism.") and items[0]["cited_url"] == "https://wiki.example/Three_Gorges_Dam"


def test_round_robin_gives_a_dry_run_every_type():
    items = se.round_robin(se.load_items(GOLD))
    assert [i["type"] for i in items[:3]] == ["misattribution", "dead_link", "fabricated"]
    assert len(items) == 4


def test_same_page():
    assert se.same_page("HTTPS://Example.org/a/b/", "https://example.org/a/b#x")
    assert se.same_page("https://arxiv.org/abs/1706.03762", "https://arxiv.org/pdf/1706.03762")
    assert se.same_page("https://www.who.int/x/obesity", "https://who.int/y/obesity")  # host and last segment
    assert not se.same_page("https://arxiv.org/abs/1706.03763", "https://arxiv.org/pdf/1706.03762")
    assert not se.same_page("https://other.org/1706.03762", "https://arxiv.org/pdf/1706.03762")
    assert not se.same_page(None, "https://a.example/x") and not se.same_page("https://a.example/x", "")
    assert not se.same_page("https://a.example/x", "https://a.example/")  # an origin without a last segment matches by equality only

def run(item):
    searcher = FakeSearcher({}, INDEX)
    return se.run_item(item, searcher, FakeLLM, Settings())


def test_run_item_finds_the_origin_and_excludes_the_cited_page():
    item = se.load_items(GOLD)[0]
    row = run(item)
    assert row["status"] == "supported" and row["url"] == "https://arxiv.org/abs/1706.03762"
    assert row["origin_match"] and row["quote_found"] and row["tier"] == "nano"
    assert row["searches"] == 1 and row["model_calls"] == row["candidates"] == 1  # the cited page was dropped, not judged
    assert row["cost_usd"] == 0.0  # the fake LLM reports no cost


def test_run_item_without_a_match_is_none():
    row = run({"report": "r9", "start": 1, "type": "fabricated", "claim": "Zebras compile Rust.", "cited_url": "", "origin_url": ""})
    assert row["status"] == "none" and row["url"] is None and not row["origin_match"]


def row(type_, status, origin=False, cost=0.001, tier="nano"):
    return {"report": "r", "start": 0, "type": type_, "tier": tier, "status": status, "origin_match": origin, "cost_usd": cost,
            "prompt_tokens": 100, "completion_tokens": 20, "model_calls": 3, "searches": 1, "seconds": 2.0}


def test_summary_and_results_table():
    rows = [row("misattribution", "supported", True), row("misattribution", "supported"), row("misattribution", "none"),
            row("dead_link", "contradicted"), row("dead_link", "supported", True),
            row("fabricated", "supported"), row("fabricated", "none"), row("fabricated", "none", cost=0.003),
            row("misattribution", "supported", True, tier="super"), {**row("dead_link", "error"), "status": "error"}]
    s = se.summarise(rows, "nano")
    assert s["items"] == 8  # the other tier and the failed item are not counted
    both = s["true_claims"]["all"]
    assert both["supported"]["k"] == 3 and both["supported"]["n"] == 5
    assert both["origin_page"]["k"] == 2 and both["supported_origin_page"]["k"] == 2 and both["supported_other_page"]["k"] == 1
    assert both["contradicted"]["k"] == 1 and both["none"]["k"] == 1
    assert s["true_claims"]["dead_link"]["supported"]["k"] == 1
    assert {k: v["k"] for k, v in s["fabricated"].items()} == {"supported": 1, "contradicted": 0, "none": 2}
    assert s["cost"]["fabricated"]["mean_usd"] == (0.001 + 0.001 + 0.003) / 3
    md = se.render_results(s)
    assert "| **Both** | 5 | 60% (3/5) | 40% (2/5) | 20% (1/5) | 20% (1/5) |" in md
    assert "| 3 | 33% (1/3) | 0% (0/3) | 67% (2/3) |" in md
    assert "Total for the run: $0.0100." in md


def test_empty_summary_does_not_crash():
    md = se.render_results(se.summarise([], "nano"))
    assert "n/a" in md


def test_outputs_are_written_from_the_rows(tmp_path):
    (tmp_path / "items.jsonl").write_text(json.dumps(row("misattribution", "supported", True)) + "\n")
    s = se.write_outputs(tmp_path, "nano")
    assert json.loads((tmp_path / "summary.json").read_text())["items"] == s["items"] == 1
    assert "Find a real source" in (tmp_path / "RESULTS.md").read_text()
