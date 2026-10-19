"""ReceiptsBench v1 dataset builder: 36 sources -> verified facts -> perturbed claims -> 30 reports with gold labels.

Run once (`uv run python bench/build.py`) and commit bench/data/: the output is frozen. `--dry-run` fetches and
validates the sources and makes no LLM call. Every Tavily / HTTP / Crossref / LLM answer is cached in the
Receipts DiskCache, so an interrupted build resumes without paying twice. The generator is an independent
non-NVIDIA model, so the Nemotron judges never grade their own phrasing.
"""

import argparse
import difflib
import hashlib
import json
import random
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from receipts.config import load_settings  # noqa: E402
from receipts.llm import parse_json_lenient  # noqa: E402
from receipts.scholarly import _ATOM, ARXIV_URL, _arxiv_entry  # noqa: E402
from receipts.search import DiskCache, TavilySearcher, http_status  # noqa: E402
from receipts.sources import page_title  # noqa: E402
from receipts.textutil import clean_web_text, contains_quote, normalize, numbers  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SEED, GEN_MODEL = 20261020, "Qwen/Qwen3-235B-A22B-Instruct-2507"
PRICE_IN, PRICE_OUT, BUDGET_USD = 0.20, 0.60, 1.50  # USD per 1M tokens; hard stop on generation spend
N_REPORTS, MIN_CHARS, WINDOW, WIKI_SKIP = 30, 3000, 14_000, 1_500
# ~10 distinct facts are consumed per report (5 supported + 3 perturbed + 1 misattributed + a dead-link claim),
# so 30 reports need ~285 facts: asking 18 and keeping 12 per source leaves headroom; 6/4 would supply only 144.
FACTS_ASK, FACTS_KEEP = 18, 12
CROSSREF_URL, CROSSREF_REJECT = "https://api.crossref.org/works", 0.75
GOLD = {"supported": "supported", "number_swap": "contradicted", "entity_swap": "contradicted",
        "overclaim": "partial", "misattribution": "not_in_source", "fabricated": "source_unavailable",
        "dead_link": "source_unavailable"}

FACTS_SYSTEM = """You extract checkable facts from a source text for a citation-checking benchmark.
Return ONLY a JSON object: {"facts": [{"claim": "...", "quote": "..."}]}.
- "quote": ONE sentence copied verbatim, character for character, from the text (same numbers, spelling, punctuation).
- "claim": a faithful one-sentence paraphrase of that quote, as a report would state it with a citation. Name the \
subject explicitly (no it/they/this), keep every number and name exactly as in the quote, add nothing the quote does not say.
- Choose specific, self-contained facts about the main subject of the page (numbers, dates, named entities, \
measurable properties); prefer facts with numbers; spread them over the text; at most one fact per sentence.
- Ignore menus, navigation, references, captions and unrelated promotional items."""
PERTURB_SYSTEM = """You write false variants of true claims for a citation-checking benchmark. Each input item has a \
true CLAIM and the QUOTE of the source that supports it. Return ONLY a JSON object:
{"items": [{"i": <index>, "entity_swap": "...", "overclaim": "..."}]}
- entity_swap: copy the claim but replace exactly ONE named entity, date or quantity word (a person, place, \
organisation, material, species, unit, ordinal such as first/second, direction ...) with a different plausible one, so the \
claim becomes false given the quote. The replacement must not appear anywhere in the quote. Change nothing else.
- overclaim: copy the claim but strengthen its scope or certainty (some -> all, may -> will, associated with -> \
causes, or add "the largest", "the first", "always", "never") so the source no longer fully supports it. Do not change \
any number; keep it one sentence of similar length."""
FAB_SYSTEM = """You invent plausible but NONEXISTENT academic references for a citation-checking benchmark. Return \
ONLY a JSON object: {"refs": [{"authors": ["Surname, I.", ...], "year": <2015-2025>, "title": "...", "venue": "...", \
"volume": <int>, "issue": <int>, "pages": "123-145", "claim": "<one sentence stating a specific finding this paper \
might be cited for>"}]}. Titles must be specific and original-sounding (never the title of a well-known real paper); \
venues are real journals; 2-4 authors; topics near the ones given."""


