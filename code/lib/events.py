"""Resolve per-user financial state and build categorised future cash flows."""

import calendar
import os
from collections import defaultdict
from datetime import date, timedelta

from . import config
from .messages_nlp import parse_message

try:
    from .ocr import extract_amount
    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False

    def extract_amount(_path):
        return None


def _day_of_month(d):
    return d.day


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

    def __init__(self, user_id, profile, events, payment_options_by_request,
                 messages_by_user, images_by_event, converter, request_date,
                 images_dir):
        self.user_id = user_id
        self.profile = profile
        self.request_date = request_date
        self.home_currency = profile['home_currency']
        self.balance = profile['current_available_balance']
        self.min_balance = profile['minimum_balance_to_keep'] or 0.0

        self._converter = converter
        self._images_by_event = images_by_event
        self._images_dir = images_dir

        raw_events = events or []
        self._all_events = self._normalise_events(raw_events)

        msgs = messages_by_user or []
        self._apply_messages(msgs)

        self._build_future_flows()

    def _normalise_events(self, events):
        normalised = []
        for e in events:
            e2 = dict(e)
            if e2.get('amount') is None:
                img_info = self._images_by_event.get(e2.get('event_id', ''))
                if img_info:
                    img_id = img_info.get('image_id', '')
                    img_path = os.path.join(self._images_dir, f'{img_id}.png')
                    val = extract_amount(img_path) if _OCR_AVAILABLE else None
                    e2['amount'] = val
            amount = e2.get('amount')
            cur = e2.get('currency', self.home_currency)
            settle_date = e2.get('settlement_date') or e2.get('event_date')
            if amount is not None and cur != self.home_currency and settle_date:
                try:
                    amount = self._converter.convert(
                        amount, cur, self.home_currency,
                        settle_date.strftime('%Y-%m-%d') if hasattr(settle_date, 'strftime') else settle_date
                    )
                except Exception:
                    pass
            e2['amount_home'] = amount
            normalised.append(e2)
        return normalised

    def _apply_messages(self, msgs):
        """Mutate self._all_events based on parsed message amendments."""
        msgs_sorted = sorted(msgs, key=lambda m: m.get('sent_at', '') or '')

        for msg in msgs_sorted:
            amendments = parse_message(msg)
            for amd in amendments:
                atype = amd['type']

                if atype == 'ignore_credit':
                    eid = amd.get('event_id')
                    if eid:
                        for e in self._all_events:
                            if e['event_id'] == eid:
                                e['_ignored'] = True
                    else:
                        # No specific event_id means the message says all projected
                        # income from a gig/payout platform is not yet withdrawable.
                        # Only suppress salary projection when the ignore is for
                        # a pending payout (not a refund/prize/investment).
                        text = msg.get('message_text', '').lower()
                        is_gig_payout = any(kw in text for kw in (
                            'payout', 'earnings', 'withdrawable', 'withdrawn',
                        ))
                        if is_gig_payout:
                            self._suppress_salary_projection = True

                elif atype == 'settled_credit':
                    eid = amd.get('event_id')
                    if eid:
                        for e in self._all_events:
                            if e['event_id'] == eid:
                                e['_force_settled'] = True
                                e['status'] = 'settled'

                elif atype == 'internal_transfer':
                    eid = amd.get('event_id')
                    if eid:
                        for e in self._all_events:
                            if e['event_id'] == eid:
                                e['_ignored'] = True
                    linked = msg.get('related_event_id')
                    if linked and linked != eid:
                        for e in self._all_events:
                            if e['event_id'] == linked:
                                e['_ignored'] = True

                elif atype == 'reimbursement':
                    eid = amd.get('event_id')
                    if eid:
                        for e in self._all_events:
                            if e['event_id'] == eid:
                                e['_is_reimbursement'] = True
                                e['_non_recurring'] = True

                elif atype == 'card_dispute':
                    eid = amd.get('event_id')
                    if eid:
                        for e in self._all_events:
                            if e['event_id'] == eid:
                                e['_card_dispute'] = True

                elif atype == 'failed_debit':
                    eid = amd.get('event_id')
                    if eid:
                        for e in self._all_events:
                            if e['event_id'] == eid:
                                e['status'] = 'pending'

                elif atype == 'salary_ended':
                    remaining = amd.get('remaining_amount')
                    self._salary_ended = True
                    self._salary_remaining = remaining
                    if remaining is None:
                        for e in self._all_events:
                            if (e.get('direction') == config.CREDIT
                                    and e.get('event_type') in ('income', 'salary')
                                    and e.get('status') in ('scheduled', 'pending')
                                    and (e.get('settlement_date') or date.min) > self.request_date):
                                e['status'] = 'cancelled'

                elif atype == 'salary_temp':
                    amount = amd.get('amount')
                    if amount is not None:
                        self._salary_temp_amount = amount
                        for e in self._all_events:
                            if (e.get('direction') == config.CREDIT
                                    and e.get('event_type') in ('income', 'salary')
                                    and e.get('status') in ('scheduled', 'pending')
                                    and (e.get('settlement_date') or date.min) > self.request_date):
                                e['amount_home'] = amount
                                break

                elif atype == 'salary_resume':
                    amount = amd.get('amount')
                    eff_date = amd.get('effective_date')
                    if amount is not None:
                        self._salary_resume_amount = amount
                        self._salary_resume_date = eff_date
                        # Inject a scheduled salary event so _project_salary has
                        # a concrete anchor to project from.
                        if eff_date is not None:
                            self._all_events.append({
                                'event_id': '_msg_salary_resume',
                                'user_id': self.user_id,
                                'event_type': 'income',
                                'category': 'salary',
                                'direction': config.CREDIT,
                                'amount_home': amount,
                                'currency': self.home_currency,
                                'event_date': eff_date,
                                'settlement_date': eff_date,
                                'status': 'scheduled',
                                'flexibility': 'fixed',
                                '_msg_injected': True,
                            })

                elif atype == 'salary_date':
                    new_date = amd.get('new_date')
                    if new_date:
                        updated = False
                        for e in self._all_events:
                            if (e.get('direction') == config.CREDIT
                                    and e.get('event_type') in ('income', 'salary')
                                    and e.get('status') in ('scheduled', 'pending')
                                    and (e.get('settlement_date') or date.min) > self.request_date):
                                e['settlement_date'] = new_date
                                e['event_date'] = new_date
                                updated = True
                                break
                        if not updated:
                            self._salary_next_date_override = new_date

                elif atype == 'salary_first':
                    amount = amd.get('amount')
                    eff_date = amd.get('effective_date')
                    if amount is not None:
                        self._first_salary_amount = amount
                        self._first_salary_date = eff_date
                        updated = False
                        for e in self._all_events:
                            if (e.get('direction') == config.CREDIT
                                    and e.get('event_type') in ('income', 'salary')
                                    and e.get('status') in ('scheduled', 'pending')
                                    and (e.get('settlement_date') or date.min) > self.request_date):
                                if amount is not None:
                                    e['amount_home'] = amount
                                if eff_date:
                                    e['settlement_date'] = eff_date
                                    e['event_date'] = eff_date
                                updated = True
                                break
                        if not updated and eff_date and amount:
                            self._all_events.append({
                                'event_id': '_msg_salary_first',
                                'user_id': self.user_id,
                                'event_type': 'income',
                                'category': 'salary',
                                'direction': config.CREDIT,
                                'amount_home': amount,
                                'currency': self.home_currency,
                                'event_date': eff_date,
                                'settlement_date': eff_date,
                                'status': 'scheduled',
                                'flexibility': 'fixed',
                                '_msg_injected': True,
                            })

                elif atype == 'salary_change':
                    amount = amd.get('amount')
                    eff_date = amd.get('effective_date')
                    if amount is not None:
                        self._salary_change_amount = amount
                        self._salary_change_date = eff_date
                        for e in self._all_events:
                            if (e.get('direction') == config.CREDIT
                                    and e.get('event_type') in ('income', 'salary')
                                    and e.get('status') in ('scheduled', 'pending')):
                                sdate = e.get('settlement_date') or date.min
                                if sdate > self.request_date:
                                    if eff_date is None or sdate >= eff_date:
                                        e['amount_home'] = amount

                elif atype == 'rent_increase':
                    pct = amd.get('percent', 12.0)
                    best = None
                    for e in self._all_events:
                        if (e.get('category') == 'rent'
                                and e.get('direction') == config.DEBIT
                                and e.get('status') == 'settled'):
                            if best is None or (e.get('settlement_date') or date.min) > (best.get('settlement_date') or date.min):
                                best = e
                    if best:
                        self._rent_increase_pct = pct
                        self._rent_base_amount = (best.get('amount_home') or 0) * (1 + pct / 100)
                        self._rent_day_of_month = _day_of_month(best.get('settlement_date') or self.request_date)

                elif atype == 'childcare_start':
                    eff_date = amd.get('effective_date')
                    self._childcare_start_date = eff_date

                elif atype == 'invoice_confirmed':
                    amount = amd.get('amount')
                    settle_date = amd.get('settlement_date')
                    if amount is not None and settle_date is not None:
                        self._all_events.append({
                            'event_id': '_msg_invoice',
                            'user_id': self.user_id,
                            'event_type': 'income',
                            'category': 'freelance',
                            'direction': config.CREDIT,
                            'amount_home': amount,
                            'currency': self.home_currency,
                            'event_date': settle_date,
                            'settlement_date': settle_date,
                            'status': 'scheduled',
                            'flexibility': 'fixed',
                            '_msg_injected': True,
                        })

    def _build_future_flows(self):
        """Populate self.future_debits, self.future_credits, self.recurring_debits."""
        rd = self.request_date
        horizon = rd + timedelta(days=config.FORECAST_DAYS + 5)

        self.settled_history = []
        self.future_debits = []
        self.future_credits = []
        self.recurring_debits = []

        settled_debits_by_cat = defaultdict(list)
        salary_settled = []

        for e in self._all_events:
            if e.get('_ignored'):
                continue
            status = e.get('status', '')
            direction = e.get('direction', '')
            sdate = e.get('settlement_date') or e.get('event_date')
            amount = e.get('amount_home')
            if amount is None or amount <= 0:
                continue
            etype = e.get('event_type', '')
            cat = e.get('category', '')

            if status == 'settled':
                self.settled_history.append(e)
                if direction == config.DEBIT and etype not in config.NON_RECURRING_EVENT_TYPES:
                    if cat not in config.NON_RECURRING_EVENT_TYPES:
                        settled_debits_by_cat[cat].append((
                            sdate, amount, e.get('event_id'),
                            e.get('flexibility', 'fixed'),
                            e.get('minimum_allowed_amount'),
                        ))
                if direction == config.CREDIT and etype in ('income', 'salary') and cat == 'salary':
                    raw_amt = e.get('amount_home')
                    if raw_amt is not None and raw_amt > 0:
                        salary_settled.append((sdate, raw_amt))
                elif direction == config.CREDIT and etype not in config.NON_RECURRING_EVENT_TYPES:
                    if not e.get('_ignored') and not e.get('_is_reimbursement'):
                        raw_amt = e.get('amount_home')
                        if raw_amt is not None and raw_amt > 0:
                            salary_settled.append((sdate, raw_amt))

            elif status in config.CONFIRMED_FUTURE_STATUSES:
                if e.get('_ignored'):
                    continue
                if direction == config.DEBIT:
                    self.future_debits.append((
                        sdate, amount, e.get('event_id'), cat,
                        e.get('flexibility', 'fixed'),
                        e.get('minimum_allowed_amount'),
                    ))
                elif direction == config.CREDIT and etype not in config.NON_RECURRING_EVENT_TYPES:
                    self.future_credits.append((sdate, amount, e.get('event_id'), cat))
                elif direction == config.CREDIT and e.get('_force_settled'):
                    self.future_credits.append((sdate, amount, e.get('event_id'), cat))

        self._detect_recurring(settled_debits_by_cat, rd, horizon)
        self._project_salary(salary_settled, rd, horizon)

    # Categories that appear multiple times per month but are discretionary.
    _DISCRETIONARY_CATS = frozenset({
        'shopping', 'dining', 'entertainment', 'transport',
        'personal_care', 'hobbies', 'travel', 'gifts',
    })

    # Categories that are reliably fixed monthly commitments even with a single history entry.
    _FIXED_MONTHLY_CATS = frozenset({
        'rent', 'utilities', 'debt_repayment', 'insurance',
        'cloud_storage', 'music_subscription', 'streaming', 'gym',
        'healthcare', 'education', 'childcare', 'internet', 'phone',
        'family_support', 'delivery_membership', 'housing',
    })

    def _detect_recurring(self, settled_debits_by_cat, start, end):
        """Detect recurring expense categories and project them forward.

        AGGREGATED MONTHLY (avg >1.8 entries/month):
          Uses a 3-month rolling median to remove single-month variance.
          Replays the most recent complete month's transaction timing,
          scaled to the 3-month median total, shifted +30/+60/+90 days.

        STRICT MONTHLY (one entry per ~month):
          Projects the median amount on the same day-of-month going forward.

        Settled events are already in current_available_balance — we only
        project future months, never the current partial month.
        """
        for cat, entries in settled_debits_by_cat.items():
            if not entries:
                continue

            entries_sorted = sorted(entries, key=lambda x: x[0] or date.min)

            cutoff = start - timedelta(days=180)
            recent = [
                (d, amt, eid, flex, min_amt)
                for d, amt, eid, flex, min_amt in entries_sorted
                if d and d >= cutoff
            ]
            if not recent:
                recent = entries_sorted[-4:]
            if not recent:
                continue

            last_date = recent[-1][0]
            last_flex = recent[-1][3] or 'fixed'
            last_min  = recent[-1][4]
            last_eid  = recent[-1][2]
            rent_amt  = getattr(self, '_rent_base_amount', None)

            months_seen = defaultdict(list)
            for d, amt, eid, flex, min_amt in recent:
                if d:
                    months_seen[(d.year, d.month)].append(amt)

            n_months      = len(months_seen)
            total_entries = len(recent)
            avg_per_month = total_entries / max(n_months, 1)

            if avg_per_month >= 1.8:
                if cat in self._DISCRETIONARY_CATS and n_months < 2:
                    continue

                current_ym = (start.year, start.month)

                complete_months = {
                    ym: vals
                    for ym, vals in months_seen.items()
                    if ym != current_ym
                }
                if not complete_months:
                    complete_months = dict(months_seen)

                sorted_yms   = sorted(complete_months.keys())
                last3_yms    = sorted_yms[-3:]
                last3_totals = sorted(sum(complete_months[ym]) for ym in last3_yms)
                median_monthly = last3_totals[len(last3_totals) // 2]

                if len(last3_yms) < 2:
                    all_totals    = [sum(v) for v in complete_months.values()]
                    avg_monthly   = sum(all_totals) / len(all_totals) if all_totals else median_monthly
                    median_monthly = 0.7 * median_monthly + 0.3 * avg_monthly

                if cat == 'rent' and rent_amt is not None:
                    median_monthly = rent_amt

                existing_dates = {d for d, _, _, c, _, _ in self.future_debits if c == cat}

                template_ym = None
                for ym in reversed(sorted_yms):
                    if complete_months.get(ym):
                        template_ym = ym
                        break

                template = []
                if template_ym:
                    template = [
                        (d, amt) for d, amt, eid, flex, min_amt in entries_sorted
                        if d and d.year == template_ym[0] and d.month == template_ym[1]
                    ]

                if not template:
                    window_start = last_date - timedelta(days=29)
                    template = [
                        (d, amt) for d, amt, eid, flex, min_amt in entries_sorted
                        if d and window_start <= d <= last_date
                    ]

                if not template:
                    dom = _day_of_month(last_date)
                    lym = (last_date.year, last_date.month)
                    fpm = date(lym[0] + 1, 1, 1) if lym[1] == 12 else date(lym[0], lym[1] + 1, 1)
                    proj_start = max(start, fpm)
                    for pd in _dates_for_day_of_month(dom, proj_start, end):
                        if pd not in existing_dates:
                            self.recurring_debits.append((
                                pd, median_monthly, last_eid + '_proj', cat, last_flex, last_min
                            ))
                    continue

                template_total = sum(a for _, a in template)
                scale = median_monthly / template_total if template_total > 0 else 1.0

                # High-frequency discretionary categories (transport, groceries, dining)
                # are the most uncertain in the 3rd projection cycle; use only 2 cycles
                # for categories with more than 3 entries per month to avoid over-projection.
                max_cycles = 3
                if avg_per_month > 3 and cat in self._DISCRETIONARY_CATS:
                    max_cycles = 2

                for cycle in range(1, max_cycles + 1):
                    shift = timedelta(days=30 * cycle)
                    for orig_d, amt in template:
                        proj_d = orig_d + shift
                        if start <= proj_d <= end:
                            self.recurring_debits.append((
                                proj_d, amt * scale,
                                last_eid + f'_r{cycle}', cat, last_flex, last_min
                            ))

            else:
                is_monthly = False
                if len(recent) >= 2:
                    gaps = []
                    for i in range(1, len(recent)):
                        d1, d2 = recent[i - 1][0], recent[i][0]
                        if d1 and d2:
                            gaps.append((d2 - d1).days)
                    is_monthly = bool(gaps) and all(25 <= g <= 37 for g in gaps)
                else:
                    is_monthly = cat in self._FIXED_MONTHLY_CATS

                if not is_monthly:
                    continue

                amounts    = sorted(amt for _, amt, _, _, _ in recent)
                median_amt = amounts[len(amounts) // 2]

                if cat == 'rent' and rent_amt is not None:
                    median_amt = rent_amt

                dom           = _day_of_month(last_date)
                next_expected = last_date + timedelta(days=28)
                proj_start    = max(start, next_expected)

                existing_dates = {d for d, _, _, c, _, _ in self.future_debits if c == cat}
                for pd in _dates_for_day_of_month(dom, proj_start, end):
                    if pd not in existing_dates:
                        self.recurring_debits.append((
                            pd, median_amt, last_eid + '_proj', cat, last_flex, last_min
                        ))

    def _project_salary(self, salary_settled, start, end):
        """Project recurring salary forward when not already in future_credits."""
        if getattr(self, '_salary_ended', False) and getattr(self, '_salary_remaining', None) is None:
            return

        # Suppress projection when the income message explicitly says the
        # upcoming payout is not yet settled (e.g. gig-platform pending payout).
        if getattr(self, '_suppress_salary_projection', False):
            return

        if not salary_settled:
            return

        salary_sorted = sorted(salary_settled, key=lambda x: x[0] or date.min)

        # Strip one-off mid-cycle entries (bonuses/commissions that aren't regular paydays).
        regular = [salary_sorted[0]]
        for i in range(1, len(salary_sorted)):
            prev_d, cur_d = salary_sorted[i - 1][0], salary_sorted[i][0]
            gap = (cur_d - prev_d).days if (cur_d and prev_d) else 999
            if gap >= 20:
                regular.append(salary_sorted[i])
            else:
                first_dom = _day_of_month(salary_sorted[0][0]) if salary_sorted[0][0] else 0
                cur_dom   = _day_of_month(cur_d) if cur_d else -1
                if cur_dom == first_dom:
                    regular.append(salary_sorted[i])

        if not regular:
            return

        if len(regular) >= 2:
            gaps = []
            for i in range(1, len(regular)):
                d1, d2 = regular[i - 1][0], regular[i][0]
                if d1 and d2:
                    gaps.append((d2 - d1).days)
            is_monthly = bool(gaps) and all(20 <= g <= 42 for g in gaps)
        else:
            is_monthly = True

        # If salary_resume was triggered, we always project from the resume date
        # even if the history has irregular gaps (e.g. maternity/sick leave gaps).
        resume_amount = getattr(self, '_salary_resume_amount', None)
        resume_date   = getattr(self, '_salary_resume_date', None)
        if resume_amount is not None and resume_date is not None:
            is_monthly = True

        if not is_monthly:
            return

        amounts = sorted(s[1] for s in regular[-3:] if s[1] is not None)
        if not amounts:
            return
        base_amount = amounts[len(amounts) // 2]

        if getattr(self, '_salary_change_amount', None) is not None:
            base_amount = self._salary_change_amount
        elif getattr(self, '_salary_remaining', None) is not None:
            base_amount = self._salary_remaining

        last_payday = regular[-1][0]
        dom = _day_of_month(last_payday)

        next_date_override = getattr(self, '_salary_next_date_override', None)

        next_expected = last_payday + timedelta(days=28)
        proj_start    = max(start, next_expected)
        projected_dates = _dates_for_day_of_month(dom, proj_start, end)

        if next_date_override and start < next_date_override <= end:
            override_ym = (next_date_override.year, next_date_override.month)
            projected_dates = [d for d in projected_dates
                               if (d.year, d.month) != override_ym]
            projected_dates = [next_date_override] + projected_dates
            projected_dates.sort()

        existing_credit_dates = {
            d for d, _, _, cat in self.future_credits
            if cat in ('salary', 'income')
        }

        temp_amount = getattr(self, '_salary_temp_amount', None)
        used_temp   = False

        for pd in projected_dates:
            if pd in existing_credit_dates:
                continue
            if temp_amount is not None and not used_temp:
                amt = temp_amount
                used_temp = True
            elif resume_date and pd >= resume_date and resume_amount:
                amt = resume_amount
            else:
                amt = base_amount
            self.future_credits.append((pd, amt, '_proj_salary', 'salary'))

    def all_future_debits(self, stop_events=None, reduce_events=None):
        """Return combined future debits (confirmed + recurring) with optional spending changes.

        stop_events   : set of event_id prefixes to stop (amount -> 0)
        reduce_events : dict of event_id_prefix -> new_amount
        """
        stop_events = stop_events or set()
        reduce_events = reduce_events or {}

        result = []
        seen = set()
        for item in list(self.future_debits) + list(self.recurring_debits):
            d, amt, eid, cat, flex, min_amt = item
            if d is None:
                continue
            key = (d, eid)
            if key in seen:
                continue
            seen.add(key)
            base_eid = eid.replace('_proj', '')
            if base_eid in stop_events or eid in stop_events:
                amt = 0.0
            elif base_eid in reduce_events:
                amt = reduce_events[base_eid]
            elif eid in reduce_events:
                amt = reduce_events[eid]
            result.append((d, amt, eid, cat))
        return sorted(result, key=lambda x: x[0])

    def all_future_credits(self):
        """Return sorted future credits."""
        return sorted(
            [(d, amt, eid, cat) for d, amt, eid, cat in self.future_credits if d is not None],
            key=lambda x: x[0],
        )

    def flexible_recurring_debits(self):
        """Return recurring debits that are flexible (stoppable or reducible)."""
        result = []
        protected = self.profile.get('expense_categories_to_protect', set())
        willing_stop = self.profile.get('expense_categories_user_is_willing_to_stop', set())
        willing_reduce = self.profile.get('expense_categories_user_is_willing_to_reduce', set())
        for item in self.recurring_debits:
            d, amt, eid, cat, flex, min_amt = item
            if cat in protected:
                continue
            if flex in config.FLEXIBLE_STOPPABLE and cat in willing_stop:
                result.append(item)
            elif flex in config.FLEXIBLE_REDUCIBLE and cat in willing_reduce:
                result.append(item)
        return result
