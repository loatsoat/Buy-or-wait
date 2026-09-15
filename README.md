# Buy or Wait? — AI Financial Decision Agent

An AI-powered pipeline that decides whether a user can safely afford a
requested expense. For each request it produces: the safe amount to pay
today, an affordability verdict, the recommended payment method and plan,
the earliest date a full payment is safe, any spending adjustments needed,
and a grounded explanation.

---

## Setup

```bash
pip install pillow pytesseract
```

On Windows, also install Tesseract-OCR from
https://github.com/UB-Mannheim/tesseract/wiki and add it to your PATH.

Optional — higher-accuracy OCR via Claude vision for the 16 blank-amount images:

```bash
# Linux / macOS
export ANTHROPIC_API_KEY=your_key_here

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "your_key_here"
```

---

## Run

From the repo root:

```bash
python code/main.py
```

Reads `dataset/requests.csv` and all 7 supporting files, writes
`dataset/output.csv` (250 rows), and updates
`code/evaluation/usage_report.md`.

Typical runtime: **< 1 second**, zero API cost in the default local path.

---

## Evaluate

```bash
python code/evaluation/main.py
```

Compares `dataset/output.csv` against the 25 labeled rows in
`dataset/sample_requests.csv` and runs 10 structural checks across all
275 output rows.

---

## Project Structure

```
repo/
├── code/
│   ├── main.py                  # Pipeline entry point
│   ├── prompts.md               # All prompts, model config, decision rules
│   ├── evaluation/
│   │   ├── main.py              # Accuracy + structural checks
│   │   └── usage_report.md      # Token/cost report (auto-generated)
│   └── lib/
│       ├── config.py            # Shared constants and enums
│       ├── currency.py          # Multi-hop FX conversion via BFS graph
│       ├── data_loader.py       # CSV loading and normalisation
│       ├── events.py            # Per-user financial state reconstruction
│       ├── forecast.py          # 90-day timeline, astp, efpd (suffix-minimum)
│       ├── messages_nlp.py      # 20-family regex message amendment parser
│       ├── ocr.py               # Document image amount extraction
│       ├── planner.py           # Plan enumeration and 6-level ranking
│       └── explain.py           # Template-based explanation generator
└── dataset/
    ├── requests.csv             # 250 evaluation requests
    ├── sample_requests.csv      # 25 labeled examples
    ├── financial_profiles.csv
    ├── financial_events.csv
    ├── exchange_rates.csv
    ├── request_payment_options.csv
    ├── messages.csv
    ├── images.csv
    ├── output.csv               # Written by main.py
    └── media/images/            # 16 PNG financial documents
```

---

## Architecture

The pipeline combines **deterministic rule-based AI** with an optional
**vision model** for document understanding.

### AI Components

**1. Vision model — document amount extraction (`lib/ocr.py`)**

16 financial events have no amount in the CSV. The amount lives in a linked
PNG (payslip, receipt, or bill). Two paths:

- **Local (default):** Pytesseract with row-grouping heuristic and 8-signal
  priority stack (net pay → total paid → grand total → largest number).
- **Cloud (optional):** Anthropic `claude-sonnet-4-5` via vision API. Prompt
  asks for the single final monetary amount with no formatting. Costs ~$0.036
  for all 16 images. Falls back to local if the API call fails.

**2. Message NLP — 20 amendment families (`lib/messages_nlp.py`)**

96 messages in the dataset can amend the financial state: salary changes,
rent increases, refund detection, gig-payout suppression, invoice
confirmation, internal transfer netting, and 13 other types. Pure regex —
deterministic, immune to prompt injection, handles English and Bahasa
Indonesia.

**3. Financial state reconstruction (`lib/events.py`)**

Detects recurring expense patterns (aggregated monthly vs strict monthly),
projects salary with gap analysis and message-driven overrides, handles
leave gaps via salary_resume injection, suppresses unconfirmed gig income.

**4. 90-day safety forecaster (`lib/forecast.py`)**

O(n) suffix-minimum algorithm finds the earliest safe payment date.
Deterministic timeline simulation with per-day balance tracking.

**5. Plan enumerator and ranker (`lib/planner.py`)**

Tries all eligible payment methods (full, installments, partial, wait) with
and without spending changes. Ranks by a 6-level priority tuple matching the
spec exactly. Spending changes are filtered by a materiality threshold
(90-day saving ≥ 50% of shortfall).

### Design Principle

The core financial arithmetic is deterministic — same inputs, same output,
every time. The AI (vision model) handles only the one genuinely unstructured
task: reading a document image. Everything else is math and pattern matching,
which deterministic code does more reliably than a generative model.

See `code/prompts.md` for all prompts, model parameters, and decision rules.

---

## Key Results (labeled sample)

| Field | Accuracy |
|-------|----------|
| affordability_status | 84% (21/25) |
| recommended_payment_method | 92% (23/25) |
| payment_plan | 89% (16/18) |
| earliest_date_for_full_payment | 84% (21/25) |
| spending_changes_needed | 88% (22/25) |
| decision_explanation | 100% (25/25) |
| All structural checks | 100% (275/275 rows) |
