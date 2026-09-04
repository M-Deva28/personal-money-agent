"""
FastAPI wrapper — turns the detection/feedback/audit pipeline into a
real service a dashboard (or the Razorpay judges) can hit directly.

Multi-user since the auth upgrade: every account has its own isolated
data under data/users/<id>/ (transactions, subscriptions, learned
profile, audit trail, bank connections). All data endpoints require a
signed session cookie (see security.py). The bundled data/ dataset
survives as the repo's canonical demo dataset and seeds a "demo" account
on first start.

Run: uvicorn main:app --reload --port 8000
Docs: http://localhost:8000/docs  (FastAPI auto-generates this)
Demo login: demo@pma.local / demo1234
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()  # must run before jarvis_brain import below, which reads
                # GEMINI_API_KEY at import time -- same for ANTHROPIC_API_KEY
                # in llm_reasoner.py and the RAZORPAY_* keys further down.

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

import security
import store
import bank_connect
from detector import run_all_detectors
from feedback import record_feedback, load_profile, set_autopay
from audit import build_entry, write_trail, decide_action
from scoring import compute_score
from llm_reasoner import review_all_medium_confidence
from razorpay_actions import execute_action, set_user_context as set_rzp_context
from finance import build_finance_report
from jarvis_brain import respond as jarvis_respond, GeminiUnavailable
from growth import growth_summary, set_user_context as set_growth_context
from accounts import (
    AA_PROVIDERS,
    list_connected_accounts,
    connect_account,
    disconnect_account,
    add_manual_subscription,
)
import voice_tools

# One-time migration: seed the "demo" account from the bundled dataset on
# the first start after upgrading to multi-user. Safe to call on every
# boot — it no-ops once data/users/index.json exists.
store.migrate_legacy_data()

app = FastAPI(title="Personal Money Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a local demo; tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache of the last computed flags, keyed by (user_id, flag_id) -- needed
# so /feedback can look up the original flag when the user submits a verdict.
_flag_cache = {}


# ---------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------

def _valid_email(email: str) -> bool:
    email = email.strip()
    if "@" not in email or " " in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local and "." in domain and len(domain) >= 4)


def current_user(request: Request) -> str:
    """FastAPI dependency: resolves the signed session cookie to a user id."""
    token = request.cookies.get(security.cookie_name(), "")
    user_id = security.verify_session_token(token)
    if not user_id or store.load_account(user_id) is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user_id


def _set_session_cookie(resp: JSONResponse, user_id: str):
    token = security.issue_session_token(user_id)
    resp.set_cookie(
        security.cookie_name(),
        token,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


# ---------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------

class RegisterIn(BaseModel):
    name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class LoginIn(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class FeedbackRequest(BaseModel):
    flag_id: str
    verdict: str  # "confirmed" or "false_positive"
    merchant_name: str | None = None


class TransactionIn(BaseModel):
    amount: float = Field(..., gt=0)
    merchant_name: str = Field(..., min_length=1, max_length=80)
    direction: str = "debit"          # debit | credit
    category: str = "misc"
    payment_mode: str = "UPI"
    timestamp: str | None = None      # ISO; defaults to now
    memo: str = ""
    account: str = "manual"
    linked_subscription_id: str | None = None


class ConnectIn(BaseModel):
    holder_name: str
    account_number: str
    ifsc: str
    phone: str | None = None


class SyncIn(BaseModel):
    fund_account_id: str | None = None


class AutopayIn(BaseModel):
    merchant_name: str
    enabled: bool


class ProviderConnectIn(BaseModel):
    provider_id: str


class AccountIdIn(BaseModel):
    account_id: str


class ManualSubscriptionIn(BaseModel):
    merchant_name: str
    amount: float
    billing_cycle: str | None = "monthly"
    started_date: str | None = None
    last_charged_date: str | None = None
    next_due_date: str | None = None
    status: str | None = "active"
    trial_end_date: str | None = None
    last_usage_signal_date: str | None = None


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    history: list[ChatMessage]


# ---------------------------------------------------------------------
# Pipeline (per user)
# ---------------------------------------------------------------------

def _run_pipeline(user_id: str):
    """Single source of truth: load THE USER's data, detect, review, log,
    cache. Runs under the user's write lock so the dashboard's four
    concurrent fetches serialize instead of racing each other's file
    writes (see the lock comment below)."""
    with store.user_write_lock(user_id):
        transactions = store.read_user_json(user_id, "transactions.json", [])
        subscriptions = store.read_user_json(user_id, "subscriptions.json", [])
        profile_path = store.user_path(user_id, "profile.json")
        profile = load_profile(path=profile_path)
        audit_path = store.user_path(user_id, "audit_trail.json")

        flags = run_all_detectors(transactions, subscriptions, profile=profile)
        flags = review_all_medium_confidence(flags)

        entries = []
        set_rzp_context(store.user_dir(user_id))  # real Razorpay entity IDs are per-user
        for flag in flags:
            action = decide_action(flag)
            entries.append(build_entry(flag, action))
            if action == "auto_executed":
                flag["execution_result"] = execute_action(flag)
        write_trail(entries, log_path=audit_path)

        _flag_cache[user_id] = {f["flag_id"]: f for f in flags}
        return flags


def _user_flag_cache(user_id: str):
    return _flag_cache.setdefault(user_id, {})


# ---------------------------------------------------------------------
# Open routes
# ---------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "Personal Money Agent",
        "auth": ["/register (POST)", "/login (POST)", "/logout (POST)", "/me"],
        "data_endpoints": ["/flags", "/feedback (POST)", "/audit", "/score", "/finance", "/growth", "/chat (POST)", "/transactions", "/subscriptions", "/bank", "/accounts"],
        "pages": ["/dashboard", "/login.html", "/register.html"],
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """
    Live dashboard, served straight from the same app so it can call
    /flags, /feedback, /audit, /finance same-origin (no CORS setup
    needed for the demo). Read from disk on every request rather than
    caching, so edits to dashboard.html show up on a plain refresh.
    """
    path = os.path.join(os.path.dirname(__file__), "..", "static", "dashboard.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="static/dashboard.html not found")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _serve_page(filename: str):
    path = os.path.join(os.path.dirname(__file__), "..", "static", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"static/{filename} not found")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/login.html", response_class=HTMLResponse)
def login_page():
    return _serve_page("login.html")


@app.get("/register.html", response_class=HTMLResponse)
def register_page():
    return _serve_page("register.html")


@app.get("/auth.css", response_class=Response)
def auth_css():
    """Shared stylesheet for the auth pages. Served as text/css — browsers
    refuse to apply a stylesheet whose Content-Type is text/html."""
    path = os.path.join(os.path.dirname(__file__), "..", "static", "auth.css")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="static/auth.css not found")
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), media_type="text/css",
                        headers={"Cache-Control": "no-store"})


@app.get("/auth.js", response_class=Response)
def auth_js():
    """Shared interactivity for the auth pages (served with a JS media
    type — the browser refuses to execute scripts served as HTML)."""
    path = os.path.join(os.path.dirname(__file__), "..", "static", "auth.js")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="static/auth.js not found")
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------

@app.post("/register")
def register(body: RegisterIn):
    name = body.name.strip()
    email = body.email.strip().lower()
    password = body.password

    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Please enter your full name.")
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid email address.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    try:
        user_id = store.create_account(email, name, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    resp = JSONResponse({"status": "registered", "user": store.public_user(user_id)})
    _set_session_cookie(resp, user_id)
    return resp


@app.post("/login")
def login(body: LoginIn):
    email = body.email.strip().lower()
    record = store.find_user_by_email(email)
    if not record:
        raise HTTPException(status_code=401, detail="No account found for that email.")
    acct = store.load_account(record["id"])
    if not acct or not security.verify_password(body.password, acct.get("password", {})):
        raise HTTPException(status_code=401, detail="Incorrect password.")

    resp = JSONResponse({"status": "ok", "user": store.public_user(record["id"])})
    _set_session_cookie(resp, record["id"])
    return resp


@app.post("/logout")
def logout():
    resp = JSONResponse({"status": "signed_out"})
    resp.set_cookie(security.cookie_name(), "", max_age=0, httponly=True,
                    samesite="lax", path="/")
    return resp


@app.get("/me")
def me(user_id: str = Depends(current_user)):
    txns = store.read_user_json(user_id, "transactions.json", [])
    conns = store.read_user_json(user_id, "connections.json", [])
    return {
        "user": store.public_user(user_id),
        "stats": {
            "transaction_count": len(txns),
            "connected_accounts": len(conns),
        },
        "bank_mode": "live" if bank_connect.keys_present() else "mocked",
    }


# ---------------------------------------------------------------------
# Data endpoints (all require a session)
# ---------------------------------------------------------------------

@app.get("/flags")
def get_flags(user_id: str = Depends(current_user)):
    """Runs the full pipeline fresh on THIS user's ledger and returns
    every flag raised."""
    flags = _run_pipeline(user_id)
    return {"count": len(flags), "flags": flags}


@app.post("/feedback")
def submit_feedback(req: FeedbackRequest, user_id: str = Depends(current_user)):
    """
    Records a user correction and adjusts THAT USER's thresholds for next
    run. Must be called with a flag_id returned by a prior /flags call.
    """
    flag = _user_flag_cache(user_id).get(req.flag_id)
    if not flag:
        raise HTTPException(
            status_code=404,
            detail="Unknown flag_id -- call GET /flags first to populate the cache.",
        )
    if req.verdict not in ("confirmed", "false_positive"):
        raise HTTPException(status_code=400, detail="verdict must be 'confirmed' or 'false_positive'")

    profile_path = store.user_path(user_id, "profile.json")
    updated_profile = record_feedback(
        flag, req.verdict, merchant_name=req.merchant_name, profile_path=profile_path
    )
    return {"status": "recorded", "updated_profile": updated_profile}


@app.get("/audit")
def get_audit(user_id: str = Depends(current_user)):
    # Read under the same user lock the pipeline writes hold, so a
    # concurrent /flags run can't have audit_trail.json open for writing
    # when we open it for reading (PermissionError on Windows otherwise).
    with store.user_write_lock(user_id):
        path = store.user_path(user_id, "audit_trail.json")
        if not os.path.exists(path):
            return {"trail": []}
        with open(path, encoding="utf-8") as f:
            return {"trail": json.load(f)}


@app.get("/score")
def get_score(user_id: str = Depends(current_user)):
    ground_truth = store.read_user_json(user_id, "ground_truth.json", [])
    flags = _run_pipeline(user_id)
    if not ground_truth:
        return {
            "scored": False,
            "note": (
                "No labeled ground truth for this account — precision/recall "
                "is a demo-dataset metric. The demo@pma.local account has it."
            ),
        }
    return {"scored": True, **compute_score(flags, ground_truth)}


@app.get("/finance")
def get_finance(user_id: str = Depends(current_user)):
    """
    Finance mode: categorized spend + a next-month forecast, computed on
    THIS user's ledger and the same flags Risk/Recovery mode raised (via
    _run_pipeline) — one shared understanding of the user's financial
    state, not a separate pipeline.
    """
    transactions = store.read_user_json(user_id, "transactions.json", [])
    subscriptions = store.read_user_json(user_id, "subscriptions.json", [])
    flags = _run_pipeline(user_id)
    return build_finance_report(transactions, subscriptions, flags=flags)


@app.get("/growth")
def get_growth(user_id: str = Depends(current_user)):
    """
    Growth mode: upcoming/overdue bill reminders. Never auto-pays a
    merchant unless the user has explicitly opted in via POST
    /growth/autopay -- see growth.py and feedback.set_autopay() for why
    that is a hard rule, not just a default.
    """
    set_growth_context(store.user_dir(user_id))
    return growth_summary()


@app.post("/growth/autopay")
def toggle_autopay(req: AutopayIn, user_id: str = Depends(current_user)):
    """Explicit, per-merchant autopay opt-in/opt-out for Growth mode."""
    updated = set_autopay(
        req.merchant_name, req.enabled,
        path=store.user_path(user_id, "profile.json"),
    )
    return {
        "status": "recorded",
        "autopay_enabled_merchants": updated["autopay_enabled_merchants"],
    }


# ---------------------------------------------------------------------
# Subscriptions (manual entry + listing)
# ---------------------------------------------------------------------

@app.get("/subscriptions")
def list_subscriptions(user_id: str = Depends(current_user)):
    """Lists THIS user's subscriptions -- the growth/detection inputs."""
    subs = store.read_user_json(user_id, "subscriptions.json", [])
    return {"count": len(subs), "subscriptions": subs}


