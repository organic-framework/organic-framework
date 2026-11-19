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
import of_classify  # noqa: E402
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

    def test_review_two(self):
        """of_review on organic-framework PR 178, second round."""
        for cmd in ("python3 -c 'if 1: import boto3'", "python3 -c 'try: import boto3\nexcept Exception: pass'",
                    "python3 -c 'exec(\"import boto3\")'", "parallel -j 4 aws s3 ls ::: a", "parallel --jobs 4 aws s3 ls ::: a",
                    "python3 -X utf8 -m awscli s3 ls", "python3 -W ignore -m awscli s3 ls", "uv run aws s3 ls",
                    "uv run --with awscli aws s3 ls", "poetry run aws s3 ls", "direnv exec . aws s3 ls", "mise exec -- aws s3 ls",
                    "nix shell nixpkgs#awscli2 -c aws s3 ls", "nix-shell -p awscli2 --run 'aws s3 ls'",
                    "conda run -n tools aws s3 ls", "bundle exec terraform plan"):
            self.assertEqual(hit(cmd), "deny", cmd)
        for cmd in ("uv run pytest -q", "poetry run python x.py", "python3 -c 'import json, os'", "python3 -X utf8 -m pytest",
                    "parallel -j 4 echo ::: a", "mise exec -- node x.js"):
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


HELM = {"names": ["helm"],
        "match": [{"tool": "helm", "words": ["upgrade|rollback|uninstall|un|delete|del", "orders|billing-*"],
                   "options": {"-n|--namespace": ["prod", ""]}}],
        "allow": [{"positions": ["db_keeper"], "tool": "helm", "argv": ["rollback", "orders", "7"]}]}
HELM_RULE = {"id": "g-helm", "mode": "deny", "reason": "These releases own the primaries' Services.", "subject": "*",
             "except": [], "cli": HELM}


def helm_hit(cmd, position=None):
    h = of_guard.org_rule_hits("Bash", {"command": cmd}, [HELM_RULE], position=position)
    return h[0][0]["mode"] if h else None


class MatchListTest(unittest.TestCase):
    """O4 `match` (of_architect, msg 01a1e245-c3a5; a user organization's request, msg 01a1e243-1387): a deny on some
    calls of a tool, by its words and options, not on the tool."""

    def test_the_named_calls_deny(self):
        for cmd in ("helm upgrade orders ./chart -n prod", "helm upgrade --version 1.2 orders ./chart -n prod",
                    "helm -n prod rollback orders 3", "helm rollback orders 3 --namespace=prod",
                    "helm uninstall a orders -nprod", "helm un billing-eu --namespace prod",
                    "helm upgrade --install -f v.yaml orders ./chart -n prod", "sudo helm upgrade orders ./c -n prod",
                    "bash -c 'helm upgrade orders ./c -n prod'",
                    "helm upgrade orders ./chart",                       # no namespace: helm takes the context's
                    "HELM_NAMESPACE=prod helm upgrade orders ./chart",   # not given on the line: '' matches
                    "helm upgrade $R ./chart -n prod",                   # a variable may hold a listed release
                    "helm upgrade orders ./chart -n $NS",
                    "helm upgrade orders ./c -n pr*"):
            self.assertEqual(helm_hit(cmd), "deny", cmd)

    def test_a_subcommand_only_a_variable_fits_is_recorded_not_denied(self):
        """The replay of a user organization's helm calls: `helm get $R -n $NS` read $R as the subcommand. The first
        word an entry names must be literal; one a variable fits is soft, as a command word in a variable is."""
        for cmd in ("helm get $R -n $NS", "helm template $R $C -n $NS", "helm $CMD orders -n prod"):
            self.assertNotEqual(helm_hit(cmd), "deny", cmd)
        h = of_guard.commands_rule_match(HELM, of_guard.command_findings("helm $CMD orders -n prod"), None)
        self.assertEqual(h and h[1], True)
        self.assertIsNone(of_guard.commands_rule_match(HELM, of_guard.command_findings("$K get pods -n prod"), None))

    def test_input_from_xargs_or_find_and_a_value_in_a_cluster(self):
        """of_secrets-review on PR 237: the words xargs, parallel or find -exec {} add can be the listed ones, and pflag
        reads `-inprod` as -i, then -n prod."""
        strict = dict(HELM, match=[dict(HELM["match"][0], options={"-n|--namespace": ["prod"]})])
        rule = dict(HELM_RULE, cli=strict)

        def strict_hit(cmd):
            h = of_guard.org_rule_hits("Bash", {"command": cmd}, [rule])
            return h[0][0]["mode"] if h else None
        for cmd in ("helm list -q -n prod | grep billing | xargs -n1 helm uninstall -n prod",
                    "echo orders | xargs helm upgrade -n prod", "xargs -I% helm upgrade % ./c -n prod < list",
                    "find . -name orders -exec helm upgrade {} ./c -n prod \\;",
                    "printf 'orders\\n' | parallel helm uninstall -n prod", "parallel helm upgrade {} ./c -n prod ::: orders",
                    "helm upgrade -inprod orders ./c", "helm upgrade -in prod orders ./c", "helm upgrade -in=prod orders ./c",
                    "S=upgrade; helm $S orders -n prod", "S=upgrade && helm ${S} orders -n prod",
                    "C=upgrade; helm $C orders -n prod"):
            self.assertEqual(strict_hit(cmd), "deny", cmd)
        # only the subcommand is resolved: a release a loop, a read or a branch sets stays a hole (of_secrets-review)
        for cmd in ("R=payments; for R in orders; do helm upgrade $R ./c -n prod; done",
                    "R=payments; read R < f; helm upgrade $R ./c -n prod",
                    "if c; then R=orders; else R=payments; fi; helm upgrade $R ./c -n prod"):
            self.assertEqual(strict_hit(cmd), "deny", cmd)
        self.assertEqual(helm_hit("helm upgrade orders ./c -fingress.yaml"), "deny")   # a cluster guess, '' listed
        self.assertIsNone(strict_hit("echo x | xargs helm list -n prod"))
        self.assertIsNone(strict_hit("helm upgrade -instaging orders ./c"))

    def test_other_calls_of_the_tool_pass(self):
        for cmd in ("helm list -n prod", "helm status orders -n prod", "helm upgrade payments ./chart -n prod",
                    "helm upgrade orders ./chart -n staging", "helm template orders ./chart -n prod",
                    "helm diff upgrade-check payments -n prod", "helm repo update", "kubectl get svc -n prod"):
            self.assertIsNone(helm_hit(cmd), cmd)

    def test_an_allow_entry_stays_an_exception(self):
        self.assertIsNone(helm_hit("helm rollback orders 7 -n prod", position="db_keeper"))
        self.assertEqual(helm_hit("helm rollback orders 7 -n prod", position="ops_x"), "deny")
        self.assertEqual(helm_hit("helm rollback orders 8 -n prod", position="db_keeper"), "deny")

    def test_shape(self):
        g = cmd_grant(scope={"cli": HELM})
        self.assertEqual(of_grants.shape_problems(g), [])
        for bad, why in ((dict(HELM, env=["X"]), "carries no env or code"),
                         (dict(HELM, match=[{"tool": "kubectl", "words": ["x"]}]), "cli.match"),
                         (dict(HELM, match=[{"tool": "helm"}]), "cli.match"),
                         (dict(HELM, match=[{"options": {"namespace": ["prod"]}}]), "cli.match"),
                         (dict(HELM, match=[]), "cli.match")):
            self.assertTrue(any(why in p for p in of_grants.shape_problems(cmd_grant(scope={"cli": bad}))), (bad, why))


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


