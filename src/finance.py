"""
Finance mode — the Finance Controller capability of the shared agent.

Not a separate app with its own pipeline: it reads the SAME classified
ledger (transactions/subscriptions) that Risk and Recovery mode already
work from, and the SAME flags they raise. This is the "one shared
understanding of the user's financial state" part of the pitch --
a subscription Risk/Recovery mode flags as zombie_sub automatically
shows up here as recoverable monthly savings, without Finance mode
needing any detection logic of its own.

Two things this mode does:
  1. categorize_spend  -- monthly totals, overall and per category.
  2. build_forecast    -- projects next month's spend from a rolling
     average of recent months, and an "if you act on the agent's
     high-confidence recommendations" adjusted forecast that nets out
     the waste Risk/Recovery mode already found.
"""

from collections import defaultdict


def _month_key(ts):
    return ts[:7]  # "YYYY-MM"


def monthly_totals(transactions, direction="debit"):
    """Total amount per calendar month, e.g. {'2026-06': 18342.10, ...}."""
    totals = defaultdict(float)
    for t in transactions:
        if t["direction"] != direction:
            continue
        totals[_month_key(t["timestamp"])] += t["amount"]
    return dict(sorted(totals.items()))


def categorize_spend(transactions, months_back=3):
    """
    Per-category debit totals: overall (whole history) and restricted to
    the most recent `months_back` calendar months present in the data,
    so the demo can show "here's where your money actually goes lately"
    without one giant all-time number drowning out recent behavior.
    """
    all_months = sorted({
        _month_key(t["timestamp"]) for t in transactions if t["direction"] == "debit"
    })
    if not all_months:
        return {"overall_by_category": {}, "recent_months_used": [], "recent_by_category": {}}

    recent_months = set(all_months[-months_back:])

    overall = defaultdict(float)
    recent = defaultdict(float)
    for t in transactions:
        if t["direction"] != "debit":
            continue
        cat = t.get("category", "unknown")
        overall[cat] += t["amount"]
        if _month_key(t["timestamp"]) in recent_months:
            recent[cat] += t["amount"]

    return {
        "overall_by_category": {
            k: round(v, 2) for k, v in sorted(overall.items(), key=lambda kv: -kv[1])
        },
        "recent_months_used": sorted(recent_months),
        "recent_by_category": {
            k: round(v, 2) for k, v in sorted(recent.items(), key=lambda kv: -kv[1])
        },
    }


def build_forecast(transactions, subscriptions, flags=None, months_back=3):
    """
    Baseline forecast: average total monthly debit spend over the last
    `months_back` months, projected one month forward. Deliberately
    simple (a rolling average, not a model) -- "lighter but real" per
    the build plan; the interesting part is the adjustment below, not
    the forecasting math.

    Adjusted forecast: baseline minus monthly savings already identified
    by Risk/Recovery mode's high-confidence zombie_sub flags. These are
    recurring charges, not one-off refunds, so they reduce every future
    month, not just next month -- hence also reporting an annualized
    figure.

    flags: optional output of detector.run_all_detectors(). Passing None
    skips the adjustment and returns baseline only (e.g. before any
    detection has run).
    """
    totals = monthly_totals(transactions)
    months = sorted(totals.keys())
    recent = months[-months_back:] if len(months) >= months_back else months
    baseline_monthly = sum(totals[m] for m in recent) / len(recent) if recent else 0.0

    identified_monthly_savings = 0.0
    savings_sources = []
    if flags:
        for f in flags:
            if (
                f["pattern"] == "zombie_sub"
                and f["confidence"] == "high"
                and f["suggested_action"] == "pause_subscription"
            ):
                identified_monthly_savings += f["amount_at_stake"]
                savings_sources.append({
                    "flag_id": f["flag_id"],
                    "reasoning": f["reasoning"],
                    "monthly_amount": f["amount_at_stake"],
                })

    adjusted_monthly = baseline_monthly - identified_monthly_savings

    return {
        "months_used_for_baseline": recent,
        "baseline_next_month_forecast": round(baseline_monthly, 2),
        "identified_monthly_savings": round(identified_monthly_savings, 2),
        "adjusted_next_month_forecast": round(adjusted_monthly, 2),
        "savings_sources": savings_sources,
        "annualized_savings_if_acted_on": round(identified_monthly_savings * 12, 2),
    }


def build_finance_report(transactions, subscriptions, flags=None, months_back=3):
    """Single entry point main.py calls for GET /finance."""
    return {
        "spend_by_category": categorize_spend(transactions, months_back=months_back),
        "monthly_totals": {k: round(v, 2) for k, v in monthly_totals(transactions).items()},
        "forecast": build_forecast(transactions, subscriptions, flags=flags, months_back=months_back),
    }