@app.post("/subscriptions/manual")
def post_manual_subscription(req: ManualSubscriptionIn, user_id: str = Depends(current_user)):
    """
    Appends a user-entered subscription to THIS user's ledger -- the same
    file the detector/growth read, so it flows through detection,
    scoring, finance, and bills exactly like generated data.
    """
    entry = add_manual_subscription(user_id, req.model_dump())
    return {"status": "added", "subscription": entry}


# ---------------------------------------------------------------------
# Accounts (AA-style consent-flow simulation)
# ---------------------------------------------------------------------

@app.get("/accounts/providers")
def get_providers():
    """AA-style provider picker -- what the user chooses from to 'connect' an account."""
    return {"providers": AA_PROVIDERS}


@app.get("/accounts")
def get_accounts(user_id: str = Depends(current_user)):
    return {"accounts": list_connected_accounts(user_id)}


@app.post("/accounts/connect")
def post_connect_account(req: ProviderConnectIn, user_id: str = Depends(current_user)):
    """
    Simulates an Account Aggregator consent flow completing for THIS
    user. No bank credentials are collected here or anywhere in this
    app -- that's the actual point of the AA model this mirrors.
    """
    try:
        accounts = connect_account(user_id, req.provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "connected", "accounts": accounts}


@app.post("/accounts/disconnect")
def post_disconnect_account(req: AccountIdIn, user_id: str = Depends(current_user)):
    accounts = disconnect_account(user_id, req.account_id)
    return {"status": "disconnected", "accounts": accounts}


