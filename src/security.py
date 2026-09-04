"""
Security layer — password hashing and signed session cookies.

Design (demo-grade, no new dependencies):
  - Passwords are never stored or logged. We store only a PBKDF2-HMAC-SHA256
    hash with a per-user random salt and a high iteration count (stdlib
    hashlib — no bcrypt/argon2 dependency needed).
  - Sessions are stateless signed tokens (HMAC-SHA256 over user_id + expiry)
    using a server secret. The secret comes from PMA_SECRET_KEY if set,
    otherwise from a generated file (data/.secret) so tokens survive server
    restarts on the same machine.
  - Cookies are HttpOnly + SameSite=Lax, so page JavaScript can't read them
    and cross-site POSTs can't ride the session.

This is explicitly demo-grade, not bank-grade: no rate limiting, no
password-reset flow, no encryption at rest of the JSON data files. See
README.md for the honest security notes before any real deployment.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_SECRET_PATH = os.path.join(DATA_DIR, ".secret")

# OWASP-ish recommendation for PBKDF2-HMAC-SHA256 as of 2023+.
_PBKDF2_ITERATIONS = 600_000
_HASH_LEN = 32  # sha256 digest size
_SESSION_TTL_SECONDS = 7 * 24 * 3600  # one week

_COOKIE_NAME = "pma_session"


def _load_or_create_secret():
    """Server secret: PMA_SECRET_KEY env var, else a generated file. The file
    approach keeps tokens valid across restarts without the user having to
    set anything; the env var exists for real deployments."""
    env = os.environ.get("PMA_SECRET_KEY", "").strip()
    if env:
        return env.encode("utf-8")
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(_SECRET_PATH):
        with open(_SECRET_PATH, "w") as f:
            f.write(secrets.token_hex(32))
    with open(_SECRET_PATH) as f:
        return f.read().strip().encode("utf-8")


_SECRET = _load_or_create_secret()


# ---------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------

def hash_password(password: str) -> dict:
    """Returns the storable password record: algorithm, salt, iterations,
    and hash. Never store the raw password."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_HASH_LEN
    )
    return {
        "algorithm": "pbkdf2_sha256",
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": _PBKDF2_ITERATIONS,
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def verify_password(password: str, record: dict) -> bool:
    """Constant-time check of a candidate password against a stored record."""
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        iterations = int(record.get("iterations", _PBKDF2_ITERATIONS))
    except (KeyError, ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------

def issue_session_token(user_id: str, ttl_seconds: int = _SESSION_TTL_SECONDS) -> str:
    """user_id.expiry.hmac — expiry is a unix timestamp, hmac is over
    'user_id.expiry' with the server secret."""
    expiry = str(int(time.time()) + ttl_seconds)
    payload = f"{user_id}.{expiry}"
    sig = hmac.new(_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str):
    """Returns the user_id if the token is well-formed, unexpired, and
    signed by this server; otherwise None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id, expiry, sig = parts
    try:
        if int(expiry) < int(time.time()):
            return None
    except ValueError:
        return None
    expected = hmac.new(_SECRET, f"{user_id}.{expiry}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id


def new_cookie(token: str, ttl_seconds: int = _SESSION_TTL_SECONDS):
    """The Set-Cookie value for the session cookie."""
    return (
        f"{_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={ttl_seconds}"
    )


def expired_cookie():
    """Set-Cookie that immediately clears the session cookie."""
    return f"{_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def cookie_name():
    return _COOKIE_NAME
