import os, sys, tempfile, unittest, json, hashlib, hmac, asyncio
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import httpx
from fastapi import HTTPException
from pr_reviewer import main, tunnel, config
from pr_reviewer.models import CommitContext, Story, Decision
from pr_reviewer.decision import decide_commit
from pr_reviewer.executor import execute_commit
from pr_reviewer.scanner import scan_diff
from pr_reviewer.evaluator import call_llm, VerdictError
from pr_reviewer.story_fetcher import StoryError
from pr_reviewer.code_fetcher import fetch_compare_diff, DiffError

CTX = CommitContext("o", "r", "feature/login", "main", "abc123", "add login")
CLEAN = "diff --git a/x b/x\n+print('hi')"
diff = lambda *a: CLEAN
ok = lambda c, d: '{"fulfilled": true, "feedback": "clean"}'
bad = lambda c, d: '{"fulfilled": false, "feedback": "SQL injection in login"}'
resp = lambda code, js=None, text="": MagicMock(status_code=code, json=lambda: js, text=text)

class Base(unittest.TestCase):
    def setUp(self):
        self.p = patch.dict(os.environ, {"DRY_RUN": "true", "GITHUB_TOKEN": "t", "REQUIRE_TICKET": "false"}); self.p.start()
    def tearDown(self): self.p.stop()

class CommitGate(Base):
    def test_clean_commit_merges(self):
        d = decide_commit(CTX, diff_fn=diff, llm_fn=ok); self.assertEqual(d.action, "merge")
    def test_flagged_commit_rejected(self):
        self.assertEqual(decide_commit(CTX, diff_fn=diff, llm_fn=bad).action, "reject")
    def test_default_branch_never_merged(self):
        c = CommitContext("o", "r", "main", "main", "abc", "x")
        self.assertEqual(decide_commit(c, diff_fn=diff, llm_fn=ok).action, "skip")
    def test_deleted_branch(self):
        c = CommitContext("o", "r", "f", "main", "0" * 40, "x")
        self.assertEqual(decide_commit(c, diff_fn=diff, llm_fn=ok).action, "skip")
    def test_secret_blocks_and_llm_not_called(self):
        leaked = lambda *a: "+TOKEN = 'ghp_" + "a" * 30 + "'"
        llm = MagicMock()
        d = decide_commit(CTX, diff_fn=leaked, llm_fn=llm)
        self.assertEqual(d.action, "reject"); llm.assert_not_called()
        self.assertNotIn("aaaaaaaa", d.feedback)
    def test_empty_and_large_diff(self):
        self.assertEqual(decide_commit(CTX, diff_fn=lambda *a: " ", llm_fn=ok).action, "skip")
        with patch.dict(os.environ, {"MAX_DIFF_CHARS": "5"}):
            self.assertIn("too large", decide_commit(CTX, diff_fn=diff, llm_fn=ok).reason)
    def test_diff_error(self):
        def boom(*a): raise DiffError("x")
        self.assertEqual(decide_commit(CTX, diff_fn=boom, llm_fn=ok).action, "skip")
    def test_bad_llm_output_never_merges(self):
        for b in ["nope", "{}", '{"fulfilled": "true", "feedback": ""}']:
            with self.subTest(b=b):
                self.assertEqual(decide_commit(CTX, diff_fn=diff, llm_fn=lambda c, d: b).action, "skip")
    def test_referenced_ticket_unreadable_skips(self):
        c = CommitContext("o", "r", "feature/KAN-5-x", "main", "abc", "m")
        def boom(k): raise StoryError("404")
        self.assertEqual(decide_commit(c, story_fn=boom, diff_fn=diff, llm_fn=ok).action, "skip")
    def test_ticket_without_criteria_skips(self):
        c = CommitContext("o", "r", "feature/KAN-5-x", "main", "abc", "m")
        self.assertEqual(decide_commit(c, story_fn=lambda k: Story(k, "t", ""), diff_fn=diff, llm_fn=ok).action, "skip")
    def test_ticket_criteria_passed_to_llm(self):
        c = CommitContext("o", "r", "feature/KAN-5-x", "main", "abc", "m"); seen = {}
        def llm(crit, d): seen["c"] = crit; return '{"fulfilled": true, "feedback": "ok"}'
        decide_commit(c, story_fn=lambda k: Story(k, "t", "must log in"), diff_fn=diff, llm_fn=llm)
        self.assertEqual(seen["c"], "must log in")
    def test_require_ticket(self):
        with patch.dict(os.environ, {"REQUIRE_TICKET": "true"}):
            self.assertEqual(decide_commit(CTX, diff_fn=diff, llm_fn=ok).action, "skip")

