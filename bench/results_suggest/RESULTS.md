# Find a real source: results (nano judge)

60 claims from ReceiptsBench: the claim alone, its cited URL excluded from the search hits. Computed by `bench/suggest_eval.py` from `items.jsonl`.

## The claim is true: misattributed and dead-link items

The real origin of each claim is the gold `origin_url`. A suggestion is the origin page when its URL equals the origin's, or has the same host and last path segment.

| Items | n | Supported suggestion | Suggestion is the origin page | Contradicted | None |
|---|---|---|---|---|---|
| misattribution | 30 | 90% (27/30) | 33% (10/30) | 7% (2/30) | 3% (1/30) |
| dead_link | 15 | 80% (12/15) | 47% (7/15) | 13% (2/15) | 7% (1/15) |
| **Both** | 45 | 87% (39/45) | 38% (17/45) | 9% (4/45) | 4% (2/45) |

Supported suggestions: 38% (17/45) are the origin page, 49% (22/45) another page that states the claim. 36% (16/45) are primary or official sources (journals, preprints, agencies, .gov/.edu), 60% (27/45) primary, official or encyclopedic.

## The cited paper does not exist: fabricated items

The claim itself may or may not be true, so there is no target; the distribution is descriptive.

| n | Supported | Contradicted | None |
|---|---|---|---|
| 15 | 13% (2/15) | 7% (1/15) | 80% (12/15) |

## Cost per claim

Model tokens at the Token Factory price of the tier; Tavily searches are counted, not priced.

| Items | Mean | Median | Model calls | Tokens | Tavily searches | Median seconds |
|---|---|---|---|---|---|---|
| misattribution | $0.00092 | $0.00085 | 3.0 | 6736 | 1.0 | 9.8 |
| dead_link | $0.00094 | $0.00090 | 3.0 | 6971 | 1.0 | 9.5 |
| fabricated | $0.00088 | $0.00078 | 3.0 | 6337 | 1.0 | 10.1 |
| **All** | $0.00091 | $0.00084 | 3.0 | 6695 | 1.0 | 9.8 |

Total for the run: $0.0549.
