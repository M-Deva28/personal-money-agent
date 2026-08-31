"""
Finance mode -- the fourth "capability" of the Personal Money Agent
(mirrors Razorpay's Finance Controller track, consumer-side).

Deliberately NOT machine learning. Categorization here is a lookup
against merchants we already know (see generate_data.py's CATEGORIES
dict) -- the right tool for a known, finite personal merchant list.
We tested a Kaggle dataset for this and found its category labels
were assigned independently of merchant name (~random), which would
have meant training a classifier that memorizes noise. A lookup table
is both simpler AND more honest here.

The forecast is a plain moving average, clearly labeled as a
statistical projection -- not framed as AI, because it isn't one.
"""

import json
import os
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _load_transactions():
    with open(os.path.join(DATA_DIR, "transactions.json")) as f:
        return json.load(f)


def _month_key(timestamp_str):
    dt = datetime.fromisoformat(timestamp_str)
    return f"{dt.year:04d}-{dt.month:02d}"


def monthly_spend_by_category(transactions=None):
    """Returns {month: {category: total_spent}} for all debit transactions."""
    if transactions is None:
        transactions = _load_transactions()

    result = defaultdict(lambda: defaultdict(float))
    for t in transactions:
        if t["direction"] != "debit":
            continue
        month = _month_key(t["timestamp"])
        result[month][t["category"]] += t["amount"]

    # convert nested defaultdicts to plain dicts, round amounts
    return {
        month: {cat: round(amt, 2) for cat, amt in cats.items()}
        for month, cats in sorted(result.items())
    }


def category_totals(transactions=None):
    """Returns {category: total_spent} across the whole dataset."""
    if transactions is None:
        transactions = _load_transactions()

    totals = defaultdict(float)
    for t in transactions:
        if t["direction"] == "debit":
            totals[t["category"]] += t["amount"]
    return {cat: round(amt, 2) for cat, amt in sorted(totals.items(), key=lambda x: -x[1])}


def forecast_next_month(transactions=None, lookback_months=3):
    """
    Simple moving-average forecast per category: average of the last
    N available months' spend in that category. Explicitly NOT a
    trained model -- a plain statistical projection, labeled as such
    everywhere it's surfaced.
    """
    if transactions is None:
        transactions = _load_transactions()

    by_month = monthly_spend_by_category(transactions)
    months_sorted = sorted(by_month.keys())
    recent_months = months_sorted[-lookback_months:] if months_sorted else []

    category_sums = defaultdict(list)
    for month in recent_months:
        for cat, amt in by_month[month].items():
            category_sums[cat].append(amt)

    forecast = {
        cat: round(sum(amts) / len(amts), 2)
        for cat, amts in category_sums.items()
    }
    forecast_total = round(sum(forecast.values()), 2)

    return {
        "method": f"moving average of last {len(recent_months)} month(s)",
        "based_on_months": recent_months,
        "forecast_by_category": forecast,
        "forecast_total": forecast_total,
    }


def finance_summary(transactions=None):
    """Single endpoint's worth of data: totals, monthly trend, forecast."""
    if transactions is None:
        transactions = _load_transactions()

    return {
        "category_totals": category_totals(transactions),
        "monthly_breakdown": monthly_spend_by_category(transactions),
        "forecast": forecast_next_month(transactions),
    }
