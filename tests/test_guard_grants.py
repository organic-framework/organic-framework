"""An organization's rules are signed deny grants (docs/registry.md, Organization rules; of_architect 4a14716;
docs/guard.md O2, G13). A non-critical deny warns, a critical one denies; `subject: "*"` with a signed `except`;
a verified deny ends only by a signed revocation; only the tenant issues or revokes grants."""

import contextlib
import io
import json
import os
import subprocess
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

NOW = "2026-11-09T00:00:00Z"
RUNNER = {"verbs": ["exec", "cp"], "namespaces": ["runners-jobs"]}


def rule_grant(gid="g-1", critical=False, **kw):
    g = {"id": gid, "subject": "*", "except": ["runners_keeper"], "issuer": "tenant", "tier": "observe",
         "scope": {"kubectl": RUNNER}, "effect": "deny", "critical": critical, "expires": None, "counter": None,
         "provenance": {"kind": "standing", "chain": [], "source": "founder, ask ac3b43dc"},
         "note": "Runner work goes only through the runner gate.", "revoked": None}
    g.update(kw)
    return g


class KeyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key = os.path.join(cls.tmp.name, "grant_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", cls.key], check=True,
                       env=of_grants.no_agent_env())
        with open(cls.key + ".pub") as fh:
            cls.keys = of_grants.Keys(ed25519=[fh.read().strip()])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sign(self, g):
        g = dict(g)
        g["signature"] = of_grants.sign_ed25519(g, self.key)
        return g


class ShapeTest(unittest.TestCase):
    def test_subject_set_and_kubectl_scope(self):
        self.assertEqual(of_grants.shape_problems(rule_grant()), [])
        self.assertIn('subject "*" is only for a deny', of_grants.shape_problems(rule_grant(effect="allow")))
        self.assertIn('subject "*" is issued by the tenant only',
                      of_grants.shape_problems(rule_grant(issuer="acme_org")))
        self.assertIn('except is only for subject "*"', of_grants.shape_problems(rule_grant(subject="of_a")))
        bad = of_grants.shape_problems(rule_grant(scope={"kubectl": {"verbs": ["exec"], "pod": ["x"]}}))
        self.assertTrue(any("scope kubectl takes only" in p for p in bad), bad)
        self.assertTrue(of_grants.shape_problems(rule_grant(scope={"kubectl": {"admin": {"configs": "~/.kube/config"}}})))
        self.assertTrue(of_grants.shape_problems(rule_grant(scope={"tools": ["Bash"]})))  # nothing the guard applies

    def test_who_a_grant_applies_to(self):
        g = rule_grant()
        self.assertTrue(of_grants.applies_to(g, "returns_keeper"))
        self.assertFalse(of_grants.applies_to(g, "runners_keeper"))
        self.assertTrue(of_grants.applies_to(g, None))  # a session without a position gets no exception
        self.assertTrue(of_grants.applies_to(rule_grant(subject="of_a", **{"except": None}), "of_a"))
        self.assertFalse(of_grants.applies_to(rule_grant(subject="of_a", **{"except": None}), "of_b"))

    def test_the_rule_the_guard_applies(self):
        r = of_grants.org_rule(rule_grant(critical=True))
        self.assertEqual((r["mode"], r["reason"], r["except"]),
                         ("deny", "Runner work goes only through the runner gate.", ["runners_keeper"]))
        self.assertEqual(of_grants.org_rule(rule_grant())["mode"], "warn")
        hit = of_guard.org_rule_hits("Bash", {"command": "kubectl -n runners-jobs exec $P -- sh"}, [r], "x")
        self.assertEqual([x["id"] for x, _ in hit], ["g-1"])
        one = of_grants.org_rule(rule_grant(subject="of_a", **{"except": None}))
        self.assertEqual(of_guard.org_rule_hits("Bash", {"command": "kubectl -n runners-jobs exec p"}, [one], "of_b"),
                         [])

    def test_missing_issued(self):
        events = [("of.grant_issued", "t", {"grant_id": "g-1"}), ("of.grant_issued", "t", {"grant_id": "g-2"}),
                  ("of.grant_issued", "t", {"grant_id": "g-3"}), ("of.grant_revoked", "t", {"grant_id": "g-3"})]
        self.assertEqual(of_grants.missing_issued({"t": [{"id": "g-1"}]}, events), [("t", "g-2")])


