import json
import random
import re
from types import SimpleNamespace

import httpx
import pytest

from bench import build
from bench.build import (BudgetExceeded, Gen, assemble_report, clean_facts, crossref_novel, dead_links, fab_authors,
                         make_fabricated, plan_reports, swap_number, unrelated, valid_entity_swap, valid_overclaim,
                         word_set)
from receipts.search import DiskCache
from receipts.textutil import numbers, split_sentences


def swaps(claim, text_numbers=frozenset(), n=60):
    out = [swap_number(claim, set(text_numbers), random.Random(s)) for s in range(n)]
    return [o for o in out if o]


def test_number_swap_years_move_three_to_eight():
    out = swaps("The tower opened in 1996.")
    assert out and all(3 <= abs(int(o["new"]) - 1996) <= 8 and o["claim"].endswith(f"in {o['new']}.") for o in out)


def test_number_swap_never_uses_a_number_of_the_source():
    seen = {str(y) for y in range(1988, 2005)} - {"1996", "2001"}
    assert {o["new"] for o in swaps("The tower opened in 1996.", seen)} == {"2001"}
    assert swaps("The tower opened in 1996.", {str(y) for y in range(1988, 2005)}) == []  # nothing left to swap to


def test_number_swap_percentages_keep_range_and_decimals():
    for o in swaps("Obesity affects 31% of adults."):
        assert 8 <= abs(int(o["new"]) - 31) <= 25 and 0 <= int(o["new"]) <= 100
    for o in swaps("Coverage reached 12.5 percent of pupils."):
        assert re.fullmatch(r"\d+\.\d", o["new"]) and 8 <= abs(float(o["new"]) - 12.5) <= 25.01 and 0 <= float(o["new"]) <= 100


def test_number_swap_integers_scale_to_the_original_precision():
    assert {o["new"] for o in swaps("The canal is 80 km long.")} <= {"40", "60", "110", "140", "200"}
    out = swaps("The reef hosts 12,000 species.")
    assert out and all(re.fullmatch(r"\d{1,3},000", o["new"]) for o in out)
    assert {o["new"] for o in swaps("It has 3 lanes.")} <= {"2", "4", "5", "8"}


def test_number_swap_decimals_change_one_significant_digit():
    for o in swaps("The dam is 2.5 kilometres long."):
        a, b = "2.5", o["new"]
        assert len(b) == len(a) and sum(x != y for x, y in zip(a, b)) == 1 and not b.startswith("0")
    for o in swaps("The ratio is 0.05 here."):
        assert o["new"].startswith("0.0") and o["new"] != "0.05" and o["new"] != "0.00"


def test_number_swap_skips_identifiers_dates_and_number_free_claims():
    assert swaps("COVID-19 spread quickly, as did GPT-3 and H5N1.") == []
    assert swaps("The reef is large and old.") == []
    for o in swaps("Armstrong landed on July 20, 1969."):
        assert o["old"] == "1969" and "July 20," in o["claim"]


def test_number_swap_changes_exactly_one_number():
    claim = "Between 1990 and 2000 the population grew by 15%."
    for o in swaps(claim):
        assert len(numbers(claim) ^ numbers(o["claim"])) == 2  # one number out, one in


def test_entity_swap_validation():
    q = "Sydney is the largest city in Australia, ahead of Brisbane."
    c = "The largest city in Australia is Sydney."
    assert valid_entity_swap(c, "The largest city in Australia is Melbourne.", q)
    assert not valid_entity_swap(c, "The largest city in Australia is Sydney.", q)  # unchanged
    assert not valid_entity_swap(c, "The largest city in Australia is Brisbane.", q)  # the replacement is in the quote
    assert not valid_entity_swap(c, "The biggest town in Canada is Sydney.", q)  # two edits
    assert not valid_entity_swap(c, "The largest city in Australia is the very old, large and historic Melbourne harbour.", q)


def test_overclaim_validation():
    c = "Some studies link sugar intake to 12% more cavities."
    assert valid_overclaim(c, "All studies link sugar intake to 12% more cavities.")
    assert not valid_overclaim(c, c)
    assert not valid_overclaim(c, "All studies link sugar intake to 20% more cavities.")  # changed number
    assert not valid_overclaim(c, "Totally unrelated sentence about volcanoes erupting.")