class Scanner(unittest.TestCase):
    def test_finds_secrets_in_added_lines_only(self):
        d = "-old = 'ghp_" + "b" * 30 + "'\n+k = 'gsk_" + "c" * 30 + "'\n+AKIA" + "A" * 16
        f = scan_diff(d); self.assertEqual(len(f), 2); self.assertTrue(all("ccc" not in x for x in f))
    def test_clean(self): self.assertEqual(scan_diff(CLEAN), [])
    def test_private_key(self): self.assertTrue(scan_diff("+-----BEGIN RSA PRIVATE KEY-----"))

class ExecCommit(Base):
    def test_dry_run(self):
        with patch("httpx.request") as rq:
            d = execute_commit(CTX, Decision("merge", "ok", "fb")); rq.assert_not_called()
            self.assertTrue(all("[dry-run]" in s for s in d.steps))
    def test_live_merge_pinned(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request", return_value=resp(201)) as rq:
            execute_commit(CTX, Decision("merge", "ok", "fb")); last = rq.call_args_list[-1]
            self.assertTrue(last.args[1].endswith("/merges")); self.assertEqual(last.kwargs["json"]["head"], "abc123")
            self.assertEqual(last.kwargs["json"]["base"], "main")
    def test_live_reject_never_merges(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request", return_value=resp(201)) as rq:
            execute_commit(CTX, Decision("reject", "flags", "bug"))
            urls = [c.args[1] for c in rq.call_args_list]
            self.assertFalse(any(u.endswith("/merges") for u in urls))
            self.assertEqual(rq.call_args_list[1].kwargs["json"]["state"], "failure")
    def test_skip_no_calls(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request") as rq:
            execute_commit(CTX, Decision("skip", "x")); rq.assert_not_called()
    def test_conflict_does_not_crash(self):
        with patch.dict(os.environ, {"DRY_RUN": "false"}), patch("httpx.request", side_effect=httpx.HTTPError("409")):
            self.assertIn("Action failed", execute_commit(CTX, Decision("merge", "ok", "f")).steps[-1])

class PushWeb(Base):
    PAYLOAD = {"ref": "refs/heads/feature/login", "after": "abc123", "head_commit": {"message": "add login\n\nbody"},
               "repository": {"name": "r", "default_branch": "main", "owner": {"name": "o", "login": "o"}}}
    def test_from_push(self):
        c = main.from_push(self.PAYLOAD)
        self.assertEqual((c.owner, c.branch, c.base, c.sha), ("o", "feature/login", "main", "abc123"))
    def test_tags_and_deletes_ignored(self):
        self.assertIsNone(main.from_push({**self.PAYLOAD, "ref": "refs/tags/v1"}))
        self.assertIsNone(main.from_push({**self.PAYLOAD, "deleted": True}))
    def test_push_webhook_accepted_and_bad_signature(self):
        body = json.dumps(self.PAYLOAD).encode()
        class Req:
            async def body(s): return body
            async def json(s): return PushWeb.PAYLOAD
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "sek"}):
            sig = "sha256=" + hmac.new(b"sek", body, hashlib.sha256).hexdigest(); bg = MagicMock()
            self.assertEqual(asyncio.run(main.webhook(Req(), bg, sig, "push")), {"accepted": True})
            bg.add_task.assert_called_once()
            with self.assertRaises(HTTPException): asyncio.run(main.webhook(Req(), bg, "sha256=x", "push"))
    def test_process_commit_records_event(self):
        main.EVENTS.clear()
        with patch.object(main, "decide_commit", return_value=Decision("reject", "flags", "f")):
            main.process_commit(CTX)
        self.assertEqual(main.EVENTS[0]["pr"], "o/r@feature/login"); self.assertEqual(main.EVENTS[0]["title"], "add login")
    def test_manual_commit_review(self):
        gets = [resp(200, {"default_branch": "dev"}), resp(200, {"commit": {"sha": "s1", "commit": {"message": "m"}}})]
        bg = MagicMock()
        with patch("httpx.get", side_effect=gets):
            main.manual_review_commit({"owner": "o", "repo": "https://github.com/o/r", "branch": "f"}, bg)
        ctx = bg.add_task.call_args.args[1]; self.assertEqual((ctx.base, ctx.sha), ("dev", "s1"))
    def test_manual_commit_review_404(self):
        with patch("httpx.get", side_effect=[resp(200, {}), resp(404)]):
            with self.assertRaises(HTTPException) as c:
                main.manual_review_commit({"owner": "o", "repo": "r", "branch": "nope"}, MagicMock())
        self.assertIn("branch nope", c.exception.detail)
    def test_compare_diff_url(self):
        with patch("httpx.get", return_value=resp(200, text="D")) as g:
            fetch_compare_diff("o", "r", "main", "abc"); self.assertTrue(g.call_args.args[0].endswith("/compare/main...abc"))

class LLM(Base):
    def test_groq(self):
        r = resp(200, {"choices": [{"message": {"content": '{"fulfilled": true, "feedback": "x"}'}}]}); r.raise_for_status = lambda: None
        with patch.dict(os.environ, {"LLM_PROVIDER": "groq", "LLM_API_KEY": "k"}), patch("httpx.post", return_value=r) as p:
            self.assertIn("fulfilled", call_llm("", "diff")); self.assertIn("groq.com", p.call_args.args[0])
            self.assertIn("NO blocking flags", p.call_args.kwargs["json"]["messages"][0]["content"])
    def test_anthropic(self):
        r = resp(200, {"content": [{"text": "hello"}]}); r.raise_for_status = lambda: None
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}), patch("httpx.post", return_value=r):
            self.assertEqual(call_llm("crit", "d"), "hello")
    def test_failure_is_verdict_error(self):
        with patch("httpx.post", side_effect=httpx.HTTPError("x")):
            with self.assertRaises(VerdictError): call_llm("c", "d")
    def test_malformed_response(self):
        r = resp(200, {"weird": 1}); r.raise_for_status = lambda: None
        with patch.dict(os.environ, {"LLM_PROVIDER": "groq"}), patch("httpx.post", return_value=r):
            with self.assertRaises(VerdictError): call_llm("c", "d")

