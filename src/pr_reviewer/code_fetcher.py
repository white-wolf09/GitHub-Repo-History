import httpx
from .config import env

class DiffError(Exception):
    pass

def _get_diff(url: str) -> str:
    headers = {"Accept": "application/vnd.github.v3.diff"}
    if env("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {env('GITHUB_TOKEN')}"
    try:
        r = httpx.get(url, headers=headers, timeout=30)
    except httpx.HTTPError as e:
        raise DiffError(f"Could not reach GitHub: {e}")
    if r.status_code != 200:
        raise DiffError(f"GitHub returned {r.status_code} when fetching the diff")
    return r.text

def fetch_diff(owner: str, repo: str, number: int) -> str:
    return _get_diff(f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}")

def fetch_compare_diff(owner: str, repo: str, base: str, head: str) -> str:
    """Everything the branch adds on top of the base branch = exactly what a merge would bring in."""
    return _get_diff(f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}")
