# Track A: Automated Reviewer (Web Application)

**Track: A.** FastAPI webhook listener + dashboard. GitHub + (optional) Jira + an LLM (Groq or Anthropic) + ngrok.

## Two flows
1. **Push flow:** push a commit to a branch (not the default branch) -> the diff against the default branch is
   fetched -> deterministic secret scan -> LLM review for flags -> clean = merge into the default branch,
   flagged = commit comment + `failure` status. If the branch name or commit message has a ticket ID
   (e.g. `KAN-12`) its acceptance criteria are used too.
2. **Pull request flow:** same checks against the ticket's acceptance criteria; merge via the PR merge API.

Merge never happens on: default-branch pushes, deleted branches, unreadable ticket, ticket without criteria,
empty or oversized diff, secrets found, invalid LLM JSON, missing boolean `fulfilled`, or any flag.
Merges are pinned to the reviewed commit SHA. `DRY_RUN=true` is the default.

## Libraries
FastAPI, Uvicorn, httpx. Tests use built-in unittest.

## Setup (Windows PowerShell, project folder)
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # fill in .env with your keys (never paste keys in chat)
python -c "import secrets; print(secrets.token_hex(20))"    # use the output as GITHUB_WEBHOOK_SECRET
```
## Run
Terminal 1: `uvicorn pr_reviewer.main:app --port 8000 --app-dir src`
Terminal 2: `ngrok config add-authtoken <your ngrok authtoken>` (once), then `ngrok http 8000`
Terminal 3: `python scripts\setup_webhook.py owner/test-repo` (registers the webhook for push + pull_request)
Then push a commit to a feature branch of your TEST repo and watch http://127.0.0.1:8000.
ngrok's free URL changes on every restart: delete the old webhook in the repo settings and re-run step 3.

## Test
```
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```
Demo evidence: one run with `DRY_RUN=false` ending in a failed review (comment + failure status) and one ending in a merge. Test repository only.
