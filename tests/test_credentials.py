"""The credentials catalogue and the guard's default deny under an organization's root (docs/registry.md, Credentials;
docs/guard.md G14; of_architect, 8ef7bc0, 2026-11-11, from a user organization)."""
import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_credentials  # noqa: E402
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402

REGISTRY = """
organization: acme
credential_roots: ["{root}"]
credentials:
  grafana-viewer:
    paths: ["{root}/grafana/viewer.cfg"]
    acts_as: "Grafana service account agents-viewer, role Viewer"
    keeper: obs_keeper
  pg-main:
    paths: ["{root}/pg/*.pgpass"]
    acts_as: "Postgres role app_ro"
    keeper: db_keeper
  deploy-key:
    paths: ["{home}/.ssh/acme-deploy"]
    keeper: db_keeper
positions:
  - {{name: obs_keeper, system: obs, role: keeper, holder: {{session: S1}}}}
  - {{name: obs_reader, system: obs, role: reader, credentials: [grafana-viewer], holder: {{session: S2}}}}
  - {{name: db_keeper, system: db, role: keeper, holder: {{session: S3}}}}
  - {{name: app_dev, system: app, role: dev, credentials: [pg-main], holder: {{session: S4}}}}
"""
OTHER = """
organization: other
positions:
  - {name: other_ops, system: ops, role: ops, holder: {session: S9}}
"""


class CatalogueBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.expanduser("~"))  # roots lie under the home directory
        t = self.tmp.name
        self.root = os.path.join(t, "cfg", "acme")
        self.home = os.path.expanduser("~")
        for rel in ("grafana/viewer.cfg", "grafana/editor.cfg", "pg/main.pgpass", "pg/replica.pgpass",
                    "scratch/notes.md", "scratch/run.py"):
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            open(os.path.join(self.root, rel), "w").close()
        for org, text in (("acme", REGISTRY.format(root=self.root, home=self.home)), ("other", OTHER)):
            os.makedirs(os.path.join(t, org, "registry"))
            with open(os.path.join(t, org, "registry", "positions.yaml"), "w") as fh:
                fh.write(textwrap.dedent(text))
        self.orgs = os.path.join(t, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: acme, path: {t}/acme}}\n  - {{name: other, path: {t}/other}}\n")
        self.cat = of_credentials.load(self.orgs)
        self.keep = of_credentials.interesting(self.cat)

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, rel):
        return os.path.join(self.root, rel)

    def refs(self, cmd, cwd="/x", tool="Bash"):
        ti = {"command": cmd} if tool == "Bash" else {"file_path": cmd}
        return sorted({p for p, _ in of_guard.credential_refs(tool, ti, cwd, self.keep)})


class DecideTest(CatalogueBase):
    def test_a_position_uses_only_what_it_lists_or_keeps(self):
        d = of_credentials.decide
        self.assertEqual(d(self.p("grafana/viewer.cfg"), "obs_reader", "acme", self.cat),
                         (True, "acme", ["grafana-viewer"]))
        self.assertTrue(d(self.p("grafana/viewer.cfg"), "obs_keeper", "acme", self.cat)[0])    # its keeper
        self.assertFalse(d(self.p("grafana/viewer.cfg"), "app_dev", "acme", self.cat)[0])      # not listed
        self.assertTrue(d(self.p("pg/replica.pgpass"), "app_dev", "acme", self.cat)[0])         # a glob
        self.assertFalse(d(self.p("grafana/viewer.cfg"), "other_ops", "other", self.cat)[0])   # another org
        self.assertFalse(d(self.p("grafana/viewer.cfg"), None, None, self.cat)[0])             # no position

    def test_default_deny_under_the_root_and_names_only_outside_it(self):
        d = of_credentials.decide
        self.assertEqual(d(self.p("grafana/editor.cfg"), "obs_keeper", "acme", self.cat), (False, "acme", []))
        self.assertEqual(d(self.p("scratch/notes.md"), "obs_reader", "acme", self.cat), (False, "acme", []))
        key = os.path.join(self.home, ".ssh", "acme-deploy")
        self.assertFalse(d(key, "app_dev", "acme", self.cat)[0])     # outside the root, named: its positions only
        self.assertTrue(d(key, "db_keeper", "acme", self.cat)[0])
        self.assertIsNone(d(os.path.join(self.home, ".ssh", "id_other"), "app_dev", "acme", self.cat))
        self.assertIn("undeclared credential", of_credentials.reason(self.p("scratch/notes.md"), "obs_reader", "acme",
                                                                     [], self.cat))
        self.assertIn("db_keeper", of_credentials.reason(self.p("pg/main.pgpass"), "obs_reader", "acme",
                                                         ["pg-main"], self.cat))


    def test_the_view_of_spawn_writes_for_a_position(self):
        v = of_credentials.position_view("app_dev", "acme", self.cat)
        self.assertIn(self.root, v["roots"])                        # with the other organization's default root
        self.assertEqual(v["allowed"], [self.p("pg/*.pgpass")])
        self.assertIn(self.p("grafana/viewer.cfg"), v["denied"])
        self.assertIn(os.path.join(self.home, ".ssh", "acme-deploy"), v["denied"])
        self.assertIn(os.path.join(self.home, ".ssh", "acme-deploy"),
                      of_credentials.position_view("db_keeper", "acme", self.cat)["allowed"])  # its keeper


