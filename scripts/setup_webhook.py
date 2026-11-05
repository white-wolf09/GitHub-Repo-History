"""Usage:  python scripts/setup_webhook.py owner/repo   (ngrok http 8000 must be running)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pr_reviewer.tunnel import get_public_url, register_webhook, TunnelError

if len(sys.argv) != 2 or "/" not in sys.argv[1]:
    sys.exit("Usage: python scripts/setup_webhook.py owner/repo")
try:
    url = get_public_url()
    print("ngrok URL:", url)
    print(register_webhook(sys.argv[1], url))
except TunnelError as e:
    sys.exit(f"Error: {e}")
