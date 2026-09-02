"""
Account connection (mock Account Aggregator flow) + manual data entry.

Design principle carried over from the rest of the system: be explicit
about what's real and what's a stand-in. There is no actual bank
connection here -- this simulates the RBI-regulated Account Aggregator
(AA) consent flow (Setu / Finvu / OneMoney style) that a production
version would use: the user picks a provider, "approves" a scoped
consent, and an account appears as connected. No credentials are ever
collected -- that's the whole point of the AA model, and the mock
preserves that shape rather than faking a bank login form.

Manual entry is the other on-ramp: a user can add a transaction or
subscription by hand, which is appended to the same data files the
detector already reads -- so manually-entered data flows through
detection, scoring, finance, and growth exactly like generated data.
"""

import json
import os
import uuid
from datetime import datetime, date

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ACCOUNTS_PATH = os.path.join(DATA_DIR, "connected_accounts.json")
TRANSACTIONS_PATH = os.path.join(DATA_DIR, "transactions.json")
SUBSCRIPTIONS_PATH = os.path.join(DATA_DIR, "subscriptions.json")

# Mock catalog of AA-registered providers a user could "pick" -- mirrors
# the real-world picker (that's genuinely just choosing a bank/AA app).
AA_PROVIDERS = [
    {"provider_id": "hdfc", "name": "HDFC Bank", "via": "Finvu AA"},
    {"provider_id": "icici", "name": "ICICI Bank", "via": "OneMoney AA"},
    {"provider_id": "sbi", "name": "State Bank of India", "via": "Setu AA"},
    {"provider_id": "axis", "name": "Axis Bank", "via": "Finvu AA"},
    {"provider_id": "kotak", "name": "Kotak Mahindra Bank", "via": "Anumati AA"},
]


def _load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def list_connected_accounts():
    return _load_json(ACCOUNTS_PATH, [])


def connect_account(provider_id: str):
    """
    Simulates the AA consent flow completing successfully: user picked a
    provider, approved a scoped consent artifact, account now streams
    data. No credentials ever pass through this app -- that's the point
    of the AA model, mock or real.
    """
    provider = next((p for p in AA_PROVIDERS if p["provider_id"] == provider_id), None)
    if not provider:
        raise ValueError(f"Unknown provider_id: {provider_id}")

    accounts = list_connected_accounts()
    if any(a["provider_id"] == provider_id for a in accounts):
        return accounts  # already connected, no-op

    accounts.append({
        "account_id": f"acc_{uuid.uuid4().hex[:8]}",
        "provider_id": provider["provider_id"],
        "provider_name": provider["name"],
        "via": provider["via"],
        "connected_at": datetime.utcnow().isoformat(),
        "consent_scope": "transactions, balances -- 12 months, revocable any time",
    })
    _save_json(ACCOUNTS_PATH, accounts)
    return accounts


def disconnect_account(account_id: str):
    accounts = [a for a in list_connected_accounts() if a["account_id"] != account_id]
    _save_json(ACCOUNTS_PATH, accounts)
    return accounts


def add_manual_transaction(payload: dict):
    """
    Appends a user-entered transaction to the same file the detector
    reads. Required fields match SCHEMA.md's Transaction entity;
    anything omitted gets a sane default so a quick manual entry
    doesn't require every field.

    Note: uses `payload.get(x) or default`, not `payload.get(x, default)`.
    payload comes from a pydantic model's dict()/model_dump(), so every
    optional key is already present with an explicit None value when
    unset -- dict.get's default only kicks in for a MISSING key, so
    `payload.get("memo", "Manually added")` silently returned None
    instead of the fallback. Confirmed via a real crash: a memo of None
    reached detect_refund_owed's `.lower()` call and raised
    AttributeError. `or` catches both "missing" and "present but None".
    """
    transactions = _load_json(TRANSACTIONS_PATH, [])
    entry = {
        "id": f"txn_manual_{uuid.uuid4().hex[:8]}",
        "timestamp": payload.get("timestamp") or datetime.utcnow().isoformat(),
        "amount": float(payload["amount"]),
        "direction": payload.get("direction") or "debit",
        "merchant_name": payload["merchant_name"],
        "category": payload.get("category") or "other",
        "payment_mode": payload.get("payment_mode") or "upi",
        "memo": payload.get("memo") or "Manually added",
        "linked_subscription_id": payload.get("linked_subscription_id"),
        "account": payload.get("account") or "manual_entry",
    }
    transactions.append(entry)
    _save_json(TRANSACTIONS_PATH, transactions)
    return entry


def add_manual_subscription(payload: dict):
    """
    Appends a user-entered subscription to the same file the detector
    reads. See add_manual_transaction's docstring -- same `or`-not-`get`
    fix applies here, and matters more: an unset next_due_date/
    last_charged_date reaching growth.py or detector.py as None (instead
    of today's date) would break date-rolling logic that expects a
    parseable date string.
    """
    subscriptions = _load_json(SUBSCRIPTIONS_PATH, [])
    today = date.today().isoformat()
    entry = {
        "id": f"sub_manual_{uuid.uuid4().hex[:8]}",
        "merchant_name": payload["merchant_name"],
        "amount": float(payload["amount"]),
        "billing_cycle": payload.get("billing_cycle") or "monthly",
        "started_date": payload.get("started_date") or today,
        "last_charged_date": payload.get("last_charged_date") or today,
        "next_due_date": payload.get("next_due_date") or today,
        "status": payload.get("status") or "active",
        "trial_end_date": payload.get("trial_end_date"),
        "last_usage_signal_date": payload.get("last_usage_signal_date") or today,
    }
    subscriptions.append(entry)
    _save_json(SUBSCRIPTIONS_PATH, subscriptions)
    return entry
