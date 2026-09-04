"""
Jarvis brain, server-side — powers the dashboard's "Hey Jarvis" voice
assistant. Ported from the Jarvis desktop app's core/gemini_brain.py:
same tool-calling loop, plain urllib (no new dependency), but wired to
voice_tools.py's TOOL_SCHEMAS/TOOL_FUNCTIONS -- the canonical ones that
call detector.py/finance.py/feedback.py directly.

If you're keeping the standalone Jarvis desktop app in sync, its
tools/money_tools.py is the copy; this file + voice_tools.py are the
source of truth now that both live behind one dashboard.
"""

import json
import os
import urllib.error
import urllib.request

from voice_tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = """You are Jarvis, a witty, sharp, efficient personal AI assistant \
inspired by Tony Stark's AI, answering by voice on the user's Personal Money Agent \
dashboard. Keep replies SHORT and speakable out loud -- one or two sentences unless \
the user explicitly asks for more detail. You can check flagged fraud/waste, give a \
spend forecast, and record feedback on a flag using your tools. If something is \
outside those tools, say so plainly rather than guessing."""


class GeminiUnavailable(RuntimeError):
    """Raised when the server has no GEMINI_API_KEY configured -- lets
    main.py return a clear, actionable error instead of a stack trace."""


def _call_api(contents):
    if not GEMINI_API_KEY:
        raise GeminiUnavailable(
            "GEMINI_API_KEY is not set on the server. Get a free key at "
            "https://aistudio.google.com/apikey, then set it before starting "
            "uvicorn: export GEMINI_API_KEY='your-key-here'"
        )

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "tools": [{"functionDeclarations": TOOL_SCHEMAS}],
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API error {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach Gemini API: {e}")


def respond(history):
    """
    history: list of {"role": "user"|"assistant", "content": str}
    (conversation history lives in the browser tab, not on the server --
    the client just sends it back each turn, same shape as Jarvis's own
    memory_manager stores).

    Returns (reply_text, tool_calls) -- tool_calls is a list of
    {"name", "args", "result"} so the UI can show what Jarvis actually
    did, not just what it said.
    """
    contents = []
    for m in history:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    tool_calls_made = []

    for _ in range(MAX_TOOL_ROUNDS):
        data = _call_api(contents)

        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
        except (KeyError, IndexError):
            reason = data.get("candidates", [{}])[0].get("finishReason", "unknown")
            raise RuntimeError(f"Gemini returned no usable content (reason: {reason})")

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not function_calls:
            text = "".join(p.get("text", "") for p in parts).strip()
            return text, tool_calls_made

        contents.append(candidate["content"])

        response_parts = []
        for call in function_calls:
            name = call["name"]
            args = call.get("args", {})
            func = TOOL_FUNCTIONS.get(name)

            if func is None:
                result_text = f"Unknown tool '{name}'."
            else:
                try:
                    result_text = func(**args)
                except Exception as e:
                    result_text = f"Tool '{name}' failed: {e}"

            tool_calls_made.append({"name": name, "args": args, "result": result_text})
            response_parts.append({
                "functionResponse": {"name": name, "response": {"result": result_text}}
            })

        contents.append({"role": "user", "parts": response_parts})

    return (
        "I tried a few tool calls but couldn't land on an answer — could you rephrase?",
        tool_calls_made,
    )
