"""Command line: `receipts audit REPORT.md`."""

import argparse
import sys
from pathlib import Path

from .config import load_settings
from .judge import MODES
from .models import LABEL_ORDER, AuditReport
from .pipeline import audit
from .render import render_markdown
from .scholarly import OpenAlex
from .search import DiskCache, TavilySearcher, http_status


def _progress(event: dict) -> None:
    if event["type"] == "stage":
        print(f"[{event['stage']}]", file=sys.stderr)


def _print_summary(report: AuditReport) -> None:
    verdicts = {c.id: report.claim_verdict(c.id) for c in report.claims}
    for c in report.claims:
        v = verdicts[c.id]
        how = "-" if v is None or v.tier == "none" else v.tier + ("*" if v.escalated else "")
        sentence = " ".join(c.sentence.split())
        print(f"{c.id:<4} {report.claim_label(c.id):<19} {how:<7} {sentence[:80]}")
    sm = report.summary()
    counts = " | ".join(f"{label} {sm['counts'][label]}" for label in LABEL_ORDER)
    print(f"Claims: {sm['n_claims']} | {counts}")
    print(f"Sources: {sm['n_sources']} | Cost: ${sm['cost_usd']:.4f} | Time: {report.seconds:.1f}s")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="receipts", description="Audit the citations of an AI-generated report.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="check every cited sentence against its source")
    a.add_argument("path", help="markdown/text report to audit")
    a.add_argument("--mode", choices=sorted(MODES), default="cascade")
    a.add_argument("--json", metavar="OUT.json", help="write the full audit as JSON")
    a.add_argument("--md", metavar="OUT.md", help="write a markdown report")
    a.add_argument("--fake", metavar="FIXTURES.json", help="run offline with fake LLM and search fixtures")
    args = p.parse_args(argv)

    try:
        doc = Path(args.path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read {args.path}: {e.strerror}", file=sys.stderr)
        return 2

    settings = load_settings()
    if args.fake:
        from .testing import FakeLLM, load_fixtures

        llm, (searcher, status_fn), openalex = FakeLLM(), load_fixtures(args.fake), None
    else:
        missing = [n for n, v in (("NEBIUS_API_KEY", settings.nebius_api_key),
                                  ("TAVILY_API_KEY", settings.tavily_api_key)) if not v]
        if missing:
            print(f"error: set {' and '.join(missing)} in the environment or in .env "
                  "(see .env.example), or run offline with --fake FIXTURES.json", file=sys.stderr)
            return 2
        from .llm import TokenFactoryLLM

        llm = TokenFactoryLLM(settings)
        cache = DiskCache(settings.cache_dir)
        searcher, status_fn, openalex = TavilySearcher(settings.tavily_api_key, cache), http_status, OpenAlex(cache)

    report = audit(doc, llm, searcher, settings, mode=args.mode, on_event=_progress,
                   status_fn=status_fn, openalex=openalex)
    _print_summary(report)
    for out, text in ((args.json, lambda: report.model_dump_json(indent=2)), (args.md, lambda: render_markdown(report))):
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(text(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
