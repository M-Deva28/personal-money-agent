"""
Demonstrates the cross-capability story that's central to the "one agent,
four capabilities" pitch: the SAME zombie_sub flag Risk/Recovery mode
raises also moves Finance mode's forecast, with no separate Finance-mode
detection logic. One detection, two capabilities benefit.

Run: python src/finance_demo.py
"""

import json
import os
import sys

from detector import run_all_detectors
from feedback import load_profile
from finance import build_finance_report

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


def main():
    # Windows consoles default to cp1252/latin-1, which can't encode the
    # rupee sign (₹) used throughout the report -- force UTF-8 so the
    # demo never dies mid-print on a fresh machine.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    transactions = load("transactions.json")
    subscriptions = load("subscriptions.json")

    print("=" * 60)
    print("FINANCE MODE — categorized spend + forecast")
    print("=" * 60)

    profile = load_profile()
    flags = run_all_detectors(transactions, subscriptions, profile=profile)

    report_without_flags = build_finance_report(transactions, subscriptions, flags=None)
    report_with_flags = build_finance_report(transactions, subscriptions, flags=flags)

    print("\nTop spend categories (recent months):")
    recent = report_with_flags["spend_by_category"]["recent_by_category"]
    for cat, amt in list(recent.items())[:5]:
        print(f"  - {cat:12s}: ₹{amt:,.2f}")

    fc_before = report_without_flags["forecast"]
    fc_after = report_with_flags["forecast"]

    print(f"\nBaseline next-month forecast (no flags applied) : ₹{fc_before['baseline_next_month_forecast']:,.2f}")
    print(f"Identified monthly savings (Risk/Recovery flags) : ₹{fc_after['identified_monthly_savings']:,.2f}")
    print(f"Adjusted forecast if you act on them             : ₹{fc_after['adjusted_next_month_forecast']:,.2f}")
    print(f"Annualized savings if acted on                   : ₹{fc_after['annualized_savings_if_acted_on']:,.2f}")

    if fc_after["savings_sources"]:
        print("\nWhere the savings come from (same flags Risk/Recovery already raised):")
        for s in fc_after["savings_sources"]:
            print(f"  - {s['reasoning']}")
    else:
        print("\nNo high-confidence zombie_sub flags right now -- forecast is baseline-only.")

    print()
    print("This is the pitch moment: one detection (a zombie subscription)")
    print("improves BOTH the Recovery recommendation AND the Finance forecast --")
    print("no separate Finance-mode detection logic needed.")


if __name__ == "__main__":
    main()
