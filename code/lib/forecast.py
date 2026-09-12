"""Daily balance forecasting and safe-payment computation.

Core idea:
  balance[t] = opening_balance
               + sum(credits on or before t)
               - sum(debits on or before t)

To pay X on day T:
  balance[t] -= X  for all t >= T

A payment of X on day T is safe iff:
  min(balance[T], balance[T+1], ..., balance[horizon]) >= min_balance

We want the LARGEST X safe to pay today (amount_safe_to_pay) and the
EARLIEST future date when the full requested amount is safe.

We use a running-minimum approach:
  Let floor_after_T = min(balance[t] for t in [T, horizon])
  slack = floor_after_T - min_balance
  amount_safe = min(requested_amount, max(0, slack))
"""

from datetime import timedelta


def build_balance_timeline(
    start_date,
    opening_balance,
    future_debits,    # list of (date, amount, event_id, category)
    future_credits,   # list of (date, amount, event_id, category)
    forecast_days=90,
):
    """Return a dict {date: projected_balance} from start_date to start_date+forecast_days.

    The balance on each day is the ENDING balance (after all credits and debits
    that settle on that day).
    """
    horizon = start_date + timedelta(days=forecast_days)

    # Aggregate net flows by date
    net = {}
    for d, amt, eid, cat in future_debits:
        if d is not None and start_date <= d <= horizon:
            net[d] = net.get(d, 0.0) - amt
    for d, amt, eid, cat in future_credits:
        if d is not None and start_date <= d <= horizon:
            net[d] = net.get(d, 0.0) + amt

    # Build running balance
    timeline = {}
    bal = opening_balance
    cur = start_date
    while cur <= horizon:
        bal += net.get(cur, 0.0)
        timeline[cur] = bal
        cur += timedelta(days=1)
    return timeline


def amount_safe_to_pay_today(timeline, start_date, forecast_days, min_balance, requested_amount):
    """Return the maximum safe payment amount on start_date.

    Paying X today reduces every balance from start_date onwards by X.
    Safe iff  min(timeline[start_date:]) - X >= min_balance
    """
    horizon = start_date + timedelta(days=forecast_days)
    cur = start_date
    floor_val = None
    while cur <= horizon:
        b = timeline.get(cur)
        if b is not None:
            if floor_val is None or b < floor_val:
                floor_val = b
        cur += timedelta(days=1)
    if floor_val is None:
        floor_val = timeline.get(start_date, 0.0)
    slack = floor_val - min_balance
    return min(requested_amount, max(0.0, slack))


def earliest_full_payment_date(timeline, start_date, forecast_days, min_balance, requested_amount):
    """Return the earliest date d >= start_date when paying requested_amount is safe.

    A payment on date T is safe iff min(balance[T..horizon]) - requested_amount >= min_balance.
    We scan forward with a precomputed suffix-minimum for O(n) performance.

    Returns None if no such date exists within the forecast window.
    """
    from datetime import date as _date
    horizon = start_date + timedelta(days=forecast_days)

    # Build suffix minimum array: sfx_min[d] = min(timeline[d..horizon])
    # Walk backwards from horizon to start_date
    all_dates = []
    cur = start_date
    while cur <= horizon:
        all_dates.append(cur)
        cur += timedelta(days=1)

    if not all_dates:
        return None

    # Fill suffix min
    sfx_min = {}
    running_min = None
    for d in reversed(all_dates):
        b = timeline.get(d)
        if b is not None:
            if running_min is None or b < running_min:
                running_min = b
        sfx_min[d] = running_min

    # Scan forward: first date where sfx_min[d] - requested_amount >= min_balance
    for d in all_dates:
        floor_val = sfx_min.get(d)
        if floor_val is None:
            continue
        if floor_val - requested_amount >= min_balance - 0.005:
            return d

    return None


def build_timeline_with_payments(
    start_date,
    opening_balance,
    future_debits,
    future_credits,
    payment_schedule,   # list of (date, amount)
    forecast_days=90,
):
    """Build a timeline that includes a specific payment schedule."""
    extra_debits = [(d, amt, '_payment', 'payment') for d, amt in payment_schedule]
    return build_balance_timeline(
        start_date, opening_balance,
        list(future_debits) + extra_debits,
        future_credits,
        forecast_days,
    )


def is_plan_safe(timeline, start_date, forecast_days, min_balance, payment_schedule):
    """Return True if every day's balance stays >= min_balance with the given payment schedule."""
    horizon = start_date + timedelta(days=forecast_days)
    cur = start_date
    while cur <= horizon:
        if timeline.get(cur, float('inf')) < min_balance - 0.005:
            return False
        cur += timedelta(days=1)
    return True
