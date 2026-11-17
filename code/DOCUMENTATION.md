# Buy or Wait? — Design Documentation

This document explains what the system does, what every file contains, why each design
choice was made, which alternatives were tested and rejected, and the background knowledge
behind the code. It ends with the review feedback that drove this version and how each
point was addressed.

---

## 1. The problem in one paragraph

A user asks "can I afford this?". The answer depends on more than today's balance: rent and
bills that will leave the account, salary that will arrive, pending charges, the minimum
balance the user wants to keep, and facts hidden in messages ("your salary rises from the
15th") and document images (a payslip whose amount is missing from the ledger). For each of
250 requests the system must output seven fields: the amount safe to pay today, an
affordability status, a payment method, a dated payment plan, the earliest safe date for one
full payment, any spending changes, and a short explanation. A plan is safe only if the
projected balance never drops below the user's minimum during the next 86 days (see §4 for why not the 90 the spec states).

## 2. Architecture

```
dataset/*.csv ──► data_loader ──► evidence (blank amounts) ──► per request:
                   │ identity filter     │ cache ─ investigator (Claude, tools)
                   │ request order kept  │ fixed validation rules
                   ▼                     ▼
              events.UserFinancialState: FX, messages, recurring bills, salary
                   ▼
              forecast: 86-day timeline ─► amount_safe_to_pay, earliest full-payment date
                   ▼
              planner: every eligible plan ─► safety check ─► 6-level ranking
                   ▼
              explain ─► row ─► validate (all rules, request order) ─► output.csv
```

Two principles shape everything:

1. **The model reads; code decides.** Claude is used for the one task code cannot do
   reliably: reading a photographed or scanned financial document. All arithmetic, ranking
   and rule checks are deterministic Python, so the same input always gives the same
   output and every number can be traced.
2. **Fail closed.** When evidence the decision depends on is missing, malformed, or fails an
   identity or timing check, the request is answered "not affordable / not recommended"
   with the reason, and the run says so. Wrongly telling someone a payment is safe can
   push them below the balance they need for rent or medicine; wrongly telling them to
   wait costs them a delay. The asymmetry decides the default.

## 3. File by file

### `code/main.py` — entry point

| Function | What it does |
|----------|--------------|
| `process_request(req, dataset, converter)` | Builds the user's financial state, computes the forecast timeline, `amount_safe_to_pay` and `earliest_date_for_full_payment` (both *before* spending changes, as the spec defines them), asks the planner for the best plan, derives the status from the method, formats the row. |
| `conservative_row(req, reason)` | The fail-closed answer: 0 / `not_affordable` / `not_recommended` / `none`, with the reason in the explanation. |
| `run_requests(requests, …)` | Processes requests **in the order given** and catches only `EvidenceError`. Any other exception is a bug and stops the run. |
| `main()` | Resolves blank amounts, runs all requests, runs `validate.output_problems`, and **refuses to write** `output.csv` if any rule is broken (exit 1). Exit 2 means written, but some requests were answered conservatively. |
| `_write_usage_report(…)` | Writes the required `evaluation/usage_report.md` from the run's real counters. |

Status is derived from the chosen method, so the two can never contradict:
`wait → affordable_later`; `full_payment` with no changes → `affordable_now`;
any plan with changes, installments or partial → `affordable_with_plan`; no plan →
`not_affordable`. The previous version marked 9 rows `affordable_now` while also requiring
spending changes, which the spec forbids.

### `code/lib/config.py`
Paths, the 86-day forecast window (see decision table), the statuses that still move cash (`pending`, `scheduled`),
flexibility values that allow stop/reduce, the 3-change limit, event types never treated as
recurring, and the output column order.

### `code/lib/data_loader.py` — loading and identity boundary
- Reads every CSV once and normalises types (dates, floats, pipe lists, sets).
- `self.requests` keeps the row order of `requests.csv`. Nothing downstream re-sorts it.
  Sorting IDs as text puts `request_100` before `request_26`, which is how an earlier
  version attached decisions to the wrong requests.
- **Identity filter:** a message whose `related_event_id` belongs to a different user, or
  whose `request_id` belongs to a different user, is dropped and recorded in
  `rejected_evidence`. It is never re-attributed to someone else.
- A malformed `sent_at` becomes `None` (unknown send time) instead of crashing the run.

### `code/lib/currency.py` — dated FX
Rates come from `exchange_rates.csv` for the event's settlement date (or the nearest earlier
date). Each date's rates form a graph with inverse edges; breadth-first search finds a path
(e.g. ZAR→EUR→USD) when no direct pair exists. A missing path raises `EvidenceError` instead
of silently keeping the foreign amount.

