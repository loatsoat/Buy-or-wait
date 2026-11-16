#!/usr/bin/env python3
"""Buy or Wait? pipeline: dataset/requests.csv -> dataset/output.csv.

Usage:
    python code/main.py                     # evidence from code/evidence_cache.json
    python code/main.py --refresh-evidence  # re-read every document image with the model

Exit codes: 0 clean, 1 output failed validation (nothing written),
2 written, but some requests were answered conservatively for lack of evidence.
"""
import csv
import os
import sys
import time

from lib import config
from lib.currency import CurrencyConverter
from lib.data_loader import Dataset
from lib.events import UserFinancialState
from lib.evidence import EvidenceError, resolve_blank_amounts
from lib.explain import generate_explanation
from lib.forecast import amount_safe_to_pay_today, build_balance_timeline, earliest_full_payment_date
from lib.investigator import MODEL
from lib.planner import select_plan
from lib.validate import output_problems

# USD per million tokens (input, output) for investigator.MODEL.
_PRICE_PER_MTOK = (4.00, 20.00)


def _fmt_amount(x):
    return f'{x:.2f}'.rstrip('0').rstrip('.')


def _fmt_changes(changes):
    parts = [f"stop:{c['event_id']}" if c['action'] == 'stop'
             else f"reduce_to:{c['event_id']}:{_fmt_amount(c['new_amount'])}" for c in changes]
    return '|'.join(parts) or 'none'


def process_request(req, dataset, converter):
    """Return the output row for one request. Raises EvidenceError if its evidence is unusable."""
    rd = req['request_date']
    requested = req['requested_amount']
    profile = dataset.profiles[req['user_id']]
    state = UserFinancialState(
        user_id=req['user_id'],
        profile=profile,
        events=dataset.events_by_user.get(req['user_id'], []),
        messages=dataset.messages_by_user.get(req['user_id'], []),
        converter=converter,
        request_date=rd,
    )

    # Both figures are defined before optional spending changes, whatever plan is chosen.
    timeline = build_balance_timeline(rd, state.balance, state.all_future_debits(), state.all_future_credits())
    astp = amount_safe_to_pay_today(timeline, state.min_balance, requested)
    efpd = earliest_full_payment_date(timeline, rd, state.min_balance, requested)

    plan = select_plan(req, state, dataset.payment_options_by_request.get(req['request_id'], []), astp, efpd)
    if plan is None:
        method, status, schedule, changes = 'not_recommended', 'not_affordable', [], []
    else:
        method, schedule, changes = plan['method'], plan['schedule'], plan['spending_changes_needed']
        if method == 'wait':
            status = 'affordable_later'
        elif method == 'full_payment' and not changes:
            status = 'affordable_now'
        else:
            status = 'affordable_with_plan'

    return {
        'request_id': req['request_id'],
        'amount_safe_to_pay': _fmt_amount(astp),
        'affordability_status': status,
        'recommended_payment_method': method,
        'payment_plan': '|'.join(f'{d.isoformat()}:{_fmt_amount(a)}' for d, a in schedule) or 'none',
        'earliest_date_for_full_payment': efpd.isoformat() if efpd else '',
        'spending_changes_needed': _fmt_changes(changes),
        'decision_explanation': generate_explanation(method, req, profile, schedule, changes, state),
    }


def conservative_row(req, reason):
    """Fail-closed answer when the evidence a request depends on cannot be used."""
    return {
        'request_id': req['request_id'],
        'amount_safe_to_pay': '0',
        'affordability_status': 'not_affordable',
        'recommended_payment_method': 'not_recommended',
        'payment_plan': 'none',
        'earliest_date_for_full_payment': '',
        'spending_changes_needed': 'none',
        'decision_explanation': f'No payment is recommended because required evidence could not be verified ({reason}).',
    }


def run_requests(requests, dataset, converter, unresolved):
    """Process requests in the order given. Returns (rows, {request_id: reason} answered conservatively)."""
    rows, held = [], {}
    for req in requests:
        try:
            rows.append(process_request(req, dataset, converter))
        except EvidenceError as exc:
            event_id = str(exc).split(':')[0]
            reason = f'{exc}; {unresolved[event_id]}' if event_id in unresolved else str(exc)
            held[req['request_id']] = reason
            rows.append(conservative_row(req, reason))
    return rows, held


def _model_client():
    if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
        return None
    import anthropic  # imported here so the cached, offline path needs no third-party package
    return anthropic.Anthropic()


def main():
    started = time.time()
    dataset = Dataset()
    converter = CurrencyConverter(dataset.rates_raw)
    unresolved, usage = resolve_blank_amounts(
        dataset, client=_model_client(), refresh='--refresh-evidence' in sys.argv)

    rows, held = run_requests(dataset.requests, dataset, converter, unresolved)

    problems = output_problems(rows, dataset.requests, dataset)
    if problems:
        print(f'STOPPED: {len(problems)} output rule violations, output.csv was not written.')
        print('\n'.join(problems))
        return 1

    output_path = os.path.join(config.DATASET_DIR, 'output.csv')
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=config.OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    _write_usage_report(len(rows), usage, time.time() - started)
    print(f'{len(rows)} rows written to {output_path} in {time.time() - started:.1f}s')
    print(f"Evidence: {usage['cached']} cached findings, {usage['calls']} model calls, "
          f"{len(unresolved)} unresolved, {len(dataset.rejected_evidence)} messages rejected on identity.")
    for request_id, reason in held.items():
        print(f'  CONSERVATIVE {request_id}: {reason}')
    return 2 if held else 0


def _write_usage_report(num_requests, usage, seconds):
    tokens = usage['input_tokens'] + usage['output_tokens']
    cost = (usage['input_tokens'] * _PRICE_PER_MTOK[0] + usage['output_tokens'] * _PRICE_PER_MTOK[1]) / 1e6
    lines = [
        '# Token Usage and Cost Report',
        '',
        'Written by `code/main.py` for the run that produced `dataset/output.csv`.',
        '',
        '| Metric | Value |',
        '|--------|-------|',
        f'| Requests processed | {num_requests} |',
        '| Model provider | Anthropic |',
        f'| Model | {MODEL} |',
        f'| Model calls in this run | {usage["calls"]} |',
        f'| Input tokens | {usage["input_tokens"]} |',
        f'| Output tokens | {usage["output_tokens"]} |',
        f'| Total tokens | {tokens} |',
        f'| Average tokens per request | {tokens / num_requests:.1f} |',
        f'| Estimated total cost (USD) | {cost:.4f} |',
        f'| Estimated cost per request (USD) | {cost / num_requests:.6f} |',
        f'| Document findings served from cache | {usage["cached"]} |',
        f'| Wall-clock time | {seconds:.1f}s |',
        '',
        'The model is used only to read the document images behind blank ledger amounts',
        '(`lib/investigator.py`). Findings are cached in `code/evidence_cache.json`, keyed by',
        'image hash, so a rerun on unchanged images makes no model calls. Each cache entry',
        'records its `source`; entries whose source starts with `seeded` were read in a',
        'development session and cost no API tokens. Run `python code/main.py',
        '--refresh-evidence` with `ANTHROPIC_API_KEY` set to regenerate them through the API',
        'and get measured token counts here. Cost uses '
        f'${_PRICE_PER_MTOK[0]:.2f} / ${_PRICE_PER_MTOK[1]:.2f} per million input / output tokens.',
        '',
    ]
    with open(os.path.join(config.CODE_DIR, 'evaluation', 'usage_report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


if __name__ == '__main__':
    sys.exit(main())
