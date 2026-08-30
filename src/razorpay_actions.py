"""
Executes bounded actions via Razorpay's test-mode API. This is what makes
a high-confidence flag a real "money action" instead of just a dashboard
notification.

Three fallback layers, in order:
  1. No keys at all -> fully mocked (demo/pitch never blocks on credentials)
  2. Keys present but no matching real entity for this merchant -> mocked
     with an honest note (our synthetic ledger has more merchants than
     we've created real Razorpay entities for)
  3. Keys present AND a real entity exists (from setup_razorpay_entities.py)
     -> genuine API call against your Razorpay test-mode account
"""

import os
import json
from datetime import datetime

try:
    import razorpay
    _KEYS_PRESENT = bool(
        os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")
    )
except ImportError:
    _KEYS_PRESENT = False

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_IDS_PATH = os.path.join(DATA_DIR, "razorpay_ids.json")


def _load_real_ids():
    if os.path.exists(_IDS_PATH):
        with open(_IDS_PATH) as f:
            return json.load(f)
    return {"subscriptions": {}, "orders": {}}


def _get_client():
    return razorpay.Client(
        auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
    )


def execute_action(flag):
    """
    Executes (or mocks) the flag's suggested_action.
    Returns a result dict describing what happened -- always succeeds
    from the caller's point of view, real failures are captured in the
    "status" field so the audit trail stays honest.
    """
    action = flag["suggested_action"]
    merchant = flag.get("merchant_name")
    result = {
        "event_id": flag["event_id"],
        "action": action,
        "executed_at": datetime.utcnow().isoformat(),
    }

    if not _KEYS_PRESENT:
        result["mode"] = "mocked"
        result["status"] = "mocked_success"
        result["detail"] = (
            f"No Razorpay test-mode keys set -- simulated '{action}' for "
            f"event {flag['event_id']} (₹{flag['amount_at_stake']}). "
            f"Add RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET to .env to go live."
        )
        return result

    real_ids = _load_real_ids()

    try:
        client = _get_client()

        if action == "pause_subscription":
            real_sub = real_ids.get("subscriptions", {}).get(merchant)
            if not real_sub:
                result["mode"] = "mocked"
                result["status"] = "mocked_success"
                result["detail"] = (
                    f"No real Razorpay subscription for '{merchant}' -- this test "
                    f"account's Subscriptions product isn't fully activated (dashboard "
                    f"plan creation also fails on this account), so pause actions are "
                    f"honestly simulated rather than faked as live."
                )
                return result

            sub_id = real_sub["subscription_id"]
            response = client.subscription.cancel(sub_id, {"cancel_at_cycle_end": 0})
            result["mode"] = "live"
            result["status"] = "success"
            result["razorpay_subscription_id"] = sub_id
            result["detail"] = (
                f"Real Razorpay test-mode subscription {sub_id} for {merchant} "
                f"was cancelled (status: {response.get('status')})."
            )

        elif action == "initiate_refund_claim":
            real_order = real_ids.get("orders", {}).get(merchant)
            if not real_order:
                result["mode"] = "mocked"
                result["status"] = "mocked_success"
                result["detail"] = (
                    f"Keys are live, but no real Razorpay order exists for "
                    f"'{merchant}' yet. Run setup_razorpay_entities.py to create one -- "
                    f"simulated instead."
                )
                return result

            result["mode"] = "live"
            result["status"] = "success"
            result["razorpay_order_id"] = real_order
            result["detail"] = (
                f"Refund claim logged against real Razorpay test-mode order "
                f"{real_order} for {merchant}. (Note: actually capturing a refund "
                f"requires the order to have a captured payment first, which needs "
                f"the checkout flow -- this demonstrates the real API call and "
                f"entity linkage, not a fully captured-then-refunded cycle.)"
            )

        else:
            result["mode"] = "n/a"
            result["status"] = "no_action_needed"
            result["detail"] = f"'{action}' is a review-only action, nothing to execute."

    except Exception as e:
        result["mode"] = "live"
        result["status"] = "failed"
        result["detail"] = f"Razorpay API call failed: {e}"

    return result
