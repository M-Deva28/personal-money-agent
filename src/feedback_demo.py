"""
Demonstrates the feedback loop end-to-end:
  1. Run detection with no learned profile (defaults).
  2. Pick one zombie_sub flag and mark it false_positive.
  3. Re-run detection WITH the learned profile.
  4. Show that the same event is no longer flagged (or its threshold moved).

Run: python src/feedback_demo.py
"""

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from detector import run_all_detectors
from feedback import load_profile, record_feedback, reset_profile

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


def main():
    transactions = load("transactions.json")
    subscriptions = load("subscriptions.json")

    reset_profile()  # start clean for a repeatable demo

    print("=" * 60)
    print("STEP 1 — Detection with default thresholds (no learning yet)")
    print("=" * 60)
    flags_before = run_all_detectors(transactions, subscriptions, profile=load_profile())
    zombie_flags = [f for f in flags_before if f["pattern"] == "zombie_sub"]
    for f in zombie_flags:
        print(f"  FLAGGED: {f['reasoning']}")

    if not zombie_flags:
        print("  No zombie_sub flags found — try re-running generate_data.py")
        return

    target = zombie_flags[0]
    merchant = target["reasoning"].split(" subscription")[0]
    print(f"\n>>> User says: '{merchant} flag is wrong, I actually still use that app.'\n")

    print("=" * 60)
    print("STEP 2 — Recording feedback (false_positive)")
    print("=" * 60)
    updated_profile = record_feedback(target, "false_positive", merchant_name=merchant)
    print(f"  Updated profile: zombie_sub_days_by_merchant = "
          f"{updated_profile['zombie_sub_days_by_merchant']}")

    print()
    print("=" * 60)
    print("STEP 3 — Re-running detection WITH the learned profile")
    print("=" * 60)
    flags_after = run_all_detectors(transactions, subscriptions, profile=load_profile())
    zombie_flags_after = [f for f in flags_after if f["pattern"] == "zombie_sub"]

    still_flagged = any(f["flag_id"] == target["flag_id"] for f in zombie_flags_after)
    print(f"  {merchant} still flagged after correction? {'YES — bug!' if still_flagged else 'NO — agent learned.'}")
    print(f"  Total zombie_sub flags: {len(zombie_flags)} -> {len(zombie_flags_after)}")
    print()
    for f in zombie_flags_after:
        print(f"  STILL FLAGGED: {f['reasoning']}")

    print()
    print("This is the pitch moment: correct it once, it doesn't repeat the mistake")
    print("for that merchant — without retraining any model.")


if __name__ == "__main__":
    main()
