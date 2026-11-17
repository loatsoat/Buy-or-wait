"""Enumerate every eligible payment plan, keep the safe ones, return the best.

Ranking from the problem statement (lower tuple wins):
  1. completes by desired_completion_date   4. starts earlier
  2. needs no spending changes              5. fewer payments
  3. lower total paid                       6. lower payment_option_id
Ties between change sets go to the smaller set.
"""
import calendar
from datetime import timedelta
from itertools import combinations

from . import config
from .forecast import build_balance_timeline, is_safe

# Rank used at step 6 by plans that have no payment option (partial, wait).
_NO_OPTION = 10 ** 9


def _option_number(option):
    return int(''.join(c for c in option['payment_option_id'] if c.isdigit()))


def _plan_key(plan):
    changes = plan['spending_changes_needed']
    return (not plan['completes_by_deadline'], bool(changes), plan['total_paid'],
            plan['schedule'][0][0], len(plan['schedule']), plan['option_number'],
            len(changes), [c['event_id'] for c in changes])


def _within_installment_limit(schedule, request_date, max_months):
    """Last payment falls on or before request_date + max_months calendar months (day clamped to month end)."""
    if max_months is None:
        return False
    month_index = request_date.month - 1 + max_months
    year, month = request_date.year + month_index // 12, month_index % 12 + 1
    limit = request_date.replace(year=year, month=month,
                                 day=min(request_date.day, calendar.monthrange(year, month)[1]))
    return schedule[-1][0] <= limit


def _schedules(request, profile, options, astp, efpd, with_changes):
    """Yield (method, schedule, total_paid, option_number) for every eligible plan shape."""
    rd, requested = request['request_date'], request['requested_amount']
    deadline = request['desired_completion_date']
    accepted = profile['payment_methods_user_will_consider']

    for option in options:
        method = option['payment_method']
        if method not in accepted:
            continue
        step = timedelta(days=option['payment_frequency_days'] or 0)
        schedule = [(option['first_payment_date'] + step * i, option['payment_amount'])
                    for i in range(option['number_of_payments'])]
        if method == 'installments' and not _within_installment_limit(
                schedule, rd, profile['max_installment_months']):
            continue
        yield method, schedule, option['total_payable_amount'], _option_number(option)

    # astp and efpd are defined before spending changes, so partial payment and waiting
    # (both built from them) are only offered without changes.
    if with_changes:
        return
    if ('partial_payment' in accepted and request['allows_partial_payment']
            and 0 < astp < requested and efpd and efpd <= deadline):
        yield 'partial_payment', [(rd, astp), (efpd, requested - astp)], requested, _NO_OPTION
    if 'full_payment' in accepted and efpd and efpd > rd:
        yield 'wait', [(efpd, requested)], requested, _NO_OPTION


def _safe_plans(request, state, options, astp, efpd, changes, stop=(), reduce=None):
    debits = state.all_future_debits(stop, reduce or {})
    credits = state.all_future_credits()
    for method, schedule, total, option_number in _schedules(
            request, state.profile, options, astp, efpd, bool(changes)):
        timeline = build_balance_timeline(request['request_date'], state.balance, debits, credits, schedule)
        if is_safe(timeline, state.min_balance):
            yield {
                'method': method,
                'schedule': schedule,
                'total_paid': total,
                'spending_changes_needed': changes,
                'completes_by_deadline': schedule[-1][0] <= request['desired_completion_date'],
                'option_number': option_number,
            }


def select_plan(request, state, options, astp, efpd):
    """Return the best safe plan dict, or None when nothing eligible is safe."""
    plans = list(_safe_plans(request, state, options, astp, efpd, []))
    # Spending changes rank below every on-time immediate plan without them, so only search when none exists.
    if not any(p['completes_by_deadline'] and p['method'] != 'wait' for p in plans):
        candidates = state.spending_change_candidates()
        for size in range(1, min(config.MAX_SPENDING_CHANGES, len(candidates)) + 1):
            for combo in combinations(candidates, size):
                if len({c['event_id'] for c in combo}) < size:
                    continue  # stopping and reducing the same event are mutually exclusive
                plans += _safe_plans(
                    request, state, options, astp, efpd, list(combo),
                    stop={c['series'] for c in combo if c['action'] == 'stop'},
                    reduce={c['series']: c['new_amount'] for c in combo if c['action'] == 'reduce_to'})
    # Waiting is the fallback: it is chosen only when no immediate plan (even with changes) is safe.
    immediate = [p for p in plans if p['method'] != 'wait']
    return min(immediate or plans, key=_plan_key) if plans else None
