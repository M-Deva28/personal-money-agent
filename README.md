# Personal Money Agent

One agent, four capabilities — the consumer mirror of Razorpay's Growth,
Risk, Recovery, and Finance Controller tracks. Instead of protecting a
merchant's money, this agent protects *your* money: it watches your
transactions and subscriptions, flags waste/fraud/forgotten refunds, and
takes bounded, explainable action — with every decision logged to an
audit trail.

## Status

- [x] Synthetic dataset with planted, labeled patterns (`data/`)
- [x] Rule-based detection engine — 6 patterns, confidence-gated (`src/detector.py`)
- [x] Audit trail logger — every decision explained and logged (`src/audit.py`)
- [x] Scoring pipeline — precision/recall against ground truth (`src/score.py`, `src/scoring.py`)
- [x] Feedback loop — adaptive thresholds, now per-user (`src/feedback.py`)
- [x] LLM layer — reviews medium-confidence flags only, degrades gracefully without API key (`src/llm_reasoner.py`)
- [x] Razorpay test-mode action executor — mocks gracefully without keys (`src/razorpay_actions.py`)
- [x] Dashboard UI — ledger, audit tape, finance, Jarvis (static/dashboard.html)
- [x] Finance mode: spend categorization + forecast
- [x] **Multi-user accounts & auth** — register/login, per-user isolation, signed sessions (`src/security.py`, `src/store.py`)
- [x] **Manual transaction entry** — add expenses by hand via the dashboard or `POST /transactions`
- [x] **Manual subscription entry** — add recurring bills by hand (`src/accounts.py`, `POST /subscriptions/manual`)
- [x] **Bank account connection** — RazorpayX (test-mode) fund accounts, with auto-import (`src/bank_connect.py`)
- [x] **Live RazorpayX test mode** — connect / sync / disconnect verified end-to-end against real test-mode keys
- [x] **Growth mode: bills & autopay** — upcoming/overdue bill reminders with explicit per-merchant autopay opt-in (`src/growth.py`, `POST /growth`, `POST /growth/autopay`)
- [x] **Real Razorpay entities for actions** — `setup_razorpay_entities.py` creates real test-mode plans/subscriptions/orders; pause/refund/autopay then run live per merchant (`src/razorpay_actions.py`)
- [ ] Repo polish + pitch video

## Run the API

```bash
cd src
uvicorn main:app --reload --port 8000
```

Then open `http://localhost:8000/login.html` (or `/register.html`) in a browser,
or `http://localhost:8000/docs` for interactive API docs.

**Demo account** (seeded with the bundled dataset, one click on the login
page): `demo@pma.local` / `demo1234`

On first start the bundled `data/*.json` demo dataset is copied into that
demo account under `data/users/demo/` — the dashboard shows exactly the
same metrics the CLI demos report. Newly registered accounts start empty.

Every data endpoint requires a session. From curl, sign in first and reuse
the cookie jar:

```bash
curl -c /tmp/jar -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"email": "demo@pma.local", "password": "demo1234"}'

curl -b /tmp/jar http://localhost:8000/flags
curl -b /tmp/jar http://localhost:8000/score
curl -b /tmp/jar -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{"flag_id": "<flag_id from /flags>", "verdict": "false_positive", "merchant_name": "Notion"}'
```

**Important**: use `flag_id` (not `event_id`) when calling `/feedback` — a
single subscription can trigger more than one pattern (e.g. both
`zombie_sub` and `silent_conversion`), so `flag_id` (`event_id__pattern`)
is what's actually unique.

### Connecting a bank + auto/manual transactions

- **Manual**: "＋ Add expense" on the dashboard, or `POST /transactions`
  (`amount`, `merchant_name`, `category`, `payment_mode`, `timestamp`…).
  Rows are tagged `source: "manual"`; a near-identical entry within 2
  minutes is treated as a double-submit and skipped.
- **Automatic**: "Bank accounts" → connect with holder name, account
  number, and IFSC. With `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` in `.env`
  this runs against RazorpayX test mode (real fund-account validation);
  without keys it is clearly-labeled simulated mode. "Sync now"
  auto-imports transactions (`source: "bank_feed_demo"`) — note RazorpayX
  validates accounts but does not stream personal statements, so live
  transaction-level import needs a real statement provider.

### Bills / Growth mode (upcoming + autopay)

Growth mode looks *forward*: subscriptions whose `next_due_date` falls
within 14 days (including genuinely overdue ones) become bill reminders
on the dashboard. The agent **never auto-pays a bill the user hasn't
explicitly opted into** — each bill row has an "Enable autopay" toggle
(`POST /growth/autopay`, stored per-user in the profile as
`autopay_enabled_merchants`). Opted-in bills report an execution result
(mocked without keys, real Razorpay order linkage when
`setup_razorpay_entities.py` has created entities for that merchant).

- `GET /growth` — upcoming/overdue bills + which merchants have autopay on
- `POST /growth/autopay` — `{merchant_name, enabled}` per-merchant opt-in/opt-out
- `GET /subscriptions`, `POST /subscriptions/manual` — add a recurring bill by hand
- `GET /accounts/providers` · `GET /accounts` · `POST /accounts/connect` ·
  `POST /accounts/disconnect` — the base project's Account-Aggregator-style
  consent simulation (per-user `connected_accounts.json`; the dashboard's
  Bank panel uses the real RazorpayX connect instead, and these endpoints
  are kept for API parity)

