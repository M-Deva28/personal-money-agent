"""
Diagnostic only -- hits the Razorpay Plans API directly with `requests`
so we can see the RAW response body, since the razorpay-python SDK's
ServerError was swallowing the actual message.

Run: python src/diagnose_razorpay.py
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

key_id = os.environ["RAZORPAY_KEY_ID"]
key_secret = os.environ["RAZORPAY_KEY_SECRET"]

print("Testing direct POST to /v1/plans ...\n")

response = requests.post(
    "https://api.razorpay.com/v1/plans",
    auth=(key_id, key_secret),
    json={
        "period": "monthly",
        "interval": 1,
        "item": {
            "name": "Netflix Subscription",
            "amount": 19900,
            "currency": "INR",
        },
    },
)

print(f"Status code: {response.status_code}")
print(f"Raw response body:\n{response.text}")

try:
    print(f"\nParsed JSON:\n{json.dumps(response.json(), indent=2)}")
except Exception:
    print("\n(response was not valid JSON)")