class KeptTest(KeyCase):
    def test_a_deny_ends_only_by_a_signed_revocation(self):
        g = self.sign(rule_grant())
        kept, notes = of_grants.fold_kept({}, [g], self.keys, NOW)
        self.assertEqual((list(kept), notes), (["g-1"], []))
        kept2, notes = of_grants.fold_kept(kept, [], self.keys, NOW)  # deleted by hand
        self.assertEqual(list(kept2), ["g-1"])
        self.assertIn("missing from the file", notes[0])
        widened = dict(g, **{"except": ["runners_keeper", "returns_keeper"]})  # an exception added by hand
        kept3, notes = of_grants.fold_kept(kept, [widened], self.keys, NOW)
        self.assertEqual(kept3["g-1"]["except"], ["runners_keeper"])
        self.assertIn("does not verify", notes[0])
        revoked = self.sign(dict(g, revoked={"at": NOW, "by": "tenant", "reason": "moved to deny"}))
        self.assertEqual(of_grants.fold_kept(kept, [revoked], self.keys, NOW), ({}, []))
        forged = dict(g, revoked={"at": NOW, "by": "tenant", "reason": "x"})  # unsigned revocation
        self.assertEqual(list(of_grants.fold_kept(kept, [forged], self.keys, NOW)[0]), ["g-1"])
        old = self.sign(rule_grant(expires="2026-11-08T00:00:00Z"))
        self.assertEqual(of_grants.fold_kept({"g-1": old}, [old], self.keys, NOW)[0], {})

    def test_an_unsigned_or_malformed_deny_is_never_a_rule(self):
        self.assertEqual(of_grants.fold_kept({}, [rule_grant()], self.keys, NOW)[0], {})
        bad = self.sign(rule_grant(scope={"kubectl": {"pod": ["x"]}}))
        self.assertEqual(of_grants.fold_kept({}, [bad], self.keys, NOW)[0], {})

    def test_check_all_reports_the_shape(self):
        bad = self.sign(rule_grant(effect="allow"))
        (org, gid, ok, why), = of_grants.check_all({"t": [bad]}, self.keys, NOW)
        self.assertFalse(ok)
        self.assertIn('only for a deny', why)