Manual subscriptions flow through detection, scoring, finance, and bills
exactly like generated data (they land in the same per-user
`subscriptions.json` the detector reads).


## Why rules + feedback loop + LLM (not any one alone)

- **Rules**: fast, free, deterministic, fully explainable — handle the
  obvious cases and decide WHETHER something needs a closer look.
- **Feedback loop**: turns a static rule engine into something that learns
  *this specific user's* normal — correct a flag once, it doesn't repeat
  the mistake for that merchant, with no retraining needed.
- **LLM**: only touches the genuinely ambiguous medium-confidence cases,
  adds judgment a fixed threshold can't capture, and writes friendlier
  plain-English explanations. It can never silently downgrade a rule's
  high-confidence catch, and the whole system degrades gracefully (rule
  reasoning only) if no API key is set or the call fails.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # add your API keys when you have them
```

Session signing uses `PMA_SECRET_KEY` if set; otherwise the server
auto-generates `data/.secret` on first run (fine for a local demo — set
the env var for a real deployment).

## Quickstart

```bash
cd src

# Run detection + scoring
python score.py

# See the feedback loop actually change behavior
python feedback_demo.py
```

You should see a report like:

```
Total flags raised        : 31
Precision                 : 70.37%
Recall                    : 90.48%
₹ auto-actionable (high)  : ₹15,247.39
₹ needs human review      : ₹14,412.81
```

And the feedback demo should show a flag count drop after one correction
(e.g. `zombie_sub: 9 -> 8`) with an explicit "agent learned" confirmation.

## Architecture

```
data/transactions.json ─┐
data/subscriptions.json ┴─> detector.py (6 rule-based pattern detectors)
                              │  (thresholds pulled from user_profile.json)
                              ▼
                        confidence tier
                         high / medium
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             auto_executed       llm_reasoner.py reviews
             (bounded action)    medium-confidence cases
                    │                   │
                    │            (only ESCALATES or flags
                    │             low_likely_false_alarm;
                    │             never auto-executes alone)
                    │                   │
                    └─────────┬─────────┘
                              ▼
                     audit.py → logs/audit_trail.json
                     (every decision, explained)
                              │
                              ▼
                   user marks confirmed / false_positive
                              │
                              ▼
                  feedback.py → data/user_profile.json
                  (adjusts thresholds for NEXT run)
```

## Multi-user accounts, auth & data protection

Each account owns a private folder under `data/users/<user_id>/` with its
own `transactions.json`, `subscriptions.json`, `profile.json` (learned
thresholds, incl. `autopay_enabled_merchants`), `ground_truth.json`,
`audit_trail.json`, `connections.json` (RazorpayX fund accounts),
`connected_accounts.json` (AA-style simulation), and `razorpay_ids.json`
(real test-mode Razorpay entities, created by `setup_razorpay_entities.py`). The bundled `data/*.json` files remain the canonical
demo dataset that the CLI scripts (`score.py`, demos) and the demo account
run against.

- **Passwords** are never stored or logged — salted PBKDF2-HMAC-SHA256
  (600k iterations) via `src/security.py`.
- **Sessions** are signed HMAC tokens in HttpOnly, SameSite=Lax cookies.
  `/logout` clears them; all data endpoints return 401 without a session.
- **Isolation** is enforced in `src/store.py`: every route resolves the
  logged-in user id and reads/writes only that user's directory — a user
  can never request another account's data through the API.
- **Bank numbers** are stored masked (`••••2323`); the raw account number
  is only sent to RazorpayX (test mode) and never persisted.
- **Honest limits** (demo-grade, not bank-grade): no rate limiting, no
  email verification/KYC, no encryption at rest of the JSON files, HTTP
  not HTTPS on localhost. Do not put real credentials or production
  financial data behind this without addressing those.

Auth endpoints: `POST /register`, `POST /login`, `POST /logout`, `GET /me`.

## The six detection patterns

| Pattern | What it catches | Target action |
|---|---|---|
| `zombie_sub` | Active subscription, no usage in 60+ days | Pause subscription |
| `silent_conversion` | Trial silently converted to paid | Flag for review |
| `duplicate_charge` | Same merchant + amount within minutes | Refund claim |
| `price_hike` | Recurring charge jumped >15% with no notice | Flag for review |
| `refund_owed` | Merchant says refunded, no credit arrived | Refund claim |
| `suspicious_collect` | UPI debit to an unfamiliar payee | Flag for review |

See `SCHEMA.md` for the full data schema and labeling spec.

## Why rule-based detection (not pure LLM)

Deterministic, explainable, and free to run on the whole ledger instantly —
exactly what "bounded and gated" means in practice. The LLM layer (see
above) is added deliberately on top for the ambiguous cases only, not as
a replacement for the rules.

## Next steps (Day 2)

1. Wrap `detector.py` + `audit.py` + `feedback.py` + `llm_reasoner.py` in a
   FastAPI app (`src/main.py`) exposing:
   - `GET /flags` — current flagged events (with LLM review applied to medium-confidence ones)
   - `POST /feedback` — submit a confirmed/false_positive verdict on a flag
   - `GET /audit` — full audit trail
   - `GET /score` — precision/recall report as JSON
2. Get an Anthropic API key and a Razorpay test-mode account, drop both into `.env`
3. Wire `suggested_action == "auto_executed"` cases to real (test-mode) API calls