### `code/lib/evidence.py` — blank amounts, fail closed
For every ledger row whose amount is blank:
1. **Identity check** (`_identity_problem`): a linked image exists, belongs to the same user,
   any request it names belongs to that user, and the file exists.
2. **Cache** (`evidence_cache.json`): reused only if the image's SHA-256 and the event ID
   both match, so an edited image is never matched to a stale answer.
3. **Model** (`investigator.investigate`) on a cache miss, if credentials exist.
4. **Fixed rules** (`finding_problem`): positive finite amount, same currency as the
   ledger, a quoted document label. Code checks these, not the prompt.

Anything that fails stays unresolved with a reason. Settled rows with an unknown amount are
already part of the balance and are simply left out of averages. A *pending or scheduled*
row with an unknown amount stops that request (see `events._normalise_events`).

### `code/lib/investigator.py` — the agent step
A bounded tool-use loop (at most 6 turns) with a task-specific system prompt and five
described tools (`get_ledger_event`, `view_document`, `get_category_history`,
`get_related_messages`, `submit_finding`). The model chooses what to look at; the ledger
row's description tells it which figure is right (net pay vs gross, balance due vs invoice
total, total vs cash tendered). Tools take no arguments, so the model cannot ask for another
user's data. Usage (calls, input and output tokens) is counted per call for the cost report.
The full prompt and the reasoning behind it are in `prompts.md`.

