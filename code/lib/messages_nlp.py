"""Parse financial messages into structured amendments.

Each message can carry one or more of these intents:
  - salary_change   : new confirmed salary amount (and optional effective date)
  - salary_date     : salary date rescheduled
  - salary_ended    : employment ended, no more salary
  - salary_first    : first salary confirmed (amount + date)
  - salary_temp     : temporary reduced pay for next payroll only
  - salary_resume   : salary resumes after temporary reduction (amount + date)
  - rent_increase   : monthly rent increases by 12% from next payment
  - ignore_credit   : a pending credit on a specific event should be ignored
                      (refund in transit, investment unrealized gain, prize not yet settled,
                       internal transfer, pending freelance payout)
  - invoice_confirmed: a freelance/invoice credit is now confirmed (settle on date)
  - childcare_start : new recurring childcare debit begins (same month as salary)
  - reimbursement   : a specific event is a one-off reimbursement, not recurring salary
  - failed_debit    : a specific event's debit failed and will be re-attempted (keep as pending)
  - card_dispute    : extra charge under investigation, reversal NOT yet posted
  - internal_transfer: matching debit+credit are an internal transfer (net zero)
"""

import re
from datetime import date

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')
_AMOUNT_RE = re.compile(
    r'(?:IDR|ZAR|INR|USD|EUR)\s*([\d,]+(?:\.\d+)?)'
    r'|(?:[\d,]+(?:\.\d+)?)\s*(?:IDR|ZAR|INR|USD|EUR)'
    r'|([\d,]+(?:\.\d+)?)',
    re.I,
)


def _parse_amount(text):
    """Return the first plausible monetary amount found in text, or None."""
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(1) or m.group(2) or m.group(3)
        if raw:
            try:
                return float(raw.replace(',', ''))
            except ValueError:
                pass
    return None


def _parse_date(text):
    m = _DATE_RE.search(text)
    if m:
        try:
            y, mo, d = m.group(1).split('-')
            return date(int(y), int(mo), int(d))
        except ValueError:
            pass
    return None


def _parse_percent(text):
    m = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Pattern lists  (order = priority, first match wins per category)
# ---------------------------------------------------------------------------

# Signals that this message is about a credit/event to IGNORE (not yet real cash)
_IGNORE_PATTERNS = [
    # pending refunds
    re.compile(r'refund.{0,60}initiated.{0,60}not.{0,30}(reached|credited)', re.I | re.S),
    re.compile(r'(refund|reimburs).{0,60}still processing', re.I | re.S),
    # prize not settled
    re.compile(r'prize.{0,60}(not.{0,30}credited|still.{0,30}(processing|pending))', re.I | re.S),
    # pending payout (gig platforms)
    re.compile(r'(payout|earnings).{0,60}still pending', re.I | re.S),
    re.compile(r'balance.{0,60}not.{0,30}withdrawable', re.I | re.S),
    # investment unrealized
    re.compile(r'(market value|displayed value).{0,60}(increased|decreased|fallen).{0,120}no.{0,30}(units|proceeds|cash)', re.I | re.S),
    re.compile(r'no.{0,30}(units|proceeds).{0,30}(sold|generated)', re.I | re.S),
    # internal transfer
    re.compile(r'(debit.{0,40}credit|transfer).{0,60}(your.{0,20}two|same.{0,20}account)', re.I | re.S),
    re.compile(r'both.{0,20}accounts.{0,20}(same.{0,20}account|registered)', re.I | re.S),
    # foreign-currency refund rate unknown
    re.compile(r'foreign.{0,20}currency.{0,40}refund.{0,60}still processing', re.I | re.S),
    re.compile(r'home.{0,20}currency.{0,20}credit.{0,40}may change', re.I | re.S),
    # scam prize (pay a fee to receive)
    re.compile(r'pay.{0,30}(release|processing).{0,20}charge', re.I | re.S),
    # bonus/commission not confirmed
    re.compile(r'(quarterly bonus|commission).{0,60}(not.{0,30}approved|still.{0,30}(pending|subject))', re.I | re.S),
    # invoice not yet confirmed
    re.compile(r'(other|remaining).{0,30}(invoices|submitted).{0,60}(await|pending)', re.I | re.S),
]

# Prize/investment SETTLED (cash is real)
_SETTLED_PATTERNS = [
    re.compile(r'prize.{0,60}proceeds.{0,60}(reached|settled|credited)', re.I | re.S),
    re.compile(r'(investment|sale).{0,60}proceeds.{0,60}(settled|reached)', re.I | re.S),
    re.compile(r'sale order.{0,40}complete', re.I | re.S),
]

# Card dispute – extra charge under investigation, reversal NOT yet posted
_CARD_DISPUTE_PATTERNS = [
    re.compile(r'extra.{0,30}card.{0,30}charge.{0,60}(investigat|disputed)', re.I | re.S),
    re.compile(r'(reversal|dispute).{0,60}not.{0,30}(posted|credited)', re.I | re.S),
    re.compile(r'dispute.{0,30}(is )?open.{0,30}(no|and no).{0,30}reversal', re.I | re.S),
]

