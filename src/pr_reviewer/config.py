import os
from pathlib import Path

def load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader: never overrides variables already set, skips empty values."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #")[0].strip().strip('"').strip("'")
        if k.strip() and v:
            os.environ.setdefault(k.strip(), v)

load_dotenv()

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

def dry_run() -> bool:
    return env("DRY_RUN", "true").lower() != "false"

def require_ticket() -> bool:
    return env("REQUIRE_TICKET", "false").lower() == "true"

def max_diff_chars() -> int:
    return int(env("MAX_DIFF_CHARS", "60000"))
