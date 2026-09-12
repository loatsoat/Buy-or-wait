"""Shared constants for the Buy-or-Wait pipeline."""
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATASET_DIR = os.path.join(REPO_ROOT, "dataset")
MEDIA_IMAGES_DIR = os.path.join(DATASET_DIR, "media", "images")

FORECAST_DAYS = 90

# financial_events.csv status handling
SETTLED_STATUSES = {"settled"}
CONFIRMED_FUTURE_STATUSES = {"pending", "scheduled"}
EXCLUDED_STATUSES = {"cancelled", "failed", "unrealized"}

# direction values
DEBIT = "debit"
CREDIT = "credit"
NON_CASH = "non_cash"

# flexibility values that allow spending changes
FLEXIBLE_STOPPABLE = {"stoppable", "reducible_or_stoppable"}
FLEXIBLE_REDUCIBLE = {"reducible", "reducible_or_stoppable"}

MAX_SPENDING_CHANGES = 3

# Categories that are one-off/uncertain in nature and should never be
# projected forward as recurring, even if they repeat by chance.
NON_RECURRING_EVENT_TYPES = {
    "refund",
    "investment_purchase",
    "investment_sale",
    "investment_valuation",
}

# Allowed output enums
AFFORDABILITY_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}
PAYMENT_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]
