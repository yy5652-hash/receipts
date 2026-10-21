"""Does "find a real source" find one? Measured on the misattributed, dead-link and fabricated items of ReceiptsBench.

    uv run python bench/suggest_eval.py [--tier nano] [--limit N] [--workers 4] [--out results_suggest]

For every gold item of type misattribution, dead_link and fabricated (items in bench/data/label_audit.json are skipped)
the claim alone is run through receipts.suggest.suggest_sources: Tavily search (cached) and the Nemotron judge of
--tier. The URL the report cites for the item is excluded from the hits, as in the app.

  misattribution, dead_link   the claim is true and its real origin is the gold origin_url: the share of items with a
                              supported suggestion, with a suggestion that is the origin page, with a contradicted
                              one, and with none.
  fabricated                  the cited paper does not exist; the claim itself may or may not be true: the
                              distribution of supported / contradicted / none, reported without a target.

Each item is saved to <out>/items.jsonl as soon as it is done (the run is resumable: finished items of the same tier
are skipped); <out>/summary.json and <out>/RESULTS.md are computed from those rows. --limit N runs N items, taken
round-robin over the three types, for a dry run. Cost is model tokens priced at the Token Factory rates of the tier;
Tavily searches are counted, not priced.
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
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from receipts.config import TIERS, Settings, load_settings  # noqa: E402
from receipts.models import Claim, FetchedSource, Source, Verdict  # noqa: E402
from receipts.search import DiskCache, Searcher, TavilySearcher  # noqa: E402
from receipts.suggest import normalize_url, suggest_sources  # noqa: E402
from receipts.textutil import strip_citations  # noqa: E402

HERE = Path(__file__).resolve().parent
TYPES = ("misattribution", "dead_link", "fabricated")
TRUE_CLAIM_TYPES = ("misattribution", "dead_link")  # the claim is true and has an origin page
STATUSES = ("supported", "contradicted", "none")


# --- items --------------------------------------------------------------------------------

def load_items(gold: dict, excluded: set[tuple[str, int]] | None = None) -> list[dict]:
    """One item per gold row of the three types, in report order: the claim, the URL it cites, its origin page."""
    skip = excluded or set()
    items = []
    for rid in sorted(gold["reports"]):
        for g in gold["reports"][rid]:
            if g["type"] not in TYPES or (rid, g["start"]) in skip:
                continue
            claim = g.get("original") or strip_citations(g["sentence"])
            items.append({"report": rid, "start": g["start"], "type": g["type"], "claim": claim,
                          "cited_url": g.get("source_url") or "", "origin_url": g.get("origin_url") or ""})
    return items


def round_robin(items: list[dict]) -> list[dict]:
    """Items interleaved by type (misattribution, dead_link, fabricated, misattribution, ...)."""
    by_type = [[i for i in items if i["type"] == t] for t in TYPES]
    out = []
    for k in range(max(map(len, by_type), default=0)):
        out += [lst[k] for lst in by_type if k < len(lst)]
    return out


def last_segment(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower()


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def same_page(url: str | None, origin: str | None) -> bool:
    """The normalised URLs are equal, or both are on one host and end in the same non-empty path segment
    (so an arXiv /abs/ link matches the /pdf/ link of the same paper)."""
    if not url or not origin:
        return False
    if normalize_url(url) == normalize_url(origin):
        return True
    seg = last_segment(origin)
    return bool(seg) and host_of(url) == host_of(origin) and last_segment(url) == seg


# --- one item -----------------------------------------------------------------------------

class CountingSearcher:
    """Counts the search calls an item makes (Tavily searches are counted, not priced)."""

    def __init__(self, inner: Searcher):
        self.inner, self.searches = inner, 0

    def extract(self, urls):
        return self.inner.extract(urls)

    def search(self, query, max_results=5):
        self.searches += 1
        return self.inner.search(query, max_results)


def run_item(item: dict, searcher: Searcher, make_llm, settings: Settings) -> dict:
    """suggest_sources on the item's claim alone; its row of items.jsonl."""
    cited = item["cited_url"]
    claim = Claim(id="c1", sentence=item["claim"], start=0, end=len(item["claim"]), claim=item["claim"], source_ids=["s1"])
    source = Source(id="s1", raw=cited or item["claim"][:60], url=cited or None)
    fetched = FetchedSource(source_id="s1", status="dead_link" if cited else "not_found", url=cited or None)
    verdict = Verdict(claim_id="c1", source_id="s1", label="source_unavailable", tier="none")
    llm, counter, t0 = make_llm(), CountingSearcher(searcher), time.monotonic()
    out = suggest_sources([claim], [source], [fetched], [verdict], counter, llm, settings)
    s = out[0] if out else None
    usage = list(llm.usage.values())
    return {
        "report": item["report"], "start": item["start"], "type": item["type"], "tier": settings.suggest_tier,
        "claim": item["claim"], "cited_url": cited, "origin_url": item["origin_url"],
        "status": s.status if s else "error", "url": s.url if s else None, "title": s.title if s else None,
        "quote_found": bool(s and s.quote_found), "candidates": s.candidates if s else 0,
        "origin_match": bool(s and same_page(s.url, item["origin_url"])),
        "cost_usd": round(sum(u.cost_usd for u in usage), 6),
        "prompt_tokens": sum(u.prompt_tokens for u in usage), "completion_tokens": sum(u.completion_tokens for u in usage),
        "model_calls": sum(u.calls for u in usage), "searches": counter.searches,
        "seconds": round(time.monotonic() - t0, 2),
    }