# --- generator LLM -------------------------------------------------------------------


class BudgetExceeded(RuntimeError):
    pass


class Gen:
    """Generator client (Token Factory, OpenAI SDK): cached JSON calls, token/USD accounting, hard spend limit."""

    def __init__(self, client, cache: DiskCache, budget: float = BUDGET_USD):
        self.client, self.cache, self.budget = client, cache, budget
        self.calls = self.cached = self.prompt_tokens = self.completion_tokens = 0
        self._lock = threading.Lock()

    @property
    def usd(self) -> float:
        return (self.prompt_tokens * PRICE_IN + self.completion_tokens * PRICE_OUT) / 1e6

    def _call(self, system: str, user: str) -> str:
        key = "gen:" + hashlib.sha256(json.dumps([GEN_MODEL, system, user]).encode()).hexdigest()
        hit, fresh = self.cache.get(key), False
        if hit is None:
            if self.usd > self.budget:
                raise BudgetExceeded(f"generation spend ${self.usd:.2f} exceeds the ${self.budget:.2f} limit")
            kw = dict(model=GEN_MODEL, temperature=0.3,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            try:
                r = self.client.chat.completions.create(**kw, response_format={"type": "json_object"})
            except Exception as e:  # a server without JSON mode answers 400: ask again without it
                if type(e).__name__ != "BadRequestError":
                    raise
                r = self.client.chat.completions.create(**kw)
            hit = {"text": r.choices[0].message.content or "", "pt": r.usage.prompt_tokens, "ct": r.usage.completion_tokens}
            self.cache.set(key, hit)
            fresh = True
        with self._lock:
            self.calls += 1
            self.cached += not fresh
            self.prompt_tokens += hit["pt"]
            self.completion_tokens += hit["ct"]
            if self.usd > self.budget:
                raise BudgetExceeded(f"generation spend ${self.usd:.2f} exceeds the ${self.budget:.2f} limit")
        return hit["text"]

    def json(self, system: str, user: str) -> dict:
        try:
            return parse_json_lenient(self._call(system, user))
        except ValueError:
            return parse_json_lenient(self._call(system, user + "\n\nReturn only valid JSON."))

    def cost(self) -> dict:
        return {"model": GEN_MODEL, "calls": self.calls, "cached_calls": self.cached,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "price_per_1m_tokens_usd": {"input": PRICE_IN, "output": PRICE_OUT}, "usd": round(self.usd, 4)}


# --- sources -------------------------------------------------------------------------

_CHROME = re.compile(r"suggested searches|^menu$|navigation|skip to|cookie", re.I)


def arxiv_title(url: str, client: httpx.Client, cache: DiskCache) -> str | None:
    """The paper's real title from the arXiv API (the PDF's first line is often an author list)."""
    aid = url.rstrip("/").rsplit("/", 1)[-1]
    hit = cache.get(f"arxiv:id:{aid}")
    if hit is None:
        time.sleep(3)  # arXiv asks for one request per 3 s
        try:
            r = client.get(ARXIV_URL, params={"id_list": aid})
            entries = ET.fromstring(r.text).findall("a:entry", _ATOM) if r.status_code == 200 else []
        except (httpx.HTTPError, ET.ParseError):
            return None
        hit = _arxiv_entry(entries[0])["title"] if entries else None
        if hit:
            cache.set(f"arxiv:id:{aid}", hit)
    return hit


def source_title(s: dict, text: str, arxiv: str | None = None) -> str:
    """`page_title` of the fetched page; the arXiv API title for papers; the URL path when the page leads with site chrome."""
    t = arxiv or page_title(text)
    if not t or _CHROME.search(t):
        parts = [p for p in urlparse(s["url"]).path.split("/") if p and not p.startswith("index.")]
        t = " ".join(parts[-2:]).replace("-", " ").replace("_", " ").title()
    return t.strip()


def load_sources(searcher, cache, srcs: list[dict]) -> tuple[list[dict], list[tuple[str, int]]]:
    """Fetch and clean every source; (kept sources with text/title/chars, dropped (id, chars))."""
    pages = searcher.extract([s["url"] for s in srcs])
    kept, dropped = [], []
    with httpx.Client(timeout=30) as client:
        for s in srcs:
            text = clean_web_text(pages.get(s["url"]) or "")
            if len(text) < MIN_CHARS:
                dropped.append((s["id"], len(text)))
                continue
            arx = arxiv_title(s["url"], client, cache) if s["kind"] == "arxiv" else None
            kept.append({**s, "text": text, "chars": len(text), "title": source_title(s, text, arx)})
    return kept, dropped


def dead_url(s: dict) -> str:
    return s["url"].rstrip("/") + f"/2024-update-{hashlib.sha1(s['id'].encode()).hexdigest()[:6]}"


def cached_status(url: str, cache: DiskCache, status_fn=http_status) -> int | None:
    hit = cache.get(f"status:{url}")
    if hit is None:
        hit = status_fn(url)
        if hit is not None:
            cache.set(f"status:{url}", hit)
    return hit


def dead_links(kept: list[dict], cache: DiskCache, status_fn=http_status) -> dict[str, str | None]:
    """source id -> fake-path URL that answers 404/410 (None: arXiv, or the site does not answer that way)."""
    def probe(s):
        return dead_url(s) if s["kind"] != "arxiv" and cached_status(dead_url(s), cache, status_fn) in (404, 410) else None
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip((s["id"] for s in kept), pool.map(probe, kept)))


