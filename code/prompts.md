# Prompts and Configuration

This file documents all prompts, model configurations, and AI decision
logic used in the Buy or Wait? pipeline.

---

## 1. Anthropic Vision Prompt (ocr.py)

Used when `ANTHROPIC_API_KEY` is set to extract a monetary amount from a
financial document image (payslip, receipt, or utility bill).

**Model:** `claude-sonnet-4-5`
**Max tokens:** 100
**Temperature:** default (not set — deterministic preferred)

**Prompt text:**
```
This is a financial document (payslip, receipt, or bill) linked to a
financial ledger event with a blank amount. Reply with ONLY the single
final monetary amount that this document confirms (net pay for a
payslip, the total amount due/paid for a receipt or bill). No currency
symbol, no commas, no words -- just the plain number.
```

**Design rationale:**
- Instructs the model to return only a number — eliminates parsing ambiguity
- Explicitly names the document types so the model knows which amount is
  "final" (net pay vs gross pay, total vs subtotal)
- "No currency symbol, no commas" prevents locale-specific formatting issues
- Falls back to local Pytesseract heuristic if the API call fails or returns
  a non-numeric response

---

## 2. Message NLP Decision Logic (messages_nlp.py)

No LLM is used for message parsing. The parser uses a priority-ordered set
of regex patterns for each amendment type. This section documents the
decision logic as if it were a prompt — the rules the system follows.

**Amendment detection priority (first match wins per message):**

1. `reimbursement` — "not regular salary", "work expense reimbursement"
2. `internal_transfer` — "both accounts registered", "debit and credit from transfer"
3. `card_dispute` — "dispute is open", "reversal not posted"
4. `failed_debit` — "previous debit attempt failed", "bill still outstanding"
5. `settled_credit` — "prize proceeds reached", "sale order complete"
6. `ignore_credit` — "refund initiated not reached", "payout still pending",
   "market value increased no units sold", "pay release charge" (scam)
7. `salary_ended` — "employment ended", "no regular salary after final"
8. `salary_resume` — "regular salary resumes on YYYY-MM-DD"
9. `salary_date` — "confirmed salary now expected on YYYY-MM-DD"
10. `salary_temp` — "temporary monthly pay", "reduced next payroll"
11. `salary_first` — "first salary will be X confirmed for YYYY-MM-DD"
12. `salary_change` — "monthly salary increased to X", "confirmed base salary is X"
13. `rent_increase` — "monthly rent increases by N%"
14. `childcare_start` — "new recurring childcare payment begins"
15. `invoice_confirmed` — "client approved invoice payment", "settlement expected on"

**Gig income suppression rule:**
When `ignore_credit` has no `related_event_id` AND the message text contains
any of: `payout`, `earnings`, `withdrawable`, `withdrawn` → suppress all
projected salary for this user (`_suppress_salary_projection = True`).

Rationale: Gig platform messages say future earnings are not yet confirmed.
Regular refund/prize messages also produce `ignore_credit` but should not
suppress salary — the keyword check distinguishes them.

---

## 3. Financial State Reconstruction Rules (events.py)

**Salary projection — when to project:**
Project recurring salary when any of the following is true:
- A `scheduled` or `pending` salary event exists in `financial_events.csv`
- A message amendment sets `_salary_change_amount`, `_salary_temp_amount`,
  `_first_salary_amount`, `_salary_resume_amount`, or `_salary_next_date_override`
- `_salary_remaining` is set (one income source ended, remainder confirmed)

Do not project when:
- `_salary_ended = True` and `_salary_remaining = None`
- `_suppress_salary_projection = True` (gig payout pending)
- Salary history gaps exceed 42 days (irregular income, unless `salary_resume`)

**Recurring expense projection — pattern rules:**
- Avg > 1.8 entries/month → aggregated monthly (template-replay, 2–3 cycles)
- Avg ≤ 1.8 + gaps 25–37 days → strict monthly (same day-of-month)
- Avg ≤ 1.8 + only one history entry → project only if category is in
  `_FIXED_MONTHLY_CATS` (rent, utilities, gym, etc.)
- High-frequency discretionary (avg > 3/month, cat in DISCRETIONARY_CATS)
  → cap at 2 projection cycles to avoid tail over-projection

**Conflict resolution priority (per spec):**
1. Explicit cancellation, settlement, or amendment (message amendment wins)
2. Newer record from same source
3. Settled event over estimate/forecast
4. Financially safer interpretation (lower balance = safer)

---

## 4. Plan Selection Logic (planner.py)

**Eligibility rules:**
- `full_payment`: user must accept it in `payment_methods_user_will_consider`
- `installments`: user accepts it AND option exists AND last payment ≤ `max_installment_months × 31` days from request_date
- `partial_payment`: user accepts it AND `allows_partial_payment = True` AND `0 < astp < requested` AND `efpd ≤ desired_completion_date`
- `wait`: user accepts `full_payment` AND `efpd > request_date`
- `not_recommended`: fallback when all others fail

**Ranking (lower tuple = better):**
```
(misses_deadline, needs_spending_changes, total_paid, first_payment_date, num_payments, option_id)
```

**Spending change trigger:**
Try spending changes when:
- No safe plan exists without changes, OR
- Best plan without changes does not complete by `desired_completion_date`

**Spending change filter:**
Only consider a change if `saving_90d >= gap × 0.5` where:
- `saving_90d = monthly_amount × 3`
- `gap = requested - base_astp`

---

## 5. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Optional | Enables Claude vision for blank-amount image extraction. If not set, falls back to Pytesseract. |

No other environment variables are used. No API keys should be committed to the repository.
