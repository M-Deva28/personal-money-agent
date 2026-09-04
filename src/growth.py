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

DUE DATE NOTE -- history of two related fixes, worth keeping so the
next person doesn't reintroduce either bug:

  1. Originally we trusted subscriptions.json's stored next_due_date
     directly. That field was set ONCE at data-generation time
     (last_charged_date + 30 days) and never rolled forward, so it
     went stale for any subscription paid on schedule -- e.g. LIC
     Premium showed 242 days overdue despite its own transaction
     history proving it was charged successfully every month.

  2. The first fix (this file, previously) stopped trusting the
     stored field and instead recomputed the due date at query time,
     always rolling forward from last_charged_date. That fixed the
     staleness but silently broke something else: generate_data.py
     was later updated to correctly distinguish subscriptions that
     genuinely stopped renewing (pattern="payment_failed", a real
     stale-but-unrolled due date, 5-25 days overdue) from ones still
     renewing fine. Because this file recomputed the date from
     scratch instead of reading what the generator now correctly
     computed, it rolled EVERY subscription forward regardless of
     pattern -- including payment_failed ones -- which erased the
     only genuinely-overdue bills in the dataset.

The correct fix is neither "always trust the field" nor "always
recompute it" -- it's "fix it once, correctly, at the source, and
then trust it." generate_data.py's _resolve_next_due_dates() now
does that correctly (rolls forward for everything except
payment_failed, which keeps a real small overdue window). This file
goes back to reading next_due_date directly. Manually-added
subscriptions (accounts.py) also set a correct, current next_due_date
at creation time, so trusting the field is correct for both sources.

Per-user: main.py calls set_user_context(user_dir) before invoking
growth_summary(), so bills are computed against THE LOGGED-IN USER's
subscriptions/transactions/profile (same thread-local pattern as
voice_tools.py). The CLI scripts never set a context and keep using
the bundled data/ dataset.
"""

import json
import os
import threading
from datetime import datetime

from feedback import load_profile
from razorpay_actions import execute_bill_payment

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

_CTX = threading.local()


def set_user_context(user_dir):
    _CTX.user_dir = user_dir


def _dir():
    return getattr(_CTX, "user_dir", None) or DATA_DIR


def _load(name):
    with open(os.path.join(_dir(), name), encoding="utf-8") as f:
        return json.load(f)


def _profile_path():
    ud = getattr(_CTX, "user_dir", None)
    return os.path.join(ud, "profile.json") if ud else None


def _as_of_default(transactions):
    """Same convention as detector.py: treat the dataset's latest
    transaction as 'now', so the demo's internal clock is consistent
    across every mode rather than drifting against the real calendar.
    Falls back to the real clock for an empty ledger (fresh accounts)."""
    if not transactions:
        return datetime.utcnow()
    return max(datetime.fromisoformat(t["timestamp"]) for t in transactions)


def list_upcoming_bills(subscriptions=None, transactions=None, as_of=None, days_ahead=14):
    """
    Returns subscriptions with next_due_date within `days_ahead` days
    (including genuinely overdue ones -- see module docstring for why
    that field can now be trusted directly).
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
        profile = load_profile(path=_profile_path())

    autopay_merchants = set(profile.get("autopay_enabled_merchants", []))
    bills = list_upcoming_bills(subscriptions, transactions, as_of)

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