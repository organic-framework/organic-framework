"""Regulators and authorities (docs/guard.md G16; the founder through of_conductor, hand-over 630f29e9, 2026-11-11): an
agent never contacts one without the founder's explicit word, a signed grant that names that one contact."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_events  # noqa: E402
import of_grants  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402


def bash(cmd):
    return of_guard.authority_contacts("Bash", {"command": cmd})


class HostTest(unittest.TestCase):
    def test_government_domains_of_every_country_and_the_listed_regulators(self):
        for h in ("sec.gov", "api.sec.gov", "apps.dtic.mil", "eservices.tax.gov.ae", "www.gov.uk", "fsa.go.jp",
                  "impots.gouv.fr", "fma.gv.at", "ird.govt.nz", "rulebooks.vara.ae", "www.fca.org.uk", "cima.ky",
                  "esma.europa.eu", "fintrac-canafe.gc.ca"):
            self.assertTrue(of_guard.authority_host(h), h)
        for h in ("github.com", "go.dev", "example.com", "sec.gov.example.com", "govtech.com", "vara.ae.example.org",
                  "localhost", ""):
            self.assertFalse(of_guard.authority_host(h), h)

    def test_an_organization_names_its_own_in_its_registry(self):
        import of_credentials
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "org", "registry"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write(textwrap.dedent("""
                    organization: t
                    authorities:
                      - name: Free-zone licensing authority
                        contacts: [licensing.example, officer@mail.example]
                        evidence: "trade licence 123 (the structure keeper's record)"
                      - {name: Broken, contacts: [not a host], evidence: ""}
                    positions:
                      - {name: compliance_keeper, system: s, role: a}
                    """))
            orgs = os.path.join(t, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: t, path: org}\n")
            try:
                self.assertFalse(of_guard.authority_host("portal.licensing.example"))
                cat = of_credentials.cached(os.path.join(t, "state"), orgs)
                of_guard.use_authorities(c for e in cat.values() for c in e["authorities"])
                self.assertTrue(of_guard.authority_host("portal.licensing.example"))
                self.assertEqual(bash("mail -s x officer@mail.example < b.txt"), [("officer@mail.example", "mail")])
                self.assertEqual(bash("mail -s x other@mail.example < b.txt"), [])   # only the address listed
                problems = of_credentials.check(orgs)
                self.assertTrue(any("not an e-mail address or a host" in p for p in problems), problems)
                self.assertTrue(any("authority Broken: evidence" in p for p in problems), problems)
                self.assertFalse(any("Free-zone" in p for p in problems), problems)
            finally:
                of_guard.use_authorities(())


class ContactTest(unittest.TestCase):
    def test_sending_is_contact(self):
        for cmd, contact in (
                ("curl -X POST https://api.sec.gov/x -d '{}'", "api.sec.gov"),
                ("curl -sd a=b https://portal.vara.ae/form", "portal.vara.ae"),
                ("bash -c \"curl -F f=@x https://upload.dfsa.ae/\"", "upload.dfsa.ae"),
                ("wget --post-data a=b https://portal.vara.ae/form", "portal.vara.ae"),
                ("http POST https://api.finra.org/x a=b", "api.finra.org"),
                ("U=https://eservices.tax.gov.ae/api; curl -X PUT $U -d @f.json", "eservices.tax.gov.ae"),
                ("ssh gw 'curl -d x https://api.sec.gov/'", "api.sec.gov"),
                ("curl -d '{\"to\":\"info@sca.gov.ae\"}' https://api.mailer.example/v3/send", "info@sca.gov.ae"),
                ("printf 'To: info@sca.gov.ae\\n' | sendmail -t", "info@sca.gov.ae"),
                ("cat <<EOF | sendmail -t\nTo: a@cima.ky\nEOF", "a@cima.ky"),
                ("mail -s hi complaints@fca.org.uk < body.txt", "complaints@fca.org.uk"),
                ("osascript -e 'tell application \"Mail\" to make new outgoing message with properties "
                 "{address:\"x@vara.ae\"}'", "x@vara.ae"),
                ("python3 - <<'EOF'\nimport requests\nrequests.post('https://eservices.tax.gov.ae/x', json={})\nEOF",
                 "eservices.tax.gov.ae")):
            self.assertIn(contact, [c for c, _ in bash(cmd)], cmd)

    def test_reading_and_writing_about_them_is_not(self):
        for cmd in ("curl -sSL https://www.treasury.gov/ofac/sdn.csv -o sdn.csv", "curl -X GET https://api.sec.gov/x",
                    "wget https://rulebooks.vara.ae/x.pdf", "grep -r info@sca.gov.ae docs/",
                    "echo 'write to info@sca.gov.ae' >> notes.md", "mkdir -p rulebooks.vara.ae",
                    "python3 -c 'import requests; print(requests.get(\"https://www.cisa.gov/kev.json\").status_code)'",
                    "python3 - <<'EOF'\nimport json, urllib.request\nr = urllib.request.Request('https://www.cisa.gov/"
                    "k.json')\ndata = json.load(urllib.request.urlopen(r))\nEOF",
                    'of send acme_org "email compliance@vara.ae tomorrow"', "git commit -m 'cite https://www.sec.gov/x'",
                    "curl -X POST https://api.github.com/x -d '{}'", "curl -d x https://go.dev/api"):
            self.assertEqual(bash(cmd), [], cmd)
        self.assertEqual(of_guard.authority_contacts("WebFetch", {"url": "https://www.vara.ae/en/"}), [])
        self.assertEqual(of_guard.authority_contacts("Write", {"file_path": "x.md", "content": "https://sec.gov"}), [])

    def test_mcp_connectors_and_browsers(self):
        c = of_guard.authority_contacts
        self.assertEqual(c("mcp__mail__send_email", {"to": ["info@sca.gov.ae"], "body": "x"}),
                         [("info@sca.gov.ae", "mcp__mail__send_email")])
        self.assertTrue(c("mcp__mail__create_draft", {"to": "info@sca.gov.ae"}))   # a draft is sent by its id later
        self.assertTrue(c("mcp__browser__navigate", {"url": "eservices.tax.gov.ae/login"}))
        self.assertTrue(c("mcp__computer-use__computer", {"action": "type", "text": "info@sca.gov.ae"}))
        self.assertEqual(c("mcp__mail__search_emails", {"query": "from:info@sca.gov.ae"}), [])
        self.assertEqual(c("mcp__browser__get_page_text", {"tabId": 1}), [])
        self.assertEqual(c("mcp__mail__send_email", {"to": ["someone@example.com"]}), [])


class ShapeTest(unittest.TestCase):
    def test_a_contacts_grant_names_one_contact_for_one_position(self):
        g = {"id": "g-1", "subject": "acme_org", "issuer": "tenant", "tier": "act_with_approval", "effect": "allow",
             "scope": {"contacts": ["info@sca.gov.ae"]}}
        self.assertEqual(of_grants.shape_problems(g), [])
        for bad in (dict(g, scope={"contacts": ["a@sec.gov", "b@sec.gov"]}), dict(g, scope={"contacts": []}),
                    dict(g, subject="*"), dict(g, effect="deny"), dict(g, issuer="acme_owner")):
            self.assertTrue(of_grants.shape_problems(bad), bad)


class HookTest(unittest.TestCase):
    """The hook denies contact unless a signed grant names it for the session's position (of_hook._authority_guard)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key = os.path.join(cls.tmp.name, "grant_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", cls.key], check=True,
                       env=of_grants.no_agent_env())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        r = self.root.name
        os.makedirs(os.path.join(r, "org", "registry"))
        with open(os.path.join(r, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""
                organization: t
                positions:
                  - {name: compliance_keeper, system: s, role: a, holder: {session: S1}}
                  - {name: books_keeper, system: s, role: b, holder: {session: S2}}
                """))
        with open(os.path.join(r, "orgs.yaml"), "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        self.grants = os.path.join(r, "org", "registry", "grants.yaml")
        state = os.path.join(r, "state")
        os.makedirs(state)
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), of_grants.GRANT_KEY)
        of_events.STATE_DIR = of_hook.STATE = state
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
        signed = []
        for g in grants:
            g = dict(g)
            g["signature"] = of_grants.sign_ed25519(g, self.key)
            signed.append(g)
        with open(self.grants, "w") as fh:
            yaml.safe_dump({"grants": signed}, fh)

    def fire(self, tool, tool_input, sid="S1"):
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/x",
                                          "tool_name": tool, "tool_input": tool_input})
        printed = out.getvalue()
        return (json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})).get("hookSpecificOutput") or {}

    def checks(self):
        import of_store
        conn = of_store.connect()
        try:
            rows = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY rowid")]
        finally:
            conn.close()
        return [r.get("of.payload") or {} for r in rows if r.get("event.name") == "of.mandate_check"]

    MAIL = {"to": ["info@sca.gov.ae"], "subject": "x", "body": "y"}
    POST = "curl -X POST https://portal.vara.ae/form -d a=b"

    def grant(self, contact, subject="compliance_keeper", **kw):
        return dict({"id": "g-c1", "subject": subject, "issuer": "tenant", "tier": "act_with_approval",
                     "effect": "allow", "scope": {"contacts": [contact]}, "note": "the founder's yes, ask 0a0b0c0d"},
                    **kw)

    def test_denied_without_a_grant_and_said_how_to_ask(self):
        out = self.fire("mcp__mail__send_email", self.MAIL)
        self.assertEqual(out["permissionDecision"], "deny")
        for words in ("info@sca.gov.ae", "of ask", "G16", "WebFetch"):
            self.assertIn(words, out["permissionDecisionReason"])
        self.assertEqual(self.fire("Bash", {"command": self.POST})["permissionDecision"], "deny")
        self.assertNotIn("permissionDecision", self.fire("Bash", {"command": "curl -s https://portal.vara.ae/form"}))
        verdicts = [(c.get("rule"), c.get("decision")) for c in self.checks()]
        self.assertIn(("authority_contact", "deny"), verdicts)

    def test_a_signed_grant_lets_through_its_one_contact_for_its_one_position(self):
        self.issue(self.grant("info@sca.gov.ae"), self.grant("portal.vara.ae", id="g-c2"))
        self.assertNotIn("permissionDecision", self.fire("mcp__mail__send_email", self.MAIL))
        self.assertNotIn("permissionDecision", self.fire("Bash", {"command": self.POST}))
        self.assertIn(("admit", ["g-c1"]), [(c.get("decision"), c.get("grants")) for c in self.checks()])
        other = {"to": ["head@sca.gov.ae"], "body": "y"}  # another address at the same authority
        self.assertEqual(self.fire("mcp__mail__send_email", other)["permissionDecision"], "deny")
        self.assertEqual(self.fire("mcp__mail__send_email", self.MAIL, sid="S2")["permissionDecision"], "deny")

    def test_an_unsigned_expired_revoked_or_widened_grant_lets_nothing_through(self):
        for g in (self.grant("info@sca.gov.ae", expires="2020-01-01T00:00:00Z"),
                  self.grant("info@sca.gov.ae", revoked={"at": "2026-11-11", "by": "tenant", "reason": "x"}),
                  self.grant("info@sca.gov.ae", scope={"contacts": ["info@sca.gov.ae", "a@sec.gov"]})):
            self.issue(g)
            self.assertEqual(self.fire("mcp__mail__send_email", self.MAIL)["permissionDecision"], "deny", g)
        self.issue(self.grant("info@sca.gov.ae"))
        import yaml
        with open(self.grants) as fh:
            doc = yaml.safe_load(fh)
        doc["grants"][0]["subject"] = "books_keeper"  # edited by hand after signing
        with open(self.grants, "w") as fh:
            yaml.safe_dump(doc, fh)
        self.assertEqual(self.fire("mcp__mail__send_email", self.MAIL, sid="S2")["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
