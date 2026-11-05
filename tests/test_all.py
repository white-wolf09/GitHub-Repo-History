"""Run with:  python -m unittest discover -s tests -v   (or pytest). Needs PYTHONPATH=src."""
import asyncio, hashlib, hmac, json, os, sys, unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx
from fastapi import HTTPException
from pr_reviewer import main, executor
from pr_reviewer.models import PRContext, Story, Decision
from pr_reviewer.decision import decide
from pr_reviewer.evaluator import parse_verdict, VerdictError
from pr_reviewer.story_fetcher import extract_ticket_id, fetch_story, StoryError
from pr_reviewer.code_fetcher import fetch_diff, DiffError

PR = PRContext("o", "r", 1, "JIRA-12 login page", "feature/JIRA-12-login", "abc123")
story = lambda k: Story(k, "t", "User can log in")
diff = lambda *a: "diff --git a b\n+code"
ok = lambda c, d: '{"fulfilled": true, "feedback": "good"}'
resp = lambda code, js=None, text="": MagicMock(status_code=code, json=lambda: js, text=text)

class Env(unittest.TestCase):
    def setUp(self):
        self.p = patch.dict(os.environ, {"DRY_RUN": "true", "GITHUB_TOKEN": "t"}, clear=False); self.p.start()
    def tearDown(self): self.p.stop()

class SafetyGate(Env):
    def test_merge_only_when_all_pass(self): self.assertEqual(decide(PR, story, diff, ok).action, "merge")
    def test_false_verdict_rejects(self):
        d = decide(PR, story, diff, lambda c, d: '{"fulfilled": false, "feedback": "x"}')
        self.assertEqual(d.action, "reject")
    def test_no_ticket(self):
        pr = PRContext("o", "r", 1, "fix stuff", "tidy-up", "abc")
        d = decide(pr, story, diff, ok); self.assertEqual(d.action, "skip"); self.assertIn("No ticket", d.reason)
    def test_ticket_fetch_error(self):
        def boom(k): raise StoryError("404")
        self.assertEqual(decide(PR, boom, diff, ok).action, "skip")
    def test_no_criteria(self): self.assertEqual(decide(PR, lambda k: Story(k, "t", ""), diff, ok).action, "skip")
    def test_empty_diff(self): self.assertEqual(decide(PR, story, lambda *a: "  ", ok).action, "skip")
    def test_diff_fetch_error(self):
        def boom(*a): raise DiffError("x")
        self.assertEqual(decide(PR, story, boom, ok).action, "skip")
    def test_diff_too_large(self):
        with patch.dict(os.environ, {"MAX_DIFF_CHARS": "5"}):
            self.assertIn("too large", decide(PR, story, diff, ok).reason)
    def test_draft_skipped(self):
        self.assertEqual(decide(PRContext("o", "r", 1, "JIRA-1", "b", "a", draft=True), story, diff, ok).action, "skip")
    def test_bad_llm_output_never_merges(self):
        for bad in ["not json", "{}", '{"fulfilled": "yes", "feedback": ""}', '{"fulfilled": true}', "[1]", ""]:
            with self.subTest(bad=bad):
                with self.assertRaises(VerdictError): parse_verdict(bad)
                self.assertEqual(decide(PR, story, diff, lambda c, d: bad).action, "skip")
    def test_fenced_json_accepted(self):
        self.assertTrue(parse_verdict('```json\n{"fulfilled": true, "feedback": "ok"}\n```').fulfilled)

class Story_(Env):
    def test_extract(self):
        self.assertEqual(extract_ticket_id("x", "feature/JIRA-123-login-page"), "JIRA-123")
        self.assertEqual(extract_ticket_id("proj-7: fix", "main"), "PROJ-7")
        self.assertIsNone(extract_ticket_id("fix stuff", "tidy"))
    def test_fetch_criteria_from_description(self):
        js = {"fields": {"summary": "Login", "description": "Intro\nAcceptance Criteria:\n- can log in"}}
        with patch.dict(os.environ, {"JIRA_BASE_URL": "https://j.example"}), patch("httpx.get", return_value=resp(200, js)):
            s = fetch_story("JIRA-1"); self.assertIn("can log in", s.criteria)
    def test_fetch_custom_field(self):
        js = {"fields": {"summary": "L", "customfield_1": "AC text"}}
        with patch.dict(os.environ, {"JIRA_BASE_URL": "https://j.example", "JIRA_AC_FIELD": "customfield_1"}), \
             patch("httpx.get", return_value=resp(200, js)):
            self.assertEqual(fetch_story("JIRA-1").criteria, "AC text")
    def test_fetch_404(self):
        with patch.dict(os.environ, {"JIRA_BASE_URL": "https://j.example"}), patch("httpx.get", return_value=resp(404)):
            with self.assertRaises(StoryError): fetch_story("JIRA-1")
    def test_no_base_url(self):
        with patch.dict(os.environ, {"JIRA_BASE_URL": ""}):
            with self.assertRaises(StoryError): fetch_story("JIRA-1")

