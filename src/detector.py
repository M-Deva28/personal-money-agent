"""
Detection engine — the core "brain" of the Personal Money Agent.

Rule-based on purpose: deterministic, explainable, zero API cost, and fast
enough to run on a whole ledger instantly. Each rule returns a confidence
tier so the caller (main.py) can decide auto-action vs needs-review.

Every detection carries a `reasoning` string — this IS the audit trail.
"""

from datetime import datetime, timedelta
from collections import defaultdict

KNOWN_MERCHANTS = {
    "Netflix", "Spotify", "Amazon Prime", "Hotstar", "Swiggy One",
    "Gym Fitclub", "Zomato Gold", "Airtel Broadband", "LIC Premium",
    "Adobe Creative Cloud", "Notion", "YouTube Premium",
    "Local Grocery", "Metro Card Recharge", "Electricity Board",
    "Friend Transfer", "Cafe Coffee Day", "Petrol Pump",
}

HIGH = "high"
MEDIUM = "medium"


def _parse(d):
    return datetime.fromisoformat(d)


def detect_zombie_subscriptions(subscriptions, as_of, profile=None):
    """Active sub with no usage signal for N+ days (N is per-user, per-merchant adjustable)."""
    flags = []
    default_days = profile["zombie_sub_days"] if profile else 60
    by_merchant = profile["zombie_sub_days_by_merchant"] if profile else {}

    for sub in subscriptions:
        if sub["status"] != "active" or not sub["last_usage_signal_date"]:
            continue
        threshold = by_merchant.get(sub["merchant_name"], default_days)
        last_used = datetime.fromisoformat(sub["last_usage_signal_date"])
        days_idle = (as_of - last_used).days
        if days_idle >= threshold:
            flags.append({
                "event_id": sub["id"],
                "event_type": "subscription",
                "pattern": "zombie_sub",
                "confidence": HIGH if days_idle >= threshold + 30 else MEDIUM,
                "reasoning": (
                    f"{sub['merchant_name']} subscription (₹{sub['amount']}/mo) has had "
                    f"no usage signal for {days_idle} days, but is still billing you."
                ),
                "suggested_action": "pause_subscription",
                "amount_at_stake": sub["amount"],
                "days_idle": days_idle,
            })
    return flags


def detect_silent_conversion(subscriptions, as_of):
    """Trial ended, now active/billing, no separate notice event modeled -> flag as risk."""
    flags = []
    for sub in subscriptions:
        if not sub["trial_end_date"]:
            continue
        trial_end = datetime.fromisoformat(sub["trial_end_date"])
        if sub["status"] == "active" and trial_end <= as_of:
            flags.append({
                "event_id": sub["id"],
                "event_type": "subscription",
                "pattern": "silent_conversion",
                "confidence": MEDIUM,
                "reasoning": (
                    f"{sub['merchant_name']} trial ended on {sub['trial_end_date']} and "
                    f"silently converted to a paid ₹{sub['amount']}/mo plan."
                ),
                "suggested_action": "flag_for_review",
                "amount_at_stake": sub["amount"],
            })
    return flags


def detect_duplicate_charges(transactions, window_minutes=10):
    """Same merchant + same amount within a short window."""
    flags = []
    by_merchant_amount = defaultdict(list)
    for t in transactions:
        if t["direction"] != "debit":
            continue
        key = (t["merchant_name"], round(t["amount"], 2))
        by_merchant_amount[key].append(t)

    for key, txns in by_merchant_amount.items():
        txns.sort(key=lambda t: t["timestamp"])
        for i in range(1, len(txns)):
            prev, curr = txns[i - 1], txns[i]
            gap = (_parse(curr["timestamp"]) - _parse(prev["timestamp"])).total_seconds() / 60
            if 0 <= gap <= window_minutes:
                flags.append({
                    "event_id": curr["id"],
                    "event_type": "transaction",
                    "pattern": "duplicate_charge",
                    "confidence": HIGH,
                    "reasoning": (
                        f"₹{curr['amount']} charged to {curr['merchant_name']} twice within "
                        f"{gap:.1f} minutes — looks like a duplicate charge."
                    ),
                    "suggested_action": "initiate_refund_claim",
                    "amount_at_stake": curr["amount"],
                })
    return flags


