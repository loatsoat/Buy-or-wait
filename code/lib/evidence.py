"""Resolve blank ledger amounts from their linked document images, failing closed.

Per image: identity check -> cache (verified by sha256) -> model investigation ->
deterministic validation. Anything that does not pass stays unresolved; a blank
amount is never treated as zero.
"""
import hashlib
import json
import math
import os

from . import config, investigator

CACHE_PATH = os.path.join(config.CODE_DIR, "evidence_cache.json")


class EvidenceError(Exception):
    """Evidence a decision depends on is missing, malformed or fails an identity check."""


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def finding_problem(finding, event):
    """Return why a finding is unusable, or None when it passes every fixed rule."""
    amount = finding.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        return "amount is not a positive number"
    if finding.get("currency") != event["currency"]:
        return f"currency {finding.get('currency')!r} does not match the ledger currency {event['currency']!r}"
    if not str(finding.get("evidence_label", "")).strip():
        return "no document label quoted for the amount"
    return None


def _identity_problem(event, image, dataset):
    if image is None:
        return "no linked document image"
    if image["user_id"] != event["user_id"]:
        return "linked image belongs to a different user"
    if dataset.request_user.get(image["request_id"], event["user_id"]) != event["user_id"]:
        return "linked image belongs to another user's request"
    if not os.path.exists(dataset.image_path(image["image_id"])):
        return "image file is missing"
    return None


def resolve_blank_amounts(dataset, client=None, refresh=False):
    """Fill event['amount'] for blank rows in place.

    Returns (unresolved {event_id: reason}, usage {calls, input_tokens, output_tokens, cached}).
    `client` is an Anthropic client, or None to run from the cache only.
    """
    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    unresolved = {}
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached": 0}

    for events in dataset.events_by_user.values():
        for event in events:
            if event["amount"] is not None:
                continue
            event_id = event["event_id"]
            image = dataset.images_by_event.get(event_id)
            problem = _identity_problem(event, image, dataset)
            if problem:
                unresolved[event_id] = problem
                continue
            image_id = image["image_id"]
            path = dataset.image_path(image_id)
            digest = _sha256(path)

            entry = cache.get(image_id)
            if refresh or not entry or entry["sha256"] != digest or entry["event_id"] != event_id:
                if client is None:
                    unresolved[event_id] = "no cached finding for this image and no model credentials"
                    continue
                finding, problem, spent = investigator.investigate(
                    client,
                    {k: event[k] for k in ("event_id", "event_type", "category", "direction",
                                           "description", "currency", "status")}
                    | {"event_date": str(event["event_date"]), "settlement_date": str(event["settlement_date"])},
                    path,
                    [e["amount"] for e in events
                     if e["category"] == event["category"] and e["status"] == "settled" and e["amount"]][-6:],
                    [m["message_text"] for m in dataset.messages_by_user.get(event["user_id"], [])
                     if m["related_event_id"] == event_id],
                )
                for key in ("calls", "input_tokens", "output_tokens"):
                    usage[key] += spent[key]
                if problem:
                    unresolved[event_id] = problem
                    continue
                entry = {"sha256": digest, "event_id": event_id, "source": investigator.MODEL, **finding, **spent}
            else:
                usage["cached"] += 1

            problem = finding_problem(entry, event)
            if problem:
                unresolved[event_id] = problem
                continue
            cache[image_id] = entry
            event["amount"] = float(entry["amount"])

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(cache.items())), f, indent=2, ensure_ascii=False)
        f.write("\n")
    return unresolved, usage
