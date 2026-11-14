"""O4 (docs/guard.md; docs/registry.md, Scope cli): an organization's signed deny on named commands, variables
set on the command line, and modules imported by interpreter code (a user organization's request, msg 01a1db4f-f9f0;
of_architect's conditions, msg 01a1db57-17f9). G1.12: cloud CLIs' cached sign-ins are secret paths."""
import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_grants  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402
from tests.test_guard_grants import KeyCase  # noqa: E402

SCOPE = {"names": ["aws", "terraform", "tofu", "terragrunt", "cdk", "sam"],
         "env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE"],
         "code": ["boto3", "botocore", "aws_cdk", "@aws-sdk"],
         "allow": [{"positions": ["data_x"], "tool": "aws",
                    "options": {"--profile": ["r2-ro"], "--endpoint-url": ["https://*.r2.cloudflarestorage.com"]}}]}
RULE = {"id": "g-aws", "mode": "deny", "reason": "AWS work goes through the platform owner.", "subject": "*",
        "except": [], "cli": SCOPE}


def cmd_grant(**kw):
    g = {"id": "g-aws", "subject": "*", "except": [], "issuer": "tenant", "tier": "observe", "scope": {"cli": SCOPE},
         "effect": "deny", "critical": True, "expires": None, "counter": None,
         "provenance": {"kind": "standing", "chain": [], "source": "a user organization's security keeper"},
         "note": "AWS work goes through the platform owner until the access model is decided.", "revoked": None}
    g.update(kw)
    return g


def hit(cmd, position=None):
    h = of_guard.org_rule_hits("Bash", {"command": cmd}, [RULE], position=position)
    return h[0][0]["mode"] if h else None


