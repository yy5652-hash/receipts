"""Record the audit of a report as samples/<name>.audit.json, which the web app replays instantly.

    uv run python scripts/make_sample_audit.py samples/my-report.md              # real run (needs keys, spends credits)
    uv run python scripts/make_sample_audit.py tests/fixtures/samples/fixture-demo.md --fake tests/fixtures/sources.json
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.server import fake_factory, live_factory, public_report  # noqa: E402
from receipts.config import load_settings  # noqa: E402
from receipts.judge import MODES  # noqa: E402
from receipts.pipeline import audit  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", help="report to audit (.md)")
    p.add_argument("--fake", metavar="FIXTURES.json", help="offline run with fake LLM and search fixtures")
    p.add_argument("--mode", choices=sorted(MODES), default="ultra")
    p.add_argument("--out", help="output file (default: next to the report, .audit.json)")
    args = p.parse_args(argv)

    path = Path(args.path)
    try:
        doc = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        return 2
    settings = load_settings()
    if args.fake:
        factory = fake_factory(args.fake)
    elif settings.nebius_api_key and settings.tavily_api_key:
        factory = live_factory(settings)
    else:
        print("error: set NEBIUS_API_KEY and TAVILY_API_KEY (see .env.example), or pass --fake FIXTURES.json",
              file=sys.stderr)
        return 2

    llm, searcher, status_fn, openalex = factory()
    report = audit(doc, llm, searcher, settings, mode=args.mode, status_fn=status_fn, openalex=openalex,
                   on_event=lambda e: print(f"[{e['stage']}]", file=sys.stderr) if e["type"] == "stage" else None)
    out = Path(args.out) if args.out else path.with_suffix(".audit.json")
    out.write_text(json.dumps(public_report(report), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sm = report.summary()
    print(f"wrote {out}: {sm['n_claims']} claims, {sm['n_sources']} sources, ${sm['cost_usd']:.4f}, {report.seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
