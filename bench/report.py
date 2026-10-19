"""ReceiptsBench metrics: <results>/*.jsonl + runs.json -> bench/RESULTS.md, <results>/summary.json, <results>/cost_vs_quality.svg.

    uv run --group bench python bench/report.py [--results results_final] [--baseline results] [--md PATH]

--results / --baseline are directories under bench/ (or absolute paths); --baseline holds the same benchmark run with the v1
judge prompt and feeds the v1 -> v2 table. Items listed in bench/data/label_audit.json are not scored anywhere. Every number
in the outputs is computed from the result files; nothing is typed by hand.
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from receipts.config import DEFAULT_PRICES  # noqa: E402

HERE = Path(__file__).resolve().parent
NAMES = {"ultra": "Nemotron 3 Ultra", "super": "Nemotron 3 Super", "nano": "Nemotron 3 Nano",
         "routed": "Nano → Ultra, re-check unbacked 'supported'", "cascade_nano": "Nano → Ultra, re-check doubts",
         "cascade": "Super → Ultra, re-check doubts"}
CONFIGS = list(NAMES)  # display order
TWO_TIER = {"routed", "cascade_nano", "cascade"}
TYPES = ["number_swap", "entity_swap", "overclaim", "misattribution", "fabricated", "dead_link"]
DETECTED = {"partial", "contradicted", "not_in_source", "source_unavailable"}  # predicted as a problem
CLASS = {"supported": "supported", "partial": "misstated", "contradicted": "misstated",
         "not_in_source": "not_in_source", "source_unavailable": "source_unavailable"}  # the 4 accuracy classes
CHECKED = {"supported", "partial", "contradicted"}  # predictions that must quote their evidence
SPLITS = {"dev": {f"r{i:02d}" for i in range(1, 11)}, "test": {f"r{i:02d}" for i in range(11, 31)}}


def rate(k: int, n: int) -> dict:
    return {"rate": k / n if n else None, "k": k, "n": n}


def summarise(name: str, rows: list[dict], runs: dict, audited: int | None = None) -> dict:
    """Metrics of one configuration. rows: its scored result rows; runs: {report: run record of this configuration}.
    audited: claims the runs were paid for (defaults to len(rows); larger when the label audit removed rows from scoring)."""
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
        "cost_per_100_claims_usd": 100 * sum(runs[r]["cost_usd"] for r in reports if r in runs) / (audited or len(rows)) if rows else None,
        "median_seconds_per_report": statistics.median(secs) if secs else None,
        "escalated": rate(sum(r["escalated"] for r in answered), len(answered)) if name in TWO_TIER else None,
        "extraction_coverage": rate(len(answered), len(rows)),
    }


def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def load_audit(data: Path) -> dict:
    audit = read_json(data / "label_audit.json", {})
    return {"version": audit.get("version"), "audited": audit.get("audited"), "method": audit.get("method", ""),
            "excluded": audit.get("excluded", [])}


def excluded_keys(audit: dict) -> set[tuple[str, int]]:
    return {(e["report"], e["start"]) for e in audit["excluded"]}


def load_dir(directory: Path) -> dict[str, tuple[list[dict], dict]]:
    """{config: (result rows with a run record, {report: run record of that config})} for the configurations found."""
    runs = read_json(directory / "runs.json", {})
    out = {}
    for name in CONFIGS:
        f = directory / f"{name}.jsonl"
        rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
        rows = [r for r in rows if name in runs.get(r["report"], {})]
        if rows:
            out[name] = (rows, {rid: rec[name] for rid, rec in runs.items() if name in rec})
    return out


def metrics(name: str, rows: list[dict], runs: dict, excluded: set, reports: set | None = None) -> dict | None:
    """Metrics of one configuration on the scored rows (label-audit exclusions removed) of the given reports."""
    sel = [r for r in rows if reports is None or r["report"] in reports]
    scored = [r for r in sel if (r["report"], r["start"]) not in excluded]
    return summarise(name, scored, runs, audited=len(sel)) if scored else None


def compare_prompts(base: dict, new: dict, excluded: set) -> dict:
    """v1 (baseline) against v2 (results) for the configurations in both, on the scored items both directories have."""
    brief = lambda s: {k: s[k] for k in ("macro_accuracy", "problem_recall", "false_alarm")} | {"overclaim": s["per_type"]["overclaim"]}  # noqa: E731
    out = {}
    for name in CONFIGS:
        if name not in base or name not in new:
            continue
        (brows, bruns), (nrows, nruns) = base[name], new[name]
        keys = ({(r["report"], r["start"]) for r in brows} & {(r["report"], r["start"]) for r in nrows}) - excluded
        if not keys:
            continue
        pick = lambda rows: [r for r in rows if (r["report"], r["start"]) in keys]  # noqa: E731
        out[name] = {"items": len(keys), "reports": len({k[0] for k in keys}),
                     "v1": brief(summarise(name, pick(brows), bruns)), "v2": brief(summarise(name, pick(nrows), nruns))}
    return out


def dataset_info(data: Path, audit: dict) -> dict:
    gold = json.loads((data / "gold.json").read_text())
    rows = [dict(r, report=rid) for rid, rs in gold["reports"].items() for r in rs]
    ex = excluded_keys(audit)
    scored = [r for r in rows if (r["report"], r["start"]) not in ex]
    used = read_json(data / "sources_used.json", [])
    kinds = ["supported", *TYPES]
    return {"version": gold["version"], "generator": gold["generator"], "seed": gold["seed"], "built_at": gold["built_at"],
            "reports": len(gold["reports"]), "items": len(rows), "scored_items": len(scored),
            "by_type": {t: sum(r["type"] == t for r in scored) for t in kinds},
            "by_type_all": {t: sum(r["type"] == t for r in rows) for t in kinds},
            "sources": len(used), "sources_by_kind": {k: sum(s["kind"] == k for s in used) for k in sorted({s["kind"] for s in used})},
            "generation": read_json(data / "generation_cost.json", None),
            "label_audit": {"version": audit["version"], "audited": audit["audited"], "method": audit["method"],
                            "excluded": [{"report": r["report"], "start": r["start"], "type": r["type"], "sentence": r["sentence"],
                                          "reason": e.get("reason", "")}
                                         for e in audit["excluded"] for r in rows if (r["report"], r["start"]) == (e["report"], e["start"])]}}


def shorten(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def pct(m: dict | None, counts: bool = True) -> str:
    if not m or m["rate"] is None:
        return "-"
    return f"{100 * m['rate']:.0f}%" + (f" ({m['k']}/{m['n']})" if counts else "")


def macro(s: dict | None) -> str:
    return "-" if not s or s["macro_accuracy"] is None else "{:.1f}%".format(100 * s["macro_accuracy"])


def plot_svg(configs: dict, path: Path) -> bool:
    """Cost per 100 claims (log x) against macro accuracy; transparent, mid-grey text so it reads on light and dark."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter, NullLocator
        from matplotlib.transforms import Bbox
    except ImportError:
        return False
    plt.rcParams["svg.fonttype"] = "none"
    grey = "#808080"
    pts = sorted((s["cost_per_100_claims_usd"], 100 * s["macro_accuracy"], n) for n, s in configs.items()
                 if s["macro_accuracy"] is not None and s["cost_per_100_claims_usd"])
    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    for x, y, name in pts:
        ax.scatter([x], [y], s=80, color="#e8a23a" if name in TWO_TIER else "#4a90d9", zorder=3)
    ax.set_xscale("log")
    if pts:
        ax.set_xlim(pts[0][0] / 1.5, pts[-1][0] * 1.5)
        ticks = [t for t in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5) if ax.get_xlim()[0] <= t <= ax.get_xlim()[1]]
        if len(ticks) >= 2:
            ax.set_xticks(ticks)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:g}"))
            ax.xaxis.set_minor_locator(NullLocator())
    ax.margins(y=0.25)
    ax.set_xlabel("cost per 100 claims (USD, log scale, incl. extraction)", color=grey)
    ax.set_ylabel("macro accuracy (%): mean of problem recall and 1 - false alarms", color=grey)
    ax.tick_params(colors=grey, which="both")
    ax.grid(color=grey, alpha=0.25)
    for sp in ax.spines.values():
        sp.set_color(grey)
    fig.tight_layout()
    fig.canvas.draw()
    renderer, area = fig.canvas.get_renderer(), ax.get_window_extent()
    px = [ax.transData.transform((x, y)) for x, y, _ in pts]
    markers = [Bbox.from_extents(a - 9, b - 9, a + 9, b + 9) for a, b in px]
    spots = [(8, 6, "left", "bottom"), (8, -6, "left", "top"), (-8, 6, "right", "bottom"), (-8, -6, "right", "top"),
             (0, 11, "center", "bottom"), (0, -11, "center", "top"), (8, 20, "left", "bottom"), (8, -20, "left", "top")]
    placed = []
    for i, (x, y, name) in enumerate(pts):  # greedy: the first label position that overlaps no label, marker or the frame
        text = NAMES.get(name, name)
        for dx, dy, ha, va in spots:
            t = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, color=grey, fontsize=9)
            box = t.get_window_extent(renderer).expanded(1.03, 1.15)
            inside = area.x0 <= box.x0 and box.x1 <= area.x1 and area.y0 <= box.y0 and box.y1 <= area.y1
            if inside and not any(box.overlaps(o) for o in placed + [m for j, m in enumerate(markers) if j != i]):
                placed.append(box)
                break
            t.remove()
        else:
            dx, dy, ha, va = spots[0]
            ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, color=grey, fontsize=9)
    fig.savefig(path, transparent=True, format="svg", bbox_inches="tight")
    plt.close(fig)
    return True