class RefsTest(CatalogueBase):
    def test_reads_and_uses_count(self):
        # neutral names: G1.7 already denies *.header and *.password outright, before this rule is asked
        h, pw = self.p("grafana/viewer.cfg"), self.p("pg/main.pgpass")
        self.assertEqual(self.refs(f"cat {h}"), [h])
        self.assertEqual(self.refs(f'curl -sf -H "@{h}" https://grafana/api/health'), [h])  # use, as -H @file
        self.assertEqual(self.refs(f"kubectl --kubeconfig={pw} get pods"), [pw])
        self.assertEqual(self.refs(f'PGPASSWORD="$(cat {pw})" psql -h db -c "select 1"'), [pw])
        self.assertEqual(self.refs(f"psql -h db < {pw}"), [pw])
        self.assertEqual(self.refs(f"python3 tools/q.py --header-file {h}"), [h])          # a script's argument
        self.assertEqual(self.refs(f"S={self.root}/grafana; cat $S/viewer.cfg"), [h])  # a variable set before
        self.assertEqual(self.refs(f"cd {self.root}/pg && cat main.pgpass"), [pw])      # a bare name after cd
        self.assertEqual(self.refs(f"python3 - <<'EOF'\nprint(open('{h}').read())\nEOF"), [h])
        self.assertEqual(self.refs(f"cp {pw} /tmp/x"), [pw])                              # the source of a copy
        self.assertEqual(self.refs(f"grep -r x {self.root}/pg"),
                         sorted([pw, self.p("pg/replica.pgpass")]))                     # a directory, recursively
        self.assertEqual(self.refs(f"cat {self.root}/pg/*.pgpass"), sorted([pw, self.p("pg/replica.pgpass")]))
        self.assertEqual(self.refs(h, tool="Read"), [h])

    def test_names_metadata_writes_and_remote_commands_do_not(self):
        h = self.p("grafana/viewer.cfg")
        for cmd in (f"ls -la {self.root}/grafana", f"stat {h}", f"test -f {h} && echo yes", f"echo {h}",
                    f"cp /tmp/x {h}", f"printf %s x > {h}", f"cat {self.root}/grafana",     # a directory, not read
                    f"ssh gw cat {h}", f"kubectl exec p -- cat {h}", f'of send x "see {h} for the header"',
                    f'git commit -m "{h}"', f"mkdir -p {self.root}/new"):
            self.assertEqual(self.refs(cmd), [], cmd)
        key = os.path.join(self.home, ".ssh", "acme-deploy")
        self.assertEqual(self.refs(f"ssh -i {key} gw cat {h}"), [key])                 # the key is used here