class Diff(Env):
    def test_ok_and_header(self):
        with patch("httpx.get", return_value=resp(200, text="DIFF")) as g:
            self.assertEqual(fetch_diff("o", "r", 1), "DIFF")
            self.assertIn("Authorization", g.call_args.kwargs["headers"])
    def test_no_token_no_auth_header(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}), patch("httpx.get", return_value=resp(200, text="D")) as g:
            fetch_diff("o", "r", 1); self.assertNotIn("Authorization", g.call_args.kwargs["headers"])
    def test_404(self):
        with patch("httpx.get", return_value=resp(404)):
            with self.assertRaises(DiffError): fetch_diff("o", "r", 1)
    def test_network_error(self):
        with patch("httpx.get", side_effect=httpx.HTTPError("down")):
            with self.assertRaises(DiffError): fetch_diff("o", "r", 1)

class Executor(Env):
    def test_dry_run_makes_no_http_calls(self):
        with patch("httpx.request") as rq:
            d = executor.execute(PR, Decision("merge", "r", "fb"))
            rq.assert_not_called(); self.assertTrue(all("[dry-run]" in s for s in d.steps))
    def test_live_reject_comments_and_fails_status(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request", return_value=resp(201)) as rq:
            executor.execute(PR, Decision("reject", "no", "missing X"))
            urls = [c.args[1] for c in rq.call_args_list]
            self.assertTrue(urls[0].endswith("/issues/1/comments")); self.assertTrue(urls[1].endswith("/statuses/abc123"))
            self.assertEqual(rq.call_args_list[1].kwargs["json"]["state"], "failure")
            self.assertNotIn("PUT", [c.args[0] for c in rq.call_args_list])
    def test_live_merge_is_sha_pinned(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request", return_value=resp(200)) as rq:
            executor.execute(PR, Decision("merge", "ok", "fb"))
            last = rq.call_args_list[-1]; self.assertEqual(last.args[0], "PUT"); self.assertEqual(last.kwargs["json"]["sha"], "abc123")
    def test_skip_never_calls_github(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request") as rq:
            executor.execute(PR, Decision("skip", "no ticket")); rq.assert_not_called()
    def test_http_error_does_not_crash(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request", side_effect=httpx.HTTPError("x")):
            d = executor.execute(PR, Decision("merge", "ok", "fb")); self.assertIn("Action failed", d.steps[-1])

class Web(Env):
    def test_parse_repo(self):
        self.assertEqual(main.parse_repo("white-wolf09", "https://github.com/white-wolf09/my-repo.git"), ("white-wolf09", "my-repo"))
        self.assertEqual(main.parse_repo("a", "b"), ("a", "b"))
        self.assertEqual(main.parse_repo("https://github.com/a/b/pull/4", "x"), ("a", "b"))
    def _bg(self): return MagicMock()
    def test_manual_review_with_url_in_repo_field(self):
        pr = {"title": "JIRA-1 x", "head": {"ref": "b", "sha": "s"}}
        bg = self._bg()
        with patch("httpx.get", return_value=resp(200, pr)) as g:
            out = main.manual_review({"owner": "o", "repo": "https://github.com/o/r", "number": "4"}, bg)
        self.assertTrue(g.call_args.args[0].endswith("/repos/o/r/pulls/4")); self.assertEqual(out, {"accepted": True}); bg.add_task.assert_called_once()
    def test_manual_review_404_message(self):
        with patch("httpx.get", return_value=resp(404)):
            with self.assertRaises(HTTPException) as c: main.manual_review({"owner": "o", "repo": "r", "number": 1}, self._bg())
        self.assertEqual(c.exception.status_code, 404); self.assertIn("o/r#1", c.exception.detail)
    def test_manual_review_missing_fields(self):
        with self.assertRaises(HTTPException): main.manual_review({"owner": "o"}, self._bg())
    def test_webhook_signature(self):
        payload = {"action": "opened", "repository": {"name": "r", "owner": {"login": "o"}},
                   "pull_request": {"number": 2, "title": "JIRA-1", "head": {"ref": "b", "sha": "s"}}}
        body = json.dumps(payload).encode()
        class Req:
            async def body(s): return body
            async def json(s): return payload
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "sek"}):
            good = "sha256=" + hmac.new(b"sek", body, hashlib.sha256).hexdigest()
            bg = self._bg()
            self.assertEqual(asyncio.run(main.webhook(Req(), bg, good, "pull_request")), {"accepted": True})
            with self.assertRaises(HTTPException) as c: asyncio.run(main.webhook(Req(), bg, "sha256=bad", "pull_request"))
            self.assertEqual(c.exception.status_code, 401)
            self.assertEqual(asyncio.run(main.webhook(Req(), bg, good, "issues")), {"ignored": "issues"})
    def test_process_records_event(self):
        main.EVENTS.clear()
        with patch.object(main, "decide", return_value=Decision("skip", "no ticket")):
            main.process(PR)
        self.assertEqual(main.EVENTS[0]["action"], "skip")

if __name__ == "__main__":
    unittest.main()