@app.post("/chat")
def chat(req: ChatRequest, user_id: str = Depends(current_user)):
    """
    Powers the dashboard's "Hey Jarvis" voice assistant. The browser
    keeps conversation history client-side and sends it back each turn;
    Jarvis's tools run against THE LOGGED-IN USER's data (voice_tools
    picks up a per-request context set just below).
    """
    history = [m.model_dump() for m in req.history]
    voice_tools.set_user_context(store.user_dir(user_id))
    try:
        reply, tool_calls = jarvis_respond(history)
    except GeminiUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"reply": reply, "tool_calls": tool_calls}


# ---------------------------------------------------------------------
# Manual transactions
# ---------------------------------------------------------------------

@app.get("/transactions")
def list_transactions(user_id: str = Depends(current_user),
                      source: str = "", limit: int = 500):
    txns = store.read_user_json(user_id, "transactions.json", [])
    if source:
        txns = [t for t in txns if t.get("source", "manual") == source]
    txns = sorted(txns, key=lambda t: t["timestamp"], reverse=True)[:limit]
    return {"count": len(txns), "transactions": txns}


@app.post("/transactions")
def add_transaction(body: TransactionIn, user_id: str = Depends(current_user)):
    """
    Manually records an expense/income on the user's ledger. Every row is
    tagged source:"manual" so it can be told apart from auto-imported
    bank feed rows. A near-identical row inside the same minute is
    treated as a double-submit and skipped.
    """
    if body.direction not in ("debit", "credit"):
        raise HTTPException(status_code=400, detail="direction must be 'debit' or 'credit'")

    timestamp = body.timestamp
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            raise HTTPException(status_code=400, detail="timestamp must be ISO format, e.g. 2026-09-03T14:30:00")
        # The detectors compare naive datetimes (the bundled dataset has no
        # timezone info). Normalize any offset-aware input (browsers send
        # "...Z") to naive UTC so mixed ledgers never break detector.py's
        # max() comparison with "offset-naive vs offset-aware".
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        timestamp = parsed.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    txn = {
        "id": "txn_" + uuid.uuid4().hex[:8],
        "timestamp": timestamp,
        "amount": round(float(body.amount), 2),
        "direction": body.direction,
        "merchant_name": body.merchant_name.strip(),
        "category": (body.category or "misc").strip() or "misc",
        "payment_mode": (body.payment_mode or "UPI").strip() or "UPI",
        "memo": (body.memo or "").strip(),
        "linked_subscription_id": body.linked_subscription_id,
        "account": (body.account or "manual").strip() or "manual",
        "source": "manual",
        "added_at": datetime.utcnow().isoformat(),
    }

    with store.user_write_lock(user_id):
        txns = store.read_user_json(user_id, "transactions.json", [])
        for existing in txns:
            same = (
                existing.get("merchant_name") == txn["merchant_name"]
                and abs(existing.get("amount", 0) - txn["amount"]) < 0.005
                and existing.get("source", "manual") == "manual"
                and abs((datetime.fromisoformat(existing["timestamp"]) - datetime.fromisoformat(txn["timestamp"])).total_seconds()) < 120
            )
            if same:
                return {"status": "duplicate_skipped", "note": "An identical entry was added within the last 2 minutes.", "transaction": existing}
        txns.append(txn)
        store.write_user_json(user_id, "transactions.json", txns)

    return {"status": "recorded", "transaction": txn, "count": len(txns)}


