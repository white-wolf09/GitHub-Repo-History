import json
import httpx
from .config import env
from .models import Verdict

SYSTEM_PROMPT = (
    "You are an expert Senior Code Reviewer. Compare the provided Code Diff against the "
    "provided Jira/Azure Acceptance Criteria. Answer in JSON with two keys: fulfilled "
    "(true/false) and feedback (markdown string). If fulfilled is false, provide a precise "
    "list of missing requirements or code bugs. If true, provide a brief summary of the "
    "successful verification. Return only the JSON object, nothing else."
)

GENERAL_PROMPT = (
    "You are an expert Senior Code Reviewer. No ticket was provided, so review the Code Diff "
    "for flags: bugs, broken or incomplete code, security problems, hardcoded credentials, "
    "and dangerous changes. Answer in JSON with two keys: fulfilled (true only if there are "
    "NO blocking flags) and feedback (markdown string). If fulfilled is false, list each flag "
    "precisely. If true, give a brief summary of what was verified. Return only the JSON object."
)

class VerdictError(Exception):
    pass

def parse_verdict(text: str) -> Verdict:
    """Strict parser: anything unexpected raises VerdictError (never guesses)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        raise VerdictError("LLM answer is not valid JSON")
    if not isinstance(data, dict) or not isinstance(data.get("fulfilled"), bool):
        raise VerdictError("LLM answer is missing a boolean 'fulfilled' key")
    if not isinstance(data.get("feedback"), str):
        raise VerdictError("LLM answer is missing a string 'feedback' key")
    return Verdict(data["fulfilled"], data["feedback"])

def call_llm(criteria: str, diff: str) -> str:
    system = SYSTEM_PROMPT if criteria.strip() else GENERAL_PROMPT
    user = (f"## Acceptance Criteria\n{criteria}\n\n" if criteria.strip() else "") + f"## Code Diff\n{diff}"
    try:
        if env("LLM_PROVIDER", "anthropic").lower() == "groq":
            r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=120,
                headers={"Authorization": f"Bearer {env('LLM_API_KEY')}"},
                json={"model": env("LLM_MODEL", "llama-3.3-70b-versatile"), "temperature": 0,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        key = env("LLM_API_KEY") or env("ANTHROPIC_API_KEY")
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={"model": env("LLM_MODEL", "claude-sonnet-4-5"), "max_tokens": 1500,
                  "system": system, "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except (httpx.HTTPError, KeyError, IndexError) as e:
        raise VerdictError(f"LLM request failed: {e}")
