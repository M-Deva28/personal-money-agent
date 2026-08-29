"""
LLM layer — used ONLY for medium-confidence flags where a rule fired but
genuinely needs judgment a fixed threshold can't capture.

Design principle: rules decide WHETHER to escalate (fast, free, deterministic).
The LLM only decides HOW to handle what's already been escalated (nuanced,
costs money, non-deterministic) -- and it never gets to auto-execute an
action on its own. Its output always lands in needs_review or gets promoted
to auto_executed only if it explicitly raises confidence to high.

Requires ANTHROPIC_API_KEY in the environment. If missing, falls back to
the rule's original reasoning untouched -- the system must degrade
gracefully, not crash, if the LLM is unavailable.
"""

import os
import json

try:
    import anthropic
    _CLIENT_AVAILABLE = bool(os.environ.get("ANTHROPIC_API_KEY"))
except ImportError:
    _CLIENT_AVAILABLE = False

MODEL = "claude-sonnet-4-6"

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


def review_ambiguous_flag(flag, context=None):
    """
    context: optional dict with extra info the rule didn't have, e.g.
      {"user_note": "I travel a lot for work", "recent_similar_events": [...]}

    Returns an updated flag dict. Never raises on API failure -- degrades
    to the original rule output so the pipeline keeps running.
    """
    context = context or {}
    result = dict(flag)  # don't mutate caller's copy
    result["llm_reviewed"] = False

    if not _CLIENT_AVAILABLE:
        result["llm_reasoning"] = (
            "LLM review unavailable (no API key set) -- falling back to "
            "rule-based reasoning only."
        )
        return result

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(flag, context)}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw)

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

        result["reasoning"] = f"{flag['reasoning']} LLM review: {parsed['explanation']}"

    except Exception as e:
        result["llm_reasoning"] = f"LLM review failed ({e}) -- using rule-based reasoning only."

    return result


def review_all_medium_confidence(flags, context_lookup=None):
    """
    context_lookup: optional dict mapping event_id -> context dict.
    Only touches flags with confidence == "medium"; high-confidence flags
    pass through untouched (no need to spend API calls on things the
    rules are already sure about).
    """
    context_lookup = context_lookup or {}
    reviewed = []
    for flag in flags:
        if flag["confidence"] == "medium":
            ctx = context_lookup.get(flag["event_id"], {})
            reviewed.append(review_ambiguous_flag(flag, ctx))
        else:
            reviewed.append(flag)
    return reviewed
