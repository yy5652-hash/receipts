from receipts.config import Settings
from receipts.judge import JUDGE_SYSTEM, judge_all, judge_one, unbacked
from receipts.models import Claim, FetchedSource, Source
from receipts.testing import FakeLLM

TEXT = "Global investment in renewable energy reached $400 billion in 2022, up 8% on the previous year. Solar led the total."
SRC = Source(id="s1", raw="r", title="Investment trends")


def claim(id, text, sid="s1"):
    return Claim(id=id, sentence=text, start=0, end=len(text), claim=text, source_ids=[sid])


def ok(sid="s1", text=TEXT):
    return FetchedSource(source_id=sid, status="ok", text=text, via="tavily_extract")


def test_unavailable_makes_no_llm_call():
    llm = FakeLLM()
    for status, word in (("dead_link", "dead"), ("not_found", "could not be found")):
        f = FetchedSource(source_id="s1", status=status, note="HTTP 404")
        v = judge_one(claim("c1", "x 1."), SRC, f, llm, "super")
        assert v.label == "source_unavailable" and v.tier == "none" and word in v.rationale
    assert llm.calls == []


def test_supported_with_verified_quote():
    v = judge_one(claim("c1", "Global investment in renewable energy reached $400 billion in 2022."), SRC, ok(), FakeLLM(), "super")
    assert (v.label, v.quote_found, v.confidence, v.tier) == ("supported", True, 0.9, "super")
    assert v.passages and v.quote.startswith("Global investment")


class Scripted(FakeLLM):
    def __init__(self, out):
        super().__init__()
        self.out = out

    def _judge(self, payload):
        return dict(self.out)


def test_unverifiable_quote_caps_confidence_and_bad_label_is_not_in_source():
    c = claim("c1", "Investment hit $400 billion.")
    v = judge_one(c, SRC, ok(), Scripted({"label": "supported", "confidence": 0.95, "quote": "Invented sentence that is not there.", "rationale": "r"}), "super")
    assert v.label == "supported" and not v.quote_found and v.confidence == 0.5
    v = judge_one(c, SRC, ok(), Scripted({"label": "banana", "confidence": "high", "quote": 5}), "super")
    assert v.label == "not_in_source" and v.confidence == 0.0 and v.quote == ""
    v = judge_one(c, SRC, ok(), Scripted({"label": "not_in_source", "confidence": 0.9, "quote": ""}), "super")
    assert v.confidence == 0.9  # no cap when nothing is asserted


def test_abstract_coverage_is_flagged_to_the_judge_only_then():
    seen = []

    class Spy(FakeLLM):
        def _judge(self, payload):
            seen.append(payload)
            return super()._judge(payload)

    c = claim("c1", "Global investment in renewable energy reached $400 billion in 2022.")
    judge_one(c, SRC, ok(), Spy(), "super")
    judge_one(c, SRC, FetchedSource(source_id="s1", status="ok", text=TEXT, via="openalex", coverage="abstract"), Spy(), "super")
    assert "coverage" not in seen[0] and seen[1]["coverage"] == "abstract only"
    assert '"abstract only"' in JUDGE_SYSTEM and "not_in_source with confidence at most 0.5" in JUDGE_SYSTEM.replace("\n", " ")


def _settings(**kw):
    return Settings(models={"nano": "m-nano", "super": "m-super", "ultra": "m-ultra"}, **kw)


def test_cascade_escalates_contradicted_not_clean_supported():
    claims = [
        claim("c1", "Global investment in renewable energy reached $500 billion in 2022."),  # contradicted
        claim("c2", "Global investment in renewable energy reached $400 billion in 2022."),  # supported
    ]
    llm, events = FakeLLM(), []
    vs = judge_all(claims, [SRC], [ok()], llm, "cascade", _settings(), on_event=events.append)
    c1, c2 = vs
    assert (c1.label, c1.tier, c1.escalated, c1.model) == ("contradicted", "ultra", True, "m-ultra")
    assert (c2.label, c2.tier, c2.escalated, c2.model) == ("supported", "super", False, "m-super")
    assert sorted(t for _, t in llm.calls) == ["super", "super", "ultra"]
    assert len(events) == 2 and all(e["type"] == "verdict" for e in events)