# --- facts and perturbations ---------------------------------------------------------

_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_NUM = re.compile(r"(?<![\w.,])(?<![A-Za-z]-)\d+(?:,\d{3})*(?:\.\d+)?(?![A-Za-z\d]|[.,]\d)")
_FACTORS = (0.5, 0.7, 1.4, 1.8, 2.5)


def _new_number(tok: str, before: str, after: str, rng: random.Random) -> str | None:
    """One replacement for the number `tok` (None when it should not be swapped), by the number's kind."""
    near = {w.strip(".,;()").lower() for w in (before.split()[-1:] + after.split()[:1])}
    pct = bool(re.match(r"\s*(?:%|percent|per cent|percentage point)", after, re.I))
    if "." in tok:
        digits = [i for i, c in enumerate(tok) if c.isdigit()]
        sig = [i for i in digits if tok[:i].strip("0.,") or tok[i] != "0"]  # skip leading zeros
        if not sig:
            return None
        if pct:  # a decimal percentage: move it 8-25 points, keep its decimals
            p, dec = float(tok), len(tok.split(".")[1])
            moves = [p + d for d in range(-25, 26) if 8 <= abs(d) <= 25 and 0 <= p + d <= 100]
            return f"{rng.choice(moves):.{dec}f}" if moves else None
        i = rng.choice(sig)
        digit = rng.choice([d for d in "0123456789" if d != tok[i] and not (d == "0" and i == sig[0])])
        return tok[:i] + digit + tok[i + 1:]
    v = int(tok.replace(",", ""))
    if pct:
        moves = [v + d for d in range(-25, 26) if 8 <= abs(d) <= 25 and 0 <= v + d <= 100]
        return str(rng.choice(moves)) if moves else None
    if re.fullmatch(r"\d{4}", tok) and 1000 <= v <= 2100:  # a year
        moves = [v + d for d in range(-8, 9) if 3 <= abs(d) <= 8 and v + d <= (2026 if v <= 2026 else 2100)]
        return str(rng.choice(moves))
    if v < 1 or (v <= 31 and near & _MONTHS):
        return None  # zero, or the day of a date
    unit = 10 ** (len(str(v)) - len(str(v).rstrip("0")))  # the original's precision: its last non-zero place
    for f in rng.sample(_FACTORS, len(_FACTORS)):
        nv = int(v * f / unit + 0.5) * unit
        if nv != v and nv >= 1:
            return f"{nv:,}" if "," in tok else str(nv)
    return None


