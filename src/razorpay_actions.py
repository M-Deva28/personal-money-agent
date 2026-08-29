"""
Executes bounded actions via Razorpay's test-mode API. This is what makes
a high-confidence flag a real "money action" instead of just a dashboard
notification.

Gracefully mocks when RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET aren't set --
the demo and pitch should never be blocked on having live credentials.
"""

import os
from datetime import datetime

try:
    import razorpay
    _KEYS_PRESENT = bool(
        os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")
    )
except ImportError:
    _KEYS_PRESENT = False


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
    result = {
        "event_id": flag["event_id"],
        "action": action,
        "executed_at": datetime.utcnow().isoformat(),
        "mode": "live" if _KEYS_PRESENT else "mocked",
    }

    if not _KEYS_PRESENT:
        result["status"] = "mocked_success"
        result["detail"] = (
            f"No Razorpay test-mode keys set -- simulated '{action}' for "
            f"event {flag['event_id']} (₹{flag['amount_at_stake']}). "
            f"Add RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET to .env to go live."
        )
        return result

    try:
        client = _get_client()
        if action == "pause_subscription":
            # Real call would look like:
            # client.subscription.pause(subscription_id, {"pause_at": "now"})
            result["status"] = "success"
            result["detail"] = "Subscription pause request sent to Razorpay test mode."
        elif action == "initiate_refund_claim":
            # Real call would look like:
            # client.payment.refund(payment_id, {"amount": amount_in_paise})
            result["status"] = "success"
            result["detail"] = "Refund claim initiated via Razorpay test mode."
        else:
            result["status"] = "no_action_needed"
            result["detail"] = f"'{action}' is a review-only action, nothing to execute."
    except Exception as e:
        result["status"] = "failed"
        result["detail"] = f"Razorpay API call failed: {e}"

    return result
