# Buy or Wait? — AI Financial Decision Agent

For each request in `dataset/requests.csv` the agent decides how much the user can safely
pay today, whether the full amount is affordable now, with a plan, later or not at all,
which payment method and schedule to use, the earliest safe date for a full payment, which
flexible expenses must change, and a one-line explanation.

**Why it matters:** "can I afford this?" depends on more than today's balance. Rent and bills
that will leave the account, salary that will arrive, pending charges, the minimum balance the
user wants to keep, and facts hidden in messages ("your salary rises from the 15th") or
document images all change the answer. A plan is recommended only if the projected balance
never drops below the user's minimum over the forecast window.

A full walkthrough of every file, every design choice and the background knowledge behind
it is in [code/DOCUMENTATION.md](code/DOCUMENTATION.md). The model prompt and tool
definitions are in [code/prompts.md](code/prompts.md).

## Setup

Python 3.9 or newer. The default run needs only the standard library.

```bash
pip install anthropic   # only for --refresh-evidence (re-reading document images with Claude)
```

## Run

From the repository root:

```bash
python code/main.py                      # writes dataset/output.csv and code/evaluation/usage_report.md
python code/evaluation/main.py           # accuracy on the 25 labelled samples + output rule check
python code/evaluation/self_check.py     # assert-based checks of the fail-closed rules
```

`main.py` exits with 0 when every row is clean, 1 when the output breaks a rule (nothing is
written), and 2 when the file is written but some requests had to be answered conservatively
because evidence was missing or failed a check.

To re-read the 16 document images through the API instead of the cache:

```bash
export ANTHROPIC_API_KEY=...             # PowerShell: $env:ANTHROPIC_API_KEY = "..."
python code/main.py --refresh-evidence
```

## Layout

```
code/
├── main.py                 entry point: per-request decision, fail-closed fallback, validation gate
├── prompts.md              model prompt, tools and the rules that stay in code
├── DOCUMENTATION.md        detailed design documentation
├── evidence_cache.json     validated document findings, keyed by image hash
├── evaluation/
│   ├── main.py             labelled-sample accuracy + output rule check
│   ├── self_check.py       assert-based checks of the safety rules
│   └── usage_report.md     token and cost report (written by main.py)
└── lib/
    ├── config.py           constants
    ├── data_loader.py      CSV loading, request order, identity filter on messages
    ├── currency.py         dated FX conversion
    ├── evidence.py         blank amounts: identity check, cache, model, validation
    ├── investigator.py     bounded Claude tool-use loop that reads one document
    ├── messages_nlp.py     deterministic message parser (20 amendment families)
    ├── events.py           per-user financial state: recurring bills, salary, messages
    ├── forecast.py         balance timeline and the two safe-payment figures
    ├── planner.py          plan enumeration, safety check, ranking, spending changes
    ├── explain.py          explanation templates
    └── validate.py         output rules checked before output.csv is written
```

## Results (25 labelled samples, `python code/evaluation/main.py`)

| Field | Correct |
|-------|---------|
| amount_safe_to_pay (within 1%) | 7/25 |
| affordability_status | 22/25 |
| recommended_payment_method | 23/25 |
| payment_plan | 22/25 |
| earliest_date_for_full_payment | 22/25 |
| spending_changes_needed | 22/25 |
| Output rules on all 250 rows | 0 violations |
