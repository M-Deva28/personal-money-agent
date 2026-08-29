"""
Runs the full detector suite against the synthetic ledger, logs every
decision to the audit trail, and scores results against ground truth.

Run: python src/score.py
"""

import json
import os
from collections import defaultdict

from detector import run_all_detectors
from audit import log_decision, decide_action, clear_log
from feedback import load_profile

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


def main():
    transactions = load("transactions.json")
    subscriptions = load("subscriptions.json")
    ground_truth = load("ground_truth.json")
    truth_by_id = {g["id"]: g["label"] for g in ground_truth}

    clear_log()
    profile = load_profile()  # picks up any learned corrections from feedback_demo.py
    flags = run_all_detectors(transactions, subscriptions, profile=profile)

    # Log every decision (this builds the audit trail file)
    for flag in flags:
        action = decide_action(flag)
        log_decision(flag, action)

    # --- Scoring against planted ground truth ---
    flagged_ids = {f["event_id"]: f["pattern"] for f in flags}
    all_labeled_ids = set(truth_by_id.keys())

    true_positives = sum(
        1 for eid, pat in flagged_ids.items()
        if truth_by_id.get(eid) == pat
    )
    false_positives = sum(
        1 for eid, pat in flagged_ids.items()
        if truth_by_id.get(eid, "clean") != pat
    )
    false_negatives = sum(
        1 for eid, label in truth_by_id.items()
        if label != "clean" and flagged_ids.get(eid) != label
    )

    precision = true_positives / (true_positives + false_positives) if flagged_ids else 0
    recall = true_positives / (true_positives + false_negatives) if all_labeled_ids else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    amount_flagged = sum(f["amount_at_stake"] for f in flags)
    high_conf_amount = sum(f["amount_at_stake"] for f in flags if f["confidence"] == "high")

    by_pattern = defaultdict(int)
    for f in flags:
        by_pattern[f["pattern"]] += 1

    print("=" * 60)
    print("PERSONAL MONEY AGENT — DETECTION REPORT")
    print("=" * 60)
    print(f"Total events scanned      : {len(transactions) + len(subscriptions)}")
    print(f"Total flags raised        : {len(flags)}")
    print()
    print("Flags by pattern:")
    for pattern, count in sorted(by_pattern.items()):
        print(f"  - {pattern:22s}: {count}")
    print()
    print(f"Precision                 : {precision:.2%}")
    print(f"Recall                    : {recall:.2%}")
    print(f"F1 score                  : {f1:.2%}")
    print()
    print(f"Total ₹ flagged           : ₹{amount_flagged:,.2f}")
    print(f"₹ auto-actionable (high)  : ₹{high_conf_amount:,.2f}")
    print(f"₹ needs human review      : ₹{amount_flagged - high_conf_amount:,.2f}")
    print()
    print(f"Audit trail written to logs/audit_trail.json ({len(flags)} entries)")
    print("=" * 60)


if __name__ == "__main__":
    main()
