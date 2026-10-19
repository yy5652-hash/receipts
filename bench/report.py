"""ReceiptsBench v1 metrics: bench/results/*.jsonl + runs.json -> bench/RESULTS.md, results/summary.json, results/cost_vs_quality.svg.

    uv run --group bench python bench/report.py

Every number in the three outputs is computed from the result files; nothing is typed by hand.
"""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from receipts.config import DEFAULT_PRICES  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIGS = ["nano", "super", "ultra", "cascade", "cascade_nano"]
TYPES = ["number_swap", "entity_swap", "overclaim", "misattribution", "fabricated", "dead_link"]
DETECTED = {"partial", "contradicted", "not_in_source", "source_unavailable"}  # predicted as a problem
CLASS = {"supported": "supported", "partial": "misstated", "contradicted": "misstated",
         "not_in_source": "not_in_source", "source_unavailable": "source_unavailable"}  # the 4 accuracy classes
CHECKED = {"supported", "partial", "contradicted"}  # predictions that must quote their evidence


def rate(k: int, n: int) -> dict:
    return {"rate": k / n if n else None, "k": k, "n": n}


def summarise(name: str, rows: list[dict], runs: dict) -> dict:
    """Metrics of one configuration. rows: its result rows; runs: {report: run record of this configuration}."""
    hit = lambda r: r["predicted"] in DETECTED  # noqa: E731
    sup = [r for r in rows if r["gold"] == "supported"]
    bad = [r for r in rows if r["gold"] != "supported"]
    answered = [r for r in rows if r["predicted"] != "missed"]
    checked = [r for r in rows if r["predicted"] in CHECKED]
    reports = {r["report"] for r in rows}
    recall, alarm = rate(sum(map(hit, bad)), len(bad)), rate(sum(map(hit, sup)), len(sup))
    secs = [runs[r]["seconds"] for r in reports if r in runs]
    return {
        "items": len(rows), "reports": len(reports),
        "problem_recall": recall, "false_alarm": alarm,
        "supported_pass": rate(sum(r["predicted"] == "supported" for r in sup), len(sup)),
        "macro_accuracy": (recall["rate"] + 1 - alarm["rate"]) / 2 if bad and sup else None,
        "per_type": {t: rate(sum(map(hit, [r for r in rows if r["type"] == t])), sum(r["type"] == t for r in rows)) for t in TYPES},
        "label_accuracy": rate(sum(CLASS.get(r["predicted"]) == CLASS[r["gold"]] for r in rows), len(rows)),
        "evidence_found": rate(sum(r["quote_found"] for r in checked), len(checked)),
        "cost_per_100_claims_usd": 100 * sum(runs[r]["cost_usd"] for r in reports if r in runs) / len(rows) if rows else None,
        "median_seconds_per_report": statistics.median(secs) if secs else None,
        "escalated": rate(sum(r["escalated"] for r in answered), len(answered)) if name.startswith("cascade") else None,
        "extraction_coverage": rate(len(answered), len(rows)),
    }


def dataset_info(data: Path) -> dict:
    gold = json.loads((data / "gold.json").read_text())
    rows = [r for rs in gold["reports"].values() for r in rs]
    used = json.loads((data / "sources_used.json").read_text()) if (data / "sources_used.json").exists() else []
    cost = json.loads((data / "generation_cost.json").read_text()) if (data / "generation_cost.json").exists() else None
    return {"version": gold["version"], "generator": gold["generator"], "seed": gold["seed"], "built_at": gold["built_at"],
            "reports": len(gold["reports"]), "items": len(rows),
            "by_type": {t: sum(r["type"] == t for r in rows) for t in ["supported", *TYPES]},
            "sources": len(used), "sources_by_kind": {k: sum(s["kind"] == k for s in used) for k in sorted({s["kind"] for s in used})},
            "generation": cost}


def pct(m: dict | None, counts: bool = True) -> str:
    if not m or m["rate"] is None:
        return "-"
    return f"{100 * m['rate']:.0f}%" + (f" ({m['k']}/{m['n']})" if counts else "")


