#!/usr/bin/env python3
"""Buy or Wait? — main pipeline.

Usage:
    python code/main.py

Reads  : dataset/requests.csv  (and all supporting CSVs + images)
Writes : dataset/output.csv
"""

import csv
import os
import sys
import time

# Make sure `code/` is on the path so `lib` is importable whether we run from
# the repo root or from inside code/
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib import config
from lib.data_loader import Dataset
from lib.currency import CurrencyConverter
from lib.events import UserFinancialState
from lib.forecast import (
    build_balance_timeline,
    amount_safe_to_pay_today,
    earliest_full_payment_date,
)
from lib.planner import select_plan
from lib.explain import generate_explanation

# ---------------------------------------------------------------------------
# Token usage tracking (for evaluation/usage_report.md)
# ---------------------------------------------------------------------------
_ANTHROPIC_CALLS = 0
_ANTHROPIC_INPUT_TOKENS = 0
_ANTHROPIC_OUTPUT_TOKENS = 0

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_amount(x):
    if x is None:
        return ''
    # Use up to 2 decimal places, strip trailing zeros but keep at least whole number
    s = f'{x:.2f}'
    # Remove trailing zeros after decimal point
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def _fmt_payment_plan(schedule):
    if not schedule:
        return 'none'
    parts = []
    for d, amt in schedule:
        parts.append(f'{d.strftime("%Y-%m-%d")}:{_fmt_amount(amt)}')
    return '|'.join(parts)


def _fmt_spending_changes(changes):
    if not changes:
        return 'none'
    parts = []
    for ch in changes:
        if ch['action'] == 'stop':
            parts.append(f"stop:{ch['event_id']}")
        elif ch['action'] == 'reduce_to':
            parts.append(f"reduce_to:{ch['event_id']}:{_fmt_amount(ch['new_amount'])}")
        if len(parts) >= config.MAX_SPENDING_CHANGES:
            break
    return '|'.join(parts) if parts else 'none'


# ---------------------------------------------------------------------------
# Core per-request logic
# ---------------------------------------------------------------------------

