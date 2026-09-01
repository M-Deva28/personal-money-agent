"""
Growth mode -- the consumer mirror of Razorpay's Growth & Agentic
Commerce track. Where Risk/Recovery catch problems AFTER money moves
wrongly, Growth mode looks FORWARD: bills about to come due.

Design principle, stated explicitly because it matters: the agent
NEVER auto-pays a bill the user hasn't specifically opted into,
even for a merchant it already trusts for other purposes (e.g. one
that passed fraud review). Autopay is a separate, per-merchant,
explicit toggle -- see feedback.set_autopay(). Everything else is a
reminder only.
"""

import json
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


def _as_of_default(transactions):
    """Same convention as detector.py: treat the dataset's latest
    transaction as 'now', so the demo's internal clock is consistent
    across every mode rather than drifting against the real calendar."""
    return max(datetime.fromisoformat(t["timestamp"]) for t in transactions)


def list_upcoming_bills(subscriptions=None, transactions=None, as_of=None, days_ahead=14):
    """
    Returns subscriptions with next_due_date within `days_ahead` days
    (including already-overdue ones, which are flagged distinctly).
    """
    if subscriptions is None:
        subscriptions = _load("subscriptions.json")
    if transactions is None:
        transactions = _load("transactions.json")
    if as_of is None:
        as_of = _as_of_default(transactions)

    bills = []
    for sub in subscriptions:
        if sub["status"] != "active":
            continue
        due = datetime.fromisoformat(sub["next_due_date"])
        days_until = (due - as_of).days

        if days_until > days_ahead:
            continue  # not due soon enough to surface yet

        bills.append({
            "subscription_id": sub["id"],
            "merchant_name": sub["merchant_name"],
            "amount": sub["amount"],
            "due_date": sub["next_due_date"],
            "days_until_due": days_until,
            "overdue": days_until < 0,
        })

    bills.sort(key=lambda b: b["days_until_due"])
    return bills


def growth_summary(profile=None, subscriptions=None, transactions=None, as_of=None):
    """
    For each upcoming bill, decides reminder vs. autopay based on the
    user's explicit per-merchant opt-in -- never inferred, never
    defaulted to on.
    """
    if profile is None:
        from feedback import load_profile
        profile = load_profile()

    autopay_merchants = set(profile.get("autopay_enabled_merchants", []))
    bills = list_upcoming_bills(subscriptions, transactions, as_of)

    from razorpay_actions import execute_bill_payment

    for bill in bills:
        bill["autopay_enabled"] = bill["merchant_name"] in autopay_merchants
        bill["planned_action"] = "auto_pay" if bill["autopay_enabled"] else "remind_only"
        if bill["autopay_enabled"]:
            bill["execution_result"] = execute_bill_payment(bill)

    return {
        "count": len(bills),
        "bills": bills,
        "autopay_enabled_merchants": sorted(autopay_merchants),
    }
