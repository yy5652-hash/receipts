# Receipts

**Does the source really say that?** Receipts reads an AI-written report, follows every citation to its source, and shows sentence by sentence whether the source exists and actually says what the report claims. Each verdict comes with the verbatim quote from the source as evidence, and when a citation fails, Receipts searches the web for a source that does say it.

Built for the Nebius × NVIDIA Global AI Hackathon (Best Apps and Agents track) with **NVIDIA Nemotron 3 Nano, Super and Ultra on Nebius Token Factory** and **Tavily** search and extraction.

- **Live demo:** https://receipts.43-160-214-187.sslip.io (two recorded audits open instantly; paste your own report for a live check)
- **Demo video:** https://youtu.be/oB10mc6nSxQ (2:50)
- **Devpost:** https://devpost.com/software/receipts-5oqhv6
- **Benchmark:** [ReceiptsBench results](bench/RESULTS.md) · [Find a real source results](bench/results_suggest/RESULTS.md)

**Try it in a minute.** Open the live demo and click one of the two recorded audits; they open instantly. Press `j` to step through the sentences and read the evidence on the right. To run your own, paste any AI answer together with its sources (ChatGPT, Perplexity, Gemini or a deep-research report) and press *Check citations*: a ten-claim report takes about 20 seconds.

## Why

AI research assistants write fluent reports with confident citations, and checking them by hand means opening every link and finding the sentence that matters. The cost of skipping that step is already on record:

