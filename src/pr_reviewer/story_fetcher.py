import re
import httpx
from .config import env
from .models import Story

TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

class StoryError(Exception):
    pass

def extract_ticket_id(title: str, branch: str) -> str | None:
    for text in (title, branch):
        m = TICKET_RE.search((text or "").upper())
        if m:
            return m.group(1)
    return None

def _criteria_from_description(desc: str) -> str:
    m = re.search(r"acceptance criteria[:\s]*\n?(.*)", desc or "", re.I | re.S)
    return m.group(1).strip() if m else ""

def fetch_story(key: str) -> Story:
    base = env("JIRA_BASE_URL").rstrip("/")
    if not base:
        raise StoryError("JIRA_BASE_URL is not configured")
    try:
        r = httpx.get(f"{base}/rest/api/2/issue/{key}",
                      auth=(env("JIRA_EMAIL"), env("JIRA_API_TOKEN")), timeout=20)
    except httpx.HTTPError as e:
        raise StoryError(f"Could not reach Jira: {e}")
    if r.status_code != 200:
        raise StoryError(f"Jira returned {r.status_code} for {key}")
    fields = r.json().get("fields", {})
    custom = env("JIRA_AC_FIELD")
    criteria = (fields.get(custom) if custom else None) or \
        _criteria_from_description(fields.get("description") or "")
    return Story(key=key, title=fields.get("summary", ""), criteria=str(criteria or "").strip())
