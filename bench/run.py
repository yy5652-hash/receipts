"""ReceiptsBench v1 runner: audit every benchmark report with each Nemotron configuration.

    uv run python bench/run.py [--configs nano,super,ultra,cascade,cascade_nano] [--reports r01,r02] [--workers 4]

Per report: extract once (Nano) and fetch sources once (Tavily + OpenAlex, cached), then judge once per
configuration with a fresh LLM client, so token usage and cost are separable. The shared extraction cost and
seconds are added to every configuration. Results are saved per configuration and the run is resumable.
"""

import argparse
import dataclasses
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from receipts.config import Settings, load_settings  # noqa: E402
from receipts.extract import extract  # noqa: E402
from receipts.judge import judge_all  # noqa: E402
from receipts.models import LABEL_ORDER  # noqa: E402
from receipts.scholarly import OpenAlex  # noqa: E402
from receipts.search import DiskCache, TavilySearcher, http_status  # noqa: E402
from receipts.sources import fetch_sources  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
# config -> (judge mode, cascade tiers)
CONFIGS = {"nano": ("nano", None), "super": ("super", None), "ultra": ("ultra", None),
           "cascade": ("cascade", ("super", "ultra")), "cascade_nano": ("cascade", ("nano", "ultra")),
           "routed": ("routed", None)}  # Nano checks; Ultra re-checks supported verdicts whose quote lacks the claim's figures
MIN_OVERLAP = 0.5  # a claim matches a gold sentence when it covers at least this share of it


def match_claim(item: dict, claims: list):
    """The extracted claim covering >= 50 % of the gold sentence (the largest overlap), else None."""
    best, best_ov = None, 0
    for c in claims:
        ov = min(item["end"], c.end) - max(item["start"], c.start)
        if ov > best_ov:
            best, best_ov = c, ov
    return best if best is not None and best_ov >= MIN_OVERLAP * (item["end"] - item["start"]) else None


def label_rows(rid: str, gold: list[dict], claims: list, verdicts: list) -> list[dict]:
    """One row per gold item: the best verdict across the claim's sources, or `missed` (no extracted claim / verdict)."""
    by_claim: dict[str, list] = {}
    for v in verdicts:
        by_claim.setdefault(v.claim_id, []).append(v)
    rows = []
    for g in gold:
        c = match_claim(g, claims)
        v = min(by_claim.get(c.id, []), key=lambda v: LABEL_ORDER.index(v.label), default=None) if c else None
        rows.append({"report": rid, "start": g["start"], "type": g["type"], "gold": g["gold"],
                     "predicted": v.label if v else "missed", "tier": v.tier if v else "none",
                     "escalated": bool(v and v.escalated), "confidence": v.confidence if v else 0.0,
                     "quote_found": bool(v and v.quote_found)})
    return rows


def cost(llm) -> float:
    return round(sum(u.cost_usd for u in llm.usage.values()), 6)


def run_report(rid: str, doc: str, gold: list[dict], configs: list[str], settings: Settings, make_llm, searcher,
               openalex, status_fn=http_status, shared: dict | None = None) -> tuple[dict, dict]:
    """-> ({config: rows}, {config: run record}). `shared` reuses the extraction/fetch record of an earlier run."""
    t0 = time.monotonic()
    llm = make_llm()
    sources, claims = extract(doc, llm)
    t1 = time.monotonic()
    fetched = fetch_sources(sources, searcher, status_fn=status_fn, openalex=openalex)
    t2 = time.monotonic()
    if shared is None:
        shared = {"extract_cost_usd": cost(llm), "extract_seconds": round(t1 - t0, 2), "fetch_seconds": round(t2 - t1, 2)}
    rows, runs = {}, {}
    for name in configs:
        mode, cascade = CONFIGS[name]
        s = dataclasses.replace(settings, cascade=cascade or settings.cascade)
        judge_llm, t3 = make_llm(), time.monotonic()
        verdicts = judge_all(claims, sources, fetched, judge_llm, mode, s)
        judge_seconds = time.monotonic() - t3
        rows[name] = label_rows(rid, gold, claims, verdicts)
        runs[name] = {**shared, "judge_cost_usd": cost(judge_llm), "judge_seconds": round(judge_seconds, 2),
                      "cost_usd": round(shared["extract_cost_usd"] + cost(judge_llm), 6),
                      "seconds": round(shared["extract_seconds"] + shared["fetch_seconds"] + judge_seconds, 2),
                      "claims": len(claims), "gold_items": len(gold)}
    return rows, runs


