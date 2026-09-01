# Personal Money Agent

One agent, four capabilities — the consumer mirror of Razorpay's Growth,
Risk, Recovery, and Finance Controller tracks. Instead of protecting a
merchant's money, this agent protects *your* money: it watches your
transactions and subscriptions, flags waste/fraud/forgotten refunds, and
takes bounded, explainable action — with every decision logged to an
audit trail.

## Status: Day 6 — Growth mode live (real feature, not scripted) ✅

- [x] Synthetic dataset with planted, labeled patterns (`data/`)
- [x] Rule-based detection engine — 6 patterns, confidence-gated (`src/detector.py`)
- [x] Audit trail logger — every decision explained and logged (`src/audit.py`)
- [x] Scoring pipeline — precision/recall against ground truth (`src/score.py`, `src/scoring.py`)
- [x] Feedback loop — per-user, per-merchant adaptive thresholds (`src/feedback.py`)
- [x] LLM layer — reviews medium-confidence flags only, degrades gracefully without API key (`src/llm_reasoner.py`)
- [x] Razorpay test-mode action executor — real API calls confirmed working (`src/razorpay_actions.py`)
- [x] FastAPI service — `/flags`, `/feedback`, `/audit`, `/score`, `/finance`, `/growth` all tested live (`src/main.py`)
- [x] Passbook-style dashboard — live at `/dashboard` (`static/index.html`)
- [x] Finance mode — category breakdown + moving-average forecast (`src/finance.py`)
- [x] Growth mode — real upcoming/overdue bill detection with opt-in autopay (`src/growth.py`)
- [ ] Get Anthropic API key set and confirm LLM layer actually runs (still untested end-to-end)
- [ ] End-to-end run + final metrics (Day 7)
- [ ] Repo polish + pitch video (Day 8)

## Growth mode
Detects bills coming due or already overdue, using each subscription's
`next_due_date` (a field that existed in the synthetic data from Day 1
but no detector had ever read). Verified against real data: 9 of 10
active subscriptions are currently overdue, one (Netflix) is genuinely
upcoming -- both scenarios exercised for real.

**Hard rule, not just a default**: the agent never auto-pays a bill
unless the user has explicitly enabled autopay for that specific
merchant (`POST /growth/autopay`). Nothing is inferred from trust
built elsewhere in the system -- autopay consent is deliberate and
per-merchant, always.


## Why Finance mode isn't ML
Tested a Kaggle banking dataset for merchant categorization first. Found
its category labels were assigned independently of merchant name (310
of 327 repeat merchants showed 2+ different categories across
appearances — see `verify_kaggle_signal.py`), meaning a classifier
trained on it would memorize noise, not learn anything real. A lookup
against a known, finite personal merchant list is the honest, correct
tool here — real personal finance apps do the same for this reason.
The forecast is a plain moving average, labeled as a statistical
projection rather than framed as AI.


## View the dashboard

```bash
cd src
uvicorn main:app --reload --port 8000
```

Then open `http://localhost:8000/dashboard/` in your browser. Click
**Confirm** or **False Alarm** on any row — the ledger and the balance
strip at the top update live, without a page reload. This is the core
demo moment for the pitch video: it proves the feedback loop is real,
not just numbers in a terminal.


## Run the API

```bash
cd src
uvicorn main:app --reload --port 8000
```

Then visit `http://localhost:8000/docs` for interactive API docs, or:

```bash
curl http://localhost:8000/flags
curl http://localhost:8000/score
curl -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{"flag_id": "<flag_id from /flags>", "verdict": "false_positive", "merchant_name": "Notion"}'
```

**Important**: use `flag_id` (not `event_id`) when calling `/feedback` — a
single subscription can trigger more than one pattern (e.g. both
`zombie_sub` and `silent_conversion`), so `flag_id` (`event_id__pattern`)
is what's actually unique.


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

cp .env.example .env            # add your ANTHROPIC_API_KEY when you have one
```

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
