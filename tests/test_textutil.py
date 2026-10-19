import time

from receipts.textutil import (
    chunk,
    clean_web_text,
    contains_quote,
    normalize,
    split_body_and_references,
    split_sentences,
)


def sents(text):
    return [text[s:e] for s, e in split_sentences(text)]


def assert_covers(text, spans):
    rest = list(text)
    last_end = 0
    for s, e in spans:
        assert s >= last_end, "overlap"
        last_end = e
        for i in range(s, e):
            rest[i] = " "
    assert "".join(rest).strip() == ""


def test_basic_split_and_coverage():
    t = "First one. Second one! Third one? Fourth."
    assert sents(t) == ["First one.", "Second one!", "Third one?", "Fourth."]
    assert_covers(t, split_sentences(t))


def test_citation_markers_stay_with_sentence():
    t = "Revenue rose 40%.[1] Costs fell 5% [2]. Profit grew.[^3] Done."
    assert sents(t) == ["Revenue rose 40%.[1]", "Costs fell 5% [2].", "Profit grew.[^3]", "Done."]
    t2 = "Both agree [1, 2]. Range [3–5]. End."
    assert sents(t2) == ["Both agree [1, 2].", "Range [3–5].", "End."]


def test_abbreviations_and_decimals():
    t = "Smith et al. found 3.5 percent growth, e.g. in the U.S. market. Dr. Lee i.e. the author agreed. Next."
    out = sents(t)
    assert out == [
        "Smith et al. found 3.5 percent growth, e.g. in the U.S. market.",
        "Dr. Lee i.e. the author agreed.",
        "Next.",
    ]
    assert sents("It ended in the U.S. The rest followed.") == [
        "It ended in the U.S.",
        "The rest followed.",
    ]
    assert sents("Written by J. Smith in 2020. Fine.") == ["Written by J. Smith in 2020.", "Fine."]


def test_links_do_not_split():
    t = "See [the study (v2.1). Final](https://example.com/a.b. c) now. Next one."
    assert sents(t)[-1] == "Next one."
    assert len(sents(t)) == 2


def test_headings_and_bullets_are_own_units():
    t = "# Title. Not split\n\nIntro text here.\n- First bullet. Second sentence.\n- Third bullet\n1. Numbered item.\n"
    out = sents(t)
    assert out[0] == "# Title. Not split"
    assert "Intro text here." in out
    assert "- First bullet." in out and "Second sentence." in out
    assert "- Third bullet" in out and "1. Numbered item." in out
    assert_covers(t, split_sentences(t))


def test_multiline_paragraph():
    t = "A sentence that wraps\nacross lines. Another one."
    assert sents(t) == ["A sentence that wraps\nacross lines.", "Another one."]


def test_split_body_and_references():
    doc = "Body text [1].\n\n## References\n[1] Foo. https://a.com\n"
    be, rs = split_body_and_references(doc)
    assert doc[:be].strip() == "Body text [1]."
    assert doc[rs:].startswith("[1] Foo.")
    for heading in ("**Sources**", "Bibliography", "WORKS CITED:", "# Notes", "Citations"):
        d = f"Text.\n\n{heading}\n1. Item\n"
        be, rs = split_body_and_references(d)
        assert d[rs:] == "1. Item\n", heading
    # last heading wins; no heading -> whole doc
    d = "A.\n\n# Notes\nnote\n\n# References\n[1] x\n"
    be, rs = split_body_and_references(d)
    assert d[rs:] == "[1] x\n" and "Notes" in d[:be]
    assert split_body_and_references("No refs here.") == (13, None)


def test_normalize():
    assert normalize("  “Hello—World”  ") == 'hello-world'
    assert normalize("It’s   a\n test.") == "it's a test"


def test_contains_quote():
    hay = "The study found that sea levels rose by 3.2 mm per year between 1993 and 2020. More text."
    assert contains_quote(hay, "sea levels rose by 3.2 mm per year")
    assert contains_quote(hay, "SEA LEVELS rose by 3.2 mm  per year.")
    # fuzzy: one word changed in a long quote
    assert contains_quote(hay, "The study found that sea levels rise by 3.2 mm per year between 1993 and 2020")
    assert not contains_quote(hay, "Sea levels fell by 9 mm per year between 1900 and 1950 according to the study")
    assert not contains_quote(hay, "")
    assert not contains_quote(hay, "mm per yeer")  # short quotes: exact only


