"""
LLM layer -- used ONLY for medium-confidence flags where a rule fired but
genuinely needs judgment a fixed threshold can't capture.

Uses Google's Gemini API (via Google AI Studio) rather than Anthropic --
chosen for its generous free tier, which matters for a student budget.
Get a free key at https://aistudio.google.com -> Get API key.

Design principle: rules decide WHETHER to escalate (fast, free,
deterministic). The LLM only decides HOW to handle what's already been
escalated (nuanced, costs a request, non-deterministic) -- and it never
gets to auto-execute an action on its own. Its output always lands in
needs_review or gets promoted to auto_executed only if it explicitly
raises confidence to high.

Requires GEMINI_API_KEY in the environment. If missing, falls back to
the rule's original reasoning untouched -- the system must degrade
gracefully, not crash, if the LLM is unavailable.

CACHING -- added after a real problem surfaced in live testing: every
dashboard reload re-ran the full pipeline, which called the LLM fresh
for all 13 medium-confidence flags EVERY time. Google's free tier
allows only 20 requests/day for this model, so that burned the entire
day's quota in under two page loads (confirmed via live 429
RESOURCE_EXHAUSTED errors), and even before hitting the limit, 13
sequential live API calls made every reload visibly slow.

Fix: cache each flag's review, keyed by flag_id, to disk. Once a flag
has been successfully reviewed, every future run reuses that cached
verdict instantly -- no repeat API call, no repeat latency. Only a
flag that has never been reviewed (or whose last attempt failed)
calls the API. This is also a more honest product behavior anyway:
a real personal agent shouldn't need to re-ask the same question
about the same unchanged event every time you open the app.
"""

import os
import json

try:
    from google import genai
    from google.genai import types
    _CLIENT_AVAILABLE = bool(os.environ.get("GEMINI_API_KEY"))
except ImportError:
    _CLIENT_AVAILABLE = False

MODEL = "gemini-2.5-flash-lite"  # Switched from gemini-3.6-flash after
    # hitting a real, confirmed 20/day quota on that model (live 429
    # RESOURCE_EXHAUSTED error). Free-tier quotas are tracked per-model
    # and change frequently -- rather than guess at a number from
    # inconsistent docs, we picked the older, more established "Lite"
    # tier, which independent sources consistently describe as having
    # Google's most generous free allowance. This also gives a fresh,
    # separate quota bucket immediately, no waiting for a reset.
    #
    # Combined with the caching in this file: we only need this
    # quota to cover ONE clean pass over the unique medium-confidence
    # flags (13 in the current dataset) ONE time, ever -- after that,
    # every flag's review is permanently cached and costs nothing.

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_CACHE_PATH = os.path.join(DATA_DIR, "llm_review_cache.json")

SYSTEM_PROMPT = """You are a careful assistant reviewing a single flagged \
personal-finance event for a user. A rule-based system already detected a \
pattern and assigned medium confidence -- meaning it's plausible but not \
certain. Your job is NOT to invent new judgment calls the user hasn't \
asked for; it is to decide, given the specific details, whether this looks \
like a genuine issue worth the user's attention or a likely false alarm, \
and explain briefly why in plain, friendly language.

Respond ONLY with JSON, no markdown fences, no preamble:
{"verdict": "likely_genuine" | "likely_false_alarm", \
"confidence": "high" | "medium", \
"explanation": "one or two plain-English sentences a non-technical user would understand"}
"""


def _build_user_prompt(flag, context):
    return json.dumps({
        "flag": {
            "pattern": flag["pattern"],
            "rule_reasoning": flag["reasoning"],
            "amount_at_stake": flag["amount_at_stake"],
        },
        "additional_context": context,
    }, indent=2)