def swap_number(claim: str, text_numbers: set[str], rng: random.Random) -> dict | None:
    """Change ONE number of the claim; the new number must not occur anywhere in the source's numbers.
    -> {"claim", "old", "new"} or None."""
    found = list(_NUM.finditer(claim))
    rng.shuffle(found)
    for m in found:
        for _ in range(8):
            new = _new_number(m.group(), claim[:m.start()], claim[m.end():], rng)
            if new and new != m.group() and new.replace(",", "") not in text_numbers:
                return {"claim": claim[:m.start()] + new + claim[m.end():], "old": m.group(), "new": new}
    return None


def valid_entity_swap(claim: str, new: str, quote: str) -> bool:
    """Exactly one short word span differs from the claim, and the replacement does not occur in the quote."""
    a, b = claim.split(), new.split()
    ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
    if len(ops) != 1 or ops[0][0] != "replace" or ops[0][2] - ops[0][1] > 5 or ops[0][4] - ops[0][3] > 5:
        return False
    rep = normalize(" ".join(b[ops[0][3]:ops[0][4]]))
    return bool(rep) and not re.search(rf"(?<!\w){re.escape(rep)}(?!\w)", normalize(quote))


def valid_overclaim(claim: str, new: str) -> bool:
    """A stronger rewording with the same numbers, one sentence, similar length."""
    return (normalize(new) != normalize(claim) and "\n" not in new and numbers(new) == numbers(claim)
            and len(new) <= 1.6 * len(claim) + 20
            and difflib.SequenceMatcher(None, claim, new).ratio() >= 0.6)


def fact_window(kind: str, text: str) -> str:
    return text[WIKI_SKIP:WIKI_SKIP + WINDOW] if kind == "wikipedia" else text[:WINDOW]


def clean_facts(raw, text: str, keep: int = FACTS_KEEP) -> list[dict]:
    """Facts whose quote is verbatim in the source and whose claim adds no number; numbers first, at most `keep`."""
    out, seen = [], set()
    for f in raw if isinstance(raw, list) else []:
        claim, quote = (f.get("claim"), f.get("quote")) if isinstance(f, dict) else (None, None)
        if not (isinstance(claim, str) and isinstance(quote, str)):
            continue
        claim, quote = " ".join(claim.split()), " ".join(quote.split())
        if (not 30 <= len(claim) <= 400 or len(quote) < 30 or quote in seen or re.search(r"[\[\]]", claim)
                or not contains_quote(text, quote) or not numbers(claim) <= numbers(quote)):
            continue
        seen.add(quote)
        out.append({"claim": claim, "quote": quote})
    out.sort(key=lambda f: not numbers(f["claim"]))  # stable: facts with numbers first
    return out[:keep]


def make_facts(gen: Gen, s: dict) -> list[dict]:
    raw = gen.json(FACTS_SYSTEM, f"SOURCE TITLE: {s['title']}\nExtract {FACTS_ASK} facts.\n\nTEXT:\n{fact_window(s['kind'], s['text'])}")
    facts = clean_facts(raw.get("facts"), s["text"])
    tn = numbers(s["text"])
    for k, f in enumerate(facts):
        f.update(id=f"{s['id']}#{k}", source_id=s["id"], es=None, oc=None,
                 ns=swap_number(f["claim"], tn, random.Random(f"{SEED}:{s['id']}:{k}")))
    return facts


def add_perturbations(gen: Gen, facts: list[dict], batch: int = 10) -> None:
    """The generator writes one entity_swap and one overclaim per fact (validated; None when invalid)."""
    def run(chunk):
        items = [{"i": i, "claim": f["claim"], "quote": f["quote"]} for i, f in enumerate(chunk)]
        out = gen.json(PERTURB_SYSTEM, json.dumps({"items": items}, ensure_ascii=False)).get("items")
        for it in out if isinstance(out, list) else []:
            if isinstance(it, dict) and isinstance(it.get("i"), int) and 0 <= it["i"] < len(chunk):
                f = chunk[it["i"]]
                es, oc = it.get("entity_swap"), it.get("overclaim")
                if isinstance(es, str) and valid_entity_swap(f["claim"], es.strip(), f["quote"]):
                    f["es"] = es.strip()
                if isinstance(oc, str) and valid_overclaim(f["claim"], oc.strip()):
                    f["oc"] = oc.strip()
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(run, [facts[i:i + batch] for i in range(0, len(facts), batch)]))


