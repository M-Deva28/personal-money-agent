"""
Voice/chat tools for the dashboard's built-in Jarvis assistant.

This is the CANONICAL version -- it calls detector.py / finance.py /
feedback.py directly, the same modules /flags, /finance, and /feedback
already use. If you're also running the standalone Jarvis desktop app
with its money_tools.py, that one is a snapshot; keep it in sync with
this file, not the other way around.

Same three tools as the desktop app, same pattern: write the executor,
add its schema to TOOL_SCHEMAS, register it in TOOL_FUNCTIONS.
"""

import json
import os
import threading

from detector import run_all_detectors
from feedback import load_profile, record_feedback
from finance import build_finance_report

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# main.py sets a per-request user context before calling Jarvis, so the
# tools operate on THAT user's ledger/profile, not the bundled demo
# dataset. Thread-local because uvicorn serves sync endpoints on a thread
# pool; the CLI scripts never set a context and keep using DATA_DIR.
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


def _current_flags():
    transactions = _load("transactions.json")
    subscriptions = _load("subscriptions.json")
    profile = load_profile(path=_profile_path())
    flags = run_all_detectors(transactions, subscriptions, profile=profile)
    return flags, transactions, subscriptions


# ---------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------

def check_money_flags(confidence: str = "all") -> str:
    """Summarizes currently open money flags, optionally filtered to one
    confidence tier. Kept short and speakable for voice mode."""
    flags, _, _ = _current_flags()
    if confidence in ("high", "medium", "low"):
        flags = [f for f in flags if f["confidence"] == confidence]

    if not flags:
        scope = f"at {confidence} confidence " if confidence in ("high", "medium", "low") else ""
        return f"Nothing flagged {scope}right now — your accounts look clean."

    order = {"high": 0, "medium": 1, "low": 2}
    flags = sorted(flags, key=lambda f: order.get(f["confidence"], 3))

    lines = [f"You have {len(flags)} open item(s)."]
    for f in flags[:5]:
        lines.append(
            f"[{f['confidence']}] {f['reasoning']} Suggested action: "
            f"{f['suggested_action'].replace('_', ' ')}."
        )
    if len(flags) > 5:
        lines.append(f"...and {len(flags) - 5} more — ask me to filter by confidence for the rest.")
    return " ".join(lines)


def get_money_forecast() -> str:
    """Reports next month's spend forecast, factoring in monthly savings
    from any waste already flagged."""
    flags, transactions, subscriptions = _current_flags()
    report = build_finance_report(transactions, subscriptions, flags=flags)
    fc = report["forecast"]

    msg = f"Baseline forecast for next month is about \u20b9{fc['baseline_next_month_forecast']:.0f}."
    if fc["identified_monthly_savings"] > 0:
        msg += (
            f" I've already flagged \u20b9{fc['identified_monthly_savings']:.0f} a month in recoverable "
            f"waste, so if you act on it your real forecast is closer to "
            f"\u20b9{fc['adjusted_next_month_forecast']:.0f} — about "
            f"\u20b9{fc['annualized_savings_if_acted_on']:.0f} a year."
        )
    else:
        msg += " No flagged waste is currently pulling that number down."
    return msg


def give_money_feedback(merchant_name: str, verdict: str) -> str:
    """Records the user's verdict on a flagged merchant and adjusts
    future detection accordingly."""
    flags, _, _ = _current_flags()
    match = next(
        (f for f in flags if merchant_name.lower() in f["reasoning"].lower()), None
    )
    if not match:
        return f"I don't have an open flag mentioning '{merchant_name}' right now."

    verdict = verdict if verdict in ("confirmed", "false_positive") else "confirmed"
    updated = record_feedback(match, verdict, merchant_name=merchant_name, profile_path=_profile_path())
    last = updated["feedback_log"][-1]

    if "adjustment" in last:
        return f"Got it. {last['adjustment']}."
    return f"Got it, recorded as {verdict.replace('_', ' ')} for {merchant_name}."


# ---------------------------------------------------------------------
# Schemas (Gemini function-calling format)
# ---------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "check_money_flags",
        "description": (
            "Checks the user's finances for fraud, waste, forgotten subscriptions, "
            "duplicate charges, or refunds owed. Use this whenever the user asks "
            "how their money looks, if anything's wrong with their accounts, or "
            "about subscriptions/charges they might have forgotten."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confidence": {
                    "type": "string",
                    "enum": ["all", "high", "medium", "low"],
                    "description": "Filter to one confidence tier, or 'all' (default).",
                }
            },
        },
    },
    {
        "name": "get_money_forecast",
        "description": (
            "Reports a forecast of the user's spending for next month, including "
            "how much they could save by acting on flagged waste. Use this when "
            "the user asks about their budget, forecast, or how much they'll spend."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "give_money_feedback",
        "description": (
            "Records the user's verdict on a specific flagged merchant or charge — "
            "whether the flag was correct or a false positive — and adjusts future "
            "detection for that merchant. Use this when the user confirms a flag is "
            "right, or says a flagged charge is actually fine / still in use."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "merchant_name": {
                    "type": "string",
                    "description": "The merchant or service name the user is referring to.",
                },
                "verdict": {
                    "type": "string",
                    "enum": ["confirmed", "false_positive"],
                    "description": (
                        "'confirmed' if the user agrees the flag is right, "
                        "'false_positive' if the user says it's actually fine."
                    ),
                },
            },
            "required": ["merchant_name", "verdict"],
        },
    },
]

TOOL_FUNCTIONS = {
    "check_money_flags": check_money_flags,
    "get_money_forecast": get_money_forecast,
    "give_money_feedback": give_money_feedback,
}