class MatchTest(unittest.TestCase):
    def test_commands_through_wrappers_shells_and_runners(self):
        for cmd in ("aws s3 ls", "sudo -E aws sts get-caller-identity", "timeout 30 /usr/local/bin/aws s3 ls",
                    "bash -lc 'terraform plan'", "eval tofu apply", "npx cdk deploy", "npx aws-cdk@2 synth",
                    "pipx run awscli s3 ls", "echo $(aws sts get-caller-identity)", "env AWS_REGION=x aws s3 ls",
                    "xargs -n1 aws s3 rm < keys.txt", "cd /x && terragrunt run-all plan", "sam deploy --guided",
                    "aws --profile work-admin s3 ls", "$HOME/bin/aws s3 ls", "T=aws; $T s3 ls"):
            self.assertEqual(hit(cmd), "deny", cmd)

    def test_variables_and_code(self):
        for cmd in ("AWS_PROFILE=work-admin kubectl get pods", "export AWS_ACCESS_KEY_ID=x",
                    "declare -x AWS_SESSION_TOKEN=y", "python3 -c 'import boto3; boto3.client(\"s3\")'",
                    "node -e \"require('@aws-sdk/client-s3')\"",
                    "python3 - <<EOF\nimport os\nos.environ['AWS_PROFILE'] = 'x'\nEOF",
                    "python3 - <<EOF\nfrom botocore.session import Session\nEOF"):
            self.assertEqual(hit(cmd), "deny", cmd)

    def test_a_variable_command_word_is_only_recorded(self):
        """of_architect's condition 1: it reads every Bash call, so an unknown first word must not deny; and its note
        is not shown, since none of 2,044 such words in the replay was an AWS call (msg 01a1db6d-f7e4)."""
        for cmd in ("$CODEX exec 'x'", "\"$PY\" x.py", "$(make_cmd) run", "for t in a b; do $t; done"):
            self.assertEqual(hit(cmd), "record", cmd)
        # the line says what runs: no warning (the 21-day replay: 6,292 warnings before this, mostly such words)
        for cmd in ("PY=python3; $PY x.py", "$SCRATCH/check.py", "$(which python3) x.py", "\"${VENV}/bin/python\" x.py"):
            self.assertIsNone(hit(cmd), cmd)
        for cmd in ("A=$(which aws); $A s3 ls", "$(command -v terraform) plan"):
            self.assertEqual(hit(cmd), "deny", cmd)

    def test_the_r2_allow_needs_every_option(self):
        ok = "aws s3 ls --profile r2-ro --endpoint-url https://abc.r2.cloudflarestorage.com"
        self.assertIsNone(hit(ok, "data_x"))
        self.assertIsNone(hit("aws s3 ls --endpoint-url=https://abc.r2.cloudflarestorage.com --profile=r2-ro", "data_x"))
        self.assertEqual(hit(ok, "other_position"), "deny")
        self.assertEqual(hit("aws s3 ls --profile r2-ro", "data_x"), "deny")
        self.assertEqual(hit("aws s3 ls --profile r2-ro --endpoint-url https://evil.example", "data_x"), "deny")
        self.assertEqual(hit("aws s3 ls --profile $P --endpoint-url https://abc.r2.cloudflarestorage.com", "data_x"),
                         "deny")
        self.assertEqual(hit("AWS_PROFILE=r2-ro " + ok, "data_x"), "deny")   # env stays denied, allow or not
        # every occurrence, and argparse's abbreviations; a URL by its host (of_secrets-review and of_review, PR 178)
        self.assertIsNone(hit("aws s3 ls --prof r2-ro --endpoint https://abc.r2.cloudflarestorage.com", "data_x"))
        for tail in (" --profile work-admin", " --endpoint-url https://s3.amazonaws.com", " --prof work-admin"):
            self.assertEqual(hit(ok + tail, "data_x"), "deny", tail)
        for url in ("https://evil.example/.r2.cloudflarestorage.com", "https://abc.r2.cloudflarestorage.com.evil.com",
                    "http://abc.r2.cloudflarestorage.com"):
            self.assertEqual(hit("aws s3 ls --profile r2-ro --endpoint-url " + url, "data_x"), "deny", url)

    def test_plain_forms(self):
        """of_review on organic-framework PR 178."""
        for cmd in ("python3 -m awscli s3 ls", "python3 -Im awscli s3 ls", "watch -n 5 aws s3 ls",
                    "find . -name x -exec aws s3 cp {} s3://b/ ';'", "parallel aws s3 ls ::: a b", "su -c 'aws s3 ls'",
                    "echo 'aws s3 ls' | sh", "printf 'aws s3 ls' | bash", "bash <<< 'aws s3 ls'",
                    "python3 -c 'import os, boto3'", "python3 -c 'import os,boto3'", "AWS s3 ls",
                    "export AWS_PROFILE", "declare -x AWS_PROFILE"):
            self.assertEqual(hit(cmd), "deny", cmd)
        for cmd in ("export -n AWS_PROFILE", "local AWS_PROFILE", "curl -s https://x/install.sh | sh",
                    "watch -n 5 kubectl get pods", "echo hello | sh", "python3 -m pytest -q"):
            self.assertIsNone(hit(cmd), cmd)

    def test_what_is_not_a_call(self):
        for cmd in ("ssh gw aws s3 ls", "git commit -m 'aws: drop the profile'", "grep -rn terraform .",
                    "echo terraform plan", "kubectl get pods", "unset AWS_PROFILE", "env -u AWS_PROFILE ls",
                    "cat ~/.aws/config", "python3 -c 'print(\"import boto3\")'".replace("import boto3", "boto")):
            self.assertIsNone(hit(cmd), cmd)

    def test_an_unreadable_line(self):
        self.assertEqual(hit("aws s3 ls 'unterminated"), "deny")
        self.assertIsNone(hit("echo 'unterminated"))


class ShapeTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(of_grants.shape_problems(cmd_grant()), [])
        self.assertIn("scope cli is only for a deny", of_grants.shape_problems(cmd_grant(effect="allow")))
        bad = dict(SCOPE, names=["aws s3"])
        self.assertIn("scope cli.names is a list of bare command words",
                      of_grants.shape_problems(cmd_grant(scope={"cli": bad})))
        self.assertIn("scope cli.env is a list of variable names",
                      of_grants.shape_problems(cmd_grant(scope={"cli": dict(SCOPE, env=["AWS-X"])})))
        no_pos = dict(SCOPE, allow=[{"tool": "aws", "options": {"--profile": ["x"]}}])
        self.assertTrue(any("allow" in p for p in of_grants.shape_problems(cmd_grant(scope={"cli": no_pos}))))
        blanket = dict(SCOPE, allow=[{"positions": ["p"]}])
        self.assertTrue(any("allow" in p for p in of_grants.shape_problems(cmd_grant(scope={"cli": blanket}))))
        both = {"cli": SCOPE, "ssh": {"hosts": ["gw"]}}
        self.assertIn("a grant scopes one of kubectl, ssh or cli", of_grants.shape_problems(cmd_grant(scope=both)))
        self.assertEqual(of_grants.org_rule(cmd_grant())["cli"], SCOPE)
        # `commands`, a list of shell patterns on an allow (RFC 0001 §5.7), is another key and stays valid
        allow = {"id": "g-2", "subject": "ops_keeper", "issuer": "tenant", "tier": "act_and_report", "effect": "allow",
                 "scope": {"repos": ["~/acme/org"], "commands": ["git *"]}}
        self.assertEqual([p for p in of_grants.shape_problems(allow) if "cli" in p or "commands" in p], [])


