# Devpost submission draft: Receipts

Form fields in order. Everything below is meant to be pasted as is.

## Project name

Receipts

## Elevator pitch (max 200 characters)

Paste an AI-written report. Receipts checks every citation against its source, quotes the proof, and finds a real source when a citation fails.

## Track

Best Apps and Agents

## Try it out

- Live demo: https://receipts.43-160-214-187.sslip.io
- Code: https://github.com/yy5652-hash/receipts

## Video demo

_YouTube link added after upload_

## Built with

nvidia-nemotron, nebius-token-factory, tavily, python, fastapi, server-sent-events, javascript, openalex, arxiv, docker, nginx

## About the project

### Inspiration

AI research assistants now write the first draft of many reports, and every one of them comes with confident citations. Checking those citations means opening each link and hunting for the sentence that matters, which is exactly the step people skip. The results are on record: the [AI Hallucination Cases database](https://www.damiencharlotin.com/hallucinations/) lists 2,202 court decisions in which a party relied on hallucinated content; GPTZero [reported](https://gptzero.me/news/neurips/) at least 100 hallucinated citations in papers accepted at NeurIPS 2025, after peer review; and in a [controlled study](https://mental.jmir.org/2025/1/e80371) 19.9% of the citations GPT-4o wrote for literature reviews were fabricated.

We wanted a tool that does the checking for the person who has to sign off on a report: a student before submitting, a reviewer, an editor, an analyst forwarding a deep-research brief.

### What it does

Paste a report with its references. Receipts reads every cited sentence, finds the source it points to, and gives each sentence a verdict:

- **Supported**: the source states it, figures, names and dates included.
- **Partly supported**: the source supports part of it, or the claim adds scope or certainty the source never states ("only", "first", "will solve").
- **Contradicted**: a figure, name or date conflicts with the source.
- **Not in source**: the source exists but does not say this (a misattribution).
- **Source missing**: a dead link, or a cited work with no record anywhere (a fabricated reference).

Every Supported, Partly and Contradicted verdict comes with a quote from the source, and Receipts checks that quote character for character against the fetched text. In the web app, claims light up as they are found, sources resolve one by one, verdicts stream in, and an evidence panel shows the quote, the source, the reason and the model that decided. Results export as Markdown or JSON.

When a citation fails, Receipts does not stop at the flag. It searches the web with Tavily, has Nemotron read the top pages, and offers a page that does state the claim, preferring journals, agencies and official sites, with the verbatim quote. In the demo, a briefing credits the IEA with a forecast that is not in the IEA report; Receipts traces it to a World Economic Forum article quoting McKinsey. When the best evidence points the other way, it shows that instead.

It reads the citation styles people actually paste: numbered markers, footnotes, inline links, APA author-year lists, and reference lists copied from chat assistants without a heading. Two recorded audits open instantly in the demo: a policy briefing on AI's electricity use with six planted problems, and a related-work paragraph that shrinks GPT-3 tenfold.

### How we built it

A five-stage pipeline, with one NVIDIA Nemotron model per job on Nebius Token Factory:

1. **Extract**: Nemotron 3 Nano (reasoning off, JSON output) turns each citing sentence into a self-contained claim and parses the reference list. The few claims that still lean on their neighbours ("It reached...") are rewritten once by Nemotron 3 Super.
2. **Find sources**: Tavily Extract reads every cited URL in full, PDFs included; Tavily Search finds references that give only a title. Papers are checked against OpenAlex and read in full from arXiv when the published version is closed. No record anywhere means "source missing".
3. **Locate passages**: BM25 over the full source text, with unit normalisation and extra weight for the exact figures in the claim, so "2,100 km" finds the paragraph that says "21,196 km".
4. **Judge**: Nemotron 3 Ultra (reasoning on) reads the claim, the reference entry and the top passages, and returns a label, a confidence, a verbatim quote and a one-line reason.
5. **Verify**: the quote is matched against the source text.
6. **Find a real source**: for each failed citation, Tavily Search uses the claim itself as the query; Nemotron 3 Nano judges the top pages and Receipts keeps one with a verified quote, preferring primary and official sources.

All three Nemotron sizes sit behind one OpenAI-compatible Token Factory endpoint, and reasoning is switched per request with `chat_template_kwargs.enable_thinking`, so moving a job to another size is a single setting. The app is FastAPI streaming server-sent events to a dependency-free JavaScript front end, packaged with Docker and served over HTTPS. 160 offline tests cover the pipeline, the server and the benchmark code.

### ReceiptsBench: how we know it works

We built a benchmark to choose each model on evidence: 30 reports with 300 cited sentences from 35 real sources (Wikipedia, WHO, NASA, IEA, NOAA, Our World in Data, and ten arXiv papers read in full). Half are faithful; half carry one planted problem: a changed number, a swapped name or date, an overclaim, a misattribution, a fabricated reference, or a dead link. An independent model (Qwen3-235B, also served by Token Factory) wrote the test items so the Nemotron judges never grade their own phrasing; fabricated references were confirmed absent from Crossref, dead links confirmed to return 404, and a label audit removed 8 ambiguous items.

| Judge | Problems caught | False alarms | Macro accuracy | Cost per 100 claims |
|---|---|---|---|---|
| Nemotron 3 Ultra (default) | 141 / 142 | 0 / 150 | 99.6% | $0.30 |
| Nemotron 3 Super | 138 / 142 | 0 / 150 | 98.6% | $0.074 |
| Nemotron 3 Nano | 139 / 142 | 1 / 150 | 98.6% | $0.026 |

**Find a real source**, measured on the same benchmark: for the 45 true claims cited to the wrong source or a dead link, Receipts found a page that states the claim for 39 (87%), the original page for 17, at about $0.001 and 10 seconds per claim. For the 15 claims behind fabricated papers it found no supporting page for 12; the other 2 were true facts behind fake citations, and Receipts found real sources for them.

Ultra caught every changed number (37/37) and every overclaim (15/15) for about three tenths of a cent per claim, so it is the default judge; Nano reaches 98.6% at a twelfth of the cost and is one click away in the mode menu. The full tables, two-tier variants, development/test split and label audit are in the repository's `bench/RESULTS.md`, generated from the raw result files.

### Challenges we ran into

- **Choosing the tiers.** Our first design escalated doubtful verdicts from Super to Ultra. Measured on ReceiptsBench it did not beat a single model, so we let the data decide: Nano for extraction, Super for the occasional rewrite, Ultra for every judgement.
- **Overclaims.** Judges tended to accept a claim when its core fact was right and ignore an added "only" or "will solve". One rule, that added scope or certainty must itself be stated by the source, took Ultra from 13 to 15 of 15 overclaims and from 98.6% to 99.6% overall.
- **Real-world references.** APA lists without numbers, footnotes, lists without a heading, DOI landing pages that hold only metadata, and records whose "abstract" is really a byline each needed their own handling. Field tests on real documents drove every one of those fixes.

### Accomplishments that we're proud of

- 99.6% macro accuracy with zero false alarms on a benchmark built and audited in the open.
- Every verdict is checkable: the quote is real, and the link takes you to it.
- Fabricated references, dead links and misattributions were caught 100% of the time by every configuration.
- About $0.003 per claim with the most accurate model.
- Receipts fixes as well as flags: 87% of misattributed or dead-link claims came back with a real source and its quote.

### What we learned

Retrieval and verbatim quotes do most of the work; the larger model earns its place on the subtle cases, such as a figure that is almost right or a claim that quietly says "always". And measuring each tier on the actual task beat our intuition about how to cascade them.

### What's next

A browser extension that audits a chat assistant's answer in place, PDF upload for full papers, batch audits for journals and conference program committees on Nebius Serverless Jobs, and legal citations, where a fabricated case has the highest cost.

## Built during the submission period?

Yes. Receipts, ReceiptsBench and the demo were built from scratch starting 19 October 2026.

## Feedback on Nebius Token Factory, Nebius AI Cloud and NVIDIA models

**Token Factory: what worked well**
- `GET /v1/models?verbose=true` returns pricing, context length, per-model rate limits and supported features in one call. We used it to pick models and to compute cost per claim exactly.
- One OpenAI-compatible endpoint for every Nemotron size made tier experiments cheap: switching a pipeline stage between Nano, Super and Ultra was one setting, which is what let us run a six-configuration benchmark in an afternoon.
- JSON mode (`response_format: json_object`) worked on all Nemotron 3 models, and reasoning arrives in a separate `reasoning_content` field, so the answer stays clean JSON.
- Latency was excellent: Ultra with reasoning judged a ten-claim report in a median of 20 seconds with eight parallel requests.

**Token Factory: suggestions**
- Document the reasoning switch on each model page. We found by probing that `extra_body={"chat_template_kwargs": {"enable_thinking": false}}` turns reasoning off on Nemotron 3 Nano, Super, Ultra and 3.5 Lightning, while a `/no_think` system prefix and `reasoning_effort: "low"` had no effect. A one-line example per model would save every builder this experiment.
- Model IDs mix cases (`nvidia/nemotron-3-super-120b-a12b` next to `nvidia/Nemotron-3-Ultra-550b-a55b` and `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`). Case-insensitive IDs or consistent naming would prevent avoidable 404s.
- Add NVIDIA retrieval models (Nemotron embedding and reranking). The catalogue we saw had Qwen3-Embedding but no NVIDIA embedder or reranker, so our passage retrieval stayed on BM25; an NVIDIA reranker would be a natural fit for verification and RAG apps.
- Reasoning tokens are counted in `completion_tokens`. Reporting them separately in `usage` would make cost dashboards for reasoning-heavy apps more accurate.
- The new-account trial was $1 for 29 days; Builder Program credits arrived later. Showing the pending Builder credit in the console would remove the uncertainty while it is processed.

**Nebius AI Cloud**
- Serverless Endpoints made deploying a container straightforward on paper, but a CPU endpoint is billed for as long as it runs. Keeping a small demo up from submission through the end of judging would have cost more than the hackathon credit, so we hosted the web app elsewhere. A scale-to-zero option for CPU endpoints, or hosting credit that covers the judging period, would make Serverless the obvious home for hackathon demos.

**NVIDIA Nemotron models**
- Nemotron 3 Nano with reasoning on was a surprisingly strong judge: 98.6% macro accuracy, level with Super, at a twelfth of Ultra's cost.
- Nemotron 3 Ultra followed nuanced instructions best. One added rule about scope and certainty took it from 13 to 15 of 15 overclaims; Nano stayed at 14.
- With reasoning off, Nano occasionally left pronouns unresolved when rewriting claims ("It reached 41.8 BLEU"); Super with reasoning off resolved them reliably in a single fast call, which is how we split that job.
