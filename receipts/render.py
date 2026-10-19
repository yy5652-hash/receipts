"""Markdown rendering of an AuditReport."""

import re

from .models import LABEL_ORDER, AuditReport, Verdict

BADGES = {
    "supported": "✅ supported",
    "partial": "🟡 partial",
    "contradicted": "❌ contradicted",
    "not_in_source": "⚪ not in source",
    "source_unavailable": "🚫 source unavailable",
}


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tier_note(v: Verdict) -> str:
    if v.tier == "none":
        return "no model call"
    note = f"{v.tier}" + (" (escalated from super)" if v.escalated else "")
    return f"{note}, confidence {v.confidence:.2f}" + ("" if v.quote_found or not v.quote else ", quote not verified")


def render_markdown(report: AuditReport) -> str:
    sm = report.summary()
    heading = re.search(r"^#\s+(.+)$", report.document, re.M)
    lines = ["# Receipts: citation audit" + (f" of “{heading.group(1).strip()}”" if heading else ""), ""]
    lines += ["| Verdict | Claims |", "|---|---|"]
    lines += [f"| {BADGES[label]} | {sm['counts'][label]} |" for label in LABEL_ORDER]
    lines += [
        f"| **Total claims** | {sm['n_claims']} |",
        f"| Sources | {sm['n_sources']} |",
        f"| Mode | {report.mode} |",
        f"| Cost | ${sm['cost_usd']:.4f} |",
        f"| Time | {report.seconds:.1f} s |",
        "",
    ]
    sources = {s.id: s for s in report.sources}
    fetched = {f.source_id: f for f in report.fetched}
    for c in report.claims:
        lines += [f"## {c.id}: {BADGES[report.claim_label(c.id)]}", "", f"> {_one_line(c.sentence)}", ""]
        if _one_line(c.claim) != _one_line(c.sentence):
            lines += [f"**Claim checked:** {_one_line(c.claim)}", ""]
        for v in (v for v in report.verdicts if v.claim_id == c.id):
            s, f = sources[v.source_id], fetched.get(v.source_id)
            url = (f.url if f and f.url else s.url) or ""
            title = s.title or _one_line(s.raw)[:80]
            lines.append(f"**Source {s.marker or s.id}:** {title}" + (f" · <{url}>" if url else ""))
            lines.append(f"Verdict: {BADGES[v.label]} · judged by {_tier_note(v)}" + (f" · {v.model}" if v.model else ""))
            if v.quote:
                lines += ["", f"> {_one_line(v.quote)}"]
            if v.rationale:
                lines += ["", v.rationale]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
