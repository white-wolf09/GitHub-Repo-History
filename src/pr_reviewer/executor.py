import httpx
from .config import env, dry_run
from .models import PRContext, CommitContext, Decision

API = "https://api.github.com"

def _h():
    h = {"Accept": "application/vnd.github+json"}
    if env("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {env('GITHUB_TOKEN')}"
    return h

def _do(steps: list, label: str, method: str, url: str, body: dict):
    if dry_run():
        steps.append(f"[dry-run] {label}")
        return
    r = httpx.request(method, url, headers=_h(), json=body, timeout=30)
    steps.append(f"{label} -> HTTP {r.status_code}")
    r.raise_for_status()

def execute(pr: PRContext, d: Decision) -> Decision:
    base = f"{API}/repos/{pr.owner}/{pr.repo}"
    try:
        if d.action == "reject":
            _do(d.steps, "Post feedback comment", "POST", f"{base}/issues/{pr.number}/comments",
                {"body": f"### Automated review: changes needed\n\n{d.feedback}"})
            _do(d.steps, "Set commit status to failure (blocks merge)", "POST",
                f"{base}/statuses/{pr.head_sha}",
                {"state": "failure", "context": "ai-pr-reviewer", "description": d.reason[:140]})
        elif d.action == "merge":
            _do(d.steps, "Post verification summary", "POST", f"{base}/issues/{pr.number}/comments",
                {"body": f"### Automated review: approved\n\n{d.feedback}"})
            # sha pin: GitHub refuses (409) if the head moved since we reviewed it
            _do(d.steps, "Merge PR (sha-pinned)", "PUT", f"{base}/pulls/{pr.number}/merge",
                {"sha": pr.head_sha, "merge_method": "squash"})
        else:
            d.steps.append(f"No action taken: {d.reason}")
    except httpx.HTTPError as e:
        d.steps.append(f"Action failed, nothing further done: {e}")
    return d


def execute_commit(ctx: CommitContext, d: Decision) -> Decision:
    base = f"{API}/repos/{ctx.owner}/{ctx.repo}"
    try:
        if d.action == "reject":
            _do(d.steps, "Post flags as commit comment", "POST", f"{base}/commits/{ctx.sha}/comments",
                {"body": f"### Automated review: flags found\n\n{d.feedback}"})
            _do(d.steps, "Set commit status to failure", "POST", f"{base}/statuses/{ctx.sha}",
                {"state": "failure", "context": "ai-commit-reviewer", "description": d.reason[:140]})
        elif d.action == "merge":
            _do(d.steps, "Post verification summary", "POST", f"{base}/commits/{ctx.sha}/comments",
                {"body": f"### Automated review: no flags\n\n{d.feedback}"})
            _do(d.steps, "Set commit status to success", "POST", f"{base}/statuses/{ctx.sha}",
                {"state": "success", "context": "ai-commit-reviewer", "description": d.reason[:140]})
            # head is the reviewed SHA, so commits pushed afterwards are never merged unreviewed
            _do(d.steps, f"Merge {ctx.branch} into {ctx.base} (sha-pinned)", "POST", f"{base}/merges",
                {"base": ctx.base, "head": ctx.sha,
                 "commit_message": f"Auto-merge {ctx.branch} (AI review passed)"})
        else:
            d.steps.append(f"No action taken: {d.reason}")
    except httpx.HTTPError as e:
        d.steps.append(f"Action failed, nothing further done: {e}")
    return d