def test_clean_facts_keeps_verbatim_quotes_without_new_numbers_numbers_first():
    text = ("The reef stretches for 2,300 kilometres along the coast. It is visible from space and loved by divers. "
            "Coral cover fell by 50% between 1985 and 2012 according to surveys.")
    raw = [
        {"claim": "The reef is visible from space and loved by divers worldwide today.", "quote": "It is visible from space and loved by divers."},
        {"claim": "The reef stretches for 2,300 kilometres along the coast of Australia.", "quote": "The reef stretches for 2,300 kilometres along the coast."},
        {"claim": "The reef stretches for 2,500 kilometres along the coast of Australia.", "quote": "The reef stretches for 2,300 kilometres along the coast."},
        {"claim": "Coral cover fell by half between 1985 and 2012, surveys report.", "quote": "Coral cover fell by 50% between 1985 and 2012 according to surveys."},
        {"claim": "The reef is the largest living structure on Earth by far.", "quote": "The reef is the largest living structure on Earth by far."},  # not in the text
        {"claim": "Too short.", "quote": "Too short."},
        "garbage",
    ]
    q = ["The reef stretches for 2,300 kilometres along the coast.", "Coral cover fell by 50% between 1985 and 2012 according to surveys.",
         "It is visible from space and loved by divers."]
    assert [f["quote"] for f in clean_facts(raw, text)] == q  # numbers first; bad ones gone
    assert [f["quote"] for f in clean_facts(raw, text, keep=2)] == q[:2]
    assert clean_facts(None, text) == []


def test_unrelated_means_no_shared_proper_noun_or_number():
    words = word_set("The Burj Khalifa has 163 floors and opened in 2010.")
    assert not unrelated("The Burj Khalifa is tall.", words)
    assert not unrelated("The structure has 163 levels.", words)
    assert unrelated("Zorblax orbits Quuxia in 4001 days.", words)
    assert not unrelated("the thing is old.", words)  # nothing distinctive to check


def crossref_client(titles, calls):
    def handler(request):
        calls.append(request.url)
        assert request.url.params["rows"] == "5" and "query.bibliographic" in request.url.params
        return httpx.Response(200, json={"message": {"items": [{"title": [t]} for t in titles]}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_crossref_rejects_titles_at_ratio_075_and_caches(tmp_path):
    calls, cache = [], DiskCache(tmp_path)
    client = crossref_client(["Deep learning for coral reef monitoring in Australia", "Unrelated chemistry paper"], calls)
    assert not crossref_novel("Deep learning for coral reef monitoring in Australia", client, cache)  # exact
    assert not crossref_novel("Deep Learning for Coral Reef Monitoring in Austria", client, cache)  # ratio well above 0.75
    assert crossref_novel("A survey of volcanic ash dispersal models in Iceland", client, cache)  # far away
    n = len(calls)
    assert not crossref_novel("Deep learning for coral reef monitoring in Australia", client, cache)
    assert len(calls) == n  # the answer came from the disk cache
    assert build.CROSSREF_REJECT == 0.75
    base = "a" * 100  # a title with ratio exactly 0.75 to a hit is rejected, 0.74 is not
    assert not crossref_novel("a" * 75 + "b" * 25, crossref_client([base], []), DiskCache(tmp_path / "b"))
    assert crossref_novel("a" * 74 + "b" * 26, crossref_client([base], []), DiskCache(tmp_path / "c"))


def test_make_fabricated_filters_year_format_and_crossref(tmp_path):
    def ref(i, **kw):
        return {"authors": ["Doe, J.", "Roe, R.", "Poe, P."], "year": 2020, "title": f"A study number {i} of coral bleaching",
                "venue": "Journal of Reef Science", "volume": 12, "issue": 3, "pages": "100-120",
                "claim": f"Bleaching episodes {i} times more frequent were measured across the reef.", **kw}
    refs = [ref(1), ref(2, year=2012), ref(3, pages="n/a"), ref(4, title="A real paper that Crossref knows about"), ref(5), {"authors": []}]
    gen = SimpleNamespace(json=lambda system, user: {"refs": refs})
    client = crossref_client(["A real paper that Crossref knows about"], [])
    out = make_fabricated(gen, ["reefs"], 2, client, DiskCache(tmp_path))
    assert [r["id"] for r in out] == ["f01", "f02"] and out[0]["authors"] == "Doe, J., Roe, R., & Poe, P."
    with pytest.raises(RuntimeError):
        make_fabricated(gen, ["reefs"], 9, client, DiskCache(tmp_path / "x"))
    assert fab_authors(["A, B."]) == "A, B." and fab_authors(["A, B.", "C, D."]) == "A, B. & C, D."


def test_dead_links_keep_only_404_410_non_arxiv(tmp_path):
    kept = [{"id": "wiki-a", "kind": "wikipedia", "url": "https://w.example/a"},
            {"id": "org-b", "kind": "organisation", "url": "https://o.example/b/"},
            {"id": "org-c", "kind": "organisation", "url": "https://o.example/c"},
            {"id": "arxiv-d", "kind": "arxiv", "url": "https://arxiv.org/pdf/1"}]
    seen = []

    def fn(url):
        seen.append(url)
        return 404 if "w.example" in url else 410 if "o.example/b" in url else 200
    dead = dead_links(kept, DiskCache(tmp_path), fn)
    assert dead["wiki-a"] == build.dead_url(kept[0]) and dead["org-b"].startswith("https://o.example/b/2024-update-")
    assert dead["org-c"] is None and dead["arxiv-d"] is None and len(seen) == 3
    assert dead_links(kept, DiskCache(tmp_path), lambda u: 1 / 0) == dead  # statuses were cached


class FakeGenClient:
    def __init__(self, pt=1000, ct=500):
        self.pt, self.ct, self.n = pt, ct, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.n += 1
        assert kw["temperature"] == 0.3 and kw["response_format"] == {"type": "json_object"} and kw["model"] == build.GEN_MODEL
        msg = SimpleNamespace(content=json.dumps({"ok": self.n}))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=self.pt, completion_tokens=self.ct))


