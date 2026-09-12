"""Template-based decision_explanation generator.

Uses only already-computed numbers — never invents unsupported facts.
"""

from datetime import date


def _fmt_amount(amount, currency):
    """Format an amount with currency symbol."""
    if amount is None:
        return 'N/A'
    if currency in ('IDR', 'INR'):
        # Indian/Indonesian style with comma separators
        s = f'{amount:,.0f}'
    else:
        s = f'{amount:,.2f}'
    return f'{currency} {s}'


def _fmt_date(d):
    if d is None:
        return 'N/A'
    return d.strftime('%d %B %Y')


def generate_explanation(
    method,
    request,
    profile,
    astp,
    efpd,
    plan_schedule,
    spending_changes_needed,
    min_balance,
):
    cur = profile.get('home_currency', '')
    requested = request.get('requested_amount', 0)
    deadline = request.get('desired_completion_date')
    min_bal_fmt = _fmt_amount(min_balance, cur)
    req_fmt = _fmt_amount(requested, cur)

    sc_desc = ''
    if spending_changes_needed:
        actions = []
        for ch in spending_changes_needed:
            if ch['action'] == 'stop':
                actions.append(f"stop event {ch['event_id']}")
            else:
                actions.append(f"reduce event {ch['event_id']} to {_fmt_amount(ch['new_amount'], cur)}")
        sc_desc = ', '.join(actions)

    if method == 'full_payment':
        pay_date, pay_amt = plan_schedule[0] if plan_schedule else (request.get('request_date'), requested)
        if spending_changes_needed:
            return (f"{sc_desc.capitalize()}, then pay {req_fmt} on {_fmt_date(pay_date)}. "
                    f"This keeps the {min_bal_fmt} minimum protected.")
        elif pay_date == request.get('request_date'):
            return (f"Pay {req_fmt} today. "
                    f"This leaves at least {min_bal_fmt} available over the next 90 days.")
        else:
            return (f"Pay {req_fmt} in full on {_fmt_date(pay_date)}. "
                    f"This keeps the {min_bal_fmt} minimum protected.")

    elif method == 'installments':
        n = len(plan_schedule)
        if plan_schedule:
            pay_amt = plan_schedule[0][1]
            first_date = plan_schedule[0][0]
            pay_fmt = _fmt_amount(pay_amt, cur)
            if spending_changes_needed:
                return (f"{sc_desc.capitalize()}, then use {n} installments of {pay_fmt}, "
                        f"starting {_fmt_date(first_date)}. "
                        f"This keeps the {min_bal_fmt} minimum protected.")
            return (f"Use {n} installments of {pay_fmt}, starting {_fmt_date(first_date)}. "
                    f"This leaves at least {min_bal_fmt} available.")
        return f"Pay in {n} installments. This keeps the {min_bal_fmt} minimum protected."

    elif method == 'partial_payment':
        if len(plan_schedule) >= 2:
            d1, a1 = plan_schedule[0]
            d2, a2 = plan_schedule[1]
            return (f"Pay {_fmt_amount(a1, cur)} today, then {_fmt_amount(a2, cur)} on "
                    f"{_fmt_date(d2)}. "
                    f"This keeps the {min_bal_fmt} minimum protected throughout.")
        return f"Pay in two parts. This keeps the {min_bal_fmt} minimum protected."

    elif method == 'wait':
        wait_date = plan_schedule[0][0] if plan_schedule else efpd
        return (f"Pay {req_fmt} in full on {_fmt_date(wait_date)}. "
                f"Paying earlier would take the balance below the {min_bal_fmt} minimum.")

    elif method == 'not_recommended':
        if deadline:
            return (f"Do not make this payment by {_fmt_date(deadline)}. "
                    f"None of the available options keeps the {min_bal_fmt} minimum protected.")
        return (f"This payment is not recommended. "
                f"None of the available options keeps the {min_bal_fmt} minimum protected.")

    return "No safe payment plan found within the forecast period."
