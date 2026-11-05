"""ngrok + GitHub webhook helper. ngrok must already be running: `ngrok http 8000`."""
import httpx
from .config import env
from .executor import _h

class TunnelError(Exception):
    pass

def get_public_url(api: str = "http://127.0.0.1:4040/api/tunnels") -> str:
    try:
        r = httpx.get(api, timeout=5)
    except httpx.HTTPError:
        raise TunnelError("ngrok is not running. Start it in another terminal with: ngrok http 8000")
    for t in r.json().get("tunnels", []):
        if str(t.get("public_url", "")).startswith("https://"):
            return t["public_url"]
    raise TunnelError("ngrok is running but has no https tunnel. Run: ngrok http 8000")

def register_webhook(repo_full: str, public_url: str) -> str:
    secret = env("GITHUB_WEBHOOK_SECRET")
    if not secret or not env("GITHUB_TOKEN"):
        raise TunnelError("Set GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET in .env first")
    hook_url = public_url.rstrip("/") + "/webhook"
    try:
        r = httpx.post(f"https://api.github.com/repos/{repo_full}/hooks", headers=_h(), timeout=30,
            json={"name": "web", "active": True, "events": ["push", "pull_request"],
                  "config": {"url": hook_url, "content_type": "json", "secret": secret}})
    except httpx.HTTPError as e:
        raise TunnelError(f"Could not reach GitHub: {e}")
    if r.status_code == 201:
        return f"Webhook created: {hook_url}"
    if r.status_code == 422:
        return ("GitHub refused (422): a webhook for this URL probably exists already. "
                "Delete old ones in Repo > Settings > Webhooks, since each ngrok restart changes the URL.")
    raise TunnelError(f"GitHub returned {r.status_code}. The token needs admin access to {repo_full}.")