def test_gen_accounts_tokens_caches_and_stops_at_the_budget(tmp_path):
    client = FakeGenClient(pt=500_000, ct=500_000)  # $0.40 per call at 0.20 in / 0.60 out
    gen = Gen(client, DiskCache(tmp_path), budget=1.50)
    assert gen.json("s", "u") == {"ok": 1} and abs(gen.usd - 0.40) < 1e-9
    assert gen.json("s", "u") == {"ok": 1} and client.n == 1 and gen.cached == 1  # cached answer, still counted
    assert gen.json("s", "v") == {"ok": 2} and abs(gen.usd - 1.20) < 1e-9
    with pytest.raises(BudgetExceeded):
        gen.json("s", "w")  # $1.60 > $1.50: hard stop
    assert gen.cost()["model"] == build.GEN_MODEL and gen.cost()["usd"] == round(gen.usd, 4)
    with pytest.raises(BudgetExceeded):
        gen.json("s", "x")  # refuses before calling
    assert client.n == 3


# --- planning and assembling -----------------------------------------------------------


def synthetic(n_sources=8, per_source=10):
    pool, srcs = {}, {}
    for i in range(n_sources):
        sid = f"{'arxiv' if i % 4 == 3 else 'wiki'}-s{i}"
        name = "Zorblax" + "abcdefgh"[i] * 3
        srcs[sid] = {"id": sid, "kind": "arxiv" if sid.startswith("arxiv") else "wikipedia", "title": f"{name} Facts",
                     "url": f"https://x.example/{sid}", "text": "", "chars": 5000}
        pool[sid] = []
        for k in range(per_source):
            n = 100 + i * 20 + k
            claim = f"{name} {k} measures {n} metres across."
            pool[sid].append({"id": f"{sid}#{k}", "source_id": sid, "claim": claim, "quote": f"{name} {k} spans {n} metres.",
                              "ns": {"claim": claim.replace(str(n), str(n + 7)), "old": str(n), "new": str(n + 7)} if k % 3 else None,
                              "es": claim.replace("measures", "dwarfs") if k % 2 else None,
                              "oc": claim.replace("measures", "always measures") if k % 2 == 0 else None})
        srcs[sid]["words"] = word_set(" ".join(f["claim"] for f in pool[sid]))
    return pool, srcs


