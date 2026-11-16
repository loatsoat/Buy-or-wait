"""Loads and lightly normalizes all dataset CSV files."""
import csv
import os
from datetime import datetime

from . import config


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_date(s):
    """'YYYY-MM-DD' or an ISO timestamp -> date; blank -> None; malformed -> ValueError."""
    s = (s or "").strip().split("T")[0]
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def parse_pipe_list(s):
    if not s:
        return []
    s = s.strip()
    if not s:
        return []
    return [x.strip() for x in s.split("|") if x.strip()]


def parse_float(s):
    if s is None:
        return None
    s = str(s).strip()
    if s == "":
        return None
    return float(s)


class Dataset:
    def __init__(self, dataset_dir=config.DATASET_DIR):
        def load(name):
            return _read_csv(os.path.join(dataset_dir, name))

        self.rates_raw = load("exchange_rates.csv")
        self.profiles = {p["user_id"]: self._norm_profile(p) for p in load("financial_profiles.csv")}

        self.events_by_user = {}
        event_user = {}
        for e in load("financial_events.csv"):
            self.events_by_user.setdefault(e["user_id"], []).append(self._norm_event(e))
            event_user[e["event_id"]] = e["user_id"]

        self.payment_options_by_request = {}
        for po in load("request_payment_options.csv"):
            self.payment_options_by_request.setdefault(po["request_id"], []).append(
                self._norm_payment_option(po)
            )

        # Row order of requests.csv is the output order; nothing downstream may re-sort it.
        self.requests = [self._norm_request(r) for r in load("requests.csv")]
        self.sample_requests = [self._norm_request(r) for r in load("sample_requests.csv")]
        self.request_user = {r["request_id"]: r["user_id"] for r in self.requests + self.sample_requests}

        # Evidence that names another user's event or request is discarded, never re-attributed.
        self.rejected_evidence = []
        self.messages_by_user = {}
        for m in load("messages.csv"):
            if (event_user.get(m["related_event_id"], m["user_id"]) != m["user_id"]
                    or self.request_user.get(m["request_id"], m["user_id"]) != m["user_id"]):
                self.rejected_evidence.append(m["message_id"])
                continue
            try:
                m["sent_at_date"] = parse_date(m["sent_at"])
            except ValueError:
                m["sent_at_date"] = None  # unknown send time: events.py will not use the message
            self.messages_by_user.setdefault(m["user_id"], []).append(m)

        self.images_by_event = {im["related_event_id"]: im for im in load("images.csv")}

    @staticmethod
    def _norm_profile(p):
        p2 = dict(p)
        p2["current_available_balance"] = parse_float(p["current_available_balance"])
        p2["minimum_balance_to_keep"] = parse_float(p["minimum_balance_to_keep"])
        p2["financial_priorities"] = parse_pipe_list(p.get("financial_priorities"))
        p2["expense_categories_to_protect"] = set(parse_pipe_list(p.get("expense_categories_to_protect")))
        p2["expense_categories_user_is_willing_to_reduce"] = set(
            parse_pipe_list(p.get("expense_categories_user_is_willing_to_reduce"))
        )
        p2["expense_categories_user_is_willing_to_stop"] = set(
            parse_pipe_list(p.get("expense_categories_user_is_willing_to_stop"))
        )
        p2["payment_methods_user_will_consider"] = set(
            parse_pipe_list(p.get("payment_methods_user_will_consider"))
        )
        mim = p.get("max_installment_months", "")
        p2["max_installment_months"] = int(float(mim)) if mim and mim.strip() else None
        return p2

    @staticmethod
    def _norm_event(e):
        e2 = dict(e)
        e2["amount"] = parse_float(e.get("amount"))
        e2["event_date"] = parse_date(e.get("event_date"))
        e2["settlement_date"] = parse_date(e.get("settlement_date")) or e2["event_date"]
        e2["minimum_allowed_amount"] = parse_float(e.get("minimum_allowed_amount"))
        return e2

    @staticmethod
    def _norm_payment_option(po):
        po2 = dict(po)
        po2["payment_amount"] = parse_float(po.get("payment_amount"))
        po2["number_of_payments"] = int(float(po["number_of_payments"])) if po.get("number_of_payments") else 1
        po2["first_payment_date"] = parse_date(po.get("first_payment_date"))
        po2["payment_frequency_days"] = (
            int(float(po["payment_frequency_days"])) if po.get("payment_frequency_days") else None
        )
        po2["financing_fee"] = parse_float(po.get("financing_fee")) or 0.0
        po2["total_payable_amount"] = parse_float(po.get("total_payable_amount"))
        return po2

    @staticmethod
    def _norm_request(r):
        r2 = dict(r)
        r2["request_date"] = parse_date(r.get("request_date"))
        r2["desired_completion_date"] = parse_date(r.get("desired_completion_date"))
        r2["requested_amount"] = parse_float(r.get("requested_amount"))
        r2["allows_partial_payment"] = str(r.get("allows_partial_payment")).strip().lower() == "true"
        return r2

    def image_path(self, image_id):
        return os.path.join(config.MEDIA_IMAGES_DIR, f"{image_id}.png")
