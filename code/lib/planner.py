"""Plan enumeration and selection.

Tries all eligible plans in priority order and returns the best safe one.

Priority (per spec):
  1. Completes the full request by desired_completion_date
  2. Requires no spending changes
  3. Minimise total amount paid
  4. Start payment earlier
  5. Fewer payments
  6. Lowest payment_option_id (tie-breaker)
"""

from datetime import timedelta, date
from itertools import combinations
from . import config
from .forecast import (
    build_balance_timeline,
    build_timeline_with_payments,
    amount_safe_to_pay_today,
    earliest_full_payment_date,
    is_plan_safe,
)


def _plan_key(plan):
    """Lower is better."""
    by_deadline = 0 if plan['completes_by_deadline'] else 1
    needs_changes = 1 if plan['spending_changes_needed'] else 0
    total_paid = plan['total_paid']
    first_payment_date = plan['first_payment_date'] or date.max
    num_payments = plan['num_payments']
    option_id_num = plan['option_id_num']
    return (by_deadline, needs_changes, total_paid, first_payment_date, num_payments, option_id_num)


def _parse_option_id_num(oid):
    try:
        return int(''.join(c for c in str(oid) if c.isdigit()))
    except Exception:
        return 9999


def select_plan(
    request,
    user_state,
    payment_options,     # list of normalised payment option dicts
    base_timeline,       # timeline WITHOUT any payment
    forecast_days=config.FORECAST_DAYS,
):
    """Return the best plan dict or None if no plan is safe."""

    rd = request['request_date']
    requested = request['requested_amount']
    deadline = request['desired_completion_date']
    allows_partial = request['allows_partial_payment']
    profile = user_state.profile
    min_bal = user_state.min_balance

    payment_methods_ok = profile.get('payment_methods_user_will_consider', set())
    max_install_months = profile.get('max_installment_months')  # None = no installments

    # Pre-compute amount_safe_to_pay and earliest_full_payment_date (no spending changes)
    astp = amount_safe_to_pay_today(base_timeline, rd, forecast_days, min_bal, requested)
    efpd = earliest_full_payment_date(base_timeline, rd, forecast_days, min_bal, requested)

    candidates = []

    # ------------------------------------------------------------------
    # 1. full_payment  (pay everything today)
    # ------------------------------------------------------------------
    if 'full_payment' in payment_methods_ok:
        # Find full_payment option or synthesise it
        fp_options = [o for o in payment_options if o.get('payment_method') == 'full_payment']
        if not fp_options:
            # Synthesise: pay full amount today
            fp_options = [{'payment_option_id': 'synth_fp', 'payment_method': 'full_payment',
                           'payment_amount': requested, 'number_of_payments': 1,
                           'first_payment_date': rd, 'payment_frequency_days': None,
                           'total_payable_amount': requested, 'financing_fee': 0.0}]
        for opt in fp_options:
            pay_date = opt.get('first_payment_date') or rd
            total = opt.get('total_payable_amount') or requested
            schedule = [(pay_date, total)]
            tl = build_timeline_with_payments(rd, user_state.balance,
                                              user_state.all_future_debits(), user_state.all_future_credits(),
                                              schedule, forecast_days)
            if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                completes = (pay_date <= deadline) if deadline else True
                candidates.append({
                    'method': 'full_payment',
                    'schedule': schedule,
                    'total_paid': total,
                    'spending_changes_needed': [],
                    'completes_by_deadline': completes,
                    'first_payment_date': pay_date,
                    'num_payments': 1,
                    'option_id_num': _parse_option_id_num(opt.get('payment_option_id', 9999)),
                    'option_id': opt.get('payment_option_id'),
                    'astp': astp,
                    'efpd': efpd,
                })

    # ------------------------------------------------------------------
    # 2. installments
    # ------------------------------------------------------------------
    install_options = [o for o in payment_options if o.get('payment_method') == 'installments']
    if 'installments' in payment_methods_ok:
        for opt in install_options:
            n = opt.get('number_of_payments', 1)
            pay_amt = opt.get('payment_amount')
            freq = opt.get('payment_frequency_days')
            first = opt.get('first_payment_date') or rd
            total = opt.get('total_payable_amount') or (pay_amt * n if pay_amt else requested)

            # Check max_installment_months
            if max_install_months is not None and freq and n:
                last_payment = first + timedelta(days=freq * (n - 1))
                if (last_payment - rd).days > max_install_months * 31:
                    continue

            # Build schedule
            schedule = []
            d = first
            for i in range(n):
                schedule.append((d, pay_amt))
                if freq and i < n - 1:
                    d = d + timedelta(days=freq)

            last_date = schedule[-1][0] if schedule else first
            completes = (last_date <= deadline) if deadline else True

            tl = build_timeline_with_payments(rd, user_state.balance,
                                              user_state.all_future_debits(), user_state.all_future_credits(),
                                              schedule, forecast_days)
            if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                candidates.append({
                    'method': 'installments',
                    'schedule': schedule,
                    'total_paid': total,
                    'spending_changes_needed': [],
                    'completes_by_deadline': completes,
                    'first_payment_date': first,
                    'num_payments': n,
                    'option_id_num': _parse_option_id_num(opt.get('payment_option_id', 9999)),
                    'option_id': opt.get('payment_option_id'),
                    'astp': astp,
                    'efpd': efpd,
                })

    # ------------------------------------------------------------------
    # 3. partial_payment
    # ------------------------------------------------------------------
    if 'partial_payment' in payment_methods_ok and allows_partial:
        if 0 < astp < requested and efpd is not None and (deadline is None or efpd <= deadline):
            remainder = requested - astp
            schedule = [(rd, astp), (efpd, remainder)]
            tl = build_timeline_with_payments(rd, user_state.balance,
                                              user_state.all_future_debits(), user_state.all_future_credits(),
                                              schedule, forecast_days)
            if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                completes = (efpd <= deadline) if deadline else True
                candidates.append({
                    'method': 'partial_payment',
                    'schedule': schedule,
                    'total_paid': requested,
                    'spending_changes_needed': [],
                    'completes_by_deadline': completes,
                    'first_payment_date': rd,
                    'num_payments': 2,
                    'option_id_num': 5000,
                    'option_id': None,
                    'astp': astp,
                    'efpd': efpd,
                })

    # ------------------------------------------------------------------
    # 4. wait (full payment on a future date)
    # ------------------------------------------------------------------
    if 'full_payment' in payment_methods_ok and efpd is not None and efpd > rd:
        completes = (efpd <= deadline) if deadline else True
        schedule = [(efpd, requested)]
        tl = build_timeline_with_payments(rd, user_state.balance,
                                          user_state.all_future_debits(), user_state.all_future_credits(),
                                          schedule, forecast_days)
        if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
            candidates.append({
                'method': 'wait',
                'schedule': schedule,
                'total_paid': requested,
                'spending_changes_needed': [],
                'completes_by_deadline': completes,
                'first_payment_date': efpd,
                'num_payments': 1,
                'option_id_num': 8000,
                'option_id': None,
                'astp': astp,
                'efpd': efpd,
            })

    # ------------------------------------------------------------------
    # 5. Try plans WITH spending changes
    # ------------------------------------------------------------------
    # Try spending changes when:
    #   a) No safe plan was found without changes, OR
    #   b) The best safe plan found does not complete by the deadline, OR
    #   c) A safe plan exists but the 90-day floor margin is thin enough
    #      that the truth may require spending changes (astp within 5% of
    #      requested means we are at the boundary — try changes too)
    best_without_changes = min(candidates, key=_plan_key) if candidates else None
    floor_is_thin = (
        best_without_changes is not None
        and best_without_changes['method'] in ('full_payment',)
        and astp >= requested * 0.95  # within 5% of requested
    )
    needs_spending_change_search = (
        not candidates
        or (best_without_changes is not None and not best_without_changes['completes_by_deadline'])
        or floor_is_thin
    )
    if needs_spending_change_search:
        spending_change_candidates = _try_with_spending_changes(
            request, user_state, payment_options, rd, requested, deadline,
            allows_partial, payment_methods_ok, max_install_months, min_bal,
            forecast_days, astp, efpd
        )
        candidates += spending_change_candidates

    if not candidates:
        return None

    # Sort and return best
    candidates.sort(key=_plan_key)
    return candidates[0]