@app.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: str, user_id: str = Depends(current_user)):
    with store.user_write_lock(user_id):
        txns = store.read_user_json(user_id, "transactions.json", [])
        remaining = [t for t in txns if t.get("id") != txn_id]
        if len(remaining) == len(txns):
            raise HTTPException(status_code=404, detail="Transaction not found.")
        store.write_user_json(user_id, "transactions.json", remaining)
    return {"status": "removed", "id": txn_id}


# ---------------------------------------------------------------------
# Bank connections (RazorpayX test mode)
# ---------------------------------------------------------------------

@app.get("/bank")
def get_bank(user_id: str = Depends(current_user)):
    connections = store.read_user_json(user_id, "connections.json", [])
    return {
        "mode": "live" if bank_connect.keys_present() else "mocked",
        "connections": connections,
    }


@app.post("/bank/connect")
def connect_bank(body: ConnectIn, user_id: str = Depends(current_user)):
    """Registers a bank account with the agent (RazorpayX fund account in
    test mode when keys are set; clearly-labeled simulation otherwise)."""
    acct = store.load_account(user_id)
    email = (acct or {}).get("email", "")
    result = bank_connect.connect_bank_account(
        body.holder_name, body.account_number, body.ifsc, body.phone or "", email
    )
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["detail"])

    connection = result["connection"]
    with store.user_write_lock(user_id):
        connections = store.read_user_json(user_id, "connections.json", [])
        connections.append(connection)
        store.write_user_json(user_id, "connections.json", connections)
    return {"status": "connected", "mode": result["mode"], "connection": connection, "detail": result["detail"]}


