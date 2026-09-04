"""
Per-user data store — the multi-user backbone.

Layout:
    data/users/index.json                 registry (id -> email/name)
    data/users/<user_id>/account.json     auth record (password hash, profile)
    data/users/<user_id>/transactions.json
    data/users/<user_id>/subscriptions.json
    data/users/<user_id>/profile.json     learned thresholds (feedback.py format)
    data/users/<user_id>/ground_truth.json  only the demo user has labels
    data/users/<user_id>/audit_trail.json
    data/users/<user_id>/connections.json   connected bank accounts

The bundled data/transactions.json etc. remain the repo's canonical demo
dataset (score.py and friends still run against them). On first start they
are copied into a seeded "demo" account so the logged-in dashboard shows
exactly the numbers the CLI demos report; brand-new registered accounts
start empty, as a real user would.

Windows-safe writes: every file write goes to a temp file in the same
directory then os.replace() — atomic, and readers never observe a
half-written file (the PermissionError retry in audit.py was guarding
non-atomic writes; os.replace removes that whole class of bug).
"""

import json
import os
import shutil
import threading
import uuid
from datetime import datetime

import security

USERS_ROOT = os.path.join(os.path.dirname(__file__), "..", "data", "users")
LEGACY_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LEGACY_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "audit_trail.json")
_INDEX_PATH = os.path.join(USERS_ROOT, "index.json")

# Keep the demo account's seed byte-identical to the bundled dataset so the
# dashboard metrics match the CLI demo metrics forever.
DEMO_EMAIL = "demo@pma.local"
DEMO_PASSWORD = "demo1234"  # shown in README; it is a demo account
DEMO_DISPLAY_NAME = "Demo"

_RETRY_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 0.05

_REGISTRY_LOCK = threading.Lock()
# Per-user write locks, keyed by user id — serializes pipeline writes that
# touch several files (mirrors the _pipeline_lock pattern in main.py).
_USER_LOCKS = {}
_USER_LOCKS_GUARD = threading.Lock()


def _with_retry(func):
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return func()
        except PermissionError:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            import time
            time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))


# ---------------------------------------------------------------------
# Paths & atomic IO
# ---------------------------------------------------------------------

def user_dir(user_id: str) -> str:
    return os.path.join(USERS_ROOT, user_id)


def user_path(user_id: str, filename: str) -> str:
    return os.path.join(USERS_ROOT, user_id, filename)


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"

    def _do():
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    _with_retry(_do)


def user_write_lock(user_id: str) -> threading.Lock:
    """A lock per user so concurrent requests touching the same user's
    files serialize (dashboard fires /flags, /score, /finance together)."""
    with _USER_LOCKS_GUARD:
        lock = _USER_LOCKS.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _USER_LOCKS[user_id] = lock
        return lock


def read_user_json(user_id: str, filename: str, default):
    return read_json(user_path(user_id, filename), default)


def write_user_json(user_id: str, filename: str, data):
    write_json(user_path(user_id, filename), data)


def load_account(user_id: str):
    return read_json(user_path(user_id, "account.json"), None)


def public_user(user_id: str):
    """Safe-to-send user summary — never includes the password record."""
    acct = load_account(user_id)
    if not acct:
        return None
    return {
        "id": acct.get("id", user_id),
        "email": acct.get("email", ""),
        "name": acct.get("name", ""),
        "created_at": acct.get("created_at"),
    }


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------

def _read_index():
    data = read_json(_INDEX_PATH, {"users": []})
    if not isinstance(data, dict) or "users" not in data:
        return {"users": []}
    return data


def _write_index(index):
    write_json(_INDEX_PATH, index)


def find_user_by_email(email: str):
    email = email.strip().lower()
    index = _read_index()
    for u in index["users"]:
        if u.get("email") == email:
            return u
    return None


def new_user_id() -> str:
    return uuid.uuid4().hex[:16]


def create_account(email: str, name: str, password: str) -> str:
    """Registers a brand-new user with empty financial data. Raises
    ValueError if the email is taken. Returns the new user id."""
    email = email.strip().lower()
    with _REGISTRY_LOCK:
        if find_user_by_email(email):
            raise ValueError("An account with that email already exists.")

        user_id = new_user_id()
        os.makedirs(user_dir(user_id), exist_ok=True)

        from feedback import DEFAULT_PROFILE

        write_user_json(user_id, "account.json", {
            "id": user_id,
            "email": email,
            "name": name.strip(),
            "password": security.hash_password(password),
            "created_at": datetime.utcnow().isoformat(),
        })
        write_user_json(user_id, "transactions.json", [])
        write_user_json(user_id, "subscriptions.json", [])
        write_user_json(user_id, "profile.json", json.loads(json.dumps(DEFAULT_PROFILE)))
        write_user_json(user_id, "ground_truth.json", [])
        write_user_json(user_id, "connections.json", [])

        index = _read_index()
        index["users"].append({
            "id": user_id,
            "email": email,
            "name": name.strip(),
            "created_at": datetime.utcnow().isoformat(),
        })
        _write_index(index)
        return user_id


# ---------------------------------------------------------------------
# Legacy migration (one-time)
# ---------------------------------------------------------------------

def migrate_legacy_data():
    """First start after upgrading to multi-user: copy the bundled demo
    dataset (data/*.json + logs/audit_trail.json) into a seeded 'demo'
    account so the old single-user dashboard state survives behind the
    login. No-op once data/users/index.json exists."""
    if os.path.exists(_INDEX_PATH):
        return

    legacy_txns = os.path.join(LEGACY_DATA_DIR, "transactions.json")
    if not os.path.exists(legacy_txns):
        return

    with _REGISTRY_LOCK:
        if os.path.exists(_INDEX_PATH):  # another worker migrated first
            return

        user_id = "demo"
        os.makedirs(user_dir(user_id), exist_ok=True)

        shutil.copyfile(legacy_txns, user_path(user_id, "transactions.json"))
        shutil.copyfile(
            os.path.join(LEGACY_DATA_DIR, "subscriptions.json"),
            user_path(user_id, "subscriptions.json"),
        )
        shutil.copyfile(
            os.path.join(LEGACY_DATA_DIR, "ground_truth.json"),
            user_path(user_id, "ground_truth.json"),
        )
        if os.path.exists(os.path.join(LEGACY_DATA_DIR, "user_profile.json")):
            shutil.copyfile(
                os.path.join(LEGACY_DATA_DIR, "user_profile.json"),
                user_path(user_id, "profile.json"),
            )
        else:
            from feedback import DEFAULT_PROFILE
            write_user_json(user_id, "profile.json", json.loads(json.dumps(DEFAULT_PROFILE)))
        if os.path.exists(LEGACY_LOG_PATH):
            shutil.copyfile(LEGACY_LOG_PATH, user_path(user_id, "audit_trail.json"))

        write_user_json(user_id, "connections.json", [])
        write_user_json(user_id, "account.json", {
            "id": user_id,
            "email": DEMO_EMAIL,
            "name": DEMO_DISPLAY_NAME,
            "password": security.hash_password(DEMO_PASSWORD),
            "created_at": datetime.utcnow().isoformat(),
            "seeded_from_bundled_dataset": True,
        })

        index = {"users": [{
            "id": user_id,
            "email": DEMO_EMAIL,
            "name": DEMO_DISPLAY_NAME,
            "created_at": datetime.utcnow().isoformat(),
        }]}
        _write_index(index)
