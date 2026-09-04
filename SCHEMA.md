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

## Multi-user layer (auth upgrade)

### User
| Field | Type | Notes |
|---|---|---|
| id | string | folder name under `data/users/<id>/` |
| email | string | unique (lowercased), format-verified at signup |
| name | string | display name |
| password | dict | `{algorithm: pbkdf2_sha256, salt, iterations, hash}` — never plaintext |
| created_at | ISO datetime | |

Per-user files: `transactions.json`, `subscriptions.json`,
`profile.json` (feedback.py's `DEFAULT_PROFILE` shape),
`ground_truth.json` (only the seeded demo account has labels),
`audit_trail.json`, `connections.json`, `connected_accounts.json`,
`razorpay_ids.json`. The bundled `data/*.json` files
remain the canonical demo dataset and seed the demo account on first
start (`store.migrate_legacy_data()`).

### Growth mode & autopay (bills)
| Field | Type | Notes |
|---|---|---|
| autopay_enabled_merchants | list[string] | per-user profile field (`feedback.set_autopay`); explicit opt-in per merchant — never inferred |
| connected_accounts.json | list | AA-style simulated consent flow: `{account_id, provider_id, provider_name, via, connected_at, consent_scope}` |
| razorpay_ids.json | dict | real test-mode entities per user: `{subscriptions: {merchant: {plan_id, subscription_id}}, orders: {merchant: order_id}}` — created by `setup_razorpay_entities.py` |

`GET /growth` returns subscriptions whose `next_due_date` is within 14
days (overdue ones included), each with `days_until_due`, `overdue`, and
`planned_action` (`auto_pay` only when the merchant is in
`autopay_enabled_merchants`, else `remind_only`). `next_due_date` is
*trusted as stored* — `generate_data.py` and manual subscription entry
both set it correctly at creation time (see the history note in
growth.py for the two bugs this avoids).

### Transaction additions (source tracking)
| Field | Type | Notes |
|---|---|---|
| source | enum: manual / bank_feed_demo (optional) | where the row came from; absent on bundled rows |
| added_at / imported_at | ISO datetime (optional) | when the row entered the ledger |
| account | string | manual entries: "manual"; feed rows: "<Bank> ••••1234" |

### Bank connection
| Field | Type | Notes |
|---|---|---|
| fund_account_id | string | RazorpayX reference (or `fa_demo_*` in simulated mode) |
| holder_name | string | |
| masked_account | string | raw account number is never stored |
| ifsc | string | 11-char, validated |
| bank_name | string | from IFSC prefix (mocked) or RazorpayX (live) |
| mode | enum: live / mocked | live only when RAZORPAY keys are set |
| status | enum: active / ... | |
| connected_at / synced_at | ISO datetime or null | |
| source_of_transactions | enum | bank_feed_demo (see honest-sync note in README) |

### Auth & session spec
- `POST /register` → validates name/email/password (≥8 chars), 400 on
  duplicate email; auto-logs-in.
- `POST /login` / `POST /logout` → sets / clears the `pma_session` cookie.
- Cookie: signed `user_id.expiry.hmac` (HMAC-SHA256, server secret from
  `PMA_SECRET_KEY` or `data/.secret`), HttpOnly, SameSite=Lax, 7-day TTL.
- All data endpoints depend on `current_user` and 401 without a session.
- Isolation: routes address only `data/users/<user_id>/*` resolved from
  the session — never from user-supplied paths.

## Metrics to report in the demo

- Precision / recall per pattern (against planted ground truth)
- False positive rate (how often clean events got flagged)
- ₹ flagged as recoverable vs ₹ actually recovered (simulated via test-mode)
- Count + examples of "needs review" cases (your graceful-failure story)