# --- fabricated references -----------------------------------------------------------


def crossref_titles(title: str, client: httpx.Client, cache: DiskCache) -> list[str]:
    key = f"crossref:{normalize(title)}"
    hit = cache.get(key)
    if hit is None:
        for attempt in range(3):
            r = client.get(CROSSREF_URL, params={"query.bibliographic": title, "rows": 5})
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(2 * (attempt + 1))
        r.raise_for_status()
        hit = [t for item in r.json()["message"]["items"] for t in item.get("title") or []]
        cache.set(key, hit)
    return hit


def crossref_novel(title: str, client: httpx.Client, cache: DiskCache) -> bool:
    """True when no Crossref hit has a title within the reject ratio (independent of Receipts' OpenAlex lookup)."""
    nt = normalize(title)
    return all(difflib.SequenceMatcher(None, nt, normalize(t)).ratio() < CROSSREF_REJECT
               for t in crossref_titles(title, client, cache))


def fab_authors(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + (", & " if len(names) > 2 else " & ") + names[-1]


def make_fabricated(gen: Gen, topics: list[str], need: int, client: httpx.Client, cache: DiskCache) -> list[dict]:
    """>= `need` invented references (20 asked per round) whose titles Crossref does not know."""
    good: list[dict] = []
    seen: set[str] = set()
    for _ in range(3):
        raw = gen.json(FAB_SYSTEM, f"Write 20 references. Topics: {'; '.join(topics)}").get("refs")
        for r in raw if isinstance(raw, list) else []:
            try:
                names = [str(a).strip() for a in r["authors"] if str(a).strip()]
                ref = {"authors": fab_authors(names), "year": int(r["year"]), "title": str(r["title"]).strip().rstrip("."),
                       "venue": str(r["venue"]).strip(), "volume": int(r["volume"]), "issue": int(r["issue"]),
                       "pages": str(r["pages"]).strip(), "claim": " ".join(str(r["claim"]).split())}
            except (KeyError, TypeError, ValueError):
                continue
            if (names and 2015 <= ref["year"] <= 2025 and len(ref["title"]) >= 15 and re.fullmatch(r"\d+\s*[-–]\s*\d+", ref["pages"])
                    and 30 <= len(ref["claim"]) <= 400 and not re.search(r"[\[\]]", ref["claim"])
                    and normalize(ref["title"]) not in seen and crossref_novel(ref["title"], client, cache)):
                seen.add(normalize(ref["title"]))
                good.append({**ref, "id": f"f{len(good) + 1:02d}"})
        if len(good) >= need:
            return good
    raise RuntimeError(f"only {len(good)} fabricated references passed the Crossref check; need {need}")


# --- planning and assembling the reports ---------------------------------------------

_STOP_CAPS = {"the", "this", "that", "these", "those", "some", "many", "most", "after", "before", "during", "since",
              "while", "although", "however", "there", "its", "their", "both", "each", "all", "one", "several"}
_ELIGIBLE = {"supported": lambda f: True, "number_swap": lambda f: bool(f["ns"]),
             "entity_swap": lambda f: bool(f["es"]), "overclaim": lambda f: bool(f["oc"])}


def word_set(text: str) -> set[str]:
    return set(re.findall(r"\w+(?:\.\w+)?", re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())))


def unrelated(claim: str, words: set[str]) -> bool:
    """The claim's proper nouns and numbers (>= 2 digits) occur nowhere in the other source: a different topic."""
    caps = {w.lower() for w in re.findall(r"\b[A-Z][\w-]{2,}", claim)} - _STOP_CAPS
    distinct = caps | {n for n in numbers(claim) if len(n) >= 2}
    return bool(distinct) and not distinct & words


