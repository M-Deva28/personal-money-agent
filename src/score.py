"""
Runs the full detector suite against the synthetic ledger, logs every
decision to the audit trail, and scores results against ground truth.

Run: python src/score.py
"""

import json
import os
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from detector import run_all_detectors
from audit import log_decision, decide_action, clear_log
from feedback import load_profile
from scoring import compute_score

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
    report = compute_score(flags, ground_truth)

    print("=" * 60)
    print("PERSONAL MONEY AGENT — DETECTION REPORT")
    print("=" * 60)
    print(f"Total events scanned      : {len(transactions) + len(subscriptions)}")
    print(f"Total flags raised        : {report['total_flags']}")
    print()
    print("Flags by pattern:")
    for pattern, count in sorted(report['flags_by_pattern'].items()):
        print(f"  - {pattern:22s}: {count}")
    print()
    print(f"Precision                 : {report['precision']:.2%}")
    print(f"Recall                    : {report['recall']:.2%}")
    print(f"F1 score                  : {report['f1']:.2%}")
    print()
    print(f"Total ₹ flagged           : ₹{report['total_amount_flagged']:,.2f}")
    print(f"₹ auto-actionable (high)  : ₹{report['auto_actionable_amount']:,.2f}")
    print(f"₹ needs human review      : ₹{report['needs_review_amount']:,.2f}")
    print()
    print(f"Audit trail written to logs/audit_trail.json ({len(flags)} entries)")
    print("=" * 60)


if __name__ == "__main__":
    main()
