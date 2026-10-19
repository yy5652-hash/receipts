"""Before/after table for two result directories on one split of ReceiptsBench (dev = r01-r10, test = r11-r30)."""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from bench.report import summarise  # noqa: E402

SPLITS = {"dev": [f"r{i:02d}" for i in range(1, 11)], "test": [f"r{i:02d}" for i in range(11, 31)],
          "all": [f"r{i:02d}" for i in range(1, 31)]}


def load(directory: Path, config: str, reports: list[str]) -> tuple[list[dict], dict] | None:
    path = directory / f"{config}.jsonl"
    if not path.exists():
        return None
    rows = [r for r in map(json.loads, path.read_text().splitlines()) if r["report"] in reports]
    runs = json.loads((directory / "runs.json").read_text())
    return rows, {r: v[config] for r, v in runs.items() if r in reports and config in v}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="results")
    ap.add_argument("--new", required=True)
    ap.add_argument("--split", choices=sorted(SPLITS), default="test")
    ap.add_argument("--configs", default="nano,super,ultra,cascade,cascade_nano,routed")
    ap.add_argument("--md", help="also write the table to this file (under bench/)")
    args = ap.parse_args(argv)
    reports = SPLITS[args.split]
    lines = [f"| Config | Judge prompt | Items | Problem recall | False alarms | Macro accuracy | Overclaim caught | Cost / 100 claims |",
             "|---|---|---|---|---|---|---|---|"]
    for config in [c.strip() for c in args.configs.split(",") if c.strip()]:
        for label, d in (("v1", HERE / args.base), ("v2", HERE / args.new)):
            got = load(d, config, reports)
            if not got or not got[0]:
                continue
            m = summarise(config, *got)
            pr, fa, oc = m["problem_recall"], m["false_alarm"], m["per_type"]["overclaim"]
            lines.append(f"| {config} | {label} | {m['items']} | {pr['rate']:.0%} ({pr['k']}/{pr['n']}) | "
                         f"{fa['rate']:.0%} ({fa['k']}/{fa['n']}) | {m['macro_accuracy']:.1%} | "
                         f"{oc['rate']:.0%} ({oc['k']}/{oc['n']}) | ${m['cost_per_100_claims_usd']:.4f} |")
    table = "\n".join(lines)
    print(table)
    if args.md:
        (HERE / args.md).write_text(table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