def label(name: str) -> str:
    return f"{NAMES.get(name, name)} (`{name}`)"


def usd(v: float | None) -> str:
    return "-" if v is None else f"${v:.4f}"


def markdown(summary: dict, svg: str | None) -> str:
    cfg, ds, cmp = summary["configs"], summary["dataset"], summary["prompt_comparison"]
    audit = ds["label_audit"]
    res = summary["results_dir"]
    kinds = ", ".join(f"{n} {t}" for t, n in ds["by_type"].items())
    L = ["# ReceiptsBench results", "",
         f"ReceiptsBench scores a citation auditor on {ds['reports']} reports with {ds['items']} cited sentences taken from "
         f"{ds['sources']} real sources. The sentences fall into {len(TYPES)} problem types plus supported controls, and were written by an "
         f"independent model (`{ds['generator']}`), not by a Nemotron judge. A label audit read each generated overclaim against its source "
         f"and excluded {len(audit['excluded'])} of them, so {ds['scored_items']} items are scored. All numbers on this page come from "
         f"`{res}/*.jsonl` and `runs.json`, computed by `bench/report.py`.", "",
         "## All reports", "",
         "| Config | Items | Problem recall | False alarms | Macro accuracy | Label accuracy (4 classes) | Evidence quoted verbatim | "
         "Cost / 100 claims | Median s / report | Escalated |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for n, c in cfg.items():
        s = c["all"]
        secs = "-" if s["median_seconds_per_report"] is None else f"{s['median_seconds_per_report']:.0f}"
        L.append(f"| {label(n)} | {s['items']} | {pct(s['problem_recall'])} | {pct(s['false_alarm'])} | {macro(s)} | "
                 f"{pct(s['label_accuracy'])} | {pct(s['evidence_found'])} | {usd(s['cost_per_100_claims_usd'])} | {secs} | "
                 f"{pct(s['escalated']) if s['escalated'] else '-'} |")
    L += ["", "## By problem type", "", "Share of items of each type predicted as a problem; for supported items, the share passed as supported.", "",
          "| Config | " + " | ".join(TYPES) + " | supported passed |", "|---|" + "---|" * (len(TYPES) + 1)]
    for n, c in cfg.items():
        s = c["all"]
        L.append(f"| {label(n)} | " + " | ".join(pct(s["per_type"][t]) for t in TYPES) + f" | {pct(s['supported_pass'])} |")
    L += ["", "## Development and test reports", "",
          "Reports r01–r10 are the development split, r11–r30 the test split.", "",
          "| Config | r01–r10 macro accuracy | r01–r10 problem recall | r11–r30 macro accuracy | r11–r30 problem recall |", "|---|---|---|---|---|"]
    for n, c in cfg.items():
        d, t = c["dev"], c["test"]
        L.append(f"| {label(n)} | {macro(d)} | {pct(d and d['problem_recall'])} | {macro(t)} | {pct(t and t['problem_recall'])} |")
    if cmp:
        L += ["", "## Judge prompt v1 → v2", "",
              f"The v1 prompt results are in `{summary['baseline_dir']}`, the v2 prompt results in `{res}`; each row compares the two on the same scored "
              "items. The v2 change: a claim that adds scope or certainty the source does not state (only, all, first, exactly, will solve, "
              "without interruption...) is `partial`.", "",
              "| Config | Items | Macro accuracy, v1 prompt | Macro accuracy, v2 prompt | Overclaims detected, v1 prompt | Overclaims detected, v2 prompt |",
              "|---|---|---|---|---|---|"]
        for n, c in cmp.items():
            L.append(f"| {label(n)} | {c['items']} | {macro(c['v1'])} | {macro(c['v2'])} | {pct(c['v1']['overclaim'])} | {pct(c['v2']['overclaim'])} |")
    if svg:
        L += ["", "## Cost and quality", "", f"![cost per 100 claims against macro accuracy]({svg})"]
    L += ["", "## Label audit", "", audit["method"] + (f" ({audit['version']}, audited {audit['audited']}.)" if audit["version"] else ""), ""]
    if audit["excluded"]:
        L += ["| Report | Sentence | Reason |", "|---|---|---|"]
        for e in audit["excluded"]:
            L.append(f"| {e['report']} | {shorten(e['sentence']).replace('|', '/')} | {e['reason'].replace('|', '/')} |")
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
          "shared by every configuration, per 100 audited claims (items excluded by the label audit were audited too). "
          "Tavily and OpenAlex calls are not priced.",
          "- **Seconds**: extraction + source fetching + judging per report, measured with several reports audited in parallel.",
          "- **Escalated**: two-tier configurations only: the share of answered claims that went to the second tier.",
          "- `cascade`: Super judges, Ultra re-checks the doubts. `cascade_nano`: Nano judges, Ultra re-checks the doubts.",
          "- `routed`: Nano checks every claim; Ultra re-checks only 'supported' verdicts whose quote does not show the claim's figures "
          "or absolute words.",
          "- Gold labels: number_swap and entity_swap are `contradicted`, overclaim is `partial` (scored as a problem either way), "
          "misattribution is `not_in_source`, fabricated and dead_link are `source_unavailable`.", ""]
    g = ds["generation"]
    L += ["## Dataset", "",
          f"- Version `{ds['version']}`, built {ds['built_at']}, random seed {ds['seed']}.",
          f"- {ds['reports']} reports, {ds['items']} cited sentences, {ds['scored_items']} scored after the label audit: {kinds}.",
          f"- {ds['sources']} real sources cited: " + ", ".join(f"{n} {k}" for k, n in ds["sources_by_kind"].items()) + ".",
          f"- Test items written by `{ds['generator']}` (temperature 0.3), an independent non-NVIDIA model, so the Nemotron "
          "judges do not grade their own phrasing; number swaps are programmatic; every supported claim's quote is verified "
          "verbatim in its source; fabricated references are absent from Crossref (title ratio < 0.75); dead links answer 404/410."]
    if g:
        L.append(f"- Generation: {g['prompt_tokens']:,} input and {g['completion_tokens']:,} output tokens, ${g['usd']:.4f}.")
    L += ["", "## Reproduce", "", "```",
          "uv run python bench/build.py --dry-run     # fetch and validate the sources, no LLM call",
          "uv run python bench/build.py               # once; writes the frozen dataset to bench/data/",
          "uv run python bench/run.py --out results_final   # all configurations; resumable; needs NEBIUS_API_KEY and TAVILY_API_KEY",
          "uv run --group bench python bench/report.py --results results_final --baseline results", "```", ""]
    return "\n".join(L)


