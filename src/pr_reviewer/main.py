import hashlib, hmac, re, time
from pathlib import Path
import httpx
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from .config import env, dry_run
from .models import PRContext, CommitContext
from .decision import decide, decide_commit
from .executor import execute, execute_commit, _h, API

app = FastAPI(title="AI PR Reviewer")
EVENTS: list[dict] = []
ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

def from_payload(p: dict) -> PRContext:
    pr = p["pull_request"]
    return PRContext(p["repository"]["owner"]["login"], p["repository"]["name"], pr["number"],
                     pr["title"], pr["head"]["ref"], pr["head"]["sha"], pr.get("draft", False))

def parse_repo(owner: str, repo: str) -> tuple[str, str]:
    """Accept plain names or a pasted GitHub URL in either field."""
    m = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", f"{owner} {repo}")
    if m:
        return m.group(1), m.group(2).removesuffix(".git")
    return owner.strip(), repo.strip().removesuffix(".git")

def process(pr: PRContext):
    d = execute(pr, decide(pr))
    EVENTS.insert(0, {"time": time.strftime("%H:%M:%S"), "pr": f"{pr.owner}/{pr.repo}#{pr.number}",
                      "title": pr.title, "action": d.action, "reason": d.reason,
                      "steps": d.steps, "dry_run": dry_run()})
    del EVENTS[50:]

def from_push(p: dict) -> CommitContext | None:
    ref = p.get("ref", "")
    if not ref.startswith("refs/heads/") or p.get("deleted"):
        return None
    r = p["repository"]
    owner = r["owner"].get("login") or r["owner"].get("name")
    return CommitContext(owner, r["name"], ref.removeprefix("refs/heads/"),
                         r.get("default_branch", "main"), p.get("after", ""),
                         (p.get("head_commit") or {}).get("message", ""))

def process_commit(ctx: CommitContext):
    d = execute_commit(ctx, decide_commit(ctx))
    EVENTS.insert(0, {"time": time.strftime("%H:%M:%S"), "pr": f"{ctx.owner}/{ctx.repo}@{ctx.branch}",
                      "title": (ctx.message.splitlines() or ["(no message)"])[0], "action": d.action,
                      "reason": d.reason, "steps": d.steps, "dry_run": dry_run()})
    del EVENTS[50:]

@app.post("/webhook")
async def webhook(req: Request, bg: BackgroundTasks, x_hub_signature_256: str = Header(None),
                  x_github_event: str = Header(None)):
    body = await req.body()
    secret = env("GITHUB_WEBHOOK_SECRET").encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected, x_hub_signature_256 or ""):
        raise HTTPException(401, "Invalid signature")
    if x_github_event not in ("pull_request", "push"):
        return {"ignored": x_github_event}
    payload = await req.json()
    if x_github_event == "push":
        ctx = from_push(payload)
        if ctx is None:
            return {"ignored": "push without a branch commit"}
        bg.add_task(process_commit, ctx)
        return {"accepted": True}
    if payload.get("action") not in ACTIONS:
        return {"ignored": payload.get("action")}
    bg.add_task(process, from_payload(payload))
    return {"accepted": True}

@app.post("/api/review")
def manual_review(data: dict, bg: BackgroundTasks):
    """Dashboard trigger: review an existing PR by owner/repo/number."""
    o, r, n = data.get("owner"), data.get("repo"), data.get("number")
    if not (o and r and n):
        raise HTTPException(400, "owner, repo and number are required")
    o, r = parse_repo(str(o), str(r))
    try:
        resp = httpx.get(f"{API}/repos/{o}/{r}/pulls/{n}", headers=_h(), timeout=30)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach GitHub: {e}")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code,
            f"GitHub returned {resp.status_code} for {o}/{r}#{n}. "
            "Check the owner, repo name, PR number and GITHUB_TOKEN.")
    pr = resp.json()
    ctx = PRContext(o, r, int(n), pr["title"], pr["head"]["ref"], pr["head"]["sha"], pr.get("draft", False))
    bg.add_task(process, ctx)
    return {"accepted": True}

@app.post("/api/review-commit")
def manual_review_commit(data: dict, bg: BackgroundTasks):
    """Dashboard trigger: review the latest commit of a branch."""
    o, r, b = data.get("owner"), data.get("repo"), data.get("branch")
    if not (o and r and b):
        raise HTTPException(400, "owner, repo and branch are required")
    o, r = parse_repo(str(o), str(r))
    try:
        repo = httpx.get(f"{API}/repos/{o}/{r}", headers=_h(), timeout=30)
        br = httpx.get(f"{API}/repos/{o}/{r}/branches/{b}", headers=_h(), timeout=30)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach GitHub: {e}")
    for resp, what in ((repo, f"{o}/{r}"), (br, f"{o}/{r} branch {b}")):
        if resp.status_code != 200:
            raise HTTPException(resp.status_code,
                f"GitHub returned {resp.status_code} for {what}. Check the names and GITHUB_TOKEN.")
    c = br.json()["commit"]
    ctx = CommitContext(o, r, b, repo.json().get("default_branch", "main"), c["sha"],
                        c.get("commit", {}).get("message", ""))
    bg.add_task(process_commit, ctx)
    return {"accepted": True}

@app.get("/api/events")
def events():
    return {"dry_run": dry_run(), "events": EVENTS}

@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()
