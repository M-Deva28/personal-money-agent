"""
FastAPI wrapper — turns the detection/feedback/audit pipeline into a
real service a dashboard (or the Razorpay judges) can hit directly.

Run: uvicorn main:app --reload --port 8000
Docs: http://localhost:8000/docs  (FastAPI auto-generates this)
"""

import json
import os
import threading

from dotenv import load_dotenv
load_dotenv()  # BUG FIX: without this, RAZORPAY_KEY_ID / ANTHROPIC_API_KEY in
                # .env were invisible to this process -- confirmed via a real
                # test where execution_result showed "mode": "mocked" for
                # refund_owed flags even after keys were correctly set,
                # because load_dotenv() was only ever called in the standalone
                # test scripts, never in the actual running server.

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from detector import run_all_detectors
from feedback import load_profile, record_feedback
from audit import log_decision, decide_action, clear_log
from scoring import compute_score
from llm_reasoner import review_all_medium_confidence
from razorpay_actions import execute_action
from finance import finance_summary
from growth import growth_summary
from accounts import (
    AA_PROVIDERS,
    list_connected_accounts,
    connect_account,
    disconnect_account,
    add_manual_transaction,
    add_manual_subscription,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _as_dict(model: BaseModel) -> dict:
    """
    Pydantic v2 renamed .dict() to .model_dump(). This project's
    requirements.txt pins fastapi==0.115.0 (which wants pydantic v2),
    but a pre-existing pydantic v1 install in the environment can still
    end up satisfying it -- confirmed via a real 500
    (`'ManualTransactionRequest' object has no attribute 'model_dump'`)
    the first time a manual-entry form was submitted. Support both so
    this doesn't depend on which one happens to be installed.
    """
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


app = FastAPI(title="Personal Money Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a local demo; tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/dashboard", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

# Cache of the last computed flags. _flag_cache is keyed by flag_id for
# /feedback lookups; _last_flags preserves the list so /score can reuse it
# without re-running the pipeline (which would re-trigger clear_log() and
# race with a concurrent /audit read of the same file).
_flag_cache = {}
_last_flags = []
_pipeline_lock = threading.Lock()  # prevents concurrent runs from racing on audit_trail.json


def _load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


def _run_pipeline():
    """Single source of truth: load data, detect, review, log, cache.

    Wrapped in a lock: two concurrent requests (e.g. a dashboard firing
    /flags and /score at once, or two browser tabs) both hitting this
    at the same time can otherwise race on reading/writing
    audit_trail.json and corrupt it mid-write.
    """
    with _pipeline_lock:
        transactions = _load("transactions.json")
        subscriptions = _load("subscriptions.json")
        profile = load_profile()

        flags = run_all_detectors(transactions, subscriptions, profile=profile)
        flags = review_all_medium_confidence(flags)

        clear_log()
        for flag in flags:
            action = decide_action(flag)
            log_decision(flag, action)
            if action == "auto_executed":
                flag["execution_result"] = execute_action(flag)

        global _flag_cache, _last_flags
        _flag_cache = {f["flag_id"]: f for f in flags}
        _last_flags = flags
        return flags


class FeedbackRequest(BaseModel):
    flag_id: str
    verdict: str  # "confirmed" or "false_positive"
    merchant_name: str | None = None


class AutopayRequest(BaseModel):
    merchant_name: str
    enabled: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class ConnectAccountRequest(BaseModel):
    provider_id: str


class ManualTransactionRequest(BaseModel):
    amount: float
    merchant_name: str
    direction: str | None = "debit"
    category: str | None = "other"
    payment_mode: str | None = "upi"
    memo: str | None = None
    timestamp: str | None = None
    linked_subscription_id: str | None = None
    account: str | None = None


class ManualSubscriptionRequest(BaseModel):
    merchant_name: str
    amount: float
    billing_cycle: str | None = "monthly"
    started_date: str | None = None
    last_charged_date: str | None = None
    next_due_date: str | None = None
    status: str | None = "active"
    trial_end_date: str | None = None
    last_usage_signal_date: str | None = None


@app.get("/")
def root():
    return {
        "service": "Personal Money Agent",
        "dashboard": "/dashboard",
        "endpoints": ["/flags", "/feedback (POST)", "/audit", "/score"],
    }


@app.get("/flags")
def get_flags():
    """Runs the full pipeline fresh and returns every flag raised."""
    flags = _run_pipeline()
    return {"count": len(flags), "flags": flags}


@app.post("/feedback")
def submit_feedback(req: FeedbackRequest):
    """
    Records a user correction and adjusts thresholds for next run.
    Must be called with an event_id that was returned by a prior /flags call.
    """
    flag = _flag_cache.get(req.flag_id)
    if not flag:
        raise HTTPException(
            status_code=404,
            detail="Unknown flag_id -- call GET /flags first to populate the cache.",
        )
    if req.verdict not in ("confirmed", "false_positive"):
        raise HTTPException(status_code=400, detail="verdict must be 'confirmed' or 'false_positive'")

    updated_profile = record_feedback(flag, req.verdict, merchant_name=req.merchant_name)

    # Refresh the cache immediately so a direct /score call (without an
    # intervening /flags call) reflects this correction right away. The
    # dashboard always calls /flags before /score anyway, but the raw
    # API shouldn't silently serve stale results to someone testing it
    # directly -- caught this during a full verification pass.
    _run_pipeline()

    return {"status": "recorded", "updated_profile": updated_profile}


@app.get("/audit")
def get_audit():
    path = os.path.join(os.path.dirname(__file__), "..", "logs", "audit_trail.json")
    if not os.path.exists(path):
        return {"trail": []}
    with open(path) as f:
        return {"trail": json.load(f)}


@app.get("/score")
def get_score():
    """
    Scores the MOST RECENT flags already computed by /flags -- does not
    re-run the pipeline itself. Re-running here would call clear_log()
    again and race with a concurrent /audit read of the same file.
    Call GET /flags first (the dashboard always does).
    """
    ground_truth = _load("ground_truth.json")
    flags = _last_flags if _last_flags else _run_pipeline()
    return compute_score(flags, ground_truth)


@app.get("/finance")
def get_finance():
    """
    Spend categorization + a simple moving-average forecast. Explicitly
    NOT machine learning -- categorization is a lookup against known
    merchants (the right tool for a finite personal merchant list), and
    the forecast is a plain statistical projection. Labeled as such
    rather than oversold as AI.
    """
    return finance_summary()


@app.get("/growth")
def get_growth():
    """
    Upcoming/overdue bill reminders. Never auto-pays a merchant unless
    the user has explicitly opted in via POST /growth/autopay -- see
    growth.py and feedback.set_autopay() for why this is a hard rule,
    not just a default.
    """
    return growth_summary()


@app.post("/growth/autopay")
def set_autopay(req: AutopayRequest):
    from feedback import set_autopay as _set_autopay
    updated_profile = _set_autopay(req.merchant_name, req.enabled)
    return {"status": "recorded", "autopay_enabled_merchants": updated_profile["autopay_enabled_merchants"]}


@app.post("/auth/login")
def login(req: LoginRequest):
    """
    Demo-only gate, not real authentication -- there is no user table
    and nothing is hashed or persisted. It exists so the dashboard can
    demonstrate "the agent belongs to a logged-in user" for the pitch,
    not to secure anything. Accepts any non-empty username/password.
    """
    if not req.username.strip() or not req.password.strip():
        raise HTTPException(status_code=400, detail="Username and password required")
    return {"status": "ok", "user": req.username}


@app.get("/accounts/providers")
def get_providers():
    """AA-style provider picker -- what the user chooses from to 'connect' an account."""
    return {"providers": AA_PROVIDERS}


@app.get("/accounts")
def get_accounts():
    return {"accounts": list_connected_accounts()}


@app.post("/accounts/connect")
def post_connect_account(req: ConnectAccountRequest):
    """
    Simulates an Account Aggregator consent flow completing. No bank
    credentials are collected here or anywhere in this app -- that's
    the actual point of the AA model this mirrors, not a shortcut.
    """
    try:
        accounts = connect_account(req.provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "connected", "accounts": accounts}


@app.post("/accounts/disconnect")
def post_disconnect_account(account_id: str):
    accounts = disconnect_account(account_id)
    return {"status": "disconnected", "accounts": accounts}


@app.post("/transactions/manual")
def post_manual_transaction(req: ManualTransactionRequest):
    """
    Appends a user-entered transaction to data/transactions.json -- the
    same file the detector reads, so a manually-added entry flows
    through detection/scoring/finance/growth exactly like generated data.
    """
    entry = add_manual_transaction(_as_dict(req))
    return {"status": "added", "transaction": entry}


@app.post("/subscriptions/manual")
def post_manual_subscription(req: ManualSubscriptionRequest):
    entry = add_manual_subscription(_as_dict(req))
    return {"status": "added", "subscription": entry}