# --- metrics ------------------------------------------------------------------------------

def rate(k: int, n: int) -> dict:
    return {"rate": k / n if n else None, "k": k, "n": n}


def distribution(rows: list[dict]) -> dict:
    return {s: rate(sum(r["status"] == s for r in rows), len(rows)) for s in STATUSES}


def cost_stats(rows: list[dict]) -> dict:
    costs = [r["cost_usd"] for r in rows]
    return {
        "mean_usd": statistics.fmean(costs) if costs else None, "median_usd": statistics.median(costs) if costs else None,
        "total_usd": sum(costs), "model_calls_per_claim": statistics.fmean(r["model_calls"] for r in rows) if rows else None,
        "tokens_per_claim": statistics.fmean(r["prompt_tokens"] + r["completion_tokens"] for r in rows) if rows else None,
        "searches_per_claim": statistics.fmean(r["searches"] for r in rows) if rows else None,
        "median_seconds": statistics.median(r["seconds"] for r in rows) if rows else None,
    }


def summarise(rows: list[dict], tier: str) -> dict:
    """Every number of RESULTS.md, computed from the item rows of one tier."""
    rows = [r for r in rows if r["tier"] == tier and r["status"] != "error"]
    by = {t: [r for r in rows if r["type"] == t] for t in TYPES}
    true_claims = [r for t in TRUE_CLAIM_TYPES for r in by[t]]

    def true_claim_metrics(rs: list[dict]) -> dict:
        return {**distribution(rs),
                "origin_page": rate(sum(r["origin_match"] for r in rs), len(rs)),
                "supported_origin_page": rate(sum(r["origin_match"] and r["status"] == "supported" for r in rs), len(rs)),
                "supported_other_page": rate(sum(not r["origin_match"] and r["status"] == "supported" for r in rs), len(rs))}

    return {
        "tier": tier, "items": len(rows),
        "true_claims": {"all": true_claim_metrics(true_claims), **{t: true_claim_metrics(by[t]) for t in TRUE_CLAIM_TYPES}},
        "fabricated": distribution(by["fabricated"]),
        "cost": {"all": cost_stats(rows), **{t: cost_stats(by[t]) for t in TYPES}},
    }


def _pct(r: dict) -> str:
    return "n/a" if r["rate"] is None else f"{100 * r['rate']:.0f}% ({r['k']}/{r['n']})"


