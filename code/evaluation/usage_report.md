# Token Usage and Cost Report

## Run Summary

- Total requests processed: 250
- Total wall-clock time: 0.8s
- Average time per request: 0.00s

## Model Usage

The default pipeline is **fully deterministic and local** — it uses:

- Rule-based message parsing (`lib/messages_nlp.py`)
- Pytesseract OCR for blank-amount image extraction (`lib/ocr.py`)
- Deterministic forecast and plan selection (`lib/forecast.py`, `lib/planner.py`)

No external LLM API is called in the default path.

### Optional Anthropic Vision Path

The Anthropic vision API was **not used** in this run.
Set `ANTHROPIC_API_KEY` in the environment to enable it for the 16 blank-amount images.

| Metric | Value |
|--------|-------|
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Estimated cost | $0.00 |
| Avg tokens / request | 0 |
