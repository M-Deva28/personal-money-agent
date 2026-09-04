"""
Audit trail — every decision the agent makes, high-confidence or needs-review,
gets written here. This is the single artifact that proves the agent is
bounded, explainable, and gated (not a black box).
"""

import json
import os
import time
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "audit_trail.json")

# Callers may pass an explicit log path (each user keeps their own trail
# under data/users/<id>/audit_trail.json); the CLI scripts fall back to
# the bundled logs/audit_trail.json above.

# Windows (especially inside a OneDrive-synced folder) can transiently
# refuse a delete/write if another handle -- even a just-closed one from
# a concurrent request -- still has the file open. A short retry avoids
# that surfacing as a 500 to the dashboard, which polls several
# endpoints (each touching this file) at the same time.
_RETRY_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 0.05


def _with_retry(func):
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return func()
        except PermissionError:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))


def build_entry(flag, action_taken):
    """Pure -- no file I/O. Builds one audit entry dict for a flag."""
    return {
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


def write_trail(entries, log_path=None):
    """
    Writes the WHOLE audit trail in one file operation. Prefer this over
    log_decision() in a loop when logging a full pipeline run (see
    main.py) -- one write instead of one per flag is both faster and
    shrinks the window for the concurrent-access race the retry logic
    above is guarding against.
    """
    log_path = log_path or LOG_PATH

    def _do():
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            json.dump(entries, f, indent=2)
    _with_retry(_do)


def log_decision(flag, action_taken, log_path=None):
    """
    Single-entry logger (read, append, write) -- kept for any external
    caller that logs one decision at a time. main.py's pipeline uses
    build_entry() + write_trail() instead, to avoid N separate file
    writes for N flags.
    """
    log_path = log_path or LOG_PATH
    entry = build_entry(flag, action_taken)

    def _do():
        trail = []
        if os.path.exists(log_path):
            with open(log_path) as f:
                trail = json.load(f)
        trail.append(entry)
        with open(log_path, "w") as f:
            json.dump(trail, f, indent=2)

    _with_retry(_do)
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
    def _do():
        if os.path.exists(LOG_PATH):
            os.remove(LOG_PATH)
    _with_retry(_do)