# Failed debit to be re-attempted
_FAILED_DEBIT_PATTERNS = [
    re.compile(r'previous.{0,30}debit.{0,30}(attempt|attempt|failed)', re.I | re.S),
    re.compile(r'bill.{0,40}(still outstanding|another debit)', re.I | re.S),
]

# Internal transfer (debit+credit same owner)
_INTERNAL_TRANSFER_PATTERNS = [
    re.compile(r'(debit.{0,40}credit|both.{0,20}entries).{0,60}(transfer|same.{0,20}account)', re.I | re.S),
    re.compile(r'both.{0,20}accounts.{0,20}registered', re.I | re.S),
]

# Salary ended
_SALARY_ENDED_PATTERNS = [
    re.compile(r'(employment|contract|seasonal contract).{0,40}ended', re.I | re.S),
    re.compile(r'no.{0,40}(regular|off.?season|income|renewal).{0,40}(scheduled|confirmed)', re.I | re.S),
    re.compile(r'no.{0,30}regular salary.{0,60}after.{0,30}(final|settlement)', re.I | re.S),
    re.compile(r'(household employment record|sumber pendapatan).{0,60}(ended|berakhir)', re.I | re.S),
]

# Temporary pay (for next payroll only, then reverts)
_SALARY_TEMP_PATTERNS = [
    re.compile(r'temporary.{0,30}(monthly )?pay.{0,40}(is|was|reduced to)\s+([\w\s,\.]+?)[\.\,]', re.I | re.S),
    re.compile(r'(reduced|next salary).{0,60}(unpaid leave|sementara)', re.I | re.S),
    re.compile(r'gaji.{0,30}sementara', re.I | re.S),
]

# Salary resume after temp reduction
_SALARY_RESUME_PATTERNS = [
    re.compile(r'(regular salary|salary).{0,40}(resumes?|resumed?)\s+on\s+(\d{4}-\d{2}-\d{2})', re.I),
    re.compile(r'regular salary.{0,40}resumes?', re.I | re.S),
]

# Salary date rescheduled
_SALARY_DATE_PATTERNS = [
    re.compile(r'(confirmed salary|salary).{0,60}(now expected on|expected on)\s+(\d{4}-\d{2}-\d{2})', re.I),
    re.compile(r'revised date', re.I),
]

# First salary
_FIRST_SALARY_PATTERNS = [
    re.compile(r'(first salary|gaji pertama).{0,60}(will be|adalah|sebesar|of)\s+([\w\s,\.]+?)[\.\,]', re.I | re.S),
    re.compile(r'first salary.{0,60}(confirmed|dikonfirmasi)', re.I | re.S),
]

# Salary increase / new amount confirmed
_SALARY_CHANGE_PATTERNS = [
    re.compile(r'(monthly salary|base salary|confirmed salary|gaji bulanan|gaji pokok).{0,60}(increased to|naik menjadi|is|adalah)\s+([\w\s,\.]+?)[\.\,]', re.I | re.S),
    re.compile(r'salary.{0,30}(increased|naik)', re.I | re.S),
    re.compile(r'regular salary.{0,30}(for the next payroll|next payroll).{0,60}(is|adalah)\s+([\w\s,\.]+?)[\.\,]', re.I | re.S),
    re.compile(r'confirmed.{0,30}(base )?salary.{0,60}(is|sebesar|adalah)\s+([\w\s,\.]+?)[\.\,]', re.I | re.S),
    # Salary in foreign currency for next cycle
    re.compile(r'salary.{0,30}(of|sebesar)\s+([\w\s,\.]+?)\s+(is )?confirmed', re.I | re.S),
]

# Rent increase by %
_RENT_INCREASE_PATTERNS = [
    re.compile(r'(monthly rent|lease).{0,40}increases?.{0,30}(by|sebesar)\s*(\d+(?:\.\d+)?)\s*%', re.I | re.S),
    re.compile(r'rent.{0,30}(increases?|naik).{0,30}(\d+(?:\.\d+)?)\s*%', re.I | re.S),
]

# Childcare starts
_CHILDCARE_PATTERNS = [
    re.compile(r'new.{0,30}recurring.{0,30}childcare.{0,40}(begins|starts?)', re.I | re.S),
    re.compile(r'childcare.{0,30}(begins|payment.{0,20}begins)', re.I | re.S),
]

# Reimbursement (one-off, not recurring salary)
_REIMBURSEMENT_PATTERNS = [
    re.compile(r'(reimbursement|penggantian).{0,60}(work expense|biaya kerja)', re.I | re.S),
    re.compile(r'(latest employer credit|kredit terbaru).{0,60}(reimbursement|penggantian)', re.I | re.S),
    re.compile(r'not.{0,20}regular salary', re.I | re.S),
]