def _load_cache():
    if os.path.exists(_CACHE_PATH):
        with open(_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    with open(_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def _cache_key(flag):
    # flag_id (event_id + pattern) uniquely identifies this flag -- see
    # detector.py's run_all_detectors for why event_id alone isn't safe.
    return flag.get("flag_id") or f"{flag['event_id']}__{flag['pattern']}"


def review_ambiguous_flag(flag, context=None):
    """
    context: optional dict with extra info the rule didn't have, e.g.
      {"user_note": "I travel a lot for work", "recent_similar_events": [...]}

    Returns an updated flag dict. Never raises on API failure -- degrades
    to the original rule output so the pipeline keeps running. Checks
    the on-disk cache first; only calls the API for flags that have
    never been successfully reviewed before.
    """
    context = context or {}
    result = dict(flag)  # don't mutate caller's copy
    key = _cache_key(flag)

    cache = _load_cache()
    if key in cache:
        cached = cache[key]
        result["llm_reviewed"] = True
        result["llm_verdict"] = cached["llm_verdict"]
        result["llm_explanation"] = cached["llm_explanation"]
        result["llm_from_cache"] = True
        if cached["llm_verdict"] == "likely_genuine" and cached["confidence"] == "high":
            result["confidence"] = "high"
        elif cached["llm_verdict"] == "likely_false_alarm":
            result["confidence"] = "low_likely_false_alarm"
        return result

    result["llm_reviewed"] = False

    if not _CLIENT_AVAILABLE:
        result["llm_reasoning"] = (
            "LLM review unavailable (no GEMINI_API_KEY set) -- falling back "
            "to rule-based reasoning only."
        )
        return result

    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = client.models.generate_content(
            model=MODEL,
            contents=_build_user_prompt(flag, context),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=300,
            ),
        )
        parsed = json.loads(response.text)

        result["llm_reviewed"] = True
        result["llm_verdict"] = parsed["verdict"]
        result["llm_explanation"] = parsed["explanation"]

        # LLM can only ESCALATE confidence with a genuine verdict, never
        # silently downgrade a rule's high-confidence catch elsewhere --
        # this function only ever runs on medium-confidence flags anyway.
        if parsed["verdict"] == "likely_genuine" and parsed["confidence"] == "high":
            result["confidence"] = "high"
        elif parsed["verdict"] == "likely_false_alarm":
            result["confidence"] = "low_likely_false_alarm"

        # NOTE: deliberately NOT appending the LLM's explanation into
        # result["reasoning"] here. The dashboard already renders
        # llm_explanation in its own separate line -- appending it here
        # too caused a real duplication bug (same sentence shown twice
        # on one card), caught via a screenshot during live testing.
        # reasoning stays as the rule's original text; llm_explanation
        # carries the LLM's added commentary, shown once, separately.

        # Only cache on SUCCESS. A failed attempt (e.g. rate limit) is
        # deliberately not cached, so it retries on the next run instead
        # of permanently freezing a fallback message.
        cache[key] = {
            "llm_verdict": parsed["verdict"],
            "confidence": parsed["confidence"],
            "llm_explanation": parsed["explanation"],
        }
        _save_cache(cache)

    except Exception as e:
        result["llm_reasoning"] = f"LLM review failed ({e}) -- using rule-based reasoning only."

    return result


def review_all_medium_confidence(flags, context_lookup=None, max_live_calls=5):
    """
    context_lookup: optional dict mapping event_id -> context dict.
    Only touches flags with confidence == "medium"; high-confidence flags
    pass through untouched (no need to spend API calls on things the
    rules are already sure about).

    max_live_calls: safety cap on how many UNCACHED flags get a live API
    call in a single run. Cached flags are always resolved (free, no
    network) regardless of this cap. Prevents a bad quota day from
    turning every dashboard load into a slow sweep of 13 doomed
    requests -- once the cap is hit, remaining uncached flags fall back
    to rule-based reasoning for this run and get picked up on a later
    run instead.
    """
    context_lookup = context_lookup or {}
    cache = _load_cache()
    reviewed = []
    live_calls_made = 0

    for flag in flags:
        if flag["confidence"] != "medium":
            reviewed.append(flag)
            continue

        key = _cache_key(flag)
        if key in cache or live_calls_made < max_live_calls:
            if key not in cache:
                live_calls_made += 1
            ctx = context_lookup.get(flag["event_id"], {})
            reviewed.append(review_ambiguous_flag(flag, ctx))
        else:
            result = dict(flag)
            result["llm_reviewed"] = False
            result["llm_reasoning"] = (
                f"Skipped live LLM review this run (per-run cap of "
                f"{max_live_calls} uncached calls reached) -- falling back "
                f"to rule-based reasoning. Will retry on a future run."
            )
            reviewed.append(result)

    return reviewed
