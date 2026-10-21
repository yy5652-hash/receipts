"""Markdown rendering of an AuditReport."""

import re

from .models import LABEL_ORDER, AuditReport, FetchedSource, Suggestion, Verdict

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


def _record_note(f: FetchedSource | None) -> str:
    """For a source that could not be read: whether a record of it exists at all."""
    if f is None or f.status != "not_found" or f.exists is None:
        return ""
    return "a record of the source exists, but its text could not be read" if f.exists else "no record of the source was found"


def _suggestion_block(s: Suggestion) -> list[str]:
    """What the web search found for an unsupported claim."""
    if s.status == "none":
        return ["**Web search:** no page found that states this claim.", ""]
    says = "states the claim" if s.status == "supported" else "says otherwise"
    title = s.title or s.url or "untitled page"
    lines = [f"**Suggested source ({says}):** {title}" + (f" · <{s.url}>" if s.url else "")]
    if s.quote:
        lines += ["", f"> {_one_line(s.quote)}"]
    if s.rationale:
        lines += ["", s.rationale]
    model = f"{s.tier}" + (f" ({s.model})" if s.model else "")
    return lines + ["", f"Found with Tavily · checked by {model}" + ("" if s.quote_found or not s.quote else ", quote not verified"), ""]


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
            lines.append(f"Verdict: {BADGES[v.label]} · judged by {_tier_note(v)}" + (f" · {v.model}" if v.model else "")
                         + (" · checked against abstract only" if f and f.coverage == "abstract" else ""))
            if note := _record_note(f):
                lines.append(f"Record: {note}")
            if v.quote:
                lines += ["", f"> {_one_line(v.quote)}"]
            if v.rationale:
                lines += ["", v.rationale]
            lines.append("")
        if suggestion := report.claim_suggestion(c.id):
            lines += _suggestion_block(suggestion)
    return "\n".join(lines).rstrip() + "\n"