def detect_price_hikes(transactions, profile=None):
    threshold = profile["price_hike_threshold"] if profile else 0.15
    """Recurring amount jumps >threshold vs rolling baseline of prior charges."""
    flags = []
    by_sub = defaultdict(list)
    for t in transactions:
        if t.get("linked_subscription_id"):
            by_sub[t["linked_subscription_id"]].append(t)

    for sub_id, txns in by_sub.items():
        txns.sort(key=lambda t: t["timestamp"])
        if len(txns) < 2:
            continue
        history = txns[:-1]
        latest = txns[-1]
        baseline = sum(t["amount"] for t in history) / len(history)
        if baseline == 0:
            continue
        increase = (latest["amount"] - baseline) / baseline
        if increase >= threshold:
            flags.append({
                "event_id": latest["id"],
                "event_type": "transaction",
                "pattern": "price_hike",
                "confidence": HIGH if increase >= 0.3 else MEDIUM,
                "reasoning": (
                    f"{latest['merchant_name']} charge jumped from an average of "
                    f"₹{baseline:.2f} to ₹{latest['amount']:.2f} ({increase*100:.0f}% increase) "
                    f"with no notice event on record."
                ),
                "suggested_action": "flag_for_review",
                "amount_at_stake": round(latest["amount"] - baseline, 2),
            })
    return flags


def detect_refund_owed(transactions, as_of=None, profile=None):
    """Memo indicates refund promised, no matching credit found within grace period."""
    grace_days = profile["refund_grace_days"] if profile else 10
    flags = []
    credits_by_merchant = defaultdict(list)
    for t in transactions:
        if t["direction"] == "credit":
            credits_by_merchant[t["merchant_name"]].append(_parse(t["timestamp"]))

    for t in transactions:
        if "refund" not in t.get("memo", "").lower() or t["direction"] != "debit":
            continue
        debit_time = _parse(t["timestamp"])
        matched = any(
            debit_time <= c_time <= debit_time + timedelta(days=grace_days)
            for c_time in credits_by_merchant.get(t["merchant_name"], [])
        )
        deadline_passed = as_of and (as_of - debit_time).days > grace_days
        if not matched and deadline_passed:
            flags.append({
                "event_id": t["id"],
                "event_type": "transaction",
                "pattern": "refund_owed",
                "confidence": HIGH,
                "reasoning": (
                    f"{t['merchant_name']} marked ₹{t['amount']} as refunded on "
                    f"{t['timestamp'][:10]}, but no matching credit arrived within "
                    f"{grace_days} days."
                ),
                "suggested_action": "initiate_refund_claim",
                "amount_at_stake": t["amount"],
            })
    return flags


def detect_suspicious_collect(transactions, profile=None):
    """UPI debit to a merchant not in the known-merchant list (or user-trusted list)."""
    flags = []
    trusted_extra = set(profile["trusted_merchants_extra"]) if profile else set()
    for t in transactions:
        if t["payment_mode"] != "UPI" or t["direction"] != "debit":
            continue
        if t["merchant_name"] not in KNOWN_MERCHANTS and t["merchant_name"] not in trusted_extra:
            flags.append({
                "event_id": t["id"],
                "event_type": "transaction",
                "pattern": "suspicious_collect",
                "confidence": MEDIUM,
                "reasoning": (
                    f"UPI payment of ₹{t['amount']} to unfamiliar payee "
                    f"'{t['merchant_name']}' doesn't match your known merchant list."
                ),
                "suggested_action": "flag_for_review",
                "amount_at_stake": t["amount"],
            })
    return flags


def run_all_detectors(transactions, subscriptions, as_of=None, profile=None):
    """Run the full detection suite and return a flat list of flags.

    profile: optional per-user adjustable thresholds (see feedback.py).
    If None, hardcoded defaults are used (first-run / no history yet).
    """
    if as_of is None:
        as_of = max(_parse(t["timestamp"]) for t in transactions)

    flags = []
    flags += detect_zombie_subscriptions(subscriptions, as_of, profile)
    flags += detect_silent_conversion(subscriptions, as_of)
    flags += detect_duplicate_charges(transactions)
    flags += detect_price_hikes(transactions, profile)
    flags += detect_refund_owed(transactions, as_of=as_of, profile=profile)
    flags += detect_suspicious_collect(transactions, profile)
    return flags
