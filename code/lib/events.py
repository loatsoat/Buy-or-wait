"""Resolve per-user financial state and build categorised future cash flows."""

import calendar
from collections import defaultdict
from datetime import timedelta

from . import config
from .evidence import EvidenceError
from .messages_nlp import parse_message


# A category needs this many regularly spaced settled debits before it is projected forward.
MIN_OCCURRENCES = 3
# Days either side of a projected monthly bill in which a failed/cancelled row replaces it.
FAILED_INSTANCE_DAYS = 7


def _dates_for_day_of_month(dom, start, end):
    """Return all dates with day-of-month == dom in [start, end]."""
    results = []
    cur = start.replace(day=1)
    while cur <= end:
        last = calendar.monthrange(cur.year, cur.month)[1]
        candidate_day = min(dom, last)
        candidate = cur.replace(day=candidate_day)
        if start <= candidate <= end:
            results.append(candidate)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return results


class UserFinancialState:
    """All financial data for one user, normalised and ready for forecasting."""

    def __init__(self, user_id, profile, events, messages, converter, request_date):
        self.user_id = user_id
        self.profile = profile
        self.request_date = request_date
        self.home_currency = profile['home_currency']
        self.balance = profile['current_available_balance']
        self.min_balance = profile['minimum_balance_to_keep'] or 0.0

        # Salary facts from messages; None means "no message said so".
        self.salary_ended = False          # income stops after the last payroll
        self.salary_remaining = None       # one of two incomes ended; this one continues
        self.salary_amount = None          # confirmed new regular amount
        self.salary_temp_amount = None     # next payroll only
        self.salary_resume = None          # (date, amount) after a leave gap
        self.salary_next_date = None       # rescheduled next payday
        self.salary_unconfirmed = False    # gig payouts not yet withdrawable
        self.rent_amount = None            # rent after an announced increase

        self._all_events = self._normalise_events(events, converter)
        self._apply_messages(messages)
        self._build_future_flows()

    def _normalise_events(self, events, converter):
        """Copy the user's events and add amount_home (amount in the home currency)."""
        normalised = []
        for e in events:
            e2 = dict(e)
            amount = e2['amount']
            # A settled row with an unknown amount is already inside the balance, so it is only
            # left out of the averages. An unknown future debit or credit cannot be bounded.
            if e2['status'] == 'settled' and e2['settlement_date'] > self.request_date:
                raise EvidenceError(f"{e2['event_id']}: settled on {e2['settlement_date']}, after the request date")
            if amount is None and e2['status'] in config.CONFIRMED_FUTURE_STATUSES:
                raise EvidenceError(
                    f"{e2['event_id']}: the amount of this {e2['status']} {e2['direction']} is unknown")
            if amount is not None and e2['currency'] != self.home_currency:
                amount = converter.convert(
                    amount, e2['currency'], self.home_currency, e2['settlement_date'].isoformat())
            e2['amount_home'] = amount
            normalised.append(e2)
        return normalised

    def _event(self, event_id):
        return next((e for e in self._all_events if e['event_id'] == event_id), None)

    def _upcoming_salary_rows(self):
        return [e for e in self._all_events
                if e['direction'] == config.CREDIT and e['event_type'] == 'income'
                and e['status'] in config.CONFIRMED_FUTURE_STATUSES and e['settlement_date'] > self.request_date]

    def _add_scheduled_credit(self, event_id, category, day, amount):
        self._all_events.append({
            'event_id': event_id, 'event_type': 'income', 'category': category, 'description': '',
            'direction': config.CREDIT, 'amount_home': amount, 'currency': self.home_currency,
            'event_date': day, 'settlement_date': day, 'status': 'scheduled', 'flexibility': 'fixed',
        })

    def _apply_messages(self, msgs):
        """Apply message amendments (see messages_nlp.py) in the order they were sent.

        card_dispute and childcare_start are parsed but change nothing: a disputed charge
        stays a debit until a reversal posts, and a childcare start gives no amount to project.
        """
        for msg in sorted(msgs, key=lambda m: m['sent_at']):
            # Only evidence provably known on the request date counts: no timestamp, no use.
            if msg['sent_at_date'] is None or msg['sent_at_date'] > self.request_date:
                continue
            for amd in parse_message(msg):
                kind = amd['type']
                event = self._event(amd['event_id']) if amd.get('event_id') else None
                upcoming = self._upcoming_salary_rows()

                if kind in ('ignore_credit', 'internal_transfer'):
                    if event:
                        event['_ignored'] = True
                    elif kind == 'ignore_credit' and any(
                            w in msg['message_text'].lower() for w in ('payout', 'earnings', 'withdrawable')):
                        self.salary_unconfirmed = True
                elif kind == 'settled_credit' and event:
                    event['status'] = 'settled'
                elif kind == 'reimbursement' and event:
                    event['_is_reimbursement'] = True
                elif kind == 'failed_debit' and event:
                    event['status'] = 'pending'   # the bill is still owed and will be retried
                elif kind == 'salary_ended':
                    self.salary_ended = True
                    self.salary_remaining = amd['remaining_amount']
                    if self.salary_remaining is None:
                        for e in upcoming:
                            e['status'] = 'cancelled'
                elif kind == 'salary_temp' and amd['amount'] is not None:
                    self.salary_temp_amount = amd['amount']
                    if upcoming:
                        upcoming[0]['amount_home'] = amd['amount']
                elif kind == 'salary_resume' and amd['amount'] is not None:
                    self.salary_resume = (amd['effective_date'], amd['amount'])
                    if amd['effective_date']:
                        self._add_scheduled_credit('_msg_salary_resume', 'salary', *self.salary_resume)
                elif kind == 'salary_date' and amd['new_date']:
                    if upcoming:
                        upcoming[0]['settlement_date'] = upcoming[0]['event_date'] = amd['new_date']
                    else:
                        self.salary_next_date = amd['new_date']
                elif kind == 'salary_first' and amd['amount'] is not None:
                    if upcoming:
                        upcoming[0]['amount_home'] = amd['amount']
                        if amd['effective_date']:
                            upcoming[0]['settlement_date'] = upcoming[0]['event_date'] = amd['effective_date']
                    elif amd['effective_date'] and amd['amount']:
                        self._add_scheduled_credit('_msg_salary_first', 'salary', amd['effective_date'], amd['amount'])
                elif kind == 'salary_change' and amd['amount'] is not None:
                    self.salary_amount = amd['amount']
                    for e in upcoming:
                        if amd['effective_date'] is None or e['settlement_date'] >= amd['effective_date']:
                            e['amount_home'] = amd['amount']
                elif kind == 'rent_increase':
                    rents = [e for e in self._all_events if e['category'] == 'rent'
                             and e['direction'] == config.DEBIT and e['status'] == 'settled']
                    if rents:
                        last = max(rents, key=lambda e: e['settlement_date'])
                        self.rent_amount = last['amount_home'] * (1 + amd['percent'] / 100)
                elif kind == 'invoice_confirmed' and amd['amount'] is not None and amd['settlement_date']:
                    self._add_scheduled_credit('_msg_invoice', 'freelance', amd['settlement_date'], amd['amount'])

    def _build_future_flows(self):
        """Populate future_debits, future_credits and the recurring expense series."""
        rd = self.request_date
        horizon = rd + timedelta(days=config.FORECAST_DAYS)

        self.future_debits = []    # (date, amount, category): pending or scheduled
        self.future_credits = []   # (date, amount, event_id, category)
        self.series = {}           # category -> recurring expense series
        settled_debits = defaultdict(list)
        salary_settled = []

        for e in self._all_events:
            amount = e['amount_home']
            if e.get('_ignored') or amount is None or amount <= 0:
                continue
            recurring_type = e['event_type'] not in config.NON_RECURRING_EVENT_TYPES
            if e['status'] == 'settled':
                if e['direction'] == config.DEBIT and recurring_type:
                    settled_debits[e['category']].append(e)
                elif e['direction'] == config.CREDIT and e['category'] == 'salary' and not e.get('_is_reimbursement'):
                    salary_settled.append((e['settlement_date'], amount))
            elif e['status'] in config.CONFIRMED_FUTURE_STATUSES:
                if e['direction'] == config.DEBIT:
                    self.future_debits.append((e['settlement_date'], amount, e['category']))
                # Pending credits are ignored until they settle (problem statement).
                elif e['direction'] == config.CREDIT and recurring_type and e['status'] == 'scheduled':
                    self.future_credits.append((e['settlement_date'], amount, e['event_id'], e['category']))

        for category, events in settled_debits.items():
            series = self._recurring_series(category, events, rd, horizon)
            if series:
                self.series[category] = series

        # A payroll row labelled final ends the income stream unless something newer confirms more pay.
        payroll = [e for e in self._all_events if e['category'] == 'salary' and e['direction'] == config.CREDIT
                   and e['status'] in ('settled', 'scheduled') and not e.get('_ignored')]
        if payroll and 'final' in max(payroll, key=lambda e: e['settlement_date']).get('description', '').lower():
            self.salary_ended = True
        self._project_salary(salary_settled, rd, horizon)

    def _recurring_series(self, category, events, start, end):
        """Describe one category's recurring expense, or None if history does not support one.

        The regular chain is the run of events spaced by the median gap; an extra one-off
        purchase in the same category is left out of both the schedule and the amount.
        """
        events = sorted(events, key=lambda e: e['settlement_date'])
        if len(events) < MIN_OCCURRENCES:
            return None
        dates = [e['settlement_date'] for e in events]
        interval = sorted((b - a).days for a, b in zip(dates, dates[1:]))[(len(dates) - 1) // 2]
        monthly = 27 <= interval <= 32
        tolerance = 3 if monthly else 1
        chain = [events[0]]
        for e in events[1:]:
            if abs((e['settlement_date'] - chain[-1]['settlement_date']).days - interval) <= tolerance:
                chain.append(e)
        if len(chain) < MIN_OCCURRENCES:
            return None

        last = chain[-1]
        amount = sum(e['amount_home'] for e in chain) / len(chain)
        if category == 'rent' and self.rent_amount is not None:
            amount = self.rent_amount

        if monthly:
            day = max(e['settlement_date'].day for e in chain)
            candidates = _dates_for_day_of_month(day, last['settlement_date'] + timedelta(days=20), end)
        else:
            candidates = []
            nxt = last['settlement_date'] + timedelta(days=interval)
            while nxt <= end:
                candidates.append(nxt)
                nxt += timedelta(days=interval)
        # A failed or cancelled row near a projected date was that period's attempt at the bill.
        # The spec says to ignore failed transactions, so that period is not projected again.
        # Pending and scheduled rows are separate charges and are added on top (financially safer).
        known = [e['event_date'] for e in self._all_events
                 if e['category'] == category and e['direction'] == config.DEBIT
                 and e['status'] in ('failed', 'cancelled') and e['event_date'] > last['settlement_date']]
        window = FAILED_INSTANCE_DAYS if monthly else 0
        confirmed = {d for d, _, c in self.future_debits if c == category}
        return {
            'event_id': last['event_id'],
            'description': last['description'],
            'category': category,
            'flexibility': last['flexibility'],
            'min_amount': last['minimum_allowed_amount'],
            'amount': amount,
            'dates': [d for d in candidates
                      if d >= start and d not in confirmed and not any(abs((d - k).days) <= window for k in known)],
        }

    def _project_salary(self, salary_settled, start, end):
        """Add projected paydays to future_credits from settled payroll history and messages."""
        if (self.salary_ended and self.salary_remaining is None) or self.salary_unconfirmed or not salary_settled:
            return
        history = sorted(salary_settled)

        # Keep the regular paydays: drop mid-cycle one-offs (bonus, commission, arrears).
        regular = [history[0]]
        for prev, cur in zip(history, history[1:]):
            if (cur[0] - prev[0]).days >= 20 or cur[0].day == history[0][0].day:
                regular.append(cur)
        gaps = [(b[0] - a[0]).days for a, b in zip(regular, regular[1:])]
        # Irregular pay (gaps outside 20-42 days) is not projected, unless a resume date is confirmed.
        if not all(20 <= g <= 42 for g in gaps) and self.salary_resume is None:
            return

        amounts = sorted(amount for _, amount in regular[-3:])
        base = amounts[len(amounts) // 2]
        if self.salary_amount is not None:
            base = self.salary_amount
        elif self.salary_remaining is not None:
            base = self.salary_remaining

        last_payday = regular[-1][0]
        paydays = _dates_for_day_of_month(last_payday.day, max(start, last_payday + timedelta(days=28)), end)
        if self.salary_next_date and start < self.salary_next_date <= end:
            moved = (self.salary_next_date.year, self.salary_next_date.month)
            paydays = sorted([d for d in paydays if (d.year, d.month) != moved] + [self.salary_next_date])

        known = {d for d, _, _, category in self.future_credits if category in ('salary', 'income')}
        temp = self.salary_temp_amount
        for day in paydays:
            if day in known:
                continue
            amount = base
            if temp is not None:
                amount, temp = temp, None
            elif self.salary_resume and self.salary_resume[0] and day >= self.salary_resume[0]:
                amount = self.salary_resume[1]
            self.future_credits.append((day, amount, '_proj_salary', 'salary'))

    def all_future_debits(self, stop=(), reduce=None):
        """Confirmed plus recurring debits as (date, amount); `stop`/`reduce` are keyed by category."""
        reduce = reduce or {}
        flows = [(d, amount) for d, amount, _ in self.future_debits]
        for category, series in self.series.items():
            if category in stop:
                continue
            amount = reduce.get(category, series['amount'])
            flows += [(d, amount) for d in series['dates']]
        return flows

    def all_future_credits(self):
        return [(d, amount) for d, amount, _, _ in self.future_credits]

    def spending_change_candidates(self):
        """Changes the user permits: stop or reduce-to-minimum on flexible, unprotected series."""
        profile = self.profile
        candidates = []
        for category, series in self.series.items():
            if category in profile['expense_categories_to_protect'] or not series['dates']:
                continue
            base = {'event_id': series['event_id'], 'series': category, 'description': series['description']}
            if (series['flexibility'] in config.FLEXIBLE_STOPPABLE
                    and category in profile['expense_categories_user_is_willing_to_stop']):
                candidates.append({**base, 'action': 'stop', 'new_amount': None})
            if (series['flexibility'] in config.FLEXIBLE_REDUCIBLE
                    and category in profile['expense_categories_user_is_willing_to_reduce']
                    and series['min_amount'] is not None and series['min_amount'] < series['amount']):
                candidates.append({**base, 'action': 'reduce_to', 'new_amount': series['min_amount']})
        return candidates