def plan(n=4, seed=1):
    pool, srcs = synthetic()
    dead = {s: (None if s.startswith("arxiv") else build.dead_url(srcs[s])) for s in pool}
    fabs = [{"id": f"f{i:02d}", "authors": "Doe, J. & Roe, R.", "year": 2020, "title": f"Invented study {i} of reefs",
             "venue": "Journal of Reef Science", "volume": 12, "issue": 3, "pages": "1-9",
             "claim": f"Invented finding {i} was reported for reefs."} for i in range(1, 4)]
    rng = random.Random(seed)
    reports = plan_reports(pool, {s: srcs[s]["words"] for s in pool}, dead, fabs, rng, n=n)
    return reports, pool, srcs, {f["id"]: f for f in fabs}, rng


def test_plan_follows_the_recipe_and_never_reuses_a_fact():
    reports, pool, srcs, fabs, _ = plan()
    assert len(reports) == 4
    for i, items in enumerate(reports):
        types = [it["type"] for it in items]
        assert len(items) == 10 and types.count("supported") == 5 and types.count("misattribution") == 1
        assert types.count("number_swap") >= 1 and types.count("entity_swap") + types.count("overclaim") >= 1
        assert types.count("fabricated" if i % 2 == 0 else "dead_link") == 1
        assert types.count("dead_link" if i % 2 == 0 else "fabricated") == 0
        cited = {it["ref"][1] for it in items if it["ref"][0] == "src"}
        assert 3 <= len(cited) <= 5
    originals = [(it["origin_id"], it["quote"]) for items in reports for it in items if it["type"] != "fabricated"]
    assert len(originals) == len(set(originals))  # no fact is used twice, in any role or report
    assert {it["ref"][1] for items in reports for it in items if it["ref"][0] == "src"} == set(pool)  # every source cited
    mis = [it for items in reports for it in items if it["type"] == "misattribution"]
    assert mis and all(it["ref"][1] != it["origin_id"] and unrelated(it["claim"], srcs[it["ref"][1]]["words"]) for it in mis)


def test_plan_is_deterministic_and_fails_loudly_without_facts():
    a, b = plan(seed=7)[0], plan(seed=7)[0]
    assert a == b
    pool, srcs = synthetic(per_source=1)
    with pytest.raises(RuntimeError, match="not enough"):
        plan_reports(pool, {s: srcs[s]["words"] for s in pool}, {}, [], random.Random(1), n=4)


def test_assemble_offsets_markers_and_references_match_the_gold():
    reports, pool, srcs, fabs, rng = plan()
    for items in reports:
        text, rows = assemble_report(items, srcs, fabs, rng)
        body, refs = text.split("## References")
        assert text.startswith("# Briefing: ") and len(rows) == 10
        for r in rows:
            assert text[r["start"]:r["end"]] == r["sentence"] and re.search(r" \[\d+\]\.$", r["sentence"])
            assert r["gold"] == build.GOLD[r["type"]]
        spans = split_sentences(body)
        assert all((r["start"], r["end"]) in spans for r in rows)  # Receipts' own splitter finds every gold sentence
        lines = [x for x in refs.strip().splitlines() if x.strip()]
        ks = {int(re.search(r"\[(\d+)\]\.$", r["sentence"]).group(1)) for r in rows}
        assert ks == {int(re.match(r"\[(\d+)\]", x).group(1)) for x in lines}
        for r in rows:
            k = int(re.search(r"\[(\d+)\]\.$", r["sentence"]).group(1))
            line = next(x for x in lines if x.startswith(f"[{k}] "))
            if r["type"] == "fabricated":
                assert "http" not in line and "Journal of Reef Science, 12(3), 1-9." in line and r["source_url"] == ""
            elif r["type"] == "dead_link":
                assert "/2024-update-" in line and r["source_url"] in line
            else:
                assert r["source_url"] in line and (" arXiv. " in line) == ("arxiv" in r["source_url"])
        assert all(r["origin_url"] != r["source_url"] for r in rows if r["type"] == "misattribution")