class BrokenRegistryTest(CatalogueBase):
    """of_server, 2026-11-23: a registry that does not read, or is gone, kept the organization out of the guard's
    catalogue, and with it every protection it lists; on the agent host any session's push moves a registry."""

    def reg(self):
        return os.path.join(self.tmp.name, "acme", "registry", "positions.yaml")

    def test_a_broken_or_gone_registry_keeps_its_last_reading(self):
        good = self.cat["acme"]
        for broken in ("positions: [\n  - name: x\n", "- just\n- a list\n", "\t: tab\n"):
            with open(self.reg(), "w") as fh:
                fh.write(broken)
            self.assertEqual(of_credentials.load(self.orgs, previous=self.cat)["acme"], good, broken)
            with self.assertRaises(of_credentials.Unreadable):
                of_credentials.load(self.orgs)                          # never read whole: unknown, not empty
        os.unlink(self.reg())
        self.assertEqual(of_credentials.load(self.orgs, previous=self.cat)["acme"], good)
        self.assertNotIn("acme", of_credentials.load(self.orgs))       # never read and not there: nothing to keep

    def test_the_cache_keeps_the_last_reading_across_a_broken_push(self):
        state = os.path.join(self.tmp.name, "state")
        first = of_credentials.cached(state, self.orgs)
        with open(self.reg(), "w") as fh:
            fh.write("positions: [\n")
        st = os.stat(self.reg())
        os.utime(self.reg(), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        self.assertEqual(of_credentials.cached(state, self.orgs)["acme"], first["acme"])


class CheckTest(CatalogueBase):
    def test_the_catalogue_as_written_is_clean(self):
        self.assertEqual(of_credentials.check(self.orgs), [])

    def test_problems(self):
        bad = REGISTRY.format(root=self.root, home=self.home).replace("keeper: obs_keeper", "keeper: nobody")
        bad = bad.replace('paths: ["{0}/pg/*.pgpass"]'.format(self.root), 'paths: ["hunter2-value"]')
        bad = bad.replace("credentials: [grafana-viewer]", "credentials: [grafana-viewer, missing-id]")
        bad += textwrap.dedent(f"""
        """)
        bad = bad.replace("  deploy-key:", f'  Bad_Id:\n    paths: ["{self.root}/grafana/viewer.cfg"]\n'
                                           "    keeper: db_keeper\n  deploy-key:")
        with open(os.path.join(self.tmp.name, "acme", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent(bad))
        probs = "\n".join(of_credentials.check(self.orgs))
        self.assertIn("keeper 'nobody' is not a position of acme", probs)
        self.assertIn("is not a file path", probs)
        self.assertNotIn("hunter2-value", probs)                              # a value is never printed whole
        self.assertIn("credential 'missing-id' is not in this organization's credentials", probs)
        self.assertIn("the id must match", probs)
        self.assertIn("is also claimed by acme:grafana-viewer", probs)

    def test_roots_lie_under_the_home_directory(self):
        path = os.path.join(self.tmp.name, "acme", "registry", "positions.yaml")
        text = open(path).read()
        for bad, said in (('credential_roots: "~/.config/acme"', "must be a list"),
                          ('credential_roots: ["/etc/acme"]', "is not a directory under the home directory"),
                          ('credential_roots: ["~/.config/of"]', "the platform's own config")):
            with open(path, "w") as fh:
                fh.write(text.replace(f'credential_roots: ["{self.root}"]', bad))
            self.assertTrue(any(said in p for p in of_credentials.check(self.orgs)), bad)

    def test_another_organizations_credential_cannot_be_listed(self):
        with open(os.path.join(self.tmp.name, "other", "registry", "positions.yaml"), "w") as fh:
            fh.write(OTHER.replace("role: ops,", "role: ops, credentials: [grafana-viewer],"))
        self.assertTrue(any("other_ops: credential 'grafana-viewer' is not in this organization's" in p
                            for p in of_credentials.check(self.orgs)))


class HookTest(CatalogueBase):
    def setUp(self):
        super().setUp()
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), os.environ.get("HERDR_ENV"),
                      (of_credentials.MODE_NAMED, of_credentials.MODE_ROOT))
        of_events.STATE_DIR = of_hook.STATE = os.path.join(self.tmp.name, "state")
        os.environ["OF_ORGS"] = self.orgs
        os.environ.pop("HERDR_ENV", None)

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        for k, v in (("OF_ORGS", self.saved[2]), ("HERDR_ENV", self.saved[3])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        of_credentials.MODE_NAMED, of_credentials.MODE_ROOT = self.saved[4]
        super().tearDown()

    def fire(self, sid, cmd):
        import io
        of_hook.OUTPUT = None
        old, sys.stdout = sys.stdout, io.StringIO()
        try:
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/x",
                                          "tool_name": "Bash", "tool_input": {"command": cmd}})
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = old
        out = json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})  # a deny is printed at once
        return out.get("hookSpecificOutput") or {}

    def checks(self):
        import of_store
        conn = of_store.connect()
        try:
            rows = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY rowid")]
        finally:
            conn.close()
        return [r.get("of.payload") or {} for r in rows if r.get("event.name") == "of.mandate_check"]

    def test_warn_now(self):
        h = self.p("grafana/editor.cfg")
        out = self.fire("S2", f"cat {h}")
        self.assertNotIn("permissionDecision", out)
        self.assertIn("credential rule of acme (a warning now, a deny later)", out["additionalContext"])
        self.assertIn("undeclared credential", out["additionalContext"])
        self.assertEqual(self.fire("S2", f"head -1 {h}"), {})                 # the note once per session
        self.assertEqual(self.fire("S2", f"cat {self.p('grafana/viewer.cfg')}"), {})  # its own credential
        checks = self.checks()
        self.assertEqual([(c["verdict"], c["rule"]) for c in checks], [("warn", "credential_root")])  # once per file

    def test_phase_one_named_files_deny_while_the_root_still_warns(self):
        """Msg 01a1c89d: a catalogue of explicit paths first (no root), the root after the working files move."""
        of_credentials.MODE_NAMED = "deny"
        out = self.fire("S4", f"cat {self.p('grafana/viewer.cfg')}")             # named, not listed by app_dev
        self.assertEqual(out["permissionDecision"], "deny")
        out = self.fire("S4", f"cat {self.p('scratch/notes.md')}")               # undeclared under the root
        self.assertNotIn("permissionDecision", out)
        self.assertIn("a warning now", out["additionalContext"])
        self.assertEqual(self.fire("S2", f"cat {self.p('grafana/viewer.cfg')}"), {})  # listed: allowed

    def test_deny_later(self):
        of_credentials.MODE_NAMED = of_credentials.MODE_ROOT = "deny"
        out = self.fire("S4", f"cat {self.p('grafana/viewer.cfg')}")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("credential grafana-viewer of acme, which position app_dev does not list",
                      out["permissionDecisionReason"])
        self.assertEqual(self.fire("S4", f"cat {self.p('pg/main.pgpass')}"), {})
        self.assertEqual([c["decision"] for c in self.checks()], ["deny"])


