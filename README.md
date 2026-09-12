# Buy or Wait? — AI Financial Decision Agent

## Setup

```bash
pip install pillow pytesseract
# On Windows, also install Tesseract-OCR from https://github.com/UB-Mannheim/tesseract/wiki
# and ensure the tesseract executable is on your PATH.
```

Optional (higher-accuracy OCR for the 16 blank-amount images):
```bash
export ANTHROPIC_API_KEY=your_key_here
```

## Run

From the repo root:

```bash
python code/main.py
```

This reads `dataset/requests.csv` and all supporting files, then writes `dataset/output.csv`.

## Evaluate

```bash
python code/evaluation/main.py
```

Compares `dataset/output.csv` against the 26 labeled rows in `dataset/sample_requests.csv`
and runs structural checks on all 251 rows.

## Project structure

```
repo/
├── code/
│   ├── main.py                  # Main pipeline entry point
│   ├── evaluation/
│   │   ├── main.py              # Evaluation script
│   │   └── usage_report.md      # Generated after running main.py
│   └── lib/
│       ├── config.py            # Shared constants and enums
│       ├── currency.py          # FX conversion via BFS graph
│       ├── data_loader.py       # CSV loading and normalisation
│       ├── events.py            # Per-user financial state resolution
│       ├── forecast.py          # 90-day balance timeline and safe-pay computation
│       ├── messages_nlp.py      # Rule-based message amendment parser
│       ├── ocr.py               # Image amount extraction (Tesseract + optional Anthropic)
│       ├── planner.py           # Plan enumeration and ranking
│       └── explain.py           # Template-based explanation generator
└── dataset/
    ├── requests.csv             # 251 evaluation requests
    ├── sample_requests.csv      # 26 labeled examples
    ├── financial_profiles.csv
    ├── financial_events.csv
    ├── exchange_rates.csv
    ├── request_payment_options.csv
    ├── messages.csv
    ├── images.csv
    ├── output.csv               # Filled by main.py
    └── media/images/            # 16 PNG documents
```

## Architecture

The pipeline is **fully deterministic and local** by default — no LLM calls required.

1. **Data loading** (`data_loader.py`): Reads all 8 CSVs, normalises dates/floats/pipe-lists.
2. **Currency conversion** (`currency.py`): BFS over a per-date FX graph to convert any pair among INR/ZAR/IDR/USD/EUR.
3. **Event resolution** (`events.py`): Fills blank amounts via OCR, applies message amendments (salary changes, rent increases, internal transfers, etc.), detects monthly recurring patterns, and projects them 90 days forward.
4. **Message parsing** (`messages_nlp.py`): Rule-based regex parser covering ~20 message template families (salary raise/cut/temp/resume/ended, rent increase, pending refunds, internal transfers, confirmed invoices, etc.).
5. **Forecasting** (`forecast.py`): Builds a daily balance timeline; computes `amount_safe_to_pay` and `earliest_date_for_full_payment` via a running-minimum argument.
6. **Planning** (`planner.py`): Enumerates all eligible plans (full_payment, installments, partial_payment, wait) with and without spending changes; ranks by the spec's 6-step tie-break and returns the best safe plan.
7. **Explanation** (`explain.py`): Generates a concise, grounded explanation from computed numbers — no invented facts.
