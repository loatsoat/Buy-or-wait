"""Balance forecast over config.FORECAST_DAYS and the two safe-payment figures derived from it.

The timeline holds each day's closing balance after that day's debits, credits and any
payment of the plan being tested. The ledger gives dates, not times, so a bill and a
salary on the same day are netted.

Paying X on day T lowers every balance from T onward by X, so it is safe exactly when
min(timeline[T:]) - X >= minimum balance.
"""
from datetime import timedelta

from . import config

_EPS = 0.005


def build_balance_timeline(start_date, opening_balance, debits, credits, payments=()):
    """Return closing balances for start_date .. start_date + FORECAST_DAYS (one per day).

    debits/credits/payments are (date, amount) pairs; flows outside the window are ignored.
    """
    days = config.FORECAST_DAYS + 1
    net = [0.0] * days
    for flows, sign in ((debits, -1), (payments, -1), (credits, 1)):
        for day, amount in flows:
            offset = (day - start_date).days
            if 0 <= offset < days:
                net[offset] += sign * amount
    timeline, balance = [], opening_balance
    for change in net:
        balance += change
        timeline.append(balance)
    return timeline


def amount_safe_to_pay_today(timeline, min_balance, requested_amount):
    """Largest payment on day 0 that keeps every day at or above min_balance, capped at the request."""
    return min(requested_amount, max(0.0, min(timeline) - min_balance))


def earliest_full_payment_date(timeline, start_date, min_balance, requested_amount):
    """First date on which the full amount is safe as one payment, or None within the window."""
    suffix_min = list(timeline)
    for i in range(len(suffix_min) - 2, -1, -1):
        suffix_min[i] = min(suffix_min[i], suffix_min[i + 1])
    for offset, floor in enumerate(suffix_min):
        if floor - requested_amount >= min_balance - _EPS:
            return start_date + timedelta(days=offset)
    return None


def is_safe(timeline, min_balance):
    return min(timeline) >= min_balance - _EPS
