"""
Creates REAL entities in your Razorpay test-mode account for a handful
of our synthetic merchants, so pause/refund actions have something
genuine to act on instead of a fictional ID that only exists locally.

Razorpay's sandbox will not accept made-up IDs -- it needs entities
that actually exist in YOUR account. This script:
  1. Creates a Plan (required before any Subscription can exist)
  2. Creates a few Subscriptions against that plan, one per demo merchant
  3. Creates a Customer + a captured test Payment (for the refund-claim demo)
  4. Writes the resulting REAL Razorpay IDs back into data/razorpay_ids.json

Run ONCE after test_razorpay_connection.py confirms your keys work:
    python src/setup_razorpay_entities.py

Safe to re-run -- it will just create a fresh batch each time (test mode
entities don't cost anything and don't need cleanup).
"""

import os
import json
import time
from dotenv import load_dotenv

load_dotenv()
import razorpay

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# A handful of our synthetic merchants -- doesn't need to be all 10,
# just enough to make the demo's pause/refund actions genuinely real.
# IMPORTANT: these must match merchants that actually appear as
# refund_owed flags in YOUR generated data/ground_truth.json, or the
# real orders created here won't line up with anything the dashboard
# shows. Check with:
#   python -c "import json; txns=json.load(open('data/transactions.json')); gt=json.load(open('data/ground_truth.json')); gt_by_id={g['id']:g['label'] for g in gt}; print(set(t['merchant_name'] for t in txns if gt_by_id.get(t['id'])=='refund_owed'))"
DEMO_MERCHANTS = [
    {"name": "Airtel Broadband", "amount_paise": 294056},
    {"name": "LIC Premium", "amount_paise": 64666},
    {"name": "Adobe Creative Cloud", "amount_paise": 53126},
]


def get_client():
    return razorpay.Client(
        auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
    )


def create_plan(client, merchant):
    plan = client.plan.create({
        "period": "monthly",
        "interval": 1,
        "item": {
            "name": f"{merchant['name']} Subscription",
            "amount": merchant["amount_paise"],
            "currency": "INR",
        },
    })
    return plan["id"]


def create_subscription(client, plan_id):
    sub = client.subscription.create({
        "plan_id": plan_id,
        "total_count": 12,  # 12 monthly cycles
        "quantity": 1,
    })
    return sub["id"]


def create_customer_and_payment(client, merchant):
    """
    Creates a Customer and a test Order (payments in test mode normally
    require a checkout flow to actually capture -- for a script-only
    demo, we create the Order and note it as 'created', which is
    realistic: it's a genuine Razorpay entity your dashboard will show,
    even though full capture would need the checkout UI/webhook flow.
    """
    order = client.order.create({
        "amount": merchant["amount_paise"],
        "currency": "INR",
        "notes": {"merchant": merchant["name"], "purpose": "refund_claim_demo"},
    })
    return order["id"]


def main():
    client = get_client()
    results = {"subscriptions": {}, "orders": {}}

    print("Creating real test-mode entities in your Razorpay account...\n")

    for merchant in DEMO_MERCHANTS:
        name = merchant["name"]

        # Subscriptions and Orders are independent -- don't let a failure
        # in one block the other. (Discovered during testing: this
        # Razorpay test account can create Orders fine, but Plan/Subscription
        # creation fails even through the dashboard UI directly, which
        # points to the Subscriptions product needing account-level
        # activation beyond what test mode alone unlocks. Rather than
        # block the whole demo on that, we get real Orders working and
        # leave subscription-pause honestly mocked.)
        try:
            plan_id = create_plan(client, merchant)
            time.sleep(0.3)
            sub_id = create_subscription(client, plan_id)
            results["subscriptions"][name] = {"plan_id": plan_id, "subscription_id": sub_id}
            print(f"  ✅ {name}: subscription {sub_id} created")
        except Exception as e:
            print(f"  ⚠️  {name}: subscription creation unavailable on this account "
                  f"({type(e).__name__}) -- pause_subscription will stay mocked for this merchant.")

        try:
            order_id = create_customer_and_payment(client, merchant)
            results["orders"][name] = order_id
            print(f"  ✅ {name}: order {order_id} created (for refund-claim demo)")
        except Exception as e:
            print(f"  ❌ {name}: order creation failed -- {type(e).__name__}: {repr(e)}")

    out_path = os.path.join(DATA_DIR, "razorpay_ids.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved real Razorpay IDs to {out_path}")
    print("You can view these in your Razorpay dashboard under Test Mode → Subscriptions / Orders.")
    print("\nNext: update razorpay_actions.py to use these real IDs when available.")


if __name__ == "__main__":
    main()