class CommentTest(unittest.TestCase):
    """G0.7: a `#` inside a word is no comment, as in bash."""

    def test_a_hash_inside_a_word(self):
        key = "~/.ssh/" + "id_" + "rsa"
        self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat a#b " + key}))
        self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat a\\ #b " + key}))   # an escaped blank
        self.assertEqual(of_guard._tokens("echo a\\ #b c"), ["echo", "a #b", "c"])
        # a backslash-newline joins the words (of_secrets-review on PR 178), in _tokens and in _segments
        self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat a\\\n#b " + key}))
        self.assertEqual(of_guard._tokens("echo a\\\n#b c"), ["echo", "a#b", "c"])
        self.assertEqual(of_guard._segments("echo a#b; cat c # d"), [["echo", "a#b"], ["cat", "c"]])
        self.assertTrue(of_guard.critical_deny("Bash", {"command": "echo x; cat a#b " + key}))
        # a # inside a word before a heredoc hid its interpreter from G8 (of_review on PR 178)
        body = "print(open('/Users/alex/.ssh/" + "id_" + "rsa').read())"
        self.assertTrue(of_guard.critical_deny("Bash", {"command": "echo a#b; python3 - <<'EOF'\n" + body + "\nEOF"}))
        self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat " + key + " # a note"}))
        self.assertIsNone(of_guard.critical_deny("Bash", {"command": "echo ok # cat " + key}))
        self.assertIsNone(of_guard.critical_deny("Bash", {"command": "echo '# cat' ok"}))
        self.assertEqual(of_guard._tokens("echo a#b c # d"), ["echo", "a#b", "c"])


