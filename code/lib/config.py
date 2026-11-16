"""Shared constants for the Buy-or-Wait pipeline."""
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CODE_DIR = os.path.join(REPO_ROOT, "code")
DATASET_DIR = os.path.join(REPO_ROOT, "dataset")
MEDIA_IMAGES_DIR = os.path.join(DATASET_DIR, "media", "images")

FORECAST_DAYS = 90

# Not yet settled but will move cash. failed, cancelled and unrealized rows never do.
CONFIRMED_FUTURE_STATUSES = {"pending", "scheduled"}

# direction values
DEBIT = "debit"
CREDIT = "credit"

# flexibility values that allow spending changes
FLEXIBLE_STOPPABLE = {"stoppable", "reducible_or_stoppable"}
FLEXIBLE_REDUCIBLE = {"reducible", "reducible_or_stoppable"}

MAX_SPENDING_CHANGES = 3

# Categories that are one-off/uncertain and should never be projected as recurring.
NON_RECURRING_EVENT_TYPES = {
    "refund",
    "investment_purchase",
    "investment_sale",
    "investment_valuation",
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
