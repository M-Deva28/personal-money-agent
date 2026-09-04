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

NOTE: this is the base project's AA-style simulation, ported for
feature parity. The Enhanced dashboard's "Bank accounts" panel instead
uses the REAL RazorpayX connect (bank_connect.py), which validates
account details live -- this module's endpoints remain available for
API-level parity and for the consent-flow concept itself.

Manual entry is the other on-ramp: a user can add a transaction or
subscription by hand, which is appended to the same per-user data files
the detector already reads -- so manually-entered data flows through
detection, scoring, finance, and growth exactly like generated data.

Per-user: every function takes a user_id and reads/writes inside
data/users/<user_id>/ via store.py.
"""

import os
import uuid
from datetime import datetime, date

import store

# Mock catalog of AA-registered providers a user could "pick" -- mirrors
# the real-world picker (that's genuinely just choosing a bank/AA app).
AA_PROVIDERS = [
    {"provider_id": "hdfc", "name": "HDFC Bank", "via": "Finvu AA"},
    {"provider_id": "icici", "name": "ICICI Bank", "via": "OneMoney AA"},
    {"provider_id": "sbi", "name": "State Bank of India", "via": "Setu AA"},
    {"provider_id": "axis", "name": "Axis Bank", "via": "Finvu AA"},
    {"provider_id": "kotak", "name": "Kotak Mahindra Bank", "via": "Anumati AA"},
]


def list_connected_accounts(user_id: str):
    return store.read_user_json(user_id, "connected_accounts.json", [])


def connect_account(user_id: str, provider_id: str):
    """
    Simulates the AA consent flow completing successfully: user picked a
    provider, approved a scoped consent artifact, account now streams
    data. No credentials ever pass through this app -- that's the point
    of the AA model, mock or real.
    """
    provider = next((p for p in AA_PROVIDERS if p["provider_id"] == provider_id), None)
    if not provider:
        raise ValueError(f"Unknown provider_id: {provider_id}")

    with store.user_write_lock(user_id):
        accounts = list_connected_accounts(user_id)
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
        store.write_user_json(user_id, "connected_accounts.json", accounts)
        return accounts


def disconnect_account(user_id: str, account_id: str):
    with store.user_write_lock(user_id):
        accounts = [a for a in list_connected_accounts(user_id) if a["account_id"] != account_id]
        store.write_user_json(user_id, "connected_accounts.json", accounts)
        return accounts


def add_manual_subscription(user_id: str, payload: dict):
    """
    Appends a user-entered subscription to the same per-user file the
    detector/growth read. See add_manual_transaction's docstring --
    same `or`-not-`get` fix applies here, and matters more: an unset
    next_due_date/last_charged_date reaching growth.py or detector.py
    as None (instead of today's date) would break date-rolling logic
    that expects a parseable date string.
    """
    with store.user_write_lock(user_id):
        subscriptions = store.read_user_json(user_id, "subscriptions.json", [])
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
            "source": "manual",
            "added_at": datetime.utcnow().isoformat(),
        }
        subscriptions.append(entry)
        store.write_user_json(user_id, "subscriptions.json", subscriptions)
        return entry