def plot_svg(configs: dict, path: Path) -> bool:
    """Cost per 100 claims (log x) against macro accuracy; transparent, mid-grey text so it reads on light and dark."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    plt.rcParams["svg.fonttype"] = "none"
    grey = "#808080"
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    for name, s in configs.items():
        if s["macro_accuracy"] is None or not s["cost_per_100_claims_usd"]:
            continue
        x, y = s["cost_per_100_claims_usd"], 100 * s["macro_accuracy"]
        ax.scatter([x], [y], s=80, color="#e8a23a" if name.startswith("cascade") else "#4a90d9", zorder=3)
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(8, 6), color=grey, fontsize=10)
    ax.set_xscale("log")
    ax.set_xlabel("cost per 100 claims (USD, log scale, incl. extraction)", color=grey)
    ax.set_ylabel("macro accuracy (%): mean of problem recall and 1 - false alarms", color=grey)
    ax.tick_params(colors=grey, which="both")
    ax.grid(color=grey, alpha=0.25)
    for sp in ax.spines.values():
        sp.set_color(grey)
    fig.tight_layout()
    fig.savefig(path, transparent=True, format="svg")
    plt.close(fig)
    return True


def markdown(summary: dict, ds: dict, has_svg: bool) -> str:
    cfg = summary["configs"]
    L = ["# ReceiptsBench v1 results", "",
         f"{ds['items']} cited sentences in {ds['reports']} reports, each with a gold label. "
         "Every number below is computed from `bench/results/*.jsonl` and `runs.json` by `bench/report.py`.", "",
         "## Results per configuration", "",
         "| Config | Problem recall | False alarms | Macro accuracy | Label accuracy (4 classes) | Evidence quoted verbatim | "
         "Cost / 100 claims | Median s / report | Escalated | Extraction coverage |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for n, s in cfg.items():
        macro = "-" if s["macro_accuracy"] is None else "{:.1f}%".format(100 * s["macro_accuracy"])
        L.append(f"| {n} | {pct(s['problem_recall'])} | {pct(s['false_alarm'])} | {macro} | {pct(s['label_accuracy'])} | "
                 f"{pct(s['evidence_found'])} | ${s['cost_per_100_claims_usd']:.4f} | {s['median_seconds_per_report']:.0f} | "
                 f"{pct(s['escalated']) if s['escalated'] else '-'} | {pct(s['extraction_coverage'])} |")
    L += ["", "## Detection rate per problem type", "",
          "| Config | " + " | ".join(TYPES) + " | supported passed |", "|---|" + "---|" * (len(TYPES) + 1)]
    for n, s in cfg.items():
        L.append(f"| {n} | " + " | ".join(pct(s["per_type"][t], False) for t in TYPES) + f" | {pct(s['supported_pass'], False)} |")
    prices = ", ".join(f"{t} ${a:.2f} in / ${b:.2f} out" for t, (a, b) in DEFAULT_PRICES.items())
    L += ["", "## Definitions", "",
          "- **Problem recall**: share of non-supported gold items predicted as anything other than `supported` "
          "(an item with no extracted claim counts as not detected).",
          "- **False alarms**: share of supported gold items predicted as non-supported (a missed item raises no alarm).",
          "- **Macro accuracy**: mean of problem recall and (1 - false-alarm rate).",
          "- **Label accuracy**: four classes: supported / misstated (partial or contradicted) / not_in_source / source_unavailable.",
          "- **Evidence quoted verbatim**: share of supported, partial and contradicted predictions whose quote was found "
          "word for word in the fetched source.",
          "- **Cost**: Token Factory tokens at the list prices per 1M tokens (" + prices + "), including the Nano extraction "
          "shared by every configuration. Tavily and OpenAlex calls are not priced.",
          "- **Seconds**: extraction + source fetching + judging per report, measured with several reports audited in parallel.",
          "- **Escalated**: cascade configurations only; `cascade` is Super then Ultra, `cascade_nano` is Nano then Ultra.",
          "- Gold labels: number_swap and entity_swap are `contradicted`, overclaim is `partial` (scored as a problem either way), "
          "misattribution is `not_in_source`, fabricated and dead_link are `source_unavailable`.", ""]
    if has_svg:
        L += ["![cost versus quality](results/cost_vs_quality.svg)", ""]
    g = ds["generation"]
    L += ["## Dataset", "",
          f"- Version `{ds['version']}`, built {ds['built_at']}, random seed {ds['seed']}.",
          f"- {ds['reports']} reports, {ds['items']} cited sentences: " + ", ".join(f"{n} {t}" for t, n in ds["by_type"].items()) + ".",
          f"- {ds['sources']} real sources cited: " + ", ".join(f"{n} {k}" for k, n in ds["sources_by_kind"].items()) + ".",
          f"- Test items written by `{ds['generator']}` (temperature 0.3), an independent non-NVIDIA model, so the Nemotron "
          "judges do not grade their own phrasing; number swaps are programmatic; every supported claim's quote is verified "
          "verbatim in its source; fabricated references are absent from Crossref (title ratio < 0.75); dead links answer 404/410."]
    if g:
        L.append(f"- Generation: {g['prompt_tokens']:,} input and {g['completion_tokens']:,} output tokens, ${g['usd']:.4f}.")
    L += ["", "## Reproduce", "", "```",
          "uv run python bench/build.py --dry-run     # fetch and validate the sources, no LLM call",
          "uv run python bench/build.py               # once; writes the frozen dataset to bench/data/",
          "uv run python bench/run.py                 # all configurations; resumable; needs NEBIUS_API_KEY and TAVILY_API_KEY",
          "uv run --group bench python bench/report.py", "```", ""]
    return "\n".join(L)


def main(root: Path = HERE) -> int:
    results = root / "results"
    runs = json.loads((results / "runs.json").read_text()) if (results / "runs.json").exists() else {}
    configs = {}
    for name in CONFIGS:
        f = results / f"{name}.jsonl"
        rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
        rows = [r for r in rows if name in runs.get(r["report"], {})]
        if rows:
            configs[name] = summarise(name, rows, {rid: rec[name] for rid, rec in runs.items() if name in rec})
    if not configs:
        print("error: no results in bench/results/; run bench/run.py first", file=sys.stderr)
        return 1
    ds = dataset_info(root / "data")
    summary = {"benchmark": ds["version"], "dataset": ds, "configs": configs}
    (results / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    has_svg = plot_svg(configs, results / "cost_vs_quality.svg")
    (root / "RESULTS.md").write_text(markdown(summary, ds, has_svg), encoding="utf-8")
    print(f"wrote bench/RESULTS.md and results/summary.json for {', '.join(configs)}"
          + ("" if has_svg else "; no SVG (install the bench group: uv run --group bench python bench/report.py)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