def render_results(summary: dict) -> str:
    tc, fab, cost = summary["true_claims"], summary["fabricated"], summary["cost"]
    L = [f"# Find a real source: results ({summary['tier']} judge)", "",
         f"{summary['items']} claims from ReceiptsBench: the claim alone, its cited URL excluded from the search hits. "
         "Computed by `bench/suggest_eval.py` from `items.jsonl`.", "",
         "## The claim is true: misattributed and dead-link items", "",
         "The real origin of each claim is the gold `origin_url`. A suggestion is the origin page when its URL equals the "
         "origin's, or has the same host and last path segment.", "",
         "| Items | n | Supported suggestion | Suggestion is the origin page | Contradicted | None |", "|---|---|---|---|---|---|"]
    for name, key in (("misattribution", "misattribution"), ("dead_link", "dead_link"), ("**Both**", "all")):
        m = tc[key]
        n = m["supported"]["n"]
        L.append(f"| {name} | {n} | {_pct(m['supported'])} | {_pct(m['origin_page'])} | {_pct(m['contradicted'])} | {_pct(m['none'])} |")
    m = tc["all"]
    L += ["", f"Supported suggestions: {_pct(m['supported_origin_page'])} are the origin page, "
          f"{_pct(m['supported_other_page'])} another page that states the claim.", "",
          "## The cited paper does not exist: fabricated items", "",
          "The claim itself may or may not be true, so there is no target; the distribution is descriptive.", "",
          "| n | Supported | Contradicted | None |", "|---|---|---|---|",
          f"| {fab['supported']['n']} | {_pct(fab['supported'])} | {_pct(fab['contradicted'])} | {_pct(fab['none'])} |", "",
          "## Cost per claim", "",
          "Model tokens at the Token Factory price of the tier; Tavily searches are counted, not priced.", "",
          "| Items | Mean | Median | Model calls | Tokens | Tavily searches | Median seconds |", "|---|---|---|---|---|---|---|"]
    for name, key in (("misattribution", "misattribution"), ("dead_link", "dead_link"), ("fabricated", "fabricated"),
                      ("**All**", "all")):
        c = cost[key]
        if c["mean_usd"] is None:
            continue
        L.append(f"| {name} | ${c['mean_usd']:.5f} | ${c['median_usd']:.5f} | {c['model_calls_per_claim']:.1f} | "
                 f"{c['tokens_per_claim']:.0f} | {c['searches_per_claim']:.1f} | {c['median_seconds']:.1f} |")
    L += ["", f"Total for the run: ${cost['all']['total_usd']:.4f}.", ""]
    return "\n".join(L)


# --- driver -------------------------------------------------------------------------------

def read_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def write_outputs(out: Path, tier: str) -> dict:
    summary = summarise(read_rows(out / "items.jsonl"), tier)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "RESULTS.md").write_text(render_results(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the find-a-real-source stage on ReceiptsBench.")
    ap.add_argument("--tier", choices=TIERS, default="nano", help="Nemotron tier that judges the pages found (default: nano)")
    ap.add_argument("--limit", type=int, default=0, help="run only N items, round-robin over the types (dry run)")
    ap.add_argument("--workers", type=int, default=4, help="items run in parallel")
    ap.add_argument("--out", default="results_suggest", help="results directory under bench/ (default: results_suggest)")
    args = ap.parse_args(argv)
    settings = load_settings()
    if not (settings.nebius_api_key and settings.tavily_api_key):
        print("error: set NEBIUS_API_KEY and TAVILY_API_KEY in .env", file=sys.stderr)
        return 2
    settings = dataclasses.replace(settings, suggest_tier=args.tier)
    gold = json.loads((HERE / "data" / "gold.json").read_text())
    audit_file = HERE / "data" / "label_audit.json"
    excluded = {(e["report"], e["start"]) for e in json.loads(audit_file.read_text()).get("excluded", [])} if audit_file.exists() else set()
    items = round_robin(load_items(gold, excluded))
    if args.limit:
        items = items[: args.limit]
    out = HERE / args.out
    out.mkdir(parents=True, exist_ok=True)
    path = out / "items.jsonl"
    done = {(r["report"], r["start"]) for r in read_rows(path) if r["tier"] == args.tier and r["status"] != "error"}
    todo = [i for i in items if (i["report"], i["start"]) not in done]
    print(f"{len(todo)} of {len(items)} items to run ({len(items) - len(todo)} already done), tier {args.tier}", flush=True)

    from receipts.llm import TokenFactoryLLM

    searcher = TavilySearcher(settings.tavily_api_key, DiskCache(settings.cache_dir))
    lock = threading.Lock()

    def work(item: dict) -> None:
        try:
            row = run_item(item, searcher, lambda: TokenFactoryLLM(settings), settings)
        except Exception as e:  # one failing item must not stop the rest; rerun to resume it
            print(f"{item['report']}@{item['start']}: FAILED ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr, flush=True)
            return
        with lock:
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(f"{row['report']}@{row['start']} {row['type']:<14} {row['status']:<12} ${row['cost_usd']:.5f}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(work, todo))
    summary = write_outputs(out, args.tier)
    print(f"{summary['items']} items summarised -> {out / 'summary.json'} and {out / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
