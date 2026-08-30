"""
Run this FIRST after adding your keys to .env, before anything else.
Confirms your Razorpay test-mode keys actually work with a safe,
read-only API call (listing orders -- doesn't create or change anything).

Run: python src/test_razorpay_connection.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

import razorpay


def main():
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        print("❌ RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not found in .env")
        print("   Make sure you copied .env.example to .env and filled both in.")
        return

    if not key_id.startswith("rzp_test_"):
        print(f"⚠️  Warning: key '{key_id[:12]}...' doesn't look like a TEST key "
              f"(should start with 'rzp_test_'). Double-check you're not using live keys.")

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        result = client.order.all({"count": 1})  # safe, read-only
        print("✅ Connected to Razorpay successfully!")
        print(f"   Key ID: {key_id}")
        print(f"   Existing orders in this test account: {result.get('count', 0)}")
        print()
        print("You're ready for the next step: creating demo subscriptions/payments.")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("   Double check your Key ID and Key Secret are correct and you're")
        print("   using TEST mode keys, not live keys.")


if __name__ == "__main__":
    main()
