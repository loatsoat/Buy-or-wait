"""Template-based decision_explanation, built only from numbers the pipeline computed."""


def _money(amount, currency):
    text = f'{amount:,.2f}'
    return f"{currency} {text[:-3] if text.endswith('.00') else text}"


def _day(d):
    return f'{d.day} {d:%B %Y}'


def _change_phrase(change, currency, state):
    name = 'the ' + state.series[change['series']]['description'].lower()
    if change['action'] == 'stop':
        return f'stop {name}'
    return f"reduce {name} to {_money(change['new_amount'], currency)}"


def generate_explanation(method, request, profile, schedule, changes, state):
    cur = profile['home_currency']
    minimum = _money(state.min_balance, cur)
    requested = _money(request['requested_amount'], cur)

    if method == 'not_recommended':
        return (f"Do not make this payment by {_day(request['desired_completion_date'])}. "
                f'None of the available options keeps the {minimum} minimum protected.')
    if method == 'wait':
        return (f'Pay {requested} in full on {_day(schedule[0][0])}. '
                f'Paying earlier would take the balance below the {minimum} minimum.')
    if method == 'partial_payment':
        return (f'Pay {_money(schedule[0][1], cur)} today and the remaining {_money(schedule[1][1], cur)} '
                f'on {_day(schedule[1][0])}. This completes the full request and keeps the {minimum} '
                'minimum protected.')

    if method == 'installments':
        action = (f'use {len(schedule)} installments of {_money(schedule[0][1], cur)}, '
                  f'starting {_day(schedule[0][0])}')
        tail = f'This leaves at least {minimum} available.'
    else:
        action = f'pay {requested} today'
        tail = f'This leaves at least {minimum} available' + ('.' if changes else ' over the next 90 days.')
    if changes:
        action = ' and '.join(_change_phrase(c, cur, state) for c in changes) + ', then ' + action
    return f'{action[0].upper()}{action[1:]}. {tail}'
