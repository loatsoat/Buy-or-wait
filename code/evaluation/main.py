#!/usr/bin/env python3
"""Evaluation script for Buy or Wait?

Compares output.csv against sample_requests.csv (26 labeled rows) per-field,
and runs structural/consistency checks on all 251 rows.

Usage:
    python code/evaluation/main.py
"""

import csv
import os
import sys
from datetime import datetime

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_EVAL_DIR)
_REPO_DIR = os.path.dirname(_CODE_DIR)
_DATASET_DIR = os.path.join(_REPO_DIR, 'dataset')

if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from lib import config

# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def _read_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _parse_float(s):
    if s is None or str(s).strip() == '':
        return None
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def _parse_date(s):
    if not s or str(s).strip() == '':
        return None
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_plan(s):
    """Return list of (date, amount) from payment_plan string."""
    if not s or s.strip().lower() == 'none':
        return []
    result = []
    for part in s.split('|'):
        part = part.strip()
        if ':' in part:
            pieces = part.split(':')
            if len(pieces) >= 2:
                d = _parse_date(pieces[0])
                try:
                    amt = float(pieces[1])
                    result.append((d, amt))
                except ValueError:
                    pass
    return result


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def _close(a, b, rel_tol=0.01, abs_tol=1.0):
    """True if two floats are within 1% or 1 unit."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b), 1.0))


FIELD_NAMES = [
    'amount_safe_to_pay',
    'affordability_status',
    'recommended_payment_method',
    'payment_plan',
    'earliest_date_for_full_payment',
    'spending_changes_needed',
    'decision_explanation',
]


def compare_row(pred, truth, requests_map):
    """Return a dict of field -> (pass/fail/skip, detail)."""
    rid = pred['request_id']
    req = requests_map.get(rid, {})
    requested = _parse_float(req.get('requested_amount'))

    results = {}

    # amount_safe_to_pay
    p_astp = _parse_float(pred.get('amount_safe_to_pay'))
    t_astp = _parse_float(truth.get('amount_safe_to_pay'))
    if t_astp is not None and p_astp is not None:
        ok = _close(p_astp, t_astp)
        # Also check bounds
        if requested is not None:
            ok = ok and 0 <= p_astp <= requested + 0.01
        results['amount_safe_to_pay'] = ('pass' if ok else 'fail',
                                          f'pred={p_astp}, truth={t_astp}')
    else:
        results['amount_safe_to_pay'] = ('skip', 'no truth value')

    # affordability_status
    p_as = pred.get('affordability_status', '').strip()
    t_as = truth.get('affordability_status', '').strip()
    if t_as:
        results['affordability_status'] = (
            'pass' if p_as == t_as else 'fail',
            f'pred={p_as}, truth={t_as}'
        )
    else:
        results['affordability_status'] = ('skip', 'no truth')

    # recommended_payment_method
    p_rpm = pred.get('recommended_payment_method', '').strip()
    t_rpm = truth.get('recommended_payment_method', '').strip()
    if t_rpm:
        results['recommended_payment_method'] = (
            'pass' if p_rpm == t_rpm else 'fail',
            f'pred={p_rpm}, truth={t_rpm}'
        )
    else:
        results['recommended_payment_method'] = ('skip', 'no truth')

    # payment_plan
    p_plan = _parse_plan(pred.get('payment_plan', ''))
    t_plan = _parse_plan(truth.get('payment_plan', ''))
    if t_plan:
        if len(p_plan) == len(t_plan):
            plan_ok = all(
                pd == td and _close(pa, ta)
                for (pd, pa), (td, ta) in zip(p_plan, t_plan)
            )
        else:
            plan_ok = False
        results['payment_plan'] = (
            'pass' if plan_ok else 'fail',
            f'pred={pred.get("payment_plan")!r}, truth={truth.get("payment_plan")!r}'
        )
    else:
        results['payment_plan'] = ('skip', 'no truth')

    # earliest_date_for_full_payment
    p_efpd = _parse_date(pred.get('earliest_date_for_full_payment', ''))
    t_efpd = _parse_date(truth.get('earliest_date_for_full_payment', ''))
    if t_efpd is not None:
        results['earliest_date_for_full_payment'] = (
            'pass' if p_efpd == t_efpd else 'fail',
            f'pred={p_efpd}, truth={t_efpd}'
        )
    elif truth.get('earliest_date_for_full_payment', '').strip() == '':
        # truth says empty — our pred should also be empty
        results['earliest_date_for_full_payment'] = (
            'pass' if not p_efpd else 'fail',
            f'pred={p_efpd}, truth=empty'
        )
    else:
        results['earliest_date_for_full_payment'] = ('skip', 'no truth')

    # spending_changes_needed
    p_sc = pred.get('spending_changes_needed', 'none').strip()
    t_sc = truth.get('spending_changes_needed', 'none').strip()
    if t_sc:
        results['spending_changes_needed'] = (
            'pass' if p_sc == t_sc else 'fail',
            f'pred={p_sc!r}, truth={t_sc!r}'
        )
    else:
        results['spending_changes_needed'] = ('skip', 'no truth')

    # decision_explanation (just check non-empty)
    p_de = pred.get('decision_explanation', '').strip()
    results['decision_explanation'] = (
        'pass' if p_de else 'fail',
        f'len={len(p_de)}'
    )

    return results


# ---------------------------------------------------------------------------
# Structural checks (all 251 rows)
# ---------------------------------------------------------------------------

def structural_checks(pred, requests_map):
    """Return list of (check_name, pass/fail, detail)."""
    checks = []
    rid = pred['request_id']
    req = requests_map.get(rid, {})
    requested = _parse_float(req.get('requested_amount'))
    rd = _parse_date(req.get('request_date'))
    deadline = _parse_date(req.get('desired_completion_date'))
    allows_partial = str(req.get('allows_partial_payment', '')).strip().lower() == 'true'

    # 1. amount_safe_to_pay bounds
    astp = _parse_float(pred.get('amount_safe_to_pay'))
    if astp is not None and requested is not None:
        ok = 0 <= astp <= requested + 0.01
        checks.append(('astp_bounds', 'pass' if ok else 'fail',
                        f'astp={astp}, requested={requested}'))

    # 2. affordability_status valid enum
    status = pred.get('affordability_status', '').strip()
    checks.append(('status_enum', 'pass' if status in config.AFFORDABILITY_STATUSES else 'fail',
                   f'status={status!r}'))

    # 3. method valid enum
    method = pred.get('recommended_payment_method', '').strip()
    checks.append(('method_enum', 'pass' if method in config.PAYMENT_METHODS else 'fail',
                   f'method={method!r}'))

    # 4. affordable_now => efpd == request_date
    efpd = _parse_date(pred.get('earliest_date_for_full_payment', ''))
    if status == 'affordable_now':
        ok = efpd == rd
        checks.append(('affordable_now_efpd', 'pass' if ok else 'fail',
                        f'efpd={efpd}, request_date={rd}'))

    # 5. partial_payment rules
    if method == 'partial_payment':
        plan = _parse_plan(pred.get('payment_plan', ''))
        ok_count = len(plan) == 2
        if ok_count and requested is not None:
            total = sum(a for _, a in plan)
            ok_sum = _close(total, requested, abs_tol=0.05)
        else:
            ok_sum = True
        ok_allows = allows_partial
        checks.append(('partial_2payments', 'pass' if ok_count else 'fail', f'n={len(plan)}'))
        checks.append(('partial_sum', 'pass' if ok_sum else 'fail',
                        f'sum={sum(a for _, a in plan) if plan else None}, req={requested}'))
        checks.append(('partial_allowed', 'pass' if ok_allows else 'fail', f'allows_partial={allows_partial}'))

    # 6. payment_plan chronological
    plan = _parse_plan(pred.get('payment_plan', ''))
    if len(plan) > 1:
        dates = [d for d, _ in plan if d]
        ok = all(dates[i] <= dates[i + 1] for i in range(len(dates) - 1))
        checks.append(('plan_chronological', 'pass' if ok else 'fail', str(dates)))

    # 7. spending_changes count <= 3
    sc = pred.get('spending_changes_needed', 'none').strip()
    if sc != 'none':
        parts = sc.split('|')
        ok = len(parts) <= config.MAX_SPENDING_CHANGES
        checks.append(('spending_changes_count', 'pass' if ok else 'fail', f'n={len(parts)}'))

    # 8. decision_explanation non-empty
    de = pred.get('decision_explanation', '').strip()
    checks.append(('explanation_nonempty', 'pass' if de else 'fail', ''))

    return checks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    output_path = os.path.join(_DATASET_DIR, 'output.csv')
    sample_path = os.path.join(_DATASET_DIR, 'sample_requests.csv')
    requests_path = os.path.join(_DATASET_DIR, 'requests.csv')

    if not os.path.exists(output_path):
        print(f'ERROR: {output_path} not found. Run main.py first.')
        return 1

    predictions = {r['request_id']: r for r in _read_csv(output_path)}

    # Also run predictions on the 25 sample requests so we can compare accuracy
    from lib.data_loader import Dataset
    from lib.currency import CurrencyConverter

    # Load dataset but swap requests to sample_requests.csv temporarily
    import importlib
    import lib.config as cfg

    print('Running predictions on sample_requests.csv for accuracy evaluation...')
    ds = Dataset()
    converter = CurrencyConverter(ds.rates_raw)

    # Load sample requests as normalised rows
    sample_rows_raw = _read_csv(sample_path)
    sample_requests_norm = []
    for r in sample_rows_raw:
        r2 = dict(r)
        r2['request_date'] = _parse_date(r['request_date'])
        r2['desired_completion_date'] = _parse_date(r.get('desired_completion_date'))
        try:
            r2['requested_amount'] = float(r['requested_amount'])
        except (ValueError, TypeError):
            r2['requested_amount'] = 0.0
        r2['allows_partial_payment'] = str(r.get('allows_partial_payment', '')).strip().lower() == 'true'
        sample_requests_norm.append(r2)

    # Temporarily extend dataset with sample payment options (they share the same dataset)
    import sys
    sys.path.insert(0, _CODE_DIR)
    from main import process_request
    sample_predictions = {}
    for req in sample_requests_norm:
        try:
            row = process_request(req, ds, converter)
            sample_predictions[req['request_id']] = row
        except Exception as e:
            print(f'  Error on {req["request_id"]}: {e}')

    predictions.update(sample_predictions)

    samples = {r['request_id']: r for r in sample_rows_raw}
    requests = {r['request_id']: r for r in _read_csv(requests_path)}
    # Also add sample requests to the requests map for structural checks
    requests.update({r['request_id']: r for r in sample_rows_raw})

    print('=' * 70)
    print('LABELED SAMPLE EVALUATION (26 rows)')
    print('=' * 70)

    field_stats = {f: {'pass': 0, 'fail': 0, 'skip': 0} for f in FIELD_NAMES}

    for rid, truth in sorted(samples.items()):
        pred = predictions.get(rid)
        if pred is None:
            print(f'  MISSING: {rid}')
            continue
        results = compare_row(pred, truth, requests)
        row_pass = sum(1 for s, _ in results.values() if s == 'pass')
        row_fail = sum(1 for s, _ in results.values() if s == 'fail')
        status_icon = '✓' if row_fail == 0 else '✗'
        print(f'  {status_icon} {rid}  ({row_pass} pass, {row_fail} fail)')
        for field, (status, detail) in results.items():
            field_stats[field][status] += 1
            if status == 'fail':
                print(f'      FAIL {field}: {detail}')

    print()
    print('Field accuracy (labeled rows):')
    for field in FIELD_NAMES:
        p = field_stats[field]['pass']
        f = field_stats[field]['fail']
        total = p + f
        pct = f'{p / total * 100:.0f}%' if total > 0 else 'n/a'
        print(f'  {field:<35} {p:2d}/{total:2d}  {pct}')

    print()
    print('=' * 70)
    print('STRUCTURAL CHECKS (all rows in output.csv)')
    print('=' * 70)

    check_stats = {}
    total_rows = len(predictions)
    error_rows = []

    for rid, pred in sorted(predictions.items()):
        checks = structural_checks(pred, requests)
        for name, status, detail in checks:
            if name not in check_stats:
                check_stats[name] = {'pass': 0, 'fail': 0}
            check_stats[name][status] += 1
            if status == 'fail':
                error_rows.append((rid, name, detail))

    print(f'Rows checked: {total_rows}')
    print()
    for name, stats in sorted(check_stats.items()):
        p = stats['pass']
        f = stats['fail']
        total = p + f
        pct = f'{p / total * 100:.0f}%' if total > 0 else 'n/a'
        print(f'  {name:<35} {p:3d}/{total:3d}  {pct}')

    if error_rows:
        print()
        print(f'Structural failures ({len(error_rows)} total):')
        for rid, name, detail in error_rows[:20]:
            print(f'  {rid}  {name}  {detail}')
        if len(error_rows) > 20:
            print(f'  ... and {len(error_rows) - 20} more')

    print()
    print('Done.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
