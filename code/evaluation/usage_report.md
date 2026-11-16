# Token Usage and Cost Report

Written by `code/main.py` for the run that produced `dataset/output.csv`.

| Metric | Value |
|--------|-------|
| Requests processed | 250 |
| Model provider | Anthropic |
| Model | claude-opus-5-5 |
| Model calls in this run | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Average tokens per request | 0.0 |
| Estimated total cost (USD) | 0.0000 |
| Estimated cost per request (USD) | 0.000000 |
| Document findings served from cache | 16 |
| Wall-clock time | 0.6s |

The model is used only to read the document images behind blank ledger amounts
(`lib/investigator.py`). Findings are cached in `code/evidence_cache.json`, keyed by
image hash, so a rerun on unchanged images makes no model calls. Each cache entry
records its `source`; entries whose source starts with `seeded` were read in a
development session and cost no API tokens. Run `python code/main.py
--refresh-evidence` with `ANTHROPIC_API_KEY` set to regenerate them through the API
and get measured token counts here. Cost uses $4.00 / $20.00 per million input / output tokens.