def process_request(req, dataset, converter):
    request_id = req['request_id']
    user_id = req['user_id']
    rd = req['request_date']
    requested = req['requested_amount'] or 0.0
    deadline = req['desired_completion_date']

    profile = dataset.profiles.get(user_id, {})
    if not profile:
        # Minimal fallback
        profile = {
            'home_currency': 'USD',
            'current_available_balance': 0.0,
            'minimum_balance_to_keep': 0.0,
            'financial_priorities': [],
            'expense_categories_to_protect': set(),
            'expense_categories_user_is_willing_to_reduce': set(),
            'expense_categories_user_is_willing_to_stop': set(),
            'payment_methods_user_will_consider': {'full_payment'},
            'max_installment_months': None,
        }

    events = dataset.events_by_user.get(user_id, [])
    payment_options = dataset.payment_options_by_request.get(request_id, [])
    messages_by_user = dataset.messages_by_user.get(user_id, [])
    images_by_event = dataset.images_by_event

    # Build user financial state
    user_state = UserFinancialState(
        user_id=user_id,
        profile=profile,
        events=events,
        payment_options_by_request=dataset.payment_options_by_request,
        messages_by_user=messages_by_user,
        images_by_event=images_by_event,
        converter=converter,
        request_date=rd,
        images_dir=config.MEDIA_IMAGES_DIR,
    )

    min_bal = user_state.min_balance

    # Base timeline (no payment)
    base_tl = build_balance_timeline(
        rd,
        user_state.balance,
        user_state.all_future_debits(),
        user_state.all_future_credits(),
        config.FORECAST_DAYS,
    )

    # amount_safe_to_pay and earliest_full_payment_date (no spending changes)
    astp = amount_safe_to_pay_today(base_tl, rd, config.FORECAST_DAYS, min_bal, requested)
    efpd = earliest_full_payment_date(base_tl, rd, config.FORECAST_DAYS, min_bal, requested)

    # Select best plan
    best = select_plan(req, user_state, payment_options, base_tl, config.FORECAST_DAYS)

    # Override astp/efpd from best plan if spending changes were needed
    if best:
        astp = best.get('astp', astp)
        efpd = best.get('efpd', efpd)

    # Determine affordability_status and method
    if best is None:
        method = 'not_recommended'
        affordability_status = 'not_affordable'
        schedule = []
        spending_changes = []
        efpd_out = None
    else:
        method = best['method']
        schedule = best['schedule']
        spending_changes = best.get('spending_changes_needed', [])

        if method == 'full_payment' and schedule and schedule[0][0] == rd:
            affordability_status = 'affordable_now'
        elif method in ('installments', 'partial_payment'):
            affordability_status = 'affordable_with_plan'
        elif method == 'full_payment' and spending_changes:
            affordability_status = 'affordable_with_plan'
        elif method == 'wait':
            affordability_status = 'affordable_later'
        else:
            affordability_status = 'affordable_now'

        # earliest_date_for_full_payment
        # Must equal request_date for affordable_now
        if affordability_status == 'affordable_now':
            efpd_out = rd
        elif efpd is not None:
            efpd_out = efpd
        else:
            efpd_out = None

    # Build explanation
    explanation = generate_explanation(
        method=method,
        request=req,
        profile=profile,
        astp=astp,
        efpd=efpd_out,
        plan_schedule=schedule,
        spending_changes_needed=spending_changes,
        min_balance=min_bal,
    )

    return {
        'request_id': request_id,
        'amount_safe_to_pay': _fmt_amount(astp),
        'affordability_status': affordability_status,
        'recommended_payment_method': method,
        'payment_plan': _fmt_payment_plan(schedule),
        'earliest_date_for_full_payment': efpd_out.strftime('%Y-%m-%d') if efpd_out else '',
        'spending_changes_needed': _fmt_spending_changes(spending_changes),
        'decision_explanation': explanation,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print('Loading dataset...')
    t0 = time.time()
    ds = Dataset()
    converter = CurrencyConverter(ds.rates_raw)
    print(f'  Loaded in {time.time() - t0:.1f}s  '
          f'({len(ds.requests)} requests, {sum(len(v) for v in ds.events_by_user.values())} events)')

    output_path = os.path.join(config.DATASET_DIR, 'output.csv')
    rows = []
    errors = []

    for i, req in enumerate(ds.requests):
        rid = req['request_id']
        try:
            row = process_request(req, ds, converter)
            rows.append(row)
            if (i + 1) % 25 == 0:
                print(f'  Processed {i + 1}/{len(ds.requests)} requests...')
        except Exception as exc:
            import traceback
            errors.append((rid, str(exc)))
            print(f'  ERROR on {rid}: {exc}')
            traceback.print_exc()
            # Write a blank row so the CSV stays complete
            rows.append({
                'request_id': rid,
                'amount_safe_to_pay': '',
                'affordability_status': '',
                'recommended_payment_method': '',
                'payment_plan': 'none',
                'earliest_date_for_full_payment': '',
                'spending_changes_needed': 'none',
                'decision_explanation': '',
            })

    # Write output.csv
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=config.OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    elapsed = time.time() - t0
    print(f'\nDone. {len(rows)} rows written to {output_path}  ({elapsed:.1f}s)')
    if errors:
        print(f'  {len(errors)} errors: {[e[0] for e in errors]}')

    # Write usage report
    _write_usage_report(len(rows), elapsed)
    return 0


def _write_usage_report(num_requests, elapsed_seconds):
    report_path = os.path.join(_SCRIPT_DIR, 'evaluation', 'usage_report.md')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('# Token Usage and Cost Report\n\n')
        f.write('## Run Summary\n\n')
        f.write(f'- Total requests processed: {num_requests}\n')
        f.write(f'- Total wall-clock time: {elapsed_seconds:.1f}s\n')
        f.write(f'- Average time per request: {elapsed_seconds / max(num_requests, 1):.2f}s\n\n')
        f.write('## Model Usage\n\n')
        f.write('The default pipeline is **fully deterministic and local** — it uses:\n\n')
        f.write('- Rule-based message parsing (`lib/messages_nlp.py`)\n')
        f.write('- Pytesseract OCR for blank-amount image extraction (`lib/ocr.py`)\n')
        f.write('- Deterministic forecast and plan selection (`lib/forecast.py`, `lib/planner.py`)\n\n')
        f.write('No external LLM API is called in the default path.\n\n')
        f.write('### Optional Anthropic Vision Path\n\n')
        if _ANTHROPIC_CALLS > 0:
            f.write(f'- Provider: Anthropic\n')
            f.write(f'- Model: claude-sonnet-4-6\n')
            f.write(f'- API calls: {_ANTHROPIC_CALLS}\n')
            f.write(f'- Input tokens: {_ANTHROPIC_INPUT_TOKENS}\n')
            f.write(f'- Output tokens: {_ANTHROPIC_OUTPUT_TOKENS}\n')
            total_tokens = _ANTHROPIC_INPUT_TOKENS + _ANTHROPIC_OUTPUT_TOKENS
            # claude-sonnet-4-6: ~$3/M input, ~$15/M output (approximate)
            cost = (_ANTHROPIC_INPUT_TOKENS / 1_000_000 * 3.0 +
                    _ANTHROPIC_OUTPUT_TOKENS / 1_000_000 * 15.0)
            f.write(f'- Total tokens: {total_tokens}\n')
            f.write(f'- Estimated cost: ${cost:.4f} USD\n')
            f.write(f'- Average tokens per request: {total_tokens / max(num_requests, 1):.1f}\n')
        else:
            f.write('The Anthropic vision API was **not used** in this run.\n')
            f.write('Set `ANTHROPIC_API_KEY` in the environment to enable it for the 16 blank-amount images.\n\n')
            f.write('| Metric | Value |\n')
            f.write('|--------|-------|\n')
            f.write(f'| Model calls | 0 |\n')
            f.write(f'| Input tokens | 0 |\n')
            f.write(f'| Output tokens | 0 |\n')
            f.write(f'| Total tokens | 0 |\n')
            f.write(f'| Estimated cost | $0.00 |\n')
            f.write(f'| Avg tokens / request | 0 |\n')


if __name__ == '__main__':
    sys.exit(main())