class FixtureTokensTest(unittest.TestCase):
    """G1.2 (a user organization's security keeper through of_capacity, msg 01a1ef27-a8ad, 2026-11-18): a test fixture
    listing tokens refused every outside run on its repository."""

    def test_a_fixture_that_lists_tokens_is_no_credential(self):
        import of_classify
        for p in ("crates/tax_policy/tests/fixtures/crafted_tokens.txt", "tests/four_tokens.txt",
                  "pkg/testdata/token-prices.txt", "fixtures/bribe_tokens.txt"):
            self.assertFalse(of_classify.secret_path(p), p)
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": "cat " + p}, cwd="/Users/alex/repo"), p)
            self.assertIsNone(of_guard.critical_deny("Read", {"file_path": "/Users/alex/repo/" + p}), p)
        for p in ("tests/fixtures/token.txt", "tests/fixtures/gh-token.txt", "tests/api_token.txt",  # a credential's name
                  "work/crafted_tokens.txt", "scratch/tokens.txt", "token.txt", "notes/k8s-token.txt"):  # outside tests
            self.assertTrue(of_classify.secret_path(p), p)
            self.assertTrue(of_guard.critical_deny("Bash", {"command": "cat " + p}, cwd="/Users/alex/repo"), p)
            self.assertTrue(of_guard.critical_deny("Read", {"file_path": "/Users/alex/repo/" + p}), p)

    def test_the_native_rule_names_a_credential_by_its_last_part(self):
        import of_classify
        rules = of_classify.deny_rules()
        self.assertNotIn("Read(**/*token*.txt)", rules)
        for g in ("token.txt", "*-token.txt", "*_token.txt", "*.token.txt"):
            self.assertIn(f"Read(**/{g})", rules)


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
                    "az account get-access-token", "gcloud config config-helper --format=json",
                    "python3 -m awscli configure export-credentials"):
            self.assertTrue(of_guard.critical_deny("Bash", {"command": cmd}), cmd)


class HarnessLoginTest(unittest.TestCase):
    """G1.14 (of_secrets-review on organic-framework PR 328, through of_platform): the other harnesses' logins."""

    def test_the_logins_are_secret_paths(self):
        for p in ("~/.codex/auth.json", "/Users/alex/.codex/auth.json",
                  "~/.local/state/of/subagent/muse-login/xdg-config/muse/auth.json",
                  "~/.local/state/of/subagent/muse-login/home/Library/Keychains/login.keychain-db"):
            for cmd in ("cat ", "cp {} /tmp/x # ", "base64 "):
                c = cmd.format(p) if "{}" in cmd else cmd + p
                self.assertTrue(of_guard.critical_deny("Bash", {"command": c}), c)
            self.assertTrue(of_guard.critical_deny("Read", {"file_path": p.replace("~", "/Users/alex")}), p)
        for cmd in ("ls ~/.codex", "cat ~/.codex/config.toml", "ls ~/.local/state/of/subagent", "of subagent muse-login",
                    "of capacity status"):
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": cmd}), cmd)

    def test_each_part_of_a_path_is_read_without_its_padding(self):
        """of_review on organic-framework PR 387: a nested name that starts with a space or a quote was missed."""
        import of_classify
        for p in ("a/b/ credentials.json", 'a/b/"credentials.json"', "a/ b /.env"):
            self.assertTrue(of_classify.secret_path(p), p)
        self.assertFalse(of_classify.secret_path("a/b/ notes.txt"))

    def test_the_logins_are_in_the_native_rules(self):
        """of_secrets-review on organic-framework PR 387: the row says native rules, and none was written."""
        import of_classify
        rules = of_classify.deny_rules()
        for g in of_classify._HARNESS_LOGINS:
            self.assertIn(f"Read(**/{g})", rules)


class HarnessLoginShellFormsTest(unittest.TestCase):
    """G1.14's Bash forms and G1.2's note from PR 387's reviews (of_secrets-review's comment 7017458194, point 3 and its
    note; of_review's comment 7017518350): a login read after cd, names in another case, and the test-fixture exception
    by a name that says it holds a credential or a path outside the test folder."""

    def deny(self, cmd):
        return of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of")

    def test_a_login_after_cd_or_in_another_case(self):
        login = ".co" + "dex/auth.json"
        for cmd in ("cd ~/.codex && cat auth.json", "cat ~/" + login.upper(), "cat ~/.SSH/ID_ED25519"):
            self.assertTrue(self.deny(cmd), cmd)
        self.assertIsNone(self.deny("cd ~/.codex && ls"))

    def test_the_fixture_exception_is_narrow(self):
        t = "tok" + "en"
        for p in (f"tests/deploy_{t}_prod.txt", f"tests/github-{t}-live.txt", f"/Users/alex/fixtures/npm_{t}_live.txt",
                  f"tests/../api_{t}_prod.txt", f"tests/access_{t}s_live.txt", f"tests/api-{t}-prod.txt"):
            self.assertTrue(of_classify.secret_path(p), p)
        for p in (f"tests/fixtures/crafted_{t}s.txt", f"tests/{t}-prices.txt", f"repo/tests/fixtures/erc20_{t}s.txt"):
            self.assertFalse(of_classify.secret_path(p), p)


if __name__ == "__main__":
    unittest.main()
