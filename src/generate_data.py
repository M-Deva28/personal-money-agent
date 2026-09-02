"""
Synthetic data generator for the Personal Money Agent buildathon project.

Generates:
  - transactions.json
  - subscriptions.json
  - ground_truth.json   (labels — use ONLY for scoring, not fed to the detector)

Run: python generate_data.py
"""

import json
import os
import random
import uuid
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

random.seed(42)  # reproducible demo run

START_DATE = datetime(2025, 9, 1)
END_DATE = datetime(2026, 8, 28)

KNOWN_MERCHANTS = [
    "Netflix", "Spotify", "Amazon Prime", "Hotstar", "Swiggy One",
    "Gym Fitclub", "Zomato Gold", "Airtel Broadband", "LIC Premium",
    "Adobe Creative Cloud", "Notion", "YouTube Premium",
]

CATEGORIES = {
    "Netflix": "streaming", "Spotify": "streaming", "Hotstar": "streaming",
    "YouTube Premium": "streaming", "Amazon Prime": "shopping",
    "Swiggy One": "food", "Zomato Gold": "food", "Gym Fitclub": "fitness",
    "Airtel Broadband": "utility", "LIC Premium": "insurance",
    "Adobe Creative Cloud": "software", "Notion": "software",
}

SUSPICIOUS_PAYEES = ["QuickCashUPI", "WinRewardz", "TrustPay Refund Cell", "Fast KYC Update"]


def rand_date(start, end):
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def build_subscriptions(n=10):
    subs = []
    ground_truth = []
    merchants = random.sample(KNOWN_MERCHANTS, n)

    for m in merchants:
        sub_id = new_id("sub")
        started = rand_date(START_DATE, START_DATE + timedelta(days=180))
        amount = round(random.uniform(99, 999), 2)
        cycle = "monthly"
        trial_end = started + timedelta(days=14) if random.random() < 0.3 else None
        last_charged = rand_date(started, END_DATE - timedelta(days=5))
        # Placeholder — recomputed after transactions exist and the real
        # as_of (max transaction timestamp) is known. See _resolve_next_due_dates().
        next_due = last_charged + timedelta(days=30)

        pattern = "clean"
        status = "active"
        last_usage = rand_date(last_charged - timedelta(days=10), last_charged)

        roll = random.random()
        if roll < 0.15:
            # zombie subscription: no usage signal in 60+ days
            pattern = "zombie_sub"
            last_usage = last_charged - timedelta(days=random.randint(65, 120))
        elif roll < 0.25 and trial_end:
            # silent trial-to-paid conversion
            pattern = "silent_conversion"
            status = "active"
        elif roll < 0.35:
            # silent price hike (planted separately in transactions)
            pattern = "price_hike"
        elif roll < 0.45:
            # payment actually failed/lapsed recently -- genuinely overdue,
            # NOT rolled forward in _resolve_next_due_dates(). Distinct from
            # the "still renewing fine" case: this one really did stop.
            pattern = "payment_failed"

        subs.append({
            "id": sub_id,
            "merchant_name": m,
            "amount": amount,
            "billing_cycle": cycle,
            "started_date": started.date().isoformat(),
            "last_charged_date": last_charged.date().isoformat(),
            "next_due_date": next_due.date().isoformat(),
            "status": status,
            "trial_end_date": trial_end.date().isoformat() if trial_end else None,
            "last_usage_signal_date": last_usage.date().isoformat(),
        })
        ground_truth.append({"id": sub_id, "type": "subscription", "label": pattern})

    return subs, ground_truth


