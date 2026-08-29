# Personal Money Agent

One agent, four capabilities — the consumer mirror of Razorpay's Growth,
Risk, Recovery, and Finance Controller tracks. Instead of protecting a
merchant's money, this agent protects *your* money: it watches your
transactions and subscriptions, flags waste/fraud/forgotten refunds, and
takes bounded, explainable action — with every decision logged to an
audit trail.

## Status: Day 1 — core detection + feedback loop + LLM layer working ✅

- [x] Synthetic dataset with planted, labeled patterns (`data/`)
- [x] Rule-based detection engine — 6 patterns, confidence-gated (`src/detector.py`)
- [x] Audit trail logger — every decision explained and logged (`src/audit.py`)
- [x] Scoring pipeline — precision/recall against ground truth (`src/score.py`)
- [x] Feedback loop — per-user, per-merchant adaptive thresholds (`src/feedback.py`)
- [x] LLM layer — reviews medium-confidence flags only, degrades gracefully without API key (`src/llm_reasoner.py`)
- [ ] FastAPI wrapper + endpoints (Day 2)
- [ ] Razorpay test-mode wiring for real "pause/refund" actions (Day 3-4)
- [ ] Finance mode: spend categorization + forecast (Day 5)
- [ ] Growth mode: one scripted example (Day 6)
- [ ] Dashboard UI unifying all four (Day 6)
- [ ] End-to-end run + final metrics (Day 7)
- [ ] Repo polish + pitch video (Day 8)

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
