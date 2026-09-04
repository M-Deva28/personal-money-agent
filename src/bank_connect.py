"""
Bank account connection — RazorpayX (test mode).

Connecting a bank account here means registering it with the agent's
RazorpayX as a *fund account*: RazorpayX validates the holder name against
the account via its own checks and returns a stable fund_account_id. The
agent never stores the raw account number — only a masked form — and keeps
the authoritative reference on RazorpayX.

Same philosophy as razorpay_actions.py: when RAZORPAY_KEY_ID /
RAZORPAY_KEY_SECRET are not set the call is *simulated* and clearly labeled
(mode: mocked) so the demo and pitch are never blocked on live credentials;
when keys are set, real RazorpayX API calls run instead.

Contact + fund-account endpoints (test mode) live on the standard
Payments API host, https://api.razorpay.com/v1 (the legacy dedicated
RazorpayX host api.razorpayx.com has been retired — its DNS no longer
resolves, and current Razorpay docs point contacts/fund_accounts at the
Payments API with the normal test key pair):
  POST  /contacts         create the contact the fund account belongs to
  POST  /fund_accounts    register + validate the bank account
  PATCH /fund_accounts/{id}   body {"active": false} to deactivate on disconnect

Note on auto-import: RazorpayX is a payout-side API — it validates the bank
account but does not stream that account's personal transaction statement.
The demo feed below is therefore tagged source:"bank_feed_demo" so auto-
imported rows are never mistaken for a real bank statement.
"""

import base64
import json
import os
import random
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta

_RAZORPAYX_BASE = "https://api.razorpay.com/v1"

# Small IFSC-prefix -> bank name map for readable labels. The live API
# returns the real bank name; this only matters for the mocked path.
_IFSC_BANKS = {
    "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "State Bank of India",
    "AXIS": "Axis Bank", "KKBK": "Kotak Mahindra", "PUNB": "Punjab National Bank",
    "YESB": "Yes Bank", "UTIB": "Axis Bank (UTI)", "BARB": "Bank of Baroda",
    "CNRB": "Canara Bank", "INDB": "IndusInd Bank", "FDRL": "Federal Bank",
}

_DAILY_MERCHANTS = [
    ("Local Grocery", "misc"), ("Metro Card Recharge", "misc"),
    ("Electricity Board", "utility"), ("Cafe Coffee Day", "misc"),
    ("Petrol Pump", "misc"), ("Big Bazaar", "shopping"),
    ("Pharmacy Plus", "misc"), ("Swiggy", "food"), ("Zomato", "food"),
    ("BookMyShow", "misc"), ("Uber", "misc"), ("DMart", "shopping"),
]


def keys_present():
    return bool(
        os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")
    )


def bank_name_for_ifsc(ifsc: str) -> str:
    return _IFSC_BANKS.get((ifsc or "")[:4], "Bank")


def validate_bank_inputs(holder_name: str, account_number: str, ifsc: str, phone: str = ""):
    """Format checks only — returns (holder, account, ifsc, phone) normalized.
    Raises ValueError with a human message on failure."""
    holder = (holder_name or "").strip()
    if len(holder) < 3:
        raise ValueError("Account holder name must be at least 3 characters.")

    account = (account_number or "").strip()
    if not account.isdigit() or not (9 <= len(account) <= 18):
        raise ValueError("Account number must be 9–18 digits (no spaces or dashes).")

    ifsc = (ifsc or "").strip().upper()
    if len(ifsc) != 11 or not ifsc[:4].isalpha() or not ifsc[4:].isalnum():
        raise ValueError("IFSC must be an 11-character code, e.g. HDFC0001234.")

    phone = (phone or "").strip()
    if phone and (not phone.isdigit() or not (10 <= len(phone) <= 15)):
        raise ValueError("Phone must be 10–15 digits if provided.")

    return holder, account, ifsc, phone


def mask_account(account_number: str) -> str:
    return "••••" + account_number[-4:]


# ---------------------------------------------------------------------
# Live RazorpayX calls (used only when keys are configured)
# ---------------------------------------------------------------------

def _live_request(method: str, path: str, body=None):
    key_id = os.environ["RAZORPAY_KEY_ID"]
    key_secret = os.environ["RAZORPAY_KEY_SECRET"]
    creds = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        _RAZORPAYX_BASE + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"RazorpayX API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach RazorpayX: {e}")


def _live_create_contact(holder, email, phone):
    body = {
        "name": holder,
        "email": email or f"contact-{uuid.uuid4().hex[:8]}@example.in",
        "contact": phone or "9999999999",
        "type": "customer",
    }
    return _live_request("POST", "/contacts", body)


def _live_create_fund_account(contact_id, holder, account_number, ifsc):
    body = {
        "contact_id": contact_id,
        "account_type": "bank_account",
        "bank_account": {
            "name": holder,
            "ifsc": ifsc,
            "account_number": account_number,
        },
    }
    return _live_request("POST", "/fund_accounts", body)