- The [AI Hallucination Cases database](https://www.damiencharlotin.com/hallucinations/) lists **2,202 court decisions** (as of 19 October 2026) in which a court found that a party relied on hallucinated content.
- GPTZero [reported](https://gptzero.me/news/neurips/) **at least 100 hallucinated citations** in papers accepted at NeurIPS 2025, after peer review.
- In a [controlled study](https://mental.jmir.org/2025/1/e80371), **19.9%** of the citations GPT-4o produced for literature reviews were fabricated.
- Deloitte agreed to [partially refund](https://www.news4jax.com/business/2025/10/07/deloitte-to-partially-refund-australian-government-for-report-with-apparent-ai-generated-errors/) the Australian government for a AU$440,000 report that cited non-existent research.

Receipts is for the person who has to sign off on such a report: a student before submitting, a reviewer or editor, an analyst forwarding a deep-research brief.

## What it finds

| Verdict | Meaning |
|---|---|
| ✅ Supported | The source states the claim, including its figures, names and dates. |
| 🟡 Partly supported | The source supports part of it, or the claim adds scope or certainty the source does not state ("only", "first", "will solve"). |
| ❌ Contradicted | A figure, name or date conflicts with the source. |
| ⚪ Not in source | The cited source exists but does not address the claim (a misattribution). |
| 🚫 Source missing | The link is dead, or no record of the cited work exists anywhere (a fabricated reference). |

Every Supported, Partly supported and Contradicted verdict carries a quote that Receipts checks, character for character, against the fetched source. A verdict whose quote cannot be found there is not trusted.

## When a citation fails: find a real source

A flag is only half the job; the writer still needs something to cite. For every sentence whose source is missing or does not say it, Receipts searches the web with **Tavily**, reads the top pages, and has Nemotron 3 Nano judge each one. It then shows the page that states the claim, preferring journals, preprints, agencies and official sites, with the verbatim quote. If the best evidence points the other way, it shows that instead.

In the recorded example, a briefing credits the IEA with a forecast that is not in the IEA report. Receipts traces it to the World Economic Forum, quoting: *"A McKinsey report projects that in the United States, data centres could account for up to 12% of total electricity use by 2030."*

Measured on ReceiptsBench ([details](bench/results_suggest/RESULTS.md)):

- For the 45 true claims cited to the wrong source or to a dead link, Receipts found a page that states the claim for **39 (87%)**, the original page itself for 17, and a primary, official or encyclopedic source for 27.
- For the 15 claims backed by fabricated papers, it found no supporting page for 12 and a contradicting one for 1. The other 2 turned out to be true facts behind a fake citation, and Receipts found real sources for them, such as an EU Blockchain Observatory report.
- Cost: about $0.001 and 10 seconds per claim.

## How it works

```
report ──► 1. Extract ──► 2. Find sources ──► 3. Locate passages ──► 4. Judge ──► 5. Verify quote ──► 6. Find a real source
           Nemotron 3     Tavily Extract       figure-aware           Nemotron 3     verbatim match       Tavily Search +
           Nano           Tavily Search        BM25 over the          Ultra          in the source        Nemotron 3 Nano
           (+ Super for   OpenAlex, arXiv      full source text       (reasoning)    text                 (failed citations)
            vague claims)
```

1. **Extract.** Nemotron 3 Nano (reasoning off, JSON output) turns each citing sentence into a self-contained claim and parses the reference list. It handles numbered markers, footnotes, inline links and APA author-year references. If a claim still leans on its neighbours ("It reached…"), Nemotron 3 Super rewrites just that claim.
2. **Find sources.** Cited URLs are read with **Tavily Extract** (web pages and PDFs). References that give only a title are found with **Tavily Search**. Papers are checked against OpenAlex and read in full from arXiv when the published version is closed. A reference with no record anywhere is reported as missing; a 404 is reported as a dead link.
3. **Locate passages.** The full source text is chunked and ranked with BM25 that normalises units and gives extra weight to the exact figures in the claim, so "2,100 km" finds the paragraph that says "21,196 km".
4. **Judge.** Nemotron 3 Ultra (reasoning on) reads the claim, the source's reference entry and the top passages, and returns a label, a confidence, a verbatim quote and a one-sentence reason.
5. **Verify.** The quote is matched against the source text.
6. **Find a real source.** For citations that failed, Tavily Search finds candidate pages and Nemotron 3 Nano picks one that states the claim (or contradicts it), with a verified quote.

The web app streams every stage live: claims appear as they are found, sources resolve, verdicts land one by one, then real sources appear for the failed citations.

## NVIDIA Nemotron on Nebius Token Factory

Each model has a job, and the choice for each job is measured on ReceiptsBench (below).

| Job | Model on Token Factory | Why |
|---|---|---|
| Claim extraction, reference parsing | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | Fast structured output; extracted 299 of 300 benchmark sentences |
| Rewriting the few vague claims | `nvidia/nemotron-3-super-120b-a12b`, reasoning off | Resolves "it" and "this architecture" reliably in one short call |
| Judging every claim | `nvidia/Nemotron-3-Ultra-550b-a55b`, reasoning on | Highest accuracy on ReceiptsBench: 99.6%, no false alarms |
| Checking pages found for failed citations | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning on | 98.6% as a judge at a twelfth of Ultra's cost; three candidate pages per claim for about $0.001 |

Token Factory features used: the OpenAI-compatible chat API, JSON output, per-request reasoning control (`chat_template_kwargs.enable_thinking`), and three Nemotron sizes behind one endpoint, so switching a job to another tier is a one-line setting. The mode menu in the app exposes the alternatives with their measured accuracy and cost. Token Factory also served the independent model (Qwen3-235B) that wrote the benchmark's test items, so the Nemotron judges never grade their own phrasing.

## Tavily

Tavily does the reading and the finding at runtime:

- **Extract** fetches the full text of every cited URL, including arXiv PDFs and long reports, so the judge sees the whole source rather than a snippet.
- **Search** locates references that give only a title, which is how Receipts tells a real work cited without a link from a fabricated one (together with OpenAlex and arXiv).
- **Search** also powers *find a real source*: the claim itself is the query, and the raw content of the hits goes straight to the judge, so a failed citation comes back with a replacement and its quote.

Every call is cached, so one source is fetched once no matter how many claims cite it.

## ReceiptsBench

A benchmark built for this project: 30 reports, 300 cited sentences, 35 real sources (16 Wikipedia articles, 9 pages from WHO, NASA, IEA, NOAA and Our World in Data, 10 arXiv papers read in full). Half of the sentences are faithful to their source; the other half carry one of six planted problems: a changed number, a swapped name or date, an overclaim, a misattribution, a fabricated reference, or a dead link. Test items were written by an independent model and checked against the source text; fabricated references were confirmed absent from Crossref and dead links confirmed to return 404. A label audit excluded 8 ambiguous overclaims, leaving 292 scored sentences.

| Judge | Problems caught | False alarms | Macro accuracy | Cost per 100 claims | Median time per report |
|---|---|---|---|---|---|
| **Nemotron 3 Ultra (default)** | **141 / 142 (99%)** | **0 / 150** | **99.6%** | $0.30 | 20 s |
| Nemotron 3 Super | 138 / 142 (97%) | 0 / 150 | 98.6% | $0.074 | 17 s |
| Nemotron 3 Nano | 139 / 142 (98%) | 1 / 150 | 98.6% | $0.026 | 26 s |
| Nano, Ultra re-checks unbacked "supported" | 140 / 142 (99%) | 3 / 150 | 98.3% | $0.037 | 29 s |
| Nano, Ultra re-checks doubts | 139 / 142 (98%) | 1 / 150 | 98.6% | $0.143 | 28 s |
| Super, Ultra re-checks doubts | 137 / 142 (96%) | 0 / 150 | 98.2% | $0.186 | 20 s |

What the numbers say:

- **Ultra is the most accurate judge** and caught every changed number (37/37) and every overclaim (15/15), at about $0.003 per claim. That makes it the default.
- **Nano reaches 98.6% at about a twelfth of Ultra's cost**, so it is one click away in the mode menu for long reports or tight budgets.
- **Fabricated references, dead links and misattributions were caught 100% of the time by every configuration**: retrieval plus verbatim quotes does most of the work, and the larger model earns its place on the subtle cases.
- **One judge rule made the difference on overclaims.** Requiring that added scope or certainty ("only", "first", "will solve") be stated by the source raised Ultra from 98.6% to 99.6% macro accuracy.

Full tables, per-type rates, the development/test split, the cost-quality chart and the label audit are in [bench/RESULTS.md](bench/RESULTS.md). Every number there is generated from the raw result files by `bench/report.py`.

## Run it

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/), a [Nebius Token Factory](https://tokenfactory.nebius.com) API key and a [Tavily](https://app.tavily.com) API key.

```bash
git clone https://github.com/yy5652-hash/receipts.git && cd receipts
uv sync
cp .env.example .env   # then fill in NEBIUS_API_KEY and TAVILY_API_KEY
```

Web app (opens on http://127.0.0.1:8000):

```bash
uv run receipts serve
```

Command line:

```bash
uv run receipts audit samples/ai-energy-briefing.md --md audit.md --json audit.json
```

Options: `--mode ultra` (default), `nano`, `super`, `routed`, `cascade`. `uv run receipts serve --fake tests/fixtures/sources.json` runs the app offline with fixtures, no keys needed. Tests: `uv run pytest -q` (offline).

Reproduce the benchmark:

```bash
uv run python bench/run.py --out results_final          # all configurations; resumable
uv run python bench/suggest_eval.py                      # find a real source, on the failed-citation items
uv run --group bench python bench/report.py --results results_final --baseline results
```

## Project layout

```
receipts/   extract.py · sources.py · scholarly.py · locate.py · judge.py · suggest.py · pipeline.py · llm.py · cli.py
app/        server.py (FastAPI, server-sent events) · static/ (the web app, no build step)
bench/      build.py · run.py · report.py · data/ (frozen dataset) · RESULTS.md
samples/    recorded example audits shown in the app
tests/      offline test suite
```

## License

MIT. See [LICENSE](LICENSE).
