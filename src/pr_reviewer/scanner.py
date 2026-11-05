"""Deterministic secret scan of ADDED lines. Runs before the LLM so secrets are never sent to it."""
import re

PATTERNS = {
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}",
    "Groq/OpenAI-style API key": r"\bgsk_[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9]{32,}",
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "Atlassian API token": r"ATATT3x[A-Za-z0-9_\-=]{20,}",
    "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "Private key block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
}

def scan_diff(diff: str) -> list[str]:
    """Return findings like 'GitHub token (2 lines)'. Never returns the secret itself."""
    counts: dict[str, int] = {}
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for label, pat in PATTERNS.items():
            if re.search(pat, line):
                counts[label] = counts.get(label, 0) + 1
    return [f"{k} ({v} line{'s' if v > 1 else ''})" for k, v in counts.items()]