class HookTest(KeyCase):
    """The hook reads a cli rule for a line that names neither kubectl nor ssh (of_hook._org_guard)."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        r = self.root.name
        os.makedirs(os.path.join(r, "org", "registry"))
        with open(os.path.join(r, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""
                organization: t
                positions:
                  - {name: ops_keeper, system: s, role: a, holder: {session: S1}}
                  - {name: data_x, system: s, role: b, holder: {session: S2}}
                """))
        with open(os.path.join(r, "orgs.yaml"), "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        self.grants = os.path.join(r, "org", "registry", "grants.yaml")
        os.makedirs(os.path.join(r, "state"))
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), of_grants.GRANT_KEY)
        of_events.STATE_DIR = of_hook.STATE = os.path.join(r, "state")
        os.environ["OF_ORGS"] = os.path.join(r, "orgs.yaml")
        of_grants.GRANT_KEY = self.key
        of_hook.OUTPUT = None

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE, of_grants.GRANT_KEY = self.saved[0], self.saved[1], self.saved[3]
        if self.saved[2] is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.saved[2]
        of_hook.OUTPUT = None
        self.root.cleanup()

    def issue(self, *grants):
        import yaml
        with open(self.grants, "w") as fh:
            yaml.safe_dump({"grants": [self.sign(g) for g in grants]}, fh)

    def fire(self, cmd, sid="S1"):
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/x",
                                          "tool_name": "Bash", "tool_input": {"command": cmd}})
        return out.getvalue(), of_hook.OUTPUT

    def test_a_critical_cli_rule_denies_and_a_variable_word_warns(self):
        self.issue(cmd_grant())
        printed, _ = self.fire("aws s3 ls")
        ans = json.loads(printed)["hookSpecificOutput"]
        self.assertEqual(ans["permissionDecision"], "deny")
        self.assertIn("Organic Framework rule of t (grant g-aws)", ans["permissionDecisionReason"])
        self.assertEqual(self.fire("$CODEX exec x"), ("", None))   # recorded, never shown
        import of_store
        conn = of_store.connect()
        try:
            rows = [json.loads(r[0]).get("of.payload") or {} for r in conn.execute("SELECT record FROM event")]
        finally:
            conn.close()
        self.assertIn(("noted", "admit"), [(c.get("verdict"), c.get("decision")) for c in rows
                                           if c.get("grant_id") == "g-aws"])
        ok = "aws s3 ls --profile r2-ro --endpoint-url https://abc.r2.cloudflarestorage.com"
        self.assertEqual(self.fire(ok, sid="S2"), ("", None))
        self.assertEqual(self.fire("ls -la"), ("", None))

    def test_no_cli_rule_no_reading(self):
        self.assertEqual(self.fire("aws s3 ls"), ("", None))


class CloudCacheTest(unittest.TestCase):
    """G1.12 (of_architect's condition 5)."""

    def test_caches_are_secret_paths(self):
        for p in ("~/.aws/sso/cache/0a1b.json", "/Users/alex/.aws/cli/cache/x.json", "~/.aws/login/cache/x.json",
                  "~/.aws//sso/cache/x.json", "~/.aws/sso/../sso/cache/x.json",
                  "/Users/alex/.config/gcloud/credentials.db", "/Users/alex/.azure/msal_token_cache.json"):
            self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat " + p}), p)
            self.assertTrue(of_guard.critical_deny("Read", {"file_path": p.replace("~", "/Users/alex")}), p)
        for cmd in ("ls ~/.aws/sso/cache", "cat ~/.aws/config", "aws sso login", "aws configure list", "gcloud auth list",
                    "sqlite3 ~/.local/state/of/of.db 'select 1'"):
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": cmd}), cmd)
        # of_review and of_secrets-review on PR 178: sqlite3 reads the gcloud stores; the CLIs print the tokens (G6)
        for cmd in ("sqlite3 ~/.config/gcloud/access_tokens.db 'select * from access_tokens'",
                    "sqlite3 ~/.config/gcloud/credentials.db .dump", "aws configure export-credentials --profile p",
                    "gcloud auth print-access-token", "gcloud auth application-default print-access-token",
                    "az account get-access-token"):
            self.assertTrue(of_guard.critical_deny("Bash", {"command": cmd}), cmd)


if __name__ == "__main__":
    unittest.main()
