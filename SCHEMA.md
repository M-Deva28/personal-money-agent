# Personal Money Agent — Data Schema & Detection Spec

## Entities

### Transaction
| Field | Type | Notes |
|---|---|---|
| id | string | unique |
| timestamp | ISO datetime | |
| amount | float (INR) | |
| direction | enum: debit / credit | |
| merchant_name | string | |
| category | string | e.g. streaming, food, utility, transfer |
| payment_mode | enum: UPI / card / netbanking | |
| memo | string | free text, sometimes empty |
| linked_subscription_id | string or null | ties recurring charges together |
| account | string | which bank/card account |

### Subscription
| Field | Type | Notes |
|---|---|---|
| id | string | unique |
| merchant_name | string | |
| amount | float | current billed amount |
| billing_cycle | enum: monthly / annual | |
| started_date | date | |
| last_charged_date | date | |
| next_due_date | date | |
| status | enum: trial / active / cancelled | |
| trial_end_date | date or null | |
| last_usage_signal_date | date or null | proxy for "user actually used this" |

## Ground-truth labeled patterns (planted in generator, held out for scoring)

| Pattern | Label | Target mode | Detection signal |
|---|---|---|---|
| Zombie subscription | `zombie_sub` | Recovery | active sub, no usage signal 60+ days |
| Silent trial-to-paid | `silent_conversion` | Recovery | trial_end passed, status=active, no notice event |
| Duplicate charge | `duplicate_charge` | Risk | same merchant+amount within 5 min window |
| Silent price hike | `price_hike` | Risk | recurring amount >15% above 3-charge rolling baseline, no notice |
| Refund not received | `refund_owed` | Recovery | merchant metadata says refunded, no matching credit within 10 days |
| Suspicious UPI collect | `suspicious_collect` | Risk | payee not in known-merchant list + isolated one-off pattern |
| CLEAN (control) | `clean` | none | superficially similar but legitimate — e.g. notified price change |

## Confidence tiers → action mapping

- **High confidence** (clear pattern match, no ambiguity) → auto-flag + draft bounded action (pause sub / refund claim) via Razorpay test-mode
- **Medium confidence** (pattern present but weak signal) → routed to "needs review" queue, NOT auto-acted
- **Every decision** logged to shared audit trail:
  `{event_id, detected_pattern, confidence, action_taken, reasoning, timestamp}`

## Metrics to report in the demo

- Precision / recall per pattern (against planted ground truth)
- False positive rate (how often clean events got flagged)
- ₹ flagged as recoverable vs ₹ actually recovered (simulated via test-mode)
- Count + examples of "needs review" cases (your graceful-failure story)
