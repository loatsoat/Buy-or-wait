"""Fixed output rules from problem_statement.md, checked before output.csv is written.

main.py refuses to write a file that breaks any rule; evaluation/main.py reports the
same rules. Every row is checked against its own request, so a result attached to the
wrong request is caught here.
"""
import re
from datetime import date

from . import config

_STATUS_METHODS = {
    "affordable_now": {"full_payment"},
    "affordable_with_plan": {"full_payment", "partial_payment", "installments"},
    "affordable_later": {"wait"},
    "not_affordable": {"not_recommended"},
}
_CHANGE_RE = re.compile(r"(stop):(\w+)$|(reduce_to):(\w+):(\d+(?:\.\d+)?)$")
_CENT = 0.011


def _parse_plan(text):
    if text == "none":
        return []
    plan = []
    for part in text.split("|"):
        day, amount = part.split(":")
        plan.append((date.fromisoformat(day), float(amount)))
    return plan


def row_problems(row, request, dataset):
    """Return the rules this output row breaks for this request (empty list = valid)."""
    problems = []
    rd, requested = request["request_date"], request["requested_amount"]
    status, method = row["affordability_status"], row["recommended_payment_method"]

    if row["request_id"] != request["request_id"]:
        return [f"row is for {row['request_id']}"]
    if method not in _STATUS_METHODS.get(status, ()):
        problems.append(f"status {status!r} cannot go with method {method!r}")
    try:
        astp = float(row["amount_safe_to_pay"])
        efpd = date.fromisoformat(row["earliest_date_for_full_payment"]) if row["earliest_date_for_full_payment"] else None
        plan = _parse_plan(row["payment_plan"])
    except ValueError as exc:
        return problems + [f"unparseable field: {exc}"]

    if not 0 <= astp <= requested + _CENT:
        problems.append(f"amount_safe_to_pay {astp} is outside 0..{requested}")
    if efpd and efpd < rd:
        problems.append("earliest_date_for_full_payment is before request_date")
    if status == "affordable_now" and efpd != rd:
        problems.append("affordable_now needs earliest_date_for_full_payment == request_date")
    if bool(plan) == (method == "not_recommended"):
        problems.append("payment_plan must be none exactly when the method is not_recommended")
    if [d for d, _ in plan] != sorted(d for d, _ in plan) or any(d < rd or a <= 0 for d, a in plan):
        problems.append("payment_plan is not chronological from request_date with positive amounts")

    if method == "partial_payment":
        ok = (request["allows_partial_payment"] and len(plan) == 2 and 0 < astp < requested
              and efpd and efpd <= request["desired_completion_date"]
              and plan[0][0] == rd and abs(plan[0][1] - astp) < _CENT
              and plan[1][0] == efpd and abs(plan[0][1] + plan[1][1] - requested) < _CENT)
        if not ok:
            problems.append("partial_payment breaks the two-payment rule")
    if method == "installments":
        options = [o for o in dataset.payment_options_by_request.get(request["request_id"], [])
                   if o["payment_method"] == "installments"]
        if not any(_matches_option(plan, o) for o in options):
            problems.append("installment plan does not match a supplied payment option")

    problems += _change_problems(row["spending_changes_needed"], status, request, dataset)
    if not row["decision_explanation"].strip():
        problems.append("decision_explanation is empty")
    return problems


def _matches_option(plan, option):
    return (len(plan) == option["number_of_payments"]
            and plan[0][0] == option["first_payment_date"]
            and all(abs(a - option["payment_amount"]) < _CENT for _, a in plan))


def _change_problems(text, status, request, dataset):
    if text == "none":
        return []
    problems = []
    if status != "affordable_with_plan":
        problems.append("spending changes require status affordable_with_plan")
    parts = text.split("|")
    if len(parts) > config.MAX_SPENDING_CHANGES:
        problems.append("more than three spending changes")
    events = {e["event_id"]: e for e in dataset.events_by_user.get(request["user_id"], [])}
    profile = dataset.profiles[request["user_id"]]
    seen = set()
    for part in parts:
        m = _CHANGE_RE.match(part)
        if not m:
            problems.append(f"malformed spending change {part!r}")
            continue
        action, event_id = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        event = events.get(event_id)
        if event is None:
            problems.append(f"{event_id} is not an event of {request['user_id']}")
            continue
        if event_id in seen:
            problems.append(f"{event_id} is changed twice")
        seen.add(event_id)
        if action == "stop":
            allowed = (event["flexibility"] in config.FLEXIBLE_STOPPABLE
                       and event["category"] in profile["expense_categories_user_is_willing_to_stop"])
        else:
            allowed = (event["flexibility"] in config.FLEXIBLE_REDUCIBLE
                       and event["category"] in profile["expense_categories_user_is_willing_to_reduce"]
                       and float(m.group(5)) >= (event["minimum_allowed_amount"] or 0) - _CENT)
        if not allowed or event["category"] in profile["expense_categories_to_protect"]:
            problems.append(f"{action} is not permitted for {event_id}")
    return problems


def output_problems(rows, requests, dataset):
    """Return every problem as 'request_id: text'. Rows must be in request order."""
    if [r["request_id"] for r in rows] != [q["request_id"] for q in requests]:
        return ["output rows are not the requests in their original order"]
    return [f"{q['request_id']}: {p}" for r, q in zip(rows, requests) for p in row_problems(r, q, dataset)]