@app.delete("/bank/{fund_account_id}")
def disconnect_bank(fund_account_id: str, user_id: str = Depends(current_user)):
    with store.user_write_lock(user_id):
        connections = store.read_user_json(user_id, "connections.json", [])
        conn = next((c for c in connections if c.get("fund_account_id") == fund_account_id), None)
        if conn is None:
            raise HTTPException(status_code=404, detail="No such connected account.")
        note = bank_connect.disconnect_bank_account(conn)
        connections = [c for c in connections if c.get("fund_account_id") != fund_account_id]
        store.write_user_json(user_id, "connections.json", connections)
    return {"status": "disconnected", "fund_account_id": fund_account_id, "detail": note}


@app.post("/sync")
def sync_account(body: SyncIn, user_id: str = Depends(current_user)):
    """
    Auto-import: pulls new rows for a connected account.

    - Mocked mode (no RazorpayX keys): imports a small, clearly-labeled
      demo feed (source: bank_feed_demo) so the full detection pipeline
      is exercisable end to end.
    - Live mode (keys set): confirms the fund account is still active on
      RazorpayX. RazorpayX validates accounts but does not stream a
      personal transaction statement, so no unverifiable rows are
      invented — swap in a statement provider for production.
    """
    with store.user_write_lock(user_id):
        connections = store.read_user_json(user_id, "connections.json", [])
        if not connections:
            raise HTTPException(status_code=400, detail="No bank account connected yet — connect one first.")
        target = body.fund_account_id
        conn = next(
            (c for c in connections if c.get("fund_account_id") == target),
            connections[0],
        ) if target else connections[0]

        now = datetime.utcnow().isoformat()
        txns = store.read_user_json(user_id, "transactions.json", [])

        if conn.get("mode") == "live" and bank_connect.keys_present():
            # Real RazorpayX check: is the fund account still active?
            # Best effort — a network failure is reported, not fatal.
            live = bank_connect.verify_fund_account(conn.get("fund_account_id", ""))
            conn["synced_at"] = now
            conn["last_sync_note"] = (
                "Live RazorpayX connection re-verified (" + live + "); "
                "statement-level auto-import isn't available through "
                "RazorpayX test mode."
            )
            store.write_user_json(user_id, "connections.json", connections)
            return {"status": "synced", "imported": 0, "mode": "live", "detail": conn["last_sync_note"]}

        feed = bank_connect.build_demo_feed(conn, user_id)
        existing_ids = {t.get("id") for t in txns}
        fresh = [t for t in feed if t.get("id") not in existing_ids]
        conn["synced_at"] = now
        conn["last_sync_note"] = (
            f"Imported {len(fresh)} demo-feed rows (tagged bank_feed_demo). "
            "Set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in .env for live validation."
        )
        store.write_user_json(user_id, "connections.json", connections)
        txns.extend(fresh)
        txns.sort(key=lambda t: t["timestamp"])
        store.write_user_json(user_id, "transactions.json", txns)
        return {"status": "synced", "imported": len(fresh), "mode": "mocked", "detail": conn["last_sync_note"]}
