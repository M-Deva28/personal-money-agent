"""
Audit trail — every decision the agent makes, high-confidence or needs-review,
gets written here. This is the single artifact that proves the agent is
bounded, explainable, and gated (not a black box).
"""

import json
import os
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "audit_trail.json")


def log_decision(flag, action_taken):
    """
    action_taken: one of "auto_executed", "needs_review", "dry_run"
    """
    entry = {
        "logged_at": datetime.utcnow().isoformat(),
        "event_id": flag["event_id"],
        "event_type": flag["event_type"],
        "pattern": flag["pattern"],
        "confidence": flag["confidence"],
        "reasoning": flag["reasoning"],
        "suggested_action": flag["suggested_action"],
        "amount_at_stake": flag["amount_at_stake"],
        "action_taken": action_taken,
    }

    trail = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            trail = json.load(f)
    trail.append(entry)
    with open(LOG_PATH, "w") as f:
        json.dump(trail, f, indent=2)

    return entry


def decide_action(flag):
    """
    Gating logic: high confidence -> auto-execute (bounded action),
    medium confidence -> needs_review (agent does NOT act alone).
    This is the "gated" part of the pitch.
    """
    if flag["confidence"] == "high":
        return "auto_executed"
    return "needs_review"


def clear_log():
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