def test_contains_quote_is_fast_on_long_text():
    hay = " ".join(f"word{i} filler text number{i % 7}" for i in range(8000))
    t0 = time.time()
    assert not contains_quote(hay, "completely different sentence about something else entirely here")
    assert time.time() - t0 < 10


def test_chunk_coverage_and_size():
    paras = [f"Paragraph {i} " + "lorem ipsum dolor sit amet " * 12 for i in range(12)]
    text = "\n\n".join(paras) + "\n\n" + ("long " * 400)
    spans = chunk(text, size=700, overlap=150)
    assert len(spans) > 3
    assert_covers_union(text, spans)
    assert all(e - s <= 700 for s, e in spans)
    assert all(a[0] < b[0] for a, b in zip(spans, spans[1:]))


def assert_covers_union(text, spans):
    covered = [False] * len(text)
    for s, e in spans:
        for i in range(s, e):
            covered[i] = True
    assert all(covered[i] for i, c in enumerate(text) if not c.isspace())


def test_chunk_small_and_empty():
    assert chunk("") == []
    assert chunk("   \n\n  ") == []
    assert chunk("short text") == [(0, 10)]


def test_strip_citations_and_numbers():
    from receipts.textutil import numbers, strip_citations

    assert strip_citations("Revenue rose 40%.[1]") == "Revenue rose 40%."
    assert strip_citations("Costs fell 5% [2, 3].") == "Costs fell 5%."
    assert strip_citations("Wind grew [^4] sharply [5–7].") == "Wind grew sharply."
    assert strip_citations("See [the IEA report](https://a.org/x), which says so [1].") == "See the IEA report, which says so."
    assert strip_citations("Jobs grew (Chen et al., 2020) in solar.") == "Jobs grew in solar."
    assert strip_citations("It rose in (Q3 2021) only.") == "It rose in (Q3 2021) only."
    assert strip_citations("- Bullet text [1].") == "Bullet text."
    assert numbers("$1,200 billion, 3.5% in 2022 and 10-15") == {"1200", "3.5", "2022", "10", "15"}


def test_clean_links_keep_anchor():
    t = "centrepiece for the proposed [1889 Exposition Universelle](https://en.wikipedia.org/wiki/Exposition_Universelle_(1889)) in Paris"
    assert clean_web_text(t) == "centrepiece for the proposed 1889 Exposition Universelle in Paris"
    assert clean_web_text('see [the docs](https://x.test/d "Docs") now') == "see the docs now"


def test_clean_images_and_badges_removed():
    assert clean_web_text("![logo](https://x.test/a.png) Hello") == "Hello"
    assert clean_web_text("[![badge](https://b.test/i.svg)](https://b.test/l) text") == "text"


def test_clean_reference_markers():
    assert clean_web_text("in _Flying Circus_.[[47]]") == "in Flying Circus."
    assert clean_web_text("a claim[12], another [a]. History[[edit](https://w.test/e \"Edit section\")]") == "a claim, another. History"
    assert clean_web_text("needs support [citation needed] and [edit] and [[3]] and [^4]") == "needs support and and and"


def test_clean_keeps_ordinary_bracketed_prose():
    t = "the court said [T]he rule applies in [2019] UKSC 1, on [0, 1], [see below] and [sic]"
    assert clean_web_text(t) == t


def test_clean_emphasis_wrapping_words():
    assert clean_web_text("_x_ and *y* and **z** and __w__ and ***v***") == "x and y and z and w and v"
    assert clean_web_text("**Note:** a _long phrase_ here") == "Note: a long phrase here"


def test_clean_emphasis_leaves_identifiers_urls_and_maths():
    t = "use snake_case_name, __init__.py, https://x.test/a_b_c/_foo_/ , 2*3*4, a * b * c and `x_y_z`"
    assert clean_web_text(t) == t


def test_clean_collapses_whitespace():
    assert clean_web_text("a   b \t c\n\n\n\n d \n \n\n e  ") == "a b c\n\n d\n\n e"