def _live_deactivate_fund_account(fund_account_id):
    # Current API: PATCH the fund account with active=false (the legacy
    # POST /fund_accounts/{id}/deactivate route returns 404 now).
    return _live_request("PATCH", f"/fund_accounts/{fund_account_id}", {"active": False})


def verify_fund_account(fund_account_id) -> str:
    """Returns a short status string describing the account's state on
    RazorpayX (best effort — any failure reports 'unreachable')."""
    try:
        data = _live_request("GET", f"/fund_accounts/{fund_account_id}")
        return data.get("status", "active")
    except Exception:
        return "unreachable"


# ---------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------

def connect_bank_account(holder_name, account_number, ifsc, phone="", email=""):
    """
    Returns a dict: {ok, mode, detail, connection}. 'connection' carries
    only non-sensitive, displayable fields — the raw account number never
    leaves this function for storage.
    """
    holder, account, ifsc, phone = validate_bank_inputs(holder_name, account_number, ifsc, phone)
    mode = "live" if keys_present() else "mocked"
    connection = {
        "fund_account_id": None,
        "holder_name": holder,
        "masked_account": mask_account(account),
        "ifsc": ifsc,
        "bank_name": bank_name_for_ifsc(ifsc),
        "mode": mode,
        "status": "active",
        "contact_id": None,
        "connected_at": datetime.utcnow().isoformat(),
        "synced_at": None,
        "last_sync_note": None,
        "source_of_transactions": "bank_feed_demo" if mode == "mocked" else "razorpayx",
    }

    if mode == "mocked":
        connection["fund_account_id"] = "fa_demo_" + uuid.uuid4().hex[:8]
        connection["contact_id"] = "contact_demo_" + uuid.uuid4().hex[:6]
        return {
            "ok": True,
            "mode": "mocked",
            "connection": connection,
            "detail": (
                "No RazorpayX test keys set (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET) — "
                "validation simulated in demo mode. Account and holder details were "
                "format-checked locally; add test keys to .env to validate through "
                "RazorpayX for real."
            ),
        }

    try:
        contact = _live_create_contact(holder, email, phone)
        fa = _live_create_fund_account(contact["id"], holder, account, ifsc)
        connection["fund_account_id"] = fa.get("id")
        connection["contact_id"] = contact.get("id")
        connection["bank_name"] = fa.get("bank_name", bank_name_for_ifsc(ifsc))
        connection["status"] = fa.get("status", "active")
        return {
            "ok": True,
            "mode": "live",
            "connection": connection,
            "detail": f"Bank account verified with RazorpayX (fund account {fa.get('id')}).",
        }
    except Exception as e:
        return {"ok": False, "mode": "live", "detail": f"RazorpayX connect failed: {e}"}


def disconnect_bank_account(connection):
    """Best-effort deactivation on RazorpayX; local removal always proceeds.
    Returns a human note describing the outcome."""
    if connection.get("mode") == "live" and connection.get("fund_account_id") and keys_present():
        try:
            _live_deactivate_fund_account(connection["fund_account_id"])
            return "Fund account deactivated on Razorpay; removed locally."
        except Exception as e:
            return (
                f"Couldn't deactivate the Razorpay fund account ({e}); "
                "removed locally only."
            )
    return "No live fund account to deactivate; removed locally."


# ---------------------------------------------------------------------
# Auto-import ("Sync now")
# ---------------------------------------------------------------------

def build_demo_feed(connection, user_id, days_back=7, n=8):
    """
    Generates a small, clearly-labeled batch of recent transactions for a
    connected account (source: bank_feed_demo). Used when no real
    statement feed is available — RazorpayX validates the account but does
    not stream personal statements. Transactions look like ordinary spend
    so the full detection pipeline runs on them.
    """
    rng = random.Random(f"{user_id}:{connection['fund_account_id']}:{datetime.utcnow().date()}")
    merchant_pool = [m for m in _DAILY_MERCHANTS for _ in range(rng.randint(1, 3))]
    now = datetime.utcnow()
    feed = []
    used = set()
    for _ in range(rng.randint(max(3, n - 3), n + 2)):
        merchant, category = rng.choice(merchant_pool)
        key = (merchant, category)
        if key in used:
            continue
        used.add(key)
        ts = now - timedelta(days=rng.uniform(0, days_back), hours=rng.uniform(0, 23))
        feed.append({
            "id": "txn_" + uuid.uuid4().hex[:8],
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "amount": round(rng.uniform(80, 1800), 2),
            "direction": "debit",
            "merchant_name": merchant,
            "category": category,
            "payment_mode": rng.choice(["UPI", "card", "netbanking"]),
            "memo": "",
            "linked_subscription_id": None,
            "account": f"{connection['bank_name']} {connection['masked_account']}",
            "source": "bank_feed_demo",
            "imported_at": now.isoformat(),
        })
    feed.sort(key=lambda t: t["timestamp"])
    return feed