UNLISTED = """
organization: acme
credential_roots:
  - "{root}"
  - {{path: "{kits}", unlisted: true, keeper: sec_keeper}}
credentials:
  kit-a:
    paths: ["{kits}/kit_a/**"]
    keeper: sec_keeper
positions:
  - {{name: sec_keeper, system: sec, role: keeper, holder: {{session: S7}}}}
  - {{name: kit_a, system: kit, role: user, credentials: [kit-a], holder: {{session: S8}}}}
  - {{name: app_dev, system: app, role: dev, holder: {{session: S4}}}}
"""


class UnlistedRootTest(unittest.TestCase):
    """G43 (a user organization's request, 2026-11-24, of_architect's yes): an unlisted root's names are its keeper's
    alone to list, stat or search for; reading and using stay the catalogue's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.expanduser("~"))
        t = self.tmp.name
        self.root, self.kits = os.path.join(t, "cfg", "acme"), os.path.join(t, "kits")
        for rel in ("kit_a/kubeconfig", "kit_a/id_kit", "kit_a/bin/kit-tool", "kit_a/readme.txt"):
            os.makedirs(os.path.dirname(os.path.join(self.kits, rel)), exist_ok=True)
            open(os.path.join(self.kits, rel), "w").close()
        os.makedirs(self.root)
        os.makedirs(os.path.join(t, "acme", "registry"))
        with open(os.path.join(t, "acme", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent(UNLISTED.format(root=self.root, kits=self.kits)))
        self.orgs = os.path.join(t, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: acme, path: {t}/acme}}\n")
        self.cat = of_credentials.load(self.orgs)
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), os.environ.get("HERDR_ENV"))
        of_events.STATE_DIR = of_hook.STATE = os.path.join(t, "state")
        os.environ["OF_ORGS"] = self.orgs
        os.environ.pop("HERDR_ENV", None)

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        for k, v in (("OF_ORGS", self.saved[2]), ("HERDR_ENV", self.saved[3])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def fire(self, sid, tool, ti):
        import io
        of_hook.OUTPUT = None
        old, sys.stdout = sys.stdout, io.StringIO()
        try:
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/x",
                                          "tool_name": tool, "tool_input": ti})
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = old
        out = json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    def test_the_catalogue_reads_the_mapping_form(self):
        self.assertEqual(of_credentials.unlisted_roots(self.cat), [self.kits])
        self.assertIn(self.kits, self.cat["acme"]["roots"])
        self.assertEqual(of_credentials.unlisted_hit(self.kits + "/kit_a", "kit_a", "acme", self.cat)[1], self.kits)
        self.assertIsNone(of_credentials.unlisted_hit(self.kits + "/kit_a", "sec_keeper", "acme", self.cat))
        self.assertIsNotNone(of_credentials.unlisted_hit(os.path.dirname(self.kits), "kit_a", "acme", self.cat, True))
        self.assertIsNone(of_credentials.unlisted_hit(os.path.dirname(self.kits), "kit_a", "acme", self.cat, False))

    def test_listing_stat_and_name_searches_are_denied_but_the_keepers(self):
        k, up = self.kits, os.path.dirname(self.kits)
        for cmd in (f"ls {k}", f"ls -la {k}/kit_a", f"stat {k}/kit_a/id_kit", f"file {k}/kit_a/kubeconfig",
                    f"find {k} -name '*.key'", f"find {up} -name 'id_*'", f"tree {up}", f"ls -R {up}",
                    f"cd {k} && ls", f"python3 -c \"import os; print(os.listdir('{k}/kit_a'))\"",
                    f"python3 -c \"import os; print(list(os.walk('{up}')))\""):
            self.assertEqual(self.fire("S8", "Bash", {"command": cmd}), "deny", cmd)   # the kit's own user too
            self.assertEqual(self.fire("S4", "Bash", {"command": cmd}), "deny", cmd)
            self.assertIsNone(self.fire("S7", "Bash", {"command": cmd}), cmd)           # its keeper
        for tool, ti in (("Glob", {"pattern": "**/*", "path": k}), ("Glob", {"pattern": f"{k}/**/id_*"}),
                         ("LS", {"path": k + "/kit_a"}), ("Glob", {"pattern": "**/id_*", "path": up})):
            self.assertEqual(self.fire("S8", tool, ti), "deny", (tool, ti))
            self.assertIsNone(self.fire("S7", tool, ti), (tool, ti))
        for cmd in (f"echo {k}/*", f"printf '%s\\n' {k}/kit_a/*", f"for f in {k}/*; do echo $f; done",   # PR 649
                    f"compgen -f {k}/", f"rg --files {k}", f"tar -cvf /tmp/x.tar {k}", f"rsync -n -r {k} /tmp/x",
                    f"zip -r /tmp/x.zip {k}", f"git -C {k}/kit_a ls-files", f"echo {up}/**/id_*"):
            self.assertEqual(self.fire("S8", "Bash", {"command": cmd}), "deny", cmd)
            self.assertIsNone(self.fire("S7", "Bash", {"command": cmd}), cmd)
        link = os.path.join(self.tmp.name, "to-kits")   # of_review on PR 649: a link, find's options, the code's folder
        os.symlink(k, link)
        for cmd in (f"ls {link}/kit_a", f"find -L {k} -name '*'", f"cd {k}/kit_a && python3 -c \"import os; print(os.listdir())\"",
                    f"python3 -Ic \"import os; print(os.listdir('{k}/kit_a'))\"",
                    f"cd {k}/kit_a && python3 -c \"import glob; print(glob.glob('*'))\""):
            self.assertEqual(self.fire("S8", "Bash", {"command": cmd}), "deny", cmd)
            self.assertEqual(self.fire("S4", "Bash", {"command": cmd}), "deny", cmd)
            self.assertIsNone(self.fire("S7", "Bash", {"command": cmd}), cmd)
        for cmd in (f"ls {up}", f"{k}/kit_a/bin/kit-tool status",   # the root's own name; the kit's program
                    'echo "**Owed by me:** the review" >> /tmp/notes.md',   # a user organization, 2026-11-24: quoted text
                    "{ echo; echo '**bold** kit_a config tests'; } >> /tmp/notes.md", "printf '%s\\n' '*'",
                    f"echo {k}/\\*", f"echo $'{k}/*'", "grep -n '[a-z]*' /tmp/notes.md",
                    'h=a:b; echo "${h%%:*}" ${h#*:}', "cd /tmp && echo *"):
            self.assertIsNone(self.fire("S8", "Bash", {"command": cmd}), cmd)
        for cmd in (f'echo "{k}"/*', f"echo '{k}/'kit_a/*", f"for f in \"{up}\"/**; do :; done",   # unquoted globs
                    f"D={k}; echo ${{D}}/*", f"D={k}; for f in ${{D}}/kit_a/*; do :; done", f"cd {k} && echo *"):
            self.assertEqual(self.fire("S8", "Bash", {"command": cmd}), "deny", cmd)

    def test_the_kits_program_runs_for_those_who_list_its_credential_only(self):
        """of_architect: running a kit's program by its path stays allowed to the positions that list that kit's
        credential, and to no one else (its keeper keeps it as for every credential)."""
        prog = f"{self.kits}/kit_a/bin/kit-tool status"
        self.assertIsNone(self.fire("S8", "Bash", {"command": prog}))
        self.assertEqual(self.fire("S4", "Bash", {"command": prog}), "deny")

    def test_reading_stays_the_catalogues(self):
        f = f"{self.kits}/kit_a/readme.txt"   # a plain name: a kubeconfig or a key is G1's to deny anyway
        self.assertIsNone(self.fire("S8", "Bash", {"command": f"cat {f}"}))   # it lists kit-a
        self.assertIsNone(self.fire("S7", "Bash", {"command": f"cat {f}"}))   # its keeper

    def test_the_check_refuses_an_unlisted_root_without_a_keeper(self):
        bad = UNLISTED.replace("unlisted: true, keeper: sec_keeper", "unlisted: true").format(root=self.root,
                                                                                          kits=self.kits)
        with open(os.path.join(self.tmp.name, "acme", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent(bad))
        self.assertTrue(any("unlisted and names no keeper" in p for p in of_credentials.check(self.orgs)))
        bad = UNLISTED.replace("keeper: sec_keeper}}", "keeper: nobody}}").format(root=self.root, kits=self.kits)
        with open(os.path.join(self.tmp.name, "acme", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent(bad))
        self.assertTrue(any("keeper 'nobody' is no position here" in p for p in of_credentials.check(self.orgs)))


class CacheReadTest(unittest.TestCase):
    """The guard's cache is read as the other state files are: no link, a regular file of this user, no second name,
    no wait on open."""

    def test_a_planted_fifo_or_link_is_never_read(self):
        import of_credentials as C
        with tempfile.TemporaryDirectory() as d:
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs: []\n")
            path = os.path.join(d, C.CACHE)
            os.mkfifo(path)                                           # not a regular file
            self.assertIsNone(C.cached_protection(d))
            self.assertEqual(C.guard_protection(d, orgs), ([], []))   # rebuilt over it
            self.assertFalse(os.path.islink(path))
            victim = os.path.join(d, "victim.json")
            with open(victim, "w") as fh:
                json.dump({"version": C.CACHE_VERSION, "orgs": {"x": {"protected_code": ["/planted"]}}}, fh)
            os.unlink(path)
            os.symlink(victim, path)
            self.assertIsNone(C.cached_protection(d))                 # a link: not read
            os.unlink(path)
            os.link(victim, path)
            self.assertIsNone(C.cached_protection(d))                 # a second name: not read


if __name__ == "__main__":
    unittest.main()