def build_transactions(subs, n_extra=60):
    txns = []
    ground_truth = []

    # 1. Recurring charges from subscriptions (some with planted price hikes)
    for sub in subs:
        base_amount = sub["amount"]
        last_charged = datetime.fromisoformat(sub["last_charged_date"])
        history_amount = base_amount
        for i in range(4):
            charge_date = last_charged - timedelta(days=30 * (3 - i))
            amt = history_amount
            label = "clean"
            if i == 3 and random.random() < 0.4:
                # plant a silent price hike on the most recent charge
                amt = round(history_amount * random.uniform(1.2, 1.5), 2)
                label = "price_hike"
            txn_id = new_id("txn")
            txns.append({
                "id": txn_id,
                "timestamp": iso(charge_date),
                "amount": amt,
                "direction": "debit",
                "merchant_name": sub["merchant_name"],
                "category": CATEGORIES.get(sub["merchant_name"], "other"),
                "payment_mode": random.choice(["UPI", "card"]),
                "memo": f"Auto-debit {sub['merchant_name']}",
                "linked_subscription_id": sub["id"],
                "account": "primary_card",
            })
            if label != "clean":
                ground_truth.append({"id": txn_id, "type": "transaction", "label": label})

    # 2. Duplicate charges (planted)
    for _ in range(4):
        m = random.choice(KNOWN_MERCHANTS)
        base_time = rand_date(START_DATE, END_DATE)
        amt = round(random.uniform(200, 2000), 2)
        first_id, second_id = new_id("txn"), new_id("txn")
        for tid, offset in [(first_id, 0), (second_id, random.randint(1, 4))]:
            txns.append({
                "id": tid,
                "timestamp": iso(base_time + timedelta(minutes=offset)),
                "amount": amt,
                "direction": "debit",
                "merchant_name": m,
                "category": CATEGORIES.get(m, "other"),
                "payment_mode": "UPI",
                "memo": f"Payment to {m}",
                "linked_subscription_id": None,
                "account": "primary_card",
            })
        ground_truth.append({"id": second_id, "type": "transaction", "label": "duplicate_charge"})

    # 3. Refund owed but not received (planted)
    for _ in range(5):
        m = random.choice(KNOWN_MERCHANTS)
        d = rand_date(START_DATE, END_DATE - timedelta(days=15))
        txn_id = new_id("txn")
        txns.append({
            "id": txn_id,
            "timestamp": iso(d),
            "amount": round(random.uniform(300, 3000), 2),
            "direction": "debit",
            "merchant_name": m,
            "category": CATEGORIES.get(m, "other"),
            "payment_mode": "card",
            "memo": "Order refunded per merchant — refund pending",
            "linked_subscription_id": None,
            "account": "primary_card",
        })
        ground_truth.append({"id": txn_id, "type": "transaction", "label": "refund_owed"})

    # 4. Suspicious UPI collect requests (planted)
    for _ in range(4):
        payee = random.choice(SUSPICIOUS_PAYEES)
        d = rand_date(START_DATE, END_DATE)
        txn_id = new_id("txn")
        txns.append({
            "id": txn_id,
            "timestamp": iso(d),
            "amount": round(random.uniform(499, 4999), 2),
            "direction": "debit",
            "merchant_name": payee,
            "category": "unknown",
            "payment_mode": "UPI",
            "memo": "UPI collect request approved",
            "linked_subscription_id": None,
            "account": "primary_card",
        })
        ground_truth.append({"id": txn_id, "type": "transaction", "label": "suspicious_collect"})

    # 5. Ordinary clean noise transactions
    clean_merchants = ["Local Grocery", "Metro Card Recharge", "Electricity Board",
                        "Friend Transfer", "Cafe Coffee Day", "Petrol Pump"]
    for _ in range(n_extra):
        m = random.choice(clean_merchants)
        d = rand_date(START_DATE, END_DATE)
        txns.append({
            "id": new_id("txn"),
            "timestamp": iso(d),
            "amount": round(random.uniform(50, 1500), 2),
            "direction": "debit",
            "merchant_name": m,
            "category": "misc",
            "payment_mode": random.choice(["UPI", "card", "netbanking"]),
            "memo": "",
            "linked_subscription_id": None,
            "account": "primary_card",
        })

    txns.sort(key=lambda t: t["timestamp"])
    return txns, ground_truth


def _resolve_next_due_dates(subs, sub_patterns, txns):
    """Fix the frozen-field bug: `next_due_date` was set once at
    last_charged_date + 30 days and never rolled forward, so any
    subscription whose last_charged_date fell well before the dataset's
    real 'now' (as_of = max transaction timestamp, same convention used
    everywhere in detector.py / growth.py) showed as absurdly overdue
    even when its own transaction history proved it was being paid on
    schedule (e.g. LIC Premium showing 242 days overdue).

    Two genuinely different situations exist, and only one should roll
    forward:
      - Still renewing every cycle (all patterns except payment_failed):
        roll next_due_date forward in monthly increments from
        last_charged_date until it lands at/after as_of. Reflects that
        an actively-billing subscription's next due date is always
        within the current/upcoming cycle relative to now.
      - `payment_failed`: the subscription genuinely stopped renewing
        recently. This one is deliberately NOT rolled forward -- it
        keeps a real, small overdue window (5-25 days), which is what
        makes the "Overdue Bills" feature meaningful instead of vacuous.
    """
    as_of = max(datetime.fromisoformat(t["timestamp"]) for t in txns)
    for sub in subs:
        last_charged = datetime.fromisoformat(sub["last_charged_date"])
        pattern = sub_patterns.get(sub["id"])
        if pattern == "payment_failed":
            overdue_by = timedelta(days=random.randint(5, 25))
            sub["next_due_date"] = (as_of - overdue_by).date().isoformat()
        else:
            due = last_charged + timedelta(days=30)
            while due < as_of:
                due += timedelta(days=30)
            sub["next_due_date"] = due.date().isoformat()
    return subs


def main():
    subs, sub_labels = build_subscriptions(n=10)
    txns, txn_labels = build_transactions(subs, n_extra=60)
    sub_patterns = {g["id"]: g["label"] for g in sub_labels}
    subs = _resolve_next_due_dates(subs, sub_patterns, txns)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "transactions.json"), "w", encoding="utf-8") as f:
        json.dump(txns, f, indent=2)
    with open(os.path.join(DATA_DIR, "subscriptions.json"), "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2)
    with open(os.path.join(DATA_DIR, "ground_truth.json"), "w", encoding="utf-8") as f:
        json.dump(sub_labels + txn_labels, f, indent=2)

    print(f"Generated {len(txns)} transactions, {len(subs)} subscriptions, "
          f"{len(sub_labels) + len(txn_labels)} labeled ground-truth events.")


if __name__ == "__main__":
    main()
