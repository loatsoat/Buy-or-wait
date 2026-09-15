# Token Usage and Cost Report

## Final Full-Dataset Run

| Metric | Value |
|--------|-------|
| Total requests processed | 250 |
| Total wall-clock time | 1.3s |
| Average time per request | 0.005s |

---

## AI Components

### 1. Pytesseract OCR (local, open-source)

Extracts monetary amounts from up to 16 blank-amount financial document images.

| Metric | Value |
|--------|-------|
| Provider | Tesseract OCR (local) |
| Images processed | up to 16 |
| API calls | 0 (local) |
| Estimated cost | $0.00 |

### 2. Anthropic Claude Vision (optional)

When `ANTHROPIC_API_KEY` is set, replaces Pytesseract for higher accuracy.

| Metric | Value |
|--------|-------|
| Provider | Anthropic |
| Model | claude-sonnet-4-5 |
| Max API calls | 16 (one per blank-amount image) |
| Est. input tokens per call | ~1,500 |
| Est. output tokens per call | ~10 |
| Est. total tokens (all 16) | ~24,160 |
| Est. avg tokens per request | ~97 |
| Est. total cost | ~$0.036 USD |
| Est. cost per request | ~$0.00014 USD |

**This run:** Anthropic vision was **not active** (ANTHROPIC_API_KEY not set).

---

## Rule-Based AI Components

| Component | Description |
|-----------|-------------|
| `lib/messages_nlp.py` | 20 regex pattern families, 96 messages parsed, 5 languages |
| `lib/events.py` | Recurring pattern detection, salary projection, message-driven state mutation |
| `lib/forecast.py` | O(n) suffix-minimum forecaster, 90-day balance simulation |
| `lib/planner.py` | 6-level plan ranking, spending-change combinatorial search |

---

## Cost Summary

| Component | Cost |
|-----------|------|
| Pytesseract OCR | $0.00 |
| Anthropic Claude Vision (if enabled) | ~$0.036 |
| Rule-based NLP + forecasting | $0.00 |
| **Total (this run, no Anthropic)** | **$0.00** |
| **Total (with Anthropic)** | **~$0.036** |
