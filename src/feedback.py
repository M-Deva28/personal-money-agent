"""
Feedback loop — the "adapts to different situations" layer.

When a user marks a flag as correct or a false positive, we don't just
log it — we adjust THAT USER's thresholds going forward. This is what
turns a static rule engine into something that learns a person's normal.

Design choice: adjustments are per-pattern and, where relevant, per-merchant
(e.g. "I don't use my gym app often, stop flagging it at 60 days" shouldn't
change your Netflix threshold too).
"""

import json
import os
from datetime import datetime

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "user_profile.json")

DEFAULT_PROFILE = {
    "zombie_sub_days": 60,               # global default idle threshold
    "zombie_sub_days_by_merchant": {},   # per-merchant overrides learned from feedback
    "price_hike_threshold": 0.15,
    "refund_grace_days": 10,
    "duplicate_window_minutes": 10,
    "trusted_merchants_extra": [],       # merchants the user has confirmed are fine
    "autopay_enabled_merchants": [],     # merchants the user has opted into bill autopay for
    "feedback_log": [],
}


def load_profile():
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH) as f:
            return json.load(f)
    return json.loads(json.dumps(DEFAULT_PROFILE))  # deep copy


def save_profile(profile):
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)


def record_feedback(flag, verdict, merchant_name=None):
    """
    flag: the original flag dict from the detector
    verdict: "confirmed" (agent was right) or "false_positive" (agent was wrong)
    merchant_name: needed for merchant-specific adjustments (e.g. zombie_sub)

    Returns the updated profile so callers can see what changed.
    """
    profile = load_profile()
    pattern = flag["pattern"]

    entry = {
        "logged_at": datetime.utcnow().isoformat(),
        "event_id": flag["event_id"],
        "pattern": pattern,
        "verdict": verdict,
        "merchant_name": merchant_name,
    }
    profile["feedback_log"].append(entry)

    if verdict == "false_positive":
        if pattern == "zombie_sub" and merchant_name:
            # Widen the idle window for THIS merchant specifically.
            # Must clear the actual days_idle that triggered this flag, not
            # just bump by a fixed amount -- otherwise a 196-day-idle case
            # survives a naive +30 adjustment. Add a 30-day safety margin
            # on top of what actually happened.
            current = profile["zombie_sub_days_by_merchant"].get(
                merchant_name, profile["zombie_sub_days"]
            )
            observed_idle = flag.get("days_idle", current)
            new_threshold = max(current + 30, observed_idle + 30)
            profile["zombie_sub_days_by_merchant"][merchant_name] = new_threshold
            entry["adjustment"] = (
                f"{merchant_name} zombie threshold raised to "
                f"{profile['zombie_sub_days_by_merchant'][merchant_name]} days "
                f"(cleared observed {observed_idle}-day idle case)"
            )

        elif pattern == "price_hike":
            profile["price_hike_threshold"] = round(profile["price_hike_threshold"] + 0.05, 2)
            entry["adjustment"] = (
                f"price_hike threshold raised to {profile['price_hike_threshold']:.0%}"
            )

        elif pattern == "suspicious_collect" and merchant_name:
            if merchant_name not in profile["trusted_merchants_extra"]:
                profile["trusted_merchants_extra"].append(merchant_name)
            entry["adjustment"] = f"{merchant_name} added to trusted payees"

        elif pattern == "refund_owed":
            profile["refund_grace_days"] += 5
            entry["adjustment"] = f"refund grace period extended to {profile['refund_grace_days']} days"

    save_profile(profile)
    return profile


def reset_profile():
    if os.path.exists(PROFILE_PATH):
        os.remove(PROFILE_PATH)


def set_autopay(merchant_name, enabled):
    """
    Toggles bill autopay for a specific merchant. This is an explicit,
    per-merchant opt-in -- the agent never auto-pays a merchant the user
    hasn't specifically enabled it for, even for known/trusted merchants.
    """
    profile = load_profile()
    current = set(profile.get("autopay_enabled_merchants", []))
    if enabled:
        current.add(merchant_name)
    else:
        current.discard(merchant_name)
    profile["autopay_enabled_merchants"] = sorted(current)
    save_profile(profile)
    return profile