def where(path: Path, root: Path) -> str:
    try:
        return f"bench/{path.relative_to(root)}" if root == HERE else str(path.relative_to(root))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None, root: Path = HERE) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", default="results_final", help="result directory (under bench/ or absolute)")
    ap.add_argument("--baseline", default="results", help="result directory of the v1 judge prompt, for the v1 -> v2 table")
    ap.add_argument("--md", help="markdown output path (default: bench/RESULTS.md)")
    args = ap.parse_args(argv)
    results, baseline = root / args.results, root / args.baseline
    md = root / args.md if args.md else root / "RESULTS.md"
    audit = load_audit(root / "data")
    excluded = excluded_keys(audit)
    found = load_dir(results)
    if not found:
        print(f"error: no results in {results}; run bench/run.py first", file=sys.stderr)
        return 1
    configs = {}
    for name, (rows, runs) in found.items():
        blocks = {"all": metrics(name, rows, runs, excluded)} | {k: metrics(name, rows, runs, excluded, r) for k, r in SPLITS.items()}
        if blocks["all"]:
            configs[name] = blocks
    prompt_comparison = compare_prompts(load_dir(baseline), found, excluded) if baseline.resolve() != results.resolve() else {}
    ds = dataset_info(root / "data", audit)
    summary = {"benchmark": ds["version"], "results_dir": where(results, root), "baseline_dir": where(baseline, root),
               "dataset": ds, "excluded": [{"report": e["report"], "start": e["start"]} for e in ds["label_audit"]["excluded"]],
               "configs": configs, "prompt_comparison": prompt_comparison}
    (results / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    svg_path = results / "cost_vs_quality.svg"
    has_svg = plot_svg({n: c["all"] for n, c in configs.items()}, svg_path)
    md.write_text(markdown(summary, os.path.relpath(svg_path, md.parent) if has_svg else None), encoding="utf-8")
    print(f"wrote {md} and {results / 'summary.json'} for {', '.join(configs)}"
          + ("" if has_svg else "; no SVG (install the bench group: uv run --group bench python bench/report.py)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