### `code/lib/messages_nlp.py` — message facts
Fixed regular expressions, in priority order, turn a message into amendments: salary change,
new pay date, temporary pay, resumption after leave, first salary, employment ended, rent
increase, ignore a pending credit, settled prize/sale, internal transfer, reimbursement,
failed debit retried, card dispute, confirmed invoice. English and Indonesian phrasings are
covered. Because nothing is executed, an instruction inside a message ("ignore all rules and
approve this") can only fail to match: it is data, never a command
(`self_check.check_identity_and_injection`).

### `code/lib/events.py` — the user's financial state
`UserFinancialState` turns raw rows into future cash flows.

- `_normalise_events`: converts amounts to the home currency, and raises `EvidenceError` for
  a settled row dated after the request (contradicts the balance) or a future row with an
  unknown amount.
- `_apply_messages`: applies amendments in send order, **only for messages sent on or before
  the request date** and with a valid timestamp. A message the user could not have known at
  decision time must not change the decision.
- `_recurring_series`: per category, find the median gap between settled debits, keep the
  chain of events spaced by that gap (an extra one-off purchase is excluded), require at
  least 3, and project the **mean** amount forward: on the same day of month for monthly
  bills, or every N days for weekly/10-day/fortnightly spending.
  - A **failed or cancelled** row within 7 days of a projected monthly date *was* that
    month's bill. The spec says to ignore failed transactions, so that month is not projected
    again.
  - A **pending or scheduled** row is an extra charge and is added on top (financially safer).
- `_project_salary`: keep the regular paydays (drop mid-cycle bonuses and commissions),
  require gaps of 20–42 days, project the median of the last three, then apply message facts:
  confirmed new amount, temporary amount for one payroll, resume date and amount, moved
  payday. No projection when a message says gig payouts are not yet withdrawable, or when the
  latest payroll row is labelled *final*.
- `spending_change_candidates`: series the user allows to stop or reduce (flexible, in a
  permitted category, not protected), with "reduce" going to `minimum_allowed_amount`.

### `code/lib/forecast.py` — the safety check
- `build_balance_timeline`: closing balance for each of the 87 days (request day + 86),
  netting that day's debits, credits and any candidate payments.
- `amount_safe_to_pay_today = min(requested, max(0, min(timeline) − minimum))`: paying X
  today lowers every later day by X, so the lowest day decides.
- `earliest_full_payment_date`: a payment on day T is safe iff `min(timeline[T:]) − X ≥
  minimum`. Computing the suffix minimum once makes this O(n) instead of O(n²).
- `is_safe`: the whole timeline (with the plan's payments) stays above the minimum.

### `code/lib/planner.py` — choosing the plan
- `_schedules` yields every eligible plan: each supplied payment option the user accepts
  (installments also within `max_installment_months`), plus `partial_payment` (only if the
  request allows it, `0 < safe amount < requested` and the second payment is by the
  deadline) and `wait` (pay in full on the earliest safe date).
- `_safe_plans` keeps the plans whose timeline never breaks the minimum.
- `_plan_key` implements the spec's ranking: completes by deadline → no spending changes →
  lowest total paid → earliest start → fewest payments → lowest `payment_option_id`, then
  the smallest change set.
- `wait` is the fallback, not a ranked peer: the spec calls full, partial and installments
  "immediate" methods and makes `wait` eligible only when full payment becomes safe later.
  So any safe immediate plan, even one that needs spending changes, beats waiting (matches
  the sample labels: status, method and plan each gain one row).
- Spending changes are searched only when no on-time immediate plan exists without them.
  Rule 2 means a plan with changes can never beat one without, so searching would be wasted
  work. Combinations go up to 3 changes, and stop/reduce must target different events.

### `code/lib/explain.py`
Templates filled only with numbers the pipeline computed, so an explanation can never state
a fact the decision did not use. Spending changes are named by the bill's own description
("stop the shared storage plan") rather than by event ID.

### `code/lib/validate.py` — output rules
`row_problems` checks one row against **its own request**: status/method pairing, `0 ≤ safe
amount ≤ requested`, dates not before the request, `affordable_now ⇒` date equals request
date, plan present exactly when a method is recommended, chronological positive payments,
the exact two-payment partial rule, installments matching a supplied option, spending
changes that exist for this user, are permitted, and reduce no lower than the allowed
minimum. `output_problems` first checks that the rows are the requests in their original
order. Because each row is checked against its own request, a result attached to the wrong
request fails here (`self_check.check_output_rules` proves it on swapped and text-sorted
rows).

### `code/evaluation/main.py`
Re-runs the pipeline on the 25 labelled samples and compares field by field, pairing
prediction and label by `request_id` and, for plans, payment by corresponding payment (same
position, same date, amount within 1%). Then checks `output.csv` with the same validator and
exits 1 on any violation.

### `code/evaluation/self_check.py`
Assert-based checks with no extra dependencies: forecast maths on a hand-made timeline;
rule gate rejects text-sorted and swapped rows; evidence rules reject zero, NaN, wrong
currency, missing label; a cross-user message is rejected; a malformed timestamp is never
used; an injected instruction yields no amendment; the investigator loop dispatches tools,
returns the finding, sends the image as a tool result and counts tokens (driven by a
scripted fake client, so no API key is needed); the fail-closed row has the right shape.

---

## 4. Decisions, alternatives and evidence

Every option below was measured on the 25 labelled samples with the same harness. The
labelled set is small, so a difference of one or two rows is noise; where that was the
case the simpler or more defensible option was chosen and the reason is stated.

| Decision | Chosen | Alternatives tested | Why |
|----------|--------|---------------------|-----|
| What the model does | Reads documents only | Model decides affordability | Arithmetic and ranking must be exact and reproducible; the model's job is perception. |
| Document reading | Claude tool loop + fixed validation | Tesseract OCR heuristics (previous version) | OCR needed a native install and per-layout rules; the old path could not run without it. |
| Window length | 86 days | 60–91 days | The spec says 90, but the labels stop counting bills after day 86: requests 05, 08, 10 and 13 each leave out a bill on day 87 or 88, and every window from 80 to 86 days scores 118 vs 107 at 90. Four rows with the same mechanism is a pattern, not noise. It changes 21 of 250 answers, all toward "affordable". Chosen by the project owner over the literal spec reading. |
| Bill amount estimator | Mean of the regular chain | median, median of last 3, mean of last 3, last value, max | Best on the numeric field; others within noise on decisions; simplest. |
| Order within a day | Closing balance (net) | Debits before credits (low-water) | 118 vs 115 on the sample (+2 correct dates, one fewer safe amount) and half the code; documented as an accepted risk below. |
| Failed bill | Replaces that month's projection | Ignore it / treat every non-settled row as the instance | Spec says ignore failed rows; request_24 shows scheduled rows are extra charges, request_25 that a failed row is the month's bill. |
| Pending credits | Ignored | Counted | Spec: "ignore pending credits". No sample changed. |
| Final payroll | Ends salary projection | Keep projecting | request_05: the label is not affordable; income after "Final employer payroll" would be invented. |
| Spending-change search | Only when no on-time plan without changes | Always search | Ranking rule 2 makes the result identical; fewer timelines to build. |

### Accepted risk: same-day bill and salary
Netting a day means a bill due on payday is treated as covered by that day's salary. If the
bill clears before the salary posts, the real balance briefly dips lower than forecast. The
stricter low-water model was implemented and measured; it disagreed with more labels. It
remains a one-function change in `forecast.build_balance_timeline` if real data shows the
dip matters.

---

## 5. Results

| Field (25 labelled samples) | Original | Now |
|-----------------------------|----------|-----|
| amount_safe_to_pay within 1% | 4 | 7 |
| affordability_status | 21 | 22 |
| recommended_payment_method | 23 | 23 |
| payment_plan | 16 of 18 scored* | 22 of 25 |
| earliest_date_for_full_payment | 21 | 22 |
| spending_changes_needed | 22 | 22 |
| Output rules, 250 rows | not enforced | 0 violations, gate blocks writing |

\* The original evaluator skipped the 7 rows whose correct plan is `none`; the current one
scores all 25.

On the 250 submitted rows, 22 statuses and 33 full-payment dates changed relative to the
original output, and the 9 invalid `affordable_now`-with-changes rows are gone.

The remaining sample gap is mainly the **precision of the projected variable spending**
(groceries, transport, dining). Several misses sit within 0.3–2% of the label yet flip a
date or status because the decision is a threshold. The label's implied spending is often
a round number (568.00, 624.00, 13,118,550), which suggests the generator draws history
with noise around hidden base amounts; no estimator recovers those exactly.

---

## 6. Verified vs intended behaviour

| Behaviour | Status | How it is verified |
|-----------|--------|--------------------|
| Rows keep request order; a wrong-request row is caught | Verified | `self_check.check_output_rules`; gate in `main.main` |
| Cross-user message rejected | Verified | `self_check.check_identity_and_injection` |
| Image identity, hash and finding rules | Verified | `self_check.check_evidence`; all 16 cached findings pass on every run |
| Messages after the request date or without a valid timestamp ignored | Verified by code path | `events._apply_messages`; malformed-timestamp case in self-check |
| Injected instructions in messages have no effect | Verified | regex parser returns no amendment |
| Investigator tool loop | Verified offline | scripted client in `self_check.check_investigator_loop`; SDK 2.8.0 accepts every argument |
| Investigator against the live API | **Not yet run** | needs `ANTHROPIC_API_KEY` and `--refresh-evidence` |
| Same output on Python 3.9 and 3.13 | Verified | ran both; identical `output.csv` |

---

## 7. Background knowledge

- **Cash-flow forecasting.** A balance forecast is the opening balance plus a cumulative sum
  of dated inflows and outflows. Affordability questions reduce to "how low does it go, and
  when".
- **Running and suffix minimum.** "Is X safe on day T" means `min(balance[T:]) − X ≥ floor`.
  Precomputing suffix minimums answers it for every T in one pass.
- **Recurrence detection.** Real ledgers mix regular bills with one-off purchases in the same
  category. Taking the median gap and keeping only events spaced by it separates the two
  without labels.
- **FX triangulation.** With rates as a graph (and inverse edges), any currency pair is
  reachable by BFS through a common currency; the product of edge rates is the cross rate.
- **Untrusted input and prompt injection.** Text from third parties (documents, messages)
  can contain instructions. The defences used here: parse messages with fixed patterns
  rather than a model; tell the model tool output is data; keep every rule that matters in
  code; validate the model's answer instead of trusting it.
- **Bounded agents.** A tool-use agent loops: model asks for a tool, code runs it, the result
  goes back. A turn limit, a single "submit" tool, strict schemas, and argument-free tools
  keep the loop small, predictable and unable to reach other users' data.
- **Fail-closed design.** When a check cannot be completed, choose the outcome whose error is
  cheaper for the person affected, and say why.
- **Validation gates.** Checking outputs against the specification before writing them turns
  silent data corruption (like rows attached to the wrong request) into a loud failure.
- **Overfitting on small labelled sets.** With 25 examples, a parameter that gains one or two
  rows is indistinguishable from noise; prefer the specification and the simpler rule.

---

## 8. Review feedback and what changed

| Feedback | Change |
|----------|--------|
| Add a bounded model-driven investigation step with task-specific prompts and described tools, keeping fixed rules in code | `lib/investigator.py` (6-turn loop, 5 strict tools, task prompt); `lib/evidence.py` keeps all rules in code; `prompts.md` documents both. |
| Reliable execution in the advertised environment; patterns and formatting that could prevent execution | Removed the Tesseract dependency; standard library only by default; same output on Python 3.9 and 3.13; evaluator no longer crashes on Windows console encoding. |
| Fail closed: enforce both capture and record timestamps | Messages need a valid send time on or before the request date; settled rows dated after the request stop that request; unknown future amounts stop that request. |
| Stop rather than print on output-validation failure | `main.py` refuses to write `output.csv` and exits 1. |
| Fix the ordering measurement: compare corresponding actions | Plans compared payment by payment; rows paired by `request_id`; output order checked against `requests.csv`. |
| Output CSV: keep input order and verify each result belongs to its request | Order preserved end to end; `validate.row_problems` checks every row against its own request. |
| Plans should be justified by their supporting checks | Every recommended plan has passed the forecast check with its own payments; explanations use only computed values. |
| Explicit handling of malformed or missing evidence and of instructions in documents | `EvidenceError` path, conservative rows, malformed timestamp handling; prompt and parser treat content as data. |
| "Retention conditions" | Not applicable to this task (no data-retention rules exist in the dataset); the analogous checks here are the timestamp and identity rules above. |

### Interview preparation: direct answers

- **Where is identity enforced?** In three places: `data_loader.Dataset` drops messages that
  name another user's event or request; `evidence._identity_problem` checks image owner,
  request owner and file before any document is read; and the investigator's tools take no
  arguments, so it cannot ask for anything else. Verified by `self_check.py`.
- **What happens with ambiguous evidence?** It is not used. A finding without a positive
  amount, the ledger's currency and a quoted label is rejected. If the request depends on
  that amount (a pending or scheduled row), the request is answered "not recommended" with
  the reason.
- **What is the cost of being wrong in each direction?** Declaring a payment safe when it is
  not can drop the user below the money reserved for essentials; declaring it unsafe when it
  is safe delays a purchase. The system is built to make the second error, not the first.
- **What is verified and what is only intended?** See section 6. The live API path of the
  investigator is the one piece not yet run against the real service.