# --- end to end with a scripted generator ------------------------------------------------


def scripted_client():
    """An OpenAI-style client that answers the three generator prompts from their input, like a compliant model."""
    def answer(system, user):
        if system == build.FACTS_SYSTEM:
            lines = user.split("TEXT:\n", 1)[1].split("\n")[1:15]
            return {"facts": [{"claim": ln, "quote": ln} for ln in lines]}
        if system == build.PERTURB_SYSTEM:
            items = json.loads(user)["items"]
            return {"items": [{"i": it["i"], "entity_swap": it["claim"].replace(it["claim"].split()[0], "Quxlandia", 1),
                               "overclaim": it["claim"].replace(" measures ", " always measures ")} for it in items]}
        refs = [{"authors": ["Doe, J.", "Roe, R."], "year": 2015 + i % 11, "title": f"Invented study number {i} of volcanic plumes",
                 "venue": "Journal of Reef Science", "volume": i + 1, "issue": 2, "pages": f"{i + 1}-{i + 9}",
                 "claim": f"Invented finding number {i} was reported for volcanic plumes."} for i in range(20)]
        return {"refs": refs}

    def create(**kw):
        out = answer(kw["messages"][0]["content"], kw["messages"][1]["content"])
        msg = SimpleNamespace(content=json.dumps(out))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=2000, completion_tokens=1000))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_build_dataset_end_to_end_is_extractable_by_receipts(tmp_path):
    from bench.run import match_claim
    from receipts.extract import extract
    from receipts.testing import FakeLLM

    kept = []
    for i in range(8):
        name = "Zorblax" + "abcdefgh"[i] * 3
        text = "\n".join(f"{name} item {k} measures {100 + 20 * i + k} metres across the main basin, a figure recorded in {1900 + 11 * i + 3 * k} by the survey team." for k in range(60))
        kind = "arxiv" if i % 4 == 3 else ("wikipedia" if i % 2 == 0 else "organisation")
        kept.append({"id": f"{kind}-s{i}", "kind": kind, "url": f"https://x.example/{kind}-s{i}", "text": text, "chars": len(text), "title": f"{name} Facts"})
    dead = {s["id"]: None if s["kind"] == "arxiv" else build.dead_url(s) for s in kept}
    gen = Gen(scripted_client(), DiskCache(tmp_path / "cache"))
    out = tmp_path / "data"
    gold = build.build_dataset(kept, dead, gen, crossref_client([], []), DiskCache(tmp_path / "cache2"), out, n_reports=4)

    assert sorted(gold["reports"]) == ["r01", "r02", "r03", "r04"] and gold["seed"] == build.SEED and gold["generator"] == build.GEN_MODEL
    assert sorted(p.name for p in (out / "reports").iterdir()) == ["r01.md", "r02.md", "r03.md", "r04.md"]
    assert json.loads((out / "gold.json").read_text())["reports"]["r01"] == gold["reports"]["r01"]
    used = json.loads((out / "sources_used.json").read_text())
    assert {s["id"] for s in used} == {s["id"] for s in kept} and {"id", "kind", "url", "title", "chars"} == set(used[0])
    cost = json.loads((out / "generation_cost.json").read_text())
    assert cost["usd"] == round(gen.usd, 4) > 0 and cost["prompt_tokens"] == gen.prompt_tokens
    rows = [r for rs in gold["reports"].values() for r in rs]
    assert len(rows) == 40 and {r["type"] for r in rows} == {"supported", "number_swap", "entity_swap", "overclaim", "misattribution", "fabricated", "dead_link"}
    for rid, rs in gold["reports"].items():
        doc = (out / "reports" / f"{rid}.md").read_text(encoding="utf-8")
        assert all(doc[r["start"]:r["end"]] == r["sentence"] for r in rs)
        sources, claims = extract(doc, FakeLLM())  # the real extraction stage reads the report as intended
        by_id = {s.id: s for s in sources}
        for r in rs:
            c = match_claim(r, claims)
            assert c is not None and len(c.source_ids) == 1
            assert (by_id[c.source_ids[0]].url or "") == r["source_url"]