def test_cascade_tiers_are_configurable():
    claims = [claim("c1", "Global investment in renewable energy reached $500 billion in 2022."),
              claim("c2", "Global investment in renewable energy reached $400 billion in 2022.")]
    llm = FakeLLM()
    c1, c2 = judge_all(claims, [SRC], [ok()], llm, "cascade", _settings(cascade=("nano", "ultra")))
    assert (c1.tier, c1.escalated, c1.model) == ("ultra", True, "m-ultra")
    assert (c2.tier, c2.escalated, c2.model) == ("nano", False, "m-nano")
    assert sorted(t for _, t in llm.calls) == ["nano", "nano", "ultra"]


def test_cascade_escalates_low_confidence_and_unverified_supported():
    c = claim("c1", "Investment hit $400 billion.")
    v = judge_all([c], [SRC], [ok()], Scripted({"label": "supported", "confidence": 0.95, "quote": "nope nope nope nope"}), "cascade", _settings())[0]
    assert v.escalated and v.tier == "ultra"
    v = judge_all([c], [SRC], [ok()], Scripted({"label": "supported", "confidence": 0.6, "quote": "Solar led the total."}), "cascade", _settings())[0]
    assert v.escalated  # confidence below escalate_below


def test_fixed_modes_use_one_tier_and_unavailable_never_escalates():
    c = claim("c1", "Global investment in renewable energy reached $500 billion in 2022.")
    llm = FakeLLM()
    (v,) = judge_all([c], [SRC], [ok()], llm, "nano", _settings())
    assert (v.tier, v.escalated, llm.calls) == ("nano", False, [("judge", "nano")])
    llm = FakeLLM()
    dead = FetchedSource(source_id="s1", status="dead_link")
    (v,) = judge_all([c], [SRC], [dead], llm, "cascade", _settings())
    assert v.label == "source_unavailable" and not v.escalated and llm.calls == []


def test_one_verdict_per_claim_source_pair_in_order():
    srcs = [SRC, Source(id="s2", raw="r2", title="T2")]
    c = Claim(id="c1", sentence="x", start=0, end=1, claim="Global investment reached $400 billion.", source_ids=["s1", "s2"])
    vs = judge_all([c], srcs, [ok("s1"), ok("s2", "Unrelated text about cats.")], FakeLLM(), "super", _settings())
    assert [(v.claim_id, v.source_id) for v in vs] == [("c1", "s1"), ("c1", "s2")]
    assert vs[0].label == "supported" and vs[1].label == "not_in_source"


def test_unbacked_lists_figures_and_absolute_words_the_quote_lacks():
    q = "Global investment in renewable energy reached $400 billion in 2022, up 8% on the previous year."
    assert unbacked("Investment reached $400 billion in 2022.", q) == []
    assert unbacked("Investment reached $540 billion in 2022.", q) == ["540"]
    assert unbacked("Investment reached $400 billion, the largest ever, and will always grow.", q) == ["largest", "always"]


def test_routed_rechecks_only_supported_verdicts_whose_quote_lacks_the_claim():
    backed = claim("c1", "Global investment in renewable energy reached $400 billion in 2022.")
    llm = FakeLLM()
    (v,) = judge_all([backed], [SRC], [ok()], llm, "routed", _settings())
    assert (v.tier, v.escalated, v.unbacked) == ("nano", False, [])
    # the first judge says supported, but its quote does not carry the claimed figure: Ultra takes a second look
    odd = claim("c1", "Investment reached $540 billion in 2022.")
    out = {"label": "supported", "confidence": 0.95, "quote": "Global investment in renewable energy reached $400 billion in 2022, up 8% on the previous year."}
    (v,) = judge_all([odd], [SRC], [ok()], Scripted(out), "routed", _settings())
    assert v.escalated and v.tier == "ultra" and v.unbacked == ["540"]
    # a problem the first judge found stands without a second call
    out = {"label": "contradicted", "confidence": 0.9, "quote": "Solar led the total."}
    llm = Scripted(out)
    (v,) = judge_all([odd], [SRC], [ok()], llm, "routed", _settings())
    assert (v.tier, v.escalated) == ("nano", False) and len(llm.calls) == 1
