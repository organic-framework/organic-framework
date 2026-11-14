"""O4 (docs/guard.md; docs/registry.md, Scope commands): an organization's signed deny on named commands, variables
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
        "except": [], "commands": SCOPE}


def cmd_grant(**kw):
    g = {"id": "g-aws", "subject": "*", "except": [], "issuer": "tenant", "tier": "observe", "scope": {"commands": SCOPE},
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

    def test_a_variable_command_word_only_warns(self):
        """of_architect's condition 1: it reads every Bash call, so an unknown first word must not deny."""
        for cmd in ("$CODEX exec 'x'", "\"$PY\" x.py", "$(make_cmd) run", "for t in a b; do $t; done"):
            self.assertEqual(hit(cmd), "warn", cmd)
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
        self.assertIn("scope commands is only for a deny", of_grants.shape_problems(cmd_grant(effect="allow")))
        bad = dict(SCOPE, names=["aws s3"])
        self.assertIn("scope commands.names is a list of bare command words",
                      of_grants.shape_problems(cmd_grant(scope={"commands": bad})))
        self.assertIn("scope commands.env is a list of variable names",
                      of_grants.shape_problems(cmd_grant(scope={"commands": dict(SCOPE, env=["AWS-X"])})))
        no_pos = dict(SCOPE, allow=[{"tool": "aws", "options": {"--profile": ["x"]}}])
        self.assertTrue(any("allow" in p for p in of_grants.shape_problems(cmd_grant(scope={"commands": no_pos}))))
        blanket = dict(SCOPE, allow=[{"positions": ["p"]}])
        self.assertTrue(any("allow" in p for p in of_grants.shape_problems(cmd_grant(scope={"commands": blanket}))))
        both = {"commands": SCOPE, "ssh": {"hosts": ["gw"]}}
        self.assertIn("a grant scopes one of kubectl, ssh or commands", of_grants.shape_problems(cmd_grant(scope=both)))
        self.assertEqual(of_grants.org_rule(cmd_grant())["commands"], SCOPE)


class HookTest(KeyCase):
    """The hook reads a commands rule for a line that names neither kubectl nor ssh (of_hook._org_guard)."""

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

    def test_a_critical_commands_rule_denies_and_a_variable_word_warns(self):
        self.issue(cmd_grant())
        printed, _ = self.fire("aws s3 ls")
        ans = json.loads(printed)["hookSpecificOutput"]
        self.assertEqual(ans["permissionDecision"], "deny")
        self.assertIn("Organic Framework rule of t (grant g-aws)", ans["permissionDecisionReason"])
        printed, output = self.fire("$CODEX exec x")
        self.assertEqual(printed, "")
        self.assertIn("a warning now", output["hookSpecificOutput"]["additionalContext"])
        ok = "aws s3 ls --profile r2-ro --endpoint-url https://abc.r2.cloudflarestorage.com"
        self.assertEqual(self.fire(ok, sid="S2"), ("", None))
        self.assertEqual(self.fire("ls -la"), ("", None))

    def test_no_commands_rule_no_reading(self):
        self.assertEqual(self.fire("aws s3 ls"), ("", None))


class CloudCacheTest(unittest.TestCase):
    """G1.12 (of_architect's condition 5)."""

    def test_caches_are_secret_paths(self):
        for p in ("~/.aws/sso/cache/0a1b.json", "/Users/alex/.aws/cli/cache/x.json",
                  "/Users/alex/.config/gcloud/credentials.db", "/Users/alex/.azure/msal_token_cache.json"):
            self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat " + p}), p)
            self.assertTrue(of_guard.critical_deny("Read", {"file_path": p.replace("~", "/Users/alex")}), p)
        for cmd in ("ls ~/.aws/sso/cache", "cat ~/.aws/config", "aws sso login"):
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": cmd}), cmd)


if __name__ == "__main__":
    unittest.main()
