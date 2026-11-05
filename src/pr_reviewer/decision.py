"""Safety gate: merge only when EVERY check passes. Everything else -> no merge + reason."""
from .config import max_diff_chars, require_ticket
from .models import PRContext, CommitContext, Decision
from .story_fetcher import extract_ticket_id, fetch_story, StoryError
from .code_fetcher import fetch_diff, fetch_compare_diff, DiffError
from .scanner import scan_diff
from .evaluator import call_llm, parse_verdict, VerdictError

def decide(pr: PRContext, story_fn=fetch_story, diff_fn=fetch_diff, llm_fn=call_llm) -> Decision:
    if pr.draft:
        return Decision("skip", "PR is a draft")
    key = extract_ticket_id(pr.title, pr.branch)
    if not key:
        return Decision("skip", "No ticket ID found in PR title or branch name")
    try:
        story = story_fn(key)
    except StoryError as e:
        return Decision("skip", f"Ticket could not be fetched: {e}")
    if not story.criteria:
        return Decision("skip", f"Ticket {key} has no acceptance criteria")
    try:
        diff = diff_fn(pr.owner, pr.repo, pr.number)
    except DiffError as e:
        return Decision("skip", str(e))
    if not diff.strip():
        return Decision("skip", "Diff is empty")
    if len(diff) > max_diff_chars():
        return Decision("skip", f"Diff too large ({len(diff)} chars, limit {max_diff_chars()}); not truncating")
    try:
        verdict = parse_verdict(llm_fn(story.criteria, diff))
    except VerdictError as e:
        return Decision("skip", f"Evaluation failed: {e}")
    if verdict.fulfilled:
        return Decision("merge", f"Acceptance criteria of {key} fulfilled", verdict.feedback)
    return Decision("reject", f"Acceptance criteria of {key} not fulfilled", verdict.feedback)


def decide_commit(ctx: CommitContext, story_fn=fetch_story, diff_fn=fetch_compare_diff,
                  llm_fn=call_llm, scan_fn=scan_diff) -> Decision:
    """Push flow: review what the branch adds over the default branch; merge only if clean."""
    if ctx.branch == ctx.base:
        return Decision("skip", f"Push to default branch '{ctx.base}' is never auto-merged")
    if not ctx.sha or set(ctx.sha) == {"0"}:
        return Decision("skip", "No commit to review (branch deleted)")
    criteria, key = "", extract_ticket_id(ctx.message, ctx.branch)
    if key:
        try:
            story = story_fn(key)
        except StoryError as e:
            return Decision("skip", f"Ticket {key} was referenced but could not be fetched: {e}")
        if not story.criteria:
            return Decision("skip", f"Ticket {key} has no acceptance criteria")
        criteria = story.criteria
    elif require_ticket():
        return Decision("skip", "No ticket ID found in commit message or branch name (REQUIRE_TICKET=true)")
    try:
        diff = diff_fn(ctx.owner, ctx.repo, ctx.base, ctx.sha)
    except DiffError as e:
        return Decision("skip", str(e))
    if not diff.strip():
        return Decision("skip", "Diff is empty")
    if len(diff) > max_diff_chars():
        return Decision("skip", f"Diff too large ({len(diff)} chars, limit {max_diff_chars()}); not truncating")
    secrets = scan_fn(diff)
    if secrets:
        return Decision("reject", "Possible secrets committed (diff was not sent to the LLM)",
                        "**Blocked: possible secrets in the added code**\n\n" +
                        "\n".join(f"- {s}" for s in secrets) +
                        "\n\nRemove them, rotate the exposed credentials, and push again.")
    try:
        verdict = parse_verdict(llm_fn(criteria, diff))
    except VerdictError as e:
        return Decision("skip", f"Evaluation failed: {e}")
    what = f"ticket {key}" if key else "general review"
    if verdict.fulfilled:
        return Decision("merge", f"No flags found ({what})", verdict.feedback)
    return Decision("reject", f"Flags found ({what})", verdict.feedback)
