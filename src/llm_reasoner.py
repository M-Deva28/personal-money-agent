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
"""

import os
import json

try:
    from google import genai
    from google.genai import types
    _CLIENT_AVAILABLE = bool(os.environ.get("GEMINI_API_KEY"))
except ImportError:
    _CLIENT_AVAILABLE = False

MODEL = "gemini-3.6-flash"  # gemini-2.5-flash was deprecated for new users
                             # (confirmed via a live 404 from the API itself,
                             # which named this as the replacement)

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