# Invoice/freelance confirmed
_INVOICE_CONFIRMED_PATTERNS = [
    re.compile(r'(client approved|klien menyetujui).{0,60}invoice.{0,30}(payment|pembayaran)', re.I | re.S),
    re.compile(r'settlement.{0,30}expected.{0,30}on\s+(\d{4}-\d{2}-\d{2})', re.I),
]


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_message(msg):
    """Return a list of amendment dicts for a single message row.

    Each dict has a 'type' key plus type-specific fields.
    """
    text = msg.get('message_text', '') or ''
    related_event_id = msg.get('related_event_id', '') or ''
    amendments = []

    # ------------------------------------------------------------------
    # Reimbursement (must come before generic salary logic)
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _REIMBURSEMENT_PATTERNS):
        amendments.append({'type': 'reimbursement', 'event_id': related_event_id or None})
        return amendments  # nothing else to parse for this message

    # ------------------------------------------------------------------
    # Internal transfer (net zero – ignore both legs)
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _INTERNAL_TRANSFER_PATTERNS):
        amendments.append({'type': 'internal_transfer', 'event_id': related_event_id or None})
        return amendments

    # ------------------------------------------------------------------
    # Card dispute – keep the charge as a debit but no reversal yet
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _CARD_DISPUTE_PATTERNS):
        amendments.append({'type': 'card_dispute', 'event_id': related_event_id or None})
        return amendments

    # ------------------------------------------------------------------
    # Failed debit – bill still outstanding
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _FAILED_DEBIT_PATTERNS):
        amendments.append({'type': 'failed_debit', 'event_id': related_event_id or None})
        return amendments

    # ------------------------------------------------------------------
    # Prize / investment proceeds SETTLED
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _SETTLED_PATTERNS):
        amendments.append({'type': 'settled_credit', 'event_id': related_event_id or None})
        return amendments

    # ------------------------------------------------------------------
    # Credits to IGNORE (pending refund, unrealized gain, etc.)
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _IGNORE_PATTERNS):
        amendments.append({'type': 'ignore_credit', 'event_id': related_event_id or None})
        return amendments

    # ------------------------------------------------------------------
    # Salary ended
    # ------------------------------------------------------------------
    if any(p.search(text) for p in _SALARY_ENDED_PATTERNS):
        # "One household employment record has ended. Remaining confirmed salary is X."
        remaining = _parse_amount(text)
        amendments.append({
            'type': 'salary_ended',
            'remaining_amount': remaining,
        })
        return amendments

    # ------------------------------------------------------------------
    # Salary resumes after temp reduction
    # ------------------------------------------------------------------
    for p in _SALARY_RESUME_PATTERNS:
        m = p.search(text)
        if m:
            amount = _parse_amount(text)
            eff_date = _parse_date(text)
            amendments.append({
                'type': 'salary_resume',
                'amount': amount,
                'effective_date': eff_date,
            })
            # may also have childcare starting
            break

    # ------------------------------------------------------------------
    # Salary date rescheduled
    # ------------------------------------------------------------------
    if not amendments:
        for p in _SALARY_DATE_PATTERNS:
            if p.search(text):
                new_date = _parse_date(text)
                amendments.append({
                    'type': 'salary_date',
                    'new_date': new_date,
                })
                break

    # ------------------------------------------------------------------
    # Temporary pay
    # ------------------------------------------------------------------
    if not amendments:
        for p in _SALARY_TEMP_PATTERNS:
            if p.search(text):
                amount = _parse_amount(text)
                amendments.append({
                    'type': 'salary_temp',
                    'amount': amount,
                })
                break

    # ------------------------------------------------------------------
    # First salary
    # ------------------------------------------------------------------
    if not amendments:
        for p in _FIRST_SALARY_PATTERNS:
            if p.search(text):
                amount = _parse_amount(text)
                eff_date = _parse_date(text)
                amendments.append({
                    'type': 'salary_first',
                    'amount': amount,
                    'effective_date': eff_date,
                })
                break

    # ------------------------------------------------------------------
    # Salary change / confirmed
    # ------------------------------------------------------------------
    if not amendments:
        for p in _SALARY_CHANGE_PATTERNS:
            if p.search(text):
                amount = _parse_amount(text)
                eff_date = _parse_date(text)
                amendments.append({
                    'type': 'salary_change',
                    'amount': amount,
                    'effective_date': eff_date,
                })
                break

    # ------------------------------------------------------------------
    # Rent increase
    # ------------------------------------------------------------------
    for p in _RENT_INCREASE_PATTERNS:
        m = p.search(text)
        if m:
            pct = _parse_percent(text)
            amendments.append({
                'type': 'rent_increase',
                'percent': pct or 12.0,
            })
            break

    # ------------------------------------------------------------------
    # Childcare starts (often accompanies salary_resume)
    # ------------------------------------------------------------------
    for p in _CHILDCARE_PATTERNS:
        if p.search(text):
            eff_date = _parse_date(text)
            amendments.append({
                'type': 'childcare_start',
                'effective_date': eff_date,
            })
            break

    # ------------------------------------------------------------------
    # Invoice confirmed
    # ------------------------------------------------------------------
    if not amendments:
        for p in _INVOICE_CONFIRMED_PATTERNS:
            if p.search(text):
                amount = _parse_amount(text)
                settle_date = _parse_date(text)
                amendments.append({
                    'type': 'invoice_confirmed',
                    'amount': amount,
                    'settlement_date': settle_date,
                })
                break

    return amendments
