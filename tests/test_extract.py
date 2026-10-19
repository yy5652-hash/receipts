from pathlib import Path

from receipts.extract import extract
from receipts.testing import FakeLLM

FIX = Path(__file__).parent / "fixtures"
DOC = (FIX / "report_basic.md").read_text(encoding="utf-8")


def run(doc=DOC):
    llm = FakeLLM()
    return extract(doc, llm) + (llm,)


def test_sources_parsed():
    sources, _, llm = run()
    by = {s.marker: s for s in sources if s.marker}
    assert len(sources) == 7  # six references + one inline link
    assert by["1"].url == "https://example.org/energy-journal/investment-trends"
    assert by["1"].title == "Global renewable investment trends"
    assert by["1"].year == 2021 and by["1"].authors == ["Smith, J.", "Lee, K."]
    assert by["2"].doi == "10.5555/iea.solar.2021" and by["2"].url is None
    assert by["3"].url is None and by["3"].doi is None and "Offshore wind in Europe" in by["3"].title
    assert by["3"].year == 2021
    assert by["4"].url == "https://example.com/old-report"
    inline = [s for s in sources if s.marker is None]
    assert [s.url for s in inline] == ["https://example.net/irena-capacity"]
    assert inline[0].id == "s7"
    assert llm.calls[0] == ("parse_sources", "nano")


def test_claims_map_to_sources():
    sources, claims, _ = run()
    sid = {s.marker: s.id for s in sources if s.marker}

    def ids(prefix):
        (c,) = [c for c in claims if c.claim.startswith(prefix)]
        return c.source_ids

    assert len(claims) == 9
    assert [c.id for c in claims] == [f"c{i}" for i in range(1, 10)]
    assert ids("Global investment") == [sid["1"]]
    assert ids("Heat pumps") == [sid["2"]]
    assert ids("Offshore wind") == [sid["3"]]
    assert ids("Solar was the cheapest") == [sid["1"], sid["2"]]  # [1–2] range
    assert ids("According to the IRENA") == ["s7"]  # inline link
    assert ids("Employment in the solar") == [sid["6"]]  # author-year resolved by the LLM


def test_offsets_and_claim_text():
    _, claims, _ = run()
    for c in claims:
        assert DOC[c.start : c.end] == c.sentence
        assert "[" not in c.claim and "(Chen" not in c.claim
    c1 = claims[0]
    assert c1.sentence.endswith("2022.[1]") and c1.claim == "Global investment in renewable energy reached $500 billion in 2022."
    assert "e.g." in claims[4].sentence  # abbreviation did not split the battery sentence
    assert claims[6].claim.startswith("According to the IRENA capacity statistics,")


def test_uncited_sentences_are_not_claims():
    _, claims, _ = run()
    text = " ".join(c.sentence for c in claims)
    assert "The authors expect" not in text and "summarises" not in text


def test_footnote_and_bullet_references():
    doc = (
        "Wind grew 10% in 2021.[^1] Hydro stayed flat [^2].\n\n"
        "[^1]: Wind Report 2022. https://wind.example/r (accessed 2023).\n"
        "[^2]: Hydro Facts. 2020. https://hydro.example/f.\n"
    )
    sources, claims, _ = run(doc)
    assert [s.marker for s in sources] == ["^1", "^2"]
    assert sources[0].url == "https://wind.example/r"  # trailing ')' and '.' stripped
    assert [c.source_ids for c in claims] == [["s1"], ["s2"]]

    bullets = "Solar is cheap (Chen, 2020).\n\n## Sources\n- Chen, L. (2020). \"Cheap solar.\" https://x.example/a\n"
    sources, claims, _ = run(bullets)
    assert sources[0].marker == "b1"  # bullets get b-markers when the body has no numeric markers
    assert claims[0].source_ids == ["s1"]


def test_llm_drops_sentences_falls_back_to_markers():
    class Dropper(FakeLLM):
        def _extract_claims(self, payload):
            return {"claims": [{"index": 0, "claim": "", "source_ids": ["s99"]}]}

    llm = Dropper()
    sources, claims = extract(DOC, llm)
    assert len(claims) == 8  # author-year sentence needs the LLM mapping and is dropped
    assert claims[0].claim == "Global investment in renewable energy reached $500 billion in 2022."
    assert claims[0].source_ids == ["s1"]
    for c in claims:
        assert DOC[c.start : c.end] == c.sentence


def test_no_references_no_claims():
    sources, claims, _ = run("Plain text without any citations. Nothing to see.")
    assert sources == [] and claims == []