def _item(typ: str, f: dict, ref: tuple, claim: str, note: str = "") -> dict:
    """One cited sentence: `ref` is the reference it cites, `origin_id` the source the fact was taken from."""
    return {"type": typ, "claim": claim, "ref": ref, "origin_id": f["source_id"], "quote": f["quote"],
            "original": "" if typ == "supported" else f["claim"], "note": note}


def _try_report(i, anchors, pool, used, words, dead, fabs, rng) -> list[dict] | None:
    free = {s: [f for f in pool[s] if f["id"] not in used] for s in pool}
    chosen, others = list(anchors), [s for s in pool if s not in anchors and free[s]]
    size = max(rng.randint(3, 5), len(anchors))
    while len(chosen) < size and others:
        s = rng.choices(others, weights=[len(free[o]) for o in others])[0]
        others.remove(s)
        chosen.append(s)
    roles = ["number_swap", "entity_swap" if (i // 2) % 2 == 0 else "overclaim",
             rng.choice(["number_swap", "entity_swap", "overclaim"])] + ["supported"] * 5
    taken, items, covered = set(), [], set()
    for role in roles:  # constrained roles first; a source not cited yet is preferred
        cands = [f for s in chosen for f in free[s] if f["id"] not in taken and _ELIGIBLE[role](f)]
        if not cands:
            return None
        f = rng.choice([c for c in cands if c["source_id"] not in covered] or cands)
        taken.add(f["id"])
        covered.add(f["source_id"])
        claim = {"number_swap": lambda: f["ns"]["claim"], "entity_swap": lambda: f["es"], "overclaim": lambda: f["oc"],
                 "supported": lambda: f["claim"]}[role]()
        note = f"{f['ns']['old']} -> {f['ns']['new']}" if role == "number_swap" else ""
        items.append(_item(role, f, ("src", f["source_id"]), claim, note))
    if len(covered) < 3 or not set(anchors) <= covered:
        return None
    b = rng.choice(sorted(covered))  # misattribution: a claim of source A cited to source B on another topic
    cands = [f for s in pool if s != b for f in free[s] if f["id"] not in taken and unrelated(f["claim"], words[b])]
    if not cands:
        return None
    f = rng.choice(cands)
    taken.add(f["id"])
    items.append(_item("misattribution", f, ("src", b), f["claim"], f"claim of {f['source_id']} cited to {b}"))
    if i % 2 == 0:  # alternate: a fabricated reference, or a dead link
        if not fabs:
            return None
        fab = fabs[0]
        items.append({"type": "fabricated", "claim": fab["claim"], "ref": ("fab", fab["id"]), "origin_id": "", "quote": "",
                      "original": "", "note": fab["title"]})
    else:
        cands = [f for s in pool if dead.get(s) for f in free[s] if f["id"] not in taken]
        if not cands:
            return None
        f = rng.choice(cands)
        taken.add(f["id"])
        items.append(_item("dead_link", f, ("dead", f["source_id"]), f["claim"], dead[f["source_id"]]))
    used |= taken
    if i % 2 == 0:
        fabs.pop(0)
    return items


def plan_reports(pool: dict[str, list[dict]], words: dict[str, set[str]], dead: dict, fabs: list[dict],
                 rng: random.Random, n: int = N_REPORTS) -> list[list[dict]]:
    """The items of each report. Every source is cited by >= 1 report; no fact is used twice."""
    order = sorted(pool)
    rng.shuffle(order)
    used: set[str] = set()
    fabs, reports = list(fabs), []
    for i in range(n):
        for _ in range(500):
            items = _try_report(i, order[i::n], pool, used, words, dead, fabs, rng)
            if items:
                break
        else:
            raise RuntimeError(f"report r{i + 1:02d}: not enough unused facts (raise FACTS_KEEP or add sources)")
        reports.append(items)
    cited = {it["ref"][1] for r in reports for it in r if it["ref"][0] == "src"}
    if missing := set(pool) - cited:
        raise RuntimeError(f"sources never cited: {sorted(missing)}")
    return reports


def ref_line(k: int, ref: tuple, srcs: dict[str, dict], fabs: dict[str, dict]) -> str:
    kind, key = ref
    if kind == "fab":
        f = fabs[key]
        return f"[{k}] {f['authors']} ({f['year']}). {f['title']}. {f['venue']}, {f['volume']}({f['issue']}), {f['pages']}."
    s = srcs[key]
    title = s["title"].rstrip(".")
    if kind == "dead":
        return f"[{k}] {title}. {dead_url(s)}"
    return f"[{k}] {title}. arXiv. {s['url']}" if s["kind"] == "arxiv" else f"[{k}] {title}. {s['url']}"


def assemble_report(items: list[dict], srcs: dict[str, dict], fabs: dict[str, dict],
                    rng: random.Random) -> tuple[str, list[dict]]:
    """The report text and its gold rows; `text[start:end] == sentence` for every row."""
    items = rng.sample(items, len(items))
    nums: dict[tuple, int] = {}
    for it in items:
        it["k"] = nums.setdefault(it["ref"], len(nums) + 1)
        it["sentence"] = f"{it['claim'].strip().rstrip('.')} [{it['k']}]."
    topic = [srcs[r[1]]["title"] for r in nums if r[0] == "src"]
    topic = " and ".join(" ".join(t.split()[:4]) for t in topic[:2])
    cut = rng.choice([0, rng.randint(4, 6)])  # one paragraph, or two
    text, rows = f"# Briefing: {topic}\n\n", []
    for j, it in enumerate(items):
        if j and j == cut:
            text += "\n\n"
        elif j:
            text += " "
        start = len(text)
        text += it["sentence"]
        kind, key = it["ref"]
        cited = {"src": lambda: srcs[key]["url"], "dead": lambda: dead_url(srcs[key]), "fab": lambda: ""}[kind]()
        rows.append({"start": start, "end": len(text), "sentence": it["sentence"], "type": it["type"],
                     "gold": GOLD[it["type"]], "source_url": cited, "quote": it["quote"],
                     "origin_url": srcs[it["origin_id"]]["url"] if it["origin_id"] else "",
                     "original": it["original"], "note": it["note"]})
    text += "\n\n## References\n\n" + "\n".join(ref_line(k, r, srcs, fabs) for r, k in nums.items()) + "\n"
    assert all(text[r["start"]:r["end"]] == r["sentence"] for r in rows)
    return text, rows


# --- driver --------------------------------------------------------------------------


def dry_run(kept, dropped, dead, client, cache) -> None:
    print(f"{'source':<26}{'kind':<13}{'chars':>9}  dead-link  title")
    for s in kept:
        print(f"{s['id']:<26}{s['kind']:<13}{s['chars']:>9,}  {'404/410' if dead[s['id']] else ('n/a' if s['kind'] == 'arxiv' else 'NO'):<9}  {s['title'][:60]}")
    print(f"kept {len(kept)} sources; dropped (< {MIN_CHARS} chars): {dropped or 'none'}")
    bad = [i for i, u in dead.items() if not u and not i.startswith("arxiv-")]
    print(f"dead-link probe: {sum(bool(u) for u in dead.values())} non-arXiv sources answer 404/410; unusable for dead_link: {bad or 'none'}")
    probe = "Attention Is All You Need"
    print(f"Crossref probe: a known title is {'NOT ' if crossref_novel(probe, client, cache) else ''}recognised (expected: recognised)")
    sw = []
    for s in kept:
        tn = numbers(s["text"])
        sw += [swap_number(m.group().strip(), tn, random.Random(SEED))
               for m in re.finditer(r"[^.\n]{40,300}\d[^.\n]{0,200}\.", fact_window(s["kind"], s["text"]))]
    print(f"numeric sentences in the fact windows: {len(sw)}; programmatic number_swap works on {sum(bool(x) for x in sw)}")


def build_dataset(kept: list[dict], dead: dict, gen: Gen, client: httpx.Client, cache: DiskCache, out: Path = DATA,
                  n_reports: int = N_REPORTS) -> dict:
    """Facts -> perturbations -> fabricated references -> plan -> write `out` (reports/, gold.json, sources_used.json,
    generation_cost.json). Returns the gold dict. Raises BudgetExceeded or RuntimeError before writing anything."""
    rng = random.Random(SEED)
    with ThreadPoolExecutor(max_workers=6) as pool:
        facts = {s["id"]: f for s, f in zip(kept, pool.map(lambda s: make_facts(gen, s), kept)) if f}
    flat = [f for fs in facts.values() for f in fs]
    add_perturbations(gen, flat)
    fabs = make_fabricated(gen, [s["title"] for s in kept], (n_reports + 1) // 2, client, cache)
    print(f"facts: {len(flat)} from {len(facts)} sources ({sum(bool(f['ns']) for f in flat)} number swaps, "
          f"{sum(bool(f['es']) for f in flat)} entity swaps, {sum(bool(f['oc']) for f in flat)} overclaims); "
          f"~{int(9.5 * n_reports)} are needed; {len(fabs)} fabricated references", flush=True)
    srcs = {s["id"]: s for s in kept if s["id"] in facts}
    reports = plan_reports(facts, {i: word_set(s["text"]) for i, s in srcs.items()}, dead, fabs, rng, n_reports)
    fab_by_id = {f["id"]: f for f in fabs}
    (out / "reports").mkdir(parents=True, exist_ok=True)
    gold = {"version": "ReceiptsBench v1", "generator": GEN_MODEL, "seed": SEED,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reports": {}}
    for i, items in enumerate(reports, 1):
        text, rows = assemble_report(items, srcs, fab_by_id, rng)
        (out / "reports" / f"r{i:02d}.md").write_text(text, encoding="utf-8")
        gold["reports"][f"r{i:02d}"] = rows
    cited = {r["ref"][1] for items in reports for r in items if r["ref"][0] == "src"}
    used = [{"id": s["id"], "kind": s["kind"], "url": s["url"], "title": s["title"], "chars": s["chars"]}
            for s in srcs.values() if s["id"] in cited]
    for name, obj in (("gold.json", gold), ("sources_used.json", used), ("generation_cost.json", gen.cost())):
        (out / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return gold


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the frozen ReceiptsBench v1 dataset.")
    ap.add_argument("--dry-run", action="store_true", help="fetch and validate the sources; no LLM call, no output files")
    ap.add_argument("--force", action="store_true", help="overwrite an existing bench/data/gold.json")
    args = ap.parse_args(argv)
    settings = load_settings()
    if not settings.tavily_api_key or (not args.dry_run and not settings.nebius_api_key):
        print("error: set TAVILY_API_KEY (and NEBIUS_API_KEY for the real build) in .env", file=sys.stderr)
        return 2
    if not args.dry_run and (DATA / "gold.json").exists() and not args.force:
        print("error: bench/data/gold.json exists (the dataset is frozen); pass --force to rebuild", file=sys.stderr)
        return 2
    cache = DiskCache(settings.cache_dir)
    kept, dropped = load_sources(TavilySearcher(settings.tavily_api_key, cache), cache, json.loads((HERE / "sources.json").read_text()))
    dead = dead_links(kept, cache)
    with httpx.Client(timeout=30, headers={"User-Agent": "ReceiptsBench/1.0"}) as client:
        if args.dry_run:
            dry_run(kept, dropped, dead, client, cache)
            return 0
        from openai import OpenAI

        gen = Gen(OpenAI(base_url=settings.base_url, api_key=settings.nebius_api_key, timeout=180, max_retries=3), cache)
        print(f"{len(kept)} sources (dropped: {dropped or 'none'}); generating ...", flush=True)
        try:
            gold = build_dataset(kept, dead, gen, client, cache)
        except BudgetExceeded as e:
            print(f"STOP: {e}", file=sys.stderr)
            return 1
    types: dict[str, int] = {}
    for r in (r for rows in gold["reports"].values() for r in rows):
        types[r["type"]] = types.get(r["type"], 0) + 1
    print(f"wrote {len(gold['reports'])} reports, {sum(types.values())} cited sentences {types}")
    print(f"generator tokens: {gen.prompt_tokens:,} in / {gen.completion_tokens:,} out = ${gen.usd:.4f} "
          f"({gen.calls} calls, {gen.cached} from cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