class EnvAndTunnel(Base):
    def test_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, ".env")
            with open(f, "w", encoding="utf-8-sig") as fh:
                fh.write('# c\nZZ_A=one   # note\nZZ_B="two"\nZZ_EMPTY=\nZZ_KEEP=new\n')
            with patch.dict(os.environ, {"ZZ_KEEP": "old"}):
                config.load_dotenv(f)
                self.assertEqual((os.environ["ZZ_A"], os.environ["ZZ_B"], os.environ["ZZ_KEEP"]), ("one", "two", "old"))
                self.assertNotIn("ZZ_EMPTY", os.environ)
            for k in ("ZZ_A", "ZZ_B"): os.environ.pop(k, None)
    def test_public_url(self):
        r = resp(200, {"tunnels": [{"public_url": "http://x"}, {"public_url": "https://abc.ngrok.app"}]})
        with patch("httpx.get", return_value=r): self.assertEqual(tunnel.get_public_url(), "https://abc.ngrok.app")
    def test_ngrok_not_running(self):
        with patch("httpx.get", side_effect=httpx.HTTPError("x")):
            with self.assertRaises(tunnel.TunnelError): tunnel.get_public_url()
    def test_register_webhook(self):
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "s"}), patch("httpx.post", return_value=resp(201)) as p:
            self.assertIn("created", tunnel.register_webhook("o/r", "https://abc.ngrok.app/"))
            body = p.call_args.kwargs["json"]; self.assertEqual(body["config"]["url"], "https://abc.ngrok.app/webhook")
            self.assertEqual(body["events"], ["push", "pull_request"])
    def test_register_needs_secret(self):
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": ""}):
            with self.assertRaises(tunnel.TunnelError): tunnel.register_webhook("o/r", "https://a")
    def test_register_forbidden(self):
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "s"}), patch("httpx.post", return_value=resp(404)):
            with self.assertRaises(tunnel.TunnelError): tunnel.register_webhook("o/r", "https://a")

if __name__ == "__main__":
    unittest.main()