def _try_with_spending_changes(
    request, user_state, payment_options, rd, requested, deadline,
    allows_partial, payment_methods_ok, max_install_months, min_bal,
    forecast_days, astp_base, efpd_base
):
    """Try combinations of flexible spending changes to unlock plans."""
    profile      = user_state.profile
    willing_stop = profile.get('expense_categories_user_is_willing_to_stop', set())
    willing_reduce = profile.get('expense_categories_user_is_willing_to_reduce', set())
    protected    = profile.get('expense_categories_to_protect', set())

    # Compute the gap: how much more astp is needed to make the plan safe.
    # Only spending changes whose 90-day saving covers ≥50% of the gap are
    # considered — prevents trivially small subscriptions from appearing as
    # required changes when they cannot materially close the shortfall.
    base_debits  = user_state.all_future_debits()
    base_credits = user_state.all_future_credits()
    base_tl      = build_balance_timeline(rd, user_state.balance, base_debits, base_credits, forecast_days)
    base_astp    = amount_safe_to_pay_today(base_tl, rd, forecast_days, min_bal, requested)
    gap          = max(0.0, requested - base_astp)

    # Build list of candidate spending changes
    possible_stops   = []   # (event_id, category, monthly_amount)
    possible_reduces = []   # (event_id, category, current_amount, min_amount)

    seen_cats = set()
    for item in user_state.recurring_debits:
        d, amt, eid, cat, flex, min_amt = item
        if cat in protected:
            continue
        if cat in seen_cats:
            continue
        # Strip projection suffixes to get the real dataset event_id
        real_eid = eid
        for suffix in ('_curmon', '_proj', '_r1', '_r2', '_r3', '_r4'):
            if real_eid.endswith(suffix):
                real_eid = real_eid[: -len(suffix)]
                break
        # Skip synthesised / injected events with no real event_id
        if real_eid.startswith('_') or not real_eid:
            continue
        seen_cats.add(cat)

        if flex in config.FLEXIBLE_STOPPABLE and cat in willing_stop:
            # Monthly saving = amt × 3 months over the 90-day window
            saving_90d = amt * 3
            # Only include if the saving materially closes the gap (≥50%)
            if gap <= 0 or saving_90d >= gap * 0.5:
                possible_stops.append((real_eid, cat, amt))
        elif flex in config.FLEXIBLE_REDUCIBLE and cat in willing_reduce:
            if min_amt is not None and min_amt < amt:
                saving_90d = (amt - min_amt) * 3
                if gap <= 0 or saving_90d >= gap * 0.5:
                    possible_reduces.append((real_eid, cat, amt, min_amt))

    # Limit to MAX_SPENDING_CHANGES combinations
    max_changes = config.MAX_SPENDING_CHANGES
    results = []

    def try_combo(stops, reduces):
        stop_set = {eid for eid, cat, amt in stops}
        reduce_dict = {eid: min_amt for eid, cat, amt, min_amt in reduces}
        # stop and reduce must be mutually exclusive event ids
        if stop_set & set(reduce_dict.keys()):
            return

        debits = user_state.all_future_debits(stop_events=stop_set, reduce_events=reduce_dict)
        credits = user_state.all_future_credits()

        tl_base = build_balance_timeline(rd, user_state.balance, debits, credits, forecast_days)
        new_astp = amount_safe_to_pay_today(tl_base, rd, forecast_days, min_bal, requested)
        new_efpd = earliest_full_payment_date(tl_base, rd, forecast_days, min_bal, requested)

        spending_changes = (
            [{'action': 'stop', 'event_id': eid} for eid, cat, amt in stops] +
            [{'action': 'reduce_to', 'event_id': eid, 'new_amount': min_amt} for eid, cat, amt, min_amt in reduces]
        )

        # Try full_payment
        if 'full_payment' in payment_methods_ok:
            fp_options = [o for o in payment_options if o.get('payment_method') == 'full_payment']
            if not fp_options:
                fp_options = [{'payment_option_id': 'synth_fp', 'payment_method': 'full_payment',
                               'payment_amount': requested, 'number_of_payments': 1,
                               'first_payment_date': rd, 'total_payable_amount': requested, 'financing_fee': 0.0}]
            for opt in fp_options:
                pay_date = opt.get('first_payment_date') or rd
                total = opt.get('total_payable_amount') or requested
                schedule = [(pay_date, total)]
                tl = build_timeline_with_payments(rd, user_state.balance, debits, credits, schedule, forecast_days)
                if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                    completes = (pay_date <= deadline) if deadline else True
                    results.append({
                        'method': 'full_payment',
                        'schedule': schedule,
                        'total_paid': total,
                        'spending_changes_needed': spending_changes,
                        'completes_by_deadline': completes,
                        'first_payment_date': pay_date,
                        'num_payments': 1,
                        'option_id_num': _parse_option_id_num(opt.get('payment_option_id', 9999)),
                        'option_id': opt.get('payment_option_id'),
                        'astp': new_astp,
                        'efpd': new_efpd,
                    })

        # Try installments
        if 'installments' in payment_methods_ok:
            install_options = [o for o in payment_options if o.get('payment_method') == 'installments']
            for opt in install_options:
                n = opt.get('number_of_payments', 1)
                pay_amt = opt.get('payment_amount')
                freq = opt.get('payment_frequency_days')
                first = opt.get('first_payment_date') or rd
                total = opt.get('total_payable_amount') or (pay_amt * n if pay_amt else requested)
                if max_install_months is not None and freq and n:
                    last_payment = first + timedelta(days=freq * (n - 1))
                    if (last_payment - rd).days > max_install_months * 31:
                        continue
                schedule = []
                d = first
                for i in range(n):
                    schedule.append((d, pay_amt))
                    if freq and i < n - 1:
                        d = d + timedelta(days=freq)
                last_date = schedule[-1][0] if schedule else first
                completes = (last_date <= deadline) if deadline else True
                tl = build_timeline_with_payments(rd, user_state.balance, debits, credits, schedule, forecast_days)
                if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                    results.append({
                        'method': 'installments',
                        'schedule': schedule,
                        'total_paid': total,
                        'spending_changes_needed': spending_changes,
                        'completes_by_deadline': completes,
                        'first_payment_date': first,
                        'num_payments': n,
                        'option_id_num': _parse_option_id_num(opt.get('payment_option_id', 9999)),
                        'option_id': opt.get('payment_option_id'),
                        'astp': new_astp,
                        'efpd': new_efpd,
                    })

        # Try partial_payment
        if 'partial_payment' in payment_methods_ok and allows_partial:
            if 0 < new_astp < requested and new_efpd is not None and (deadline is None or new_efpd <= deadline):
                remainder = requested - new_astp
                schedule = [(rd, new_astp), (new_efpd, remainder)]
                tl = build_timeline_with_payments(rd, user_state.balance, debits, credits, schedule, forecast_days)
                if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                    completes = (new_efpd <= deadline) if deadline else True
                    results.append({
                        'method': 'partial_payment',
                        'schedule': schedule,
                        'total_paid': requested,
                        'spending_changes_needed': spending_changes,
                        'completes_by_deadline': completes,
                        'first_payment_date': rd,
                        'num_payments': 2,
                        'option_id_num': 5000,
                        'option_id': None,
                        'astp': new_astp,
                        'efpd': new_efpd,
                    })

        # Try wait
        if 'full_payment' in payment_methods_ok and new_efpd is not None and new_efpd > rd:
            completes = (new_efpd <= deadline) if deadline else True
            schedule = [(new_efpd, requested)]
            tl = build_timeline_with_payments(rd, user_state.balance, debits, credits, schedule, forecast_days)
            if is_plan_safe(tl, rd, forecast_days, min_bal, schedule):
                results.append({
                    'method': 'wait',
                    'schedule': schedule,
                    'total_paid': requested,
                    'spending_changes_needed': spending_changes,
                    'completes_by_deadline': completes,
                    'first_payment_date': new_efpd,
                    'num_payments': 1,
                    'option_id_num': 8000,
                    'option_id': None,
                    'astp': new_astp,
                    'efpd': new_efpd,
                })

    # Try single changes
    for stop in possible_stops:
        try_combo([stop], [])
    for red in possible_reduces:
        try_combo([], [red])

    # Try pairs (stop + reduce different events)
    if len(possible_stops) + len(possible_reduces) >= 2:
        for i, s in enumerate(possible_stops):
            for r in possible_reduces:
                if s[0] != r[0]:
                    try_combo([s], [r])
        for s1, s2 in combinations(possible_stops, 2):
            try_combo([s1, s2], [])
        for r1, r2 in combinations(possible_reduces, 2):
            try_combo([], [r1, r2])

    return results