class GuardRulesTest(KeyCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        r = self.root.name
        os.makedirs(os.path.join(r, "org", "registry"))
        os.makedirs(os.path.join(r, "state"))
        self.orgs = os.path.join(r, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        self.file = os.path.join(r, "org", "registry", "grants.yaml")
        self.state = os.path.join(r, "state")

    def tearDown(self):
        self.root.cleanup()

    def write(self, grants, mtime):
        import yaml
        with open(self.file, "w") as fh:
            yaml.safe_dump({"grants": grants}, fh)
        os.utime(self.file, (mtime, mtime))

    def rules(self):
        return of_grants.guard_rules("t", self.state, self.orgs, keys=self.keys, now_iso=NOW)

    def test_read_cached_and_kept(self):
        g = self.sign(rule_grant())
        self.write([g], 1000)
        rules, notes = self.rules()
        self.assertEqual(([r["id"] for r in rules], notes), (["g-1"], []))
        self.assertEqual(oct(os.stat(os.path.join(self.state, of_grants.GUARD_STATE)).st_mode & 0o777), "0o600")
        self.write([], 2000)  # the entry deleted by hand
        rules, notes = self.rules()
        self.assertEqual([r["id"] for r in rules], ["g-1"])
        self.assertIn("missing from the file", notes[0])
        with open(self.file, "w") as fh:
            fh.write("grants: [unclosed\n")
        os.utime(self.file, (3000, 3000))
        rules, notes = self.rules()
        self.assertEqual([r["id"] for r in rules], ["g-1"])
        self.assertIn("could not be read", notes[0])
        self.assertEqual(of_grants.guard_rules("other", self.state, self.orgs, keys=self.keys, now_iso=NOW), ([], []))


class GrantGuardTest(unittest.TestCase):
    """G13: grants are the tenant's; an agent session cannot issue, revoke, sign or rewrite them."""

    def test_denied(self):
        for cmd in ("of grant issue --org acme --subject '*' --deny --tier observe",
                    "of grant revoke --org acme --id g-2026-11-09-015 --note moved",
                    "~/organic-framework/bin/of grant revoke --org t --id g-1",
                    "python3 bin/of grant issue --org t --subject x --tier observe",
                    "of passkey enroll founder", "python3 bin/of passkey revoke founder cred-1",
                    'bash -c "of grant revoke --org t --id g-1"',
                    "ssh-keygen -Y sign -f ~/.config/of/grant_ed25519 -n of-grant /tmp/g",
                    "python3 -c 'import of_grants; of_grants.sign_ed25519(g)'",
                    "echo '- id: x' >> ~/acme/org/registry/grants.yaml",
                    "tee registry/grants.yaml < /tmp/g",
                    "sed -i '' '/g-2026-11-09-015/,+20d' ~/acme/org/registry/grants.yaml",
                    "cp /tmp/g.yaml ~/acme/org/registry/grants.yaml",
                    "rm ~/acme/org/registry/grants.yaml",
                    "git checkout -- registry/grants.yaml",
                    "rm ~/.local/state/of/guard-grants.json"):
            hit = of_guard.critical_deny("Bash", {"command": cmd})
            self.assertTrue(hit and hit[0] == of_guard.GRANT_DENY, cmd)
        for tool in ("Edit", "Write", "MultiEdit"):
            self.assertTrue(of_guard.critical_deny(tool, {"file_path": "/Users/alex/acme/org/registry/grants.yaml"}))
        self.assertTrue(of_guard.critical_deny("Write", {"file_path": "grants.yaml"},
                                               cwd="/Users/alex/acme/org/registry"))

    def test_allowed(self):
        for cmd in ("of registry check", "of grant --help", "of grant issue --help",
                    "cat ~/acme/org/registry/grants.yaml",
                    "git diff registry/grants.yaml", "git add registry/grants.yaml", "grep -n subject registry/grants.yaml",
                    "cp registry/grants.yaml /tmp/before.yaml",
                    'of send of_architect "of grant issue needs the tenant; see registry/grants.yaml"',
                    "python3 -c \"import yaml; print(len(yaml.safe_load(open('registry/grants.yaml'))['grants']))\"",
                    "ssh-keygen -Y verify -f allowed -I tenant -n of-grant -s g.sig < g"):
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": cmd}), cmd)
        self.assertIsNone(of_guard.critical_deny("Read", {"file_path": "/x/registry/grants.yaml"}))
        self.assertIsNone(of_guard.critical_deny("Write", {"file_path": "/x/registry/positions.yaml"}))


class GrantKeyGuardTest(unittest.TestCase):
    """Hand-over 4c7ac78f: the pinned key is the anchor of every verifier here, and the owner's 1Password SSH agent
    signs only for his own `of grant`."""

    def test_the_pin_the_staged_key_rekey_and_the_owners_agent_are_denied(self):
        sock = "~/Library/Group Containers/2BUA8C4S2C.com.1" + "password/t/agent.sock"
        for cmd in ("of grant rekey", "echo ssh-ed25519 AAAA > ~/.config/of/grant_ed25519.pub",
                    "cp /tmp/k.pub ~/.config/of/grant_1password.pub",
                    "ssh-keygen -Y sign -f ~/.config/of/grant_ed25519.pub -n of-grant msg",
                    f"SSH_AUTH_SOCK='{sock}' ssh-add -L", f"ssh -o IdentityAgent='{sock}' host"):
            self.assertTrue(of_guard.critical_deny("Bash", {"command": cmd}), cmd)
        self.assertTrue(of_guard.critical_deny("Write", {"file_path": os.path.expanduser(
            "~/.config/of/grant_ed25519.pub")}))
        for cmd in ("cat ~/.config/of/grant_ed25519.pub", "ssh-keygen -lf ~/.config/of/grant_ed25519.pub",
                    "of grant --help"):
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": cmd}), cmd)


class HookTest(KeyCase):
    """The hook applies an organization's deny grants to its sessions (of_hook._org_guard)."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        r = self.root.name
        os.makedirs(os.path.join(r, "org", "registry"))
        with open(os.path.join(r, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""
                organization: t
                positions:
                  - {name: returns_keeper, system: s, role: a, holder: {session: S1}}
                  - {name: runners_keeper, system: s, role: b, holder: {session: S2}}
                """))
        with open(os.path.join(r, "orgs.yaml"), "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        self.grants = os.path.join(r, "org", "registry", "grants.yaml")
        state = os.path.join(r, "state")
        os.makedirs(state)
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), of_grants.GRANT_KEY,
                      os.environ.get("KUBECONFIG"))
        of_events.STATE_DIR = of_hook.STATE = state
        os.environ["OF_ORGS"] = os.path.join(r, "orgs.yaml")
        os.environ.pop("KUBECONFIG", None)
        of_grants.GRANT_KEY = self.key
        of_hook.OUTPUT = None

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE, of_grants.GRANT_KEY = self.saved[0], self.saved[1], self.saved[3]
        for k, v in (("OF_ORGS", self.saved[2]), ("KUBECONFIG", self.saved[4])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
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

    def checks(self):
        import of_store
        conn = of_store.connect()
        try:
            rows = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY rowid")]
        finally:
            conn.close()
        return [r.get("of.payload") or {} for r in rows if r.get("event.name") == "of.mandate_check"]

    def test_warn_once_per_session_and_record_every_call(self):
        self.issue(rule_grant())
        cmd = "kubectl -n runners-jobs exec runner-1 -- sh"
        printed, output = self.fire(cmd)
        self.assertEqual(printed, "")
        ctx = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("rule of t (grant g-1", ctx)
        self.assertIn("runner gate", ctx)
        self.assertNotIn("permissionDecision", output["hookSpecificOutput"])
        self.assertIsNone(self.fire(cmd)[1])  # the note once per session
        self.assertEqual(self.fire(cmd, sid="S2"), ("", None))  # runners_keeper is excepted
        self.assertEqual(self.fire("kubectl -n evm exec db-0 -- sh"), ("", None))
        recs = [c for c in self.checks() if c.get("grant_id") == "g-1"]
        self.assertEqual([(c["verdict"], c["decision"]) for c in recs], [("warn", "admit")] * 2)

    def test_a_critical_deny_denies(self):
        self.issue(rule_grant(critical=True))
        printed, output = self.fire("kubectl -n runners-jobs cp job.json runner-1:/tmp/")
        self.assertIsNone(output)
        ans = json.loads(printed)["hookSpecificOutput"]
        self.assertEqual(ans["permissionDecision"], "deny")
        self.assertIn("Organic Framework rule of t (grant g-1)", ans["permissionDecisionReason"])
        self.assertEqual([(c["verdict"], c["decision"]) for c in self.checks() if c.get("grant_id") == "g-1"],
                         [("org_rule", "deny")])

    def test_the_index_is_rebuilt_when_the_router_version_changes(self):
        """of_studio's review of PR 49 (2026-11-12): the cached index kept the old router's compiled patterns after
        the router changed, while the verdicts were labelled with the new version."""
        import of_router
        self.assertEqual(of_hook._registry_index()["router"], of_router.VERSION)
        cached = of_hook._registry_index()
        self.assertEqual(cached["router"], of_router.VERSION)  # unchanged: read from the cache
        saved = of_router.VERSION
        self.addCleanup(setattr, of_router, "VERSION", saved)
        of_router.VERSION = "rt-v99"
        self.assertEqual(of_hook._registry_index()["router"], "rt-v99")
        with open(os.path.join(of_hook.STATE, "index.json")) as fh:
            self.assertEqual(json.load(fh)["router"], "rt-v99")

    def _stale_index(self, positions_yaml=None):
        """Build the index cache, then change the registry so the cache is stale; count rebuilds from then on."""
        of_hook._registry_index()
        reg = os.path.join(self.root.name, "org", "registry", "positions.yaml")
        if positions_yaml:
            with open(reg, "w") as fh:
                fh.write(textwrap.dedent(positions_yaml))
        later = os.path.getmtime(reg) + 5
        os.utime(reg, (later, later))
        calls, real = [], of_hook._registry_index
        of_hook._registry_index = lambda: calls.append(1) or real()
        self.addCleanup(setattr, of_hook, "_registry_index", real)
        return calls

    def test_a_deny_never_waits_for_the_registry(self):
        """2026-11-10: the pulse's guard probe timed out after 10 s at a load average of 30, parsing the registries'
        YAML after a change; a hook Claude Code kills at its 5 s timeout loses its deny. A deny and its record use
        the index cache as it is."""
        calls = self._stale_index()
        for sid in ("S1", "S9"):  # a holder, and a session the registry does not know (its organization by cwd)
            printed, _ = self.fire("printenv", sid=sid)
            self.assertEqual(json.loads(printed)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual([c["verdict"] for c in self.checks()], ["critical"] * 2)
        self.assertEqual(calls, [])

    def test_a_new_holder_still_gets_its_rules_from_a_stale_index(self):
        """The organization's rules need the session's position: a session missing from a stale cache rebuilds it."""
        self.issue(rule_grant(critical=True))
        calls = self._stale_index("""
            organization: t
            positions:
              - {name: returns_keeper, system: s, role: a, holder: {session: S3}}
              - {name: runners_keeper, system: s, role: b, holder: {session: S2}}
            """)
        printed, _ = self.fire("kubectl -n runners-jobs cp job.json runner-1:/tmp/", sid="S3")
        self.assertEqual(json.loads(printed)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertTrue(calls)
        self.assertEqual(self.fire("kubectl -n runners-jobs exec p -- sh", sid="S2"), ("", None))

    def test_a_grant_deleted_by_hand_still_applies(self):
        self.issue(rule_grant(critical=True))
        self.assertTrue(self.fire("kubectl -n runners-jobs exec p -- sh")[0])
        with open(self.grants, "w") as fh:
            fh.write("grants: []\n")
        os.utime(self.grants, (5000, 5000))
        printed, _ = self.fire("kubectl -n runners-jobs exec p -- sh")
        self.assertIn('"deny"', printed)
        notes = [c.get("grant_notes") for c in self.checks() if c.get("grant_id") == "g-1"][-1]
        self.assertIn("missing from the file", notes[0])

    def test_no_grants_no_cost(self):
        self.assertEqual(self.fire("kubectl -n runners-jobs exec p -- sh"), ("", None))
        self.assertEqual(self.fire("ls -la"), ("", None))


if __name__ == "__main__":
    unittest.main()


class PasskeyRevokedTest(KeyCase):
    """of_grants.passkey_revoked (docs/remote.md §6; of diff apply refuses a revoked credential): a signed revocation
    in the organization's grants file, kept once verified, never lifted by deleting it."""

    def test_signed_revocations_count_and_stay(self):
        import yaml
        d = tempfile.mkdtemp(dir=self.tmp.name)
        os.makedirs(os.path.join(d, "org", "registry"))
        orgs = os.path.join(d, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write("orgs:\n  - {name: acme, path: org}\n")
        state = os.path.join(d, "state")
        rec = {"kind": "passkey_revocation", "org": "acme", "credential": "cred-lost", "issued_at": "2026-11-12T12:00:00Z"}
        signed = dict(rec, signature=of_grants.sign_ed25519(rec, self.key))
        forged = dict(rec, credential="cred-other", signature=signed["signature"])   # the signature of another record
        other_org = dict(rec, org="beta")
        other_org["signature"] = of_grants.sign_ed25519(other_org, self.key)
        path = os.path.join(d, "org", "registry", "grants.yaml")

        def put(records):
            with open(path, "w") as fh:
                yaml.safe_dump({"grants": [], "passkey_revocations": records}, fh)

        revoked = lambda c: of_grants.passkey_revoked("acme", c, orgs, self.keys, state)
        self.assertFalse(revoked("cred-lost"))                     # no file yet
        put([signed, forged, other_org])
        self.assertTrue(revoked("cred-lost"))
        self.assertFalse(revoked("cred-other"))                    # its signature covers another record
        self.assertFalse(of_grants.passkey_revoked("beta", "cred-lost", orgs, self.keys, state))  # not beta's file
        put([])
        self.assertTrue(revoked("cred-lost"))                      # deleting the entry does not bring it back
        with open(path, "w") as fh:
            fh.write("{broken")
        self.assertTrue(revoked("cred-lost"))
        self.assertFalse(revoked(""))