def load_results(path: Path = RESULTS) -> tuple[dict, dict]:
    """(runs, rows per config); rows of a (report, config) pair without a run record (a crash) are dropped."""
    runs = json.loads((path / "runs.json").read_text()) if (path / "runs.json").exists() else {}
    rows = {}
    for name in CONFIGS:
        f = path / f"{name}.jsonl"
        lines = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
        rows[name] = [r for r in lines if name in runs.get(r["report"], {})]
    return runs, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run ReceiptsBench v1.")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--reports", default="", help="comma-separated report ids (default: all)")
    ap.add_argument("--workers", type=int, default=4, help="reports audited in parallel")
    ap.add_argument("--out", default="results", help="results directory under bench/ (default: results)")
    args = ap.parse_args(argv)
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    if bad := [c for c in configs if c not in CONFIGS]:
        print(f"error: unknown config {bad}; choose from {list(CONFIGS)}", file=sys.stderr)
        return 2
    settings = load_settings()
    if not (settings.nebius_api_key and settings.tavily_api_key):
        print("error: set NEBIUS_API_KEY and TAVILY_API_KEY in .env", file=sys.stderr)
        return 2
    gold = json.loads((HERE / "data" / "gold.json").read_text())["reports"]
    ids = [r.strip() for r in args.reports.split(",") if r.strip()] or sorted(gold)
    out = HERE / args.out
    out.mkdir(parents=True, exist_ok=True)
    runs, rows = load_results(out)
    for name in CONFIGS:  # drop rows of interrupted pairs, keep the file in step with runs.json
        (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows[name]))
    todo = {rid: [c for c in configs if c not in runs.get(rid, {})] for rid in ids}
    todo = {rid: cs for rid, cs in todo.items() if cs}
    print(f"{sum(len(c) for c in todo.values())} (report, config) pairs to run over {len(todo)} reports", flush=True)

    from receipts.llm import TokenFactoryLLM

    cache = DiskCache(settings.cache_dir)
    searcher, openalex, lock = TavilySearcher(settings.tavily_api_key, cache), OpenAlex(cache), threading.Lock()

    def work(rid: str) -> None:
        prior = next(iter(runs.get(rid, {}).values()), None)
        shared = {k: prior[k] for k in ("extract_cost_usd", "extract_seconds", "fetch_seconds")} if prior else None
        doc = (HERE / "data" / "reports" / f"{rid}.md").read_text(encoding="utf-8")
        new_rows, new_runs = run_report(rid, doc, gold[rid], todo[rid], settings, lambda: TokenFactoryLLM(settings),
                                        searcher, openalex, shared=shared)
        with lock:
            for name, r in new_rows.items():
                with (out / f"{name}.jsonl").open("a") as f:
                    f.write("".join(json.dumps(x) + "\n" for x in r))
            runs.setdefault(rid, {}).update(new_runs)
            (out / "runs.json").write_text(json.dumps(runs, indent=1, sort_keys=True))
            print(f"{rid}: " + ", ".join(f"{n} ${r['cost_usd']:.4f} {r['seconds']:.0f}s" for n, r in new_runs.items()), flush=True)

    def safe(rid: str) -> None:
        try:
            work(rid)
        except Exception as e:  # one failing report must not stop the rest; rerun to resume it
            print(f"{rid}: FAILED ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(safe, todo))
    _, rows = load_results(out)
    for name in configs:
        secs = [r[name]["seconds"] for r in runs.values() if name in r]
        print(f"{name}: {len(rows[name])} items, median {statistics.median(secs):.0f}s per report" if secs else f"{name}: no runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
