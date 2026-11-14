"""deploy/netcheck.py and the chart's NetworkPolicy (deploy/RUNBOOK.md §4.1): a fake kubectl stands for the cluster."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("netcheck", os.path.join(ROOT, "deploy", "netcheck.py"))
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

SERVER_LABELS = {"app.kubernetes.io/name": "of-server", "app.kubernetes.io/instance": "of-server"}


def pod(name, labels, ip):
    return {"metadata": {"name": name, "labels": labels}, "status": {"podIP": ip, "phase": "Running"}}


class FakeCluster:
    """Answers the kubectl calls netcheck makes. `reach(source, host, port)` is the network: source is the probe's
    namespace, or 'server' for the exec."""

    def __init__(self, enforce=True, rbac=None, policies=True):
        self.enforce, self.calls, self.applied = enforce, [], {}
        self.rbac = rbac or {"roles": [], "clusterroles": [], "rolebindings": [], "clusterrolebindings": []}
        self.policies = policies

    def reach(self, source, host, port):
        if host.startswith("192.0.2.10"):           # the scratch targets
            closed = host == "192.0.2.102"
            if source == "server":
                return "timeout" if self.enforce else "open"
            return "timeout" if closed and self.enforce else "open"
        if source == "server":
            return "open" if (host.startswith("clickhouse.") and port == 8123) or not self.enforce else "timeout"
        return "timeout" if self.enforce else "open"

    def run(self, args, stdin=None):
        self.calls.append(list(args))
        a = list(args)
        if a[:2] == ["get", "deployment"]:
            return 0, json.dumps({"spec": {"selector": {"matchLabels": SERVER_LABELS}, "template": {"spec": {
                "containers": [{"name": "of-server", "image": "registry.acme.example/of-server:1"}]}}}}), ""
        if a[:2] == ["get", "pods"]:
            ns, sel = a[a.index("-n") + 1], a[a.index("-l") + 1]
            if sel == "of-netcheck=probe":
                items = [pod("target-open", {"role": "open"}, "192.0.2.101"),
                         pod("target-closed", {"role": "closed"}, "192.0.2.102")]
            elif ns == "of-server":
                items = [pod("of-server-of-server-abc", SERVER_LABELS, "192.0.2.11")]
            elif ns == "clickhouse":
                items = [pod("clickhouse-xyz", {"app": "clickhouse"}, "192.0.2.21")]
            else:
                items = []
            return 0, json.dumps({"items": items}), ""
        if a[:2] == ["get", "networkpolicy"]:
            ns = a[a.index("-n") + 1]
            pols = {"of-server": [{"metadata": {"name": "of-server-of-server"}, "spec": {
                        "podSelector": {"matchLabels": SERVER_LABELS}, "policyTypes": ["Ingress", "Egress"]}}],
                    "clickhouse": [{"metadata": {"name": "default-deny"}, "spec": {"podSelector": {},
                                                                                  "policyTypes": ["Ingress"]}}]}
            return 0, json.dumps({"items": pols.get(ns, []) if self.policies else []}), ""
        if a[0] == "get" and a[1] in self.rbac:
            return 0, json.dumps({"items": self.rbac[a[1]]}), ""
        if a[0] == "get" and a[1] == "pod":
            return 0, json.dumps({"status": {"phase": "Running"}}), ""
        if a[:3] == ["apply", "-f", "-"]:
            obj = json.loads(stdin)
            for o in obj.get("items") or [obj]:
                if o["kind"] == "Pod":
                    self.applied[(o["metadata"]["namespace"], o["metadata"]["name"])] = o
            return 0, "", ""
        if a[0] in ("wait", "delete"):
            return 0, "", ""
        if a[0] == "logs":
            ns, name = a[a.index("-n") + 1], a[-1].split("/", 1)[1]
            cmd = self.applied[(ns, name)]["spec"]["containers"][0]["command"]
            return 0, self._results(ns, json.loads(cmd[4])), ""
        if a[0] == "exec":
            return 0, self._results("server", json.loads(a[a.index("-c", a.index("--")) + 2])), ""
        return 1, "", "unexpected call " + " ".join(a)

    def _results(self, source, targets):
        return "\n".join(json.dumps({"label": lab, "result": self.reach(source, h, p)}) for lab, h, p in targets)

    def ok(self, *args):
        return self.run(list(args))[0] == 0

    def json(self, *args):
        code, out, err = self.run(list(args) + ["-o", "json"])
        if code:
            raise LookupError(err)
        return json.loads(out)


def run(cluster, *argv):
    out = io.StringIO()
    code = nc.main(list(argv) + ["--deployer", "User:deployer"], k=cluster, out=out)
    return code, out.getvalue()


class NetcheckTest(unittest.TestCase):
    def test_enforcing_cluster_passes(self):
        code, out = run(FakeCluster(), "--connector-namespace", "cloudflared")
        self.assertEqual(code, 0, out)
        self.assertNotIn("FAIL", out)
        self.assertIn("a target behind deny-all (the cluster enforces NetworkPolicy) is refused", out)
        self.assertIn("from cloudflared: server Service", out)
        self.assertIn("from the server: ClickHouse clickhouse.clickhouse.svc:8123 answers", out)

    def test_cluster_without_enforcement_fails(self):
        code, out = run(FakeCluster(enforce=False))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  from of-netcheck-", out)
        self.assertIn("behind deny-all (the cluster enforces NetworkPolicy) is refused: open", out)
        self.assertIn("FAIL  from the server: the API server", out)

    def test_namespace_without_egress_is_skipped_not_passed(self):
        c = FakeCluster()
        reach = c.reach
        c.reach = lambda src, h, p: "timeout" if src == "of-server" else reach(src, h, p)
        code, out = run(c)
        self.assertEqual(code, 0, out)
        self.assertIn("from of-server: skipped, the probe reaches nothing", out)
        self.assertNotIn("PASS  from of-server:", out)

    def test_missing_policies_fail(self):
        code, out = run(FakeCluster(policies=False))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  the server's pods in of-server are selected by a NetworkPolicy with Ingress", out)

    def test_scratch_objects_are_removed(self):
        c = FakeCluster(enforce=False)
        run(c)
        deletes = [x for x in c.calls if x[0] == "delete"]
        self.assertTrue(any(x[1] == "namespace" and x[2].startswith("of-netcheck-") for x in deletes), deletes)
        self.assertTrue(any(x[1] == "pod" and "of-server" in x for x in deletes), deletes)

    def test_probe_pods_pass_restricted(self):
        for o in nc.scratch_objects("of-netcheck-abc", "img"):
            if o["kind"] != "Pod":
                continue
            sc = o["spec"]["containers"][0]["securityContext"]
            self.assertFalse(sc["allowPrivilegeEscalation"])
            self.assertTrue(sc["runAsNonRoot"])
            self.assertEqual(sc["capabilities"], {"drop": ["ALL"]})
            self.assertFalse(o["spec"]["automountServiceAccountToken"])

    def test_probe_script_reports_each_target(self):
        import socket
        srv = socket.create_server(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        try:
            p = subprocess.run(["python3", "-I", "-c", nc.PROBE,
                                json.dumps([["up", "127.0.0.1", port], ["name", "no-such-host.invalid", 1]]), "1"],
                               capture_output=True, text=True, timeout=30)
        finally:
            srv.close()
        got = nc.parse_results(p.stdout)
        self.assertEqual(got["up"], "open")
        self.assertEqual(got["name"], "unresolved")

    def test_judge(self):
        self.assertTrue(nc.judge("closed", "timeout"))
        self.assertTrue(nc.judge("closed", "refused"))
        self.assertFalse(nc.judge("closed", "unresolved"))   # proves nothing
        self.assertFalse(nc.judge("closed", "open"))
        self.assertTrue(nc.judge("open", "open"))

    def test_selects(self):
        self.assertTrue(nc.selects({}, {"a": "1"}))
        self.assertTrue(nc.selects({"matchLabels": {"a": "1"}}, {"a": "1", "b": "2"}))
        self.assertFalse(nc.selects({"matchLabels": {"a": "2"}}, {"a": "1"}))
        self.assertFalse(nc.selects({"matchExpressions": [{"key": "b", "operator": "Exists"}]}, {"a": "1"}))
        self.assertTrue(nc.selects({"matchExpressions": [{"key": "a", "operator": "In", "values": ["1"]}]},
                                   {"a": "1"}))


class RbacTest(unittest.TestCase):
    RBAC = {
        "clusterroles": [
            {"metadata": {"name": "edit"}, "rules": [{"apiGroups": [""], "resources": ["secrets"],
                                                       "verbs": ["get", "list"]}]},
            {"metadata": {"name": "view"}, "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}]},
            {"metadata": {"name": "cluster-admin"}, "rules": [{"apiGroups": ["*"], "resources": ["*"],
                                                                "verbs": ["*"]}]},
            {"metadata": {"name": "system:controller:namespace-controller"},
             "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["delete", "get", "list"]}]},
            {"metadata": {"name": "system:helper"}, "rules": [{"apiGroups": [""], "resources": ["secrets"],
                                                                "verbs": ["list"]}]},
            {"metadata": {"name": "node-proxy"}, "rules": [{"apiGroups": [""], "resources": ["nodes/proxy"],
                                                             "verbs": ["get"]}]},
            {"metadata": {"name": "debugger"}, "rules": [{"apiGroups": [""], "resources": ["pods/ephemeralcontainers"],
                                                           "verbs": ["patch"]}]},
            {"metadata": {"name": "forwarder"}, "rules": [{"apiGroups": [""], "resources": ["pods/portforward"],
                                                            "verbs": ["create"]}]},
        ],
        "roles": [{"metadata": {"name": "runner", "namespace": "of-server"},
                   "rules": [{"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["create"]}]},
                  {"metadata": {"name": "shell", "namespace": "of-server"},
                   "rules": [{"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["create"]}]},
                  {"metadata": {"name": "pods", "namespace": "cloudflared"},
                   "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["create"]}]}],
        "rolebindings": [
            {"metadata": {"name": "ci", "namespace": "of-server"}, "roleRef": {"kind": "Role", "name": "runner"},
             "subjects": [{"kind": "ServiceAccount", "name": "ci"}]},
            {"metadata": {"name": "debug", "namespace": "of-server"}, "roleRef": {"kind": "Role", "name": "shell"},
             "subjects": [{"kind": "User", "name": "oncall"}]},
            {"metadata": {"name": "other", "namespace": "elsewhere"}, "roleRef": {"kind": "ClusterRole",
                                                                                 "name": "edit"},
             "subjects": [{"kind": "User", "name": "far"}]},
            {"metadata": {"name": "debug", "namespace": "clickhouse"}, "roleRef": {"kind": "ClusterRole",
                                                                                  "name": "debugger"},
             "subjects": [{"kind": "User", "name": "debugger"}]},
            {"metadata": {"name": "fwd", "namespace": "clickhouse"}, "roleRef": {"kind": "ClusterRole",
                                                                                "name": "forwarder"},
             "subjects": [{"kind": "User", "name": "forwarder"}]},
            # nodes/proxy through a RoleBinding grants nothing: nodes are cluster-scoped
            {"metadata": {"name": "np", "namespace": "of-server"}, "roleRef": {"kind": "ClusterRole",
                                                                              "name": "node-proxy"},
             "subjects": [{"kind": "User", "name": "local-only"}]},
            {"metadata": {"name": "tunnel-ops", "namespace": "cloudflared"}, "roleRef": {"kind": "Role",
                                                                                        "name": "pods"},
             "subjects": [{"kind": "User", "name": "tunnel-ops"}]},
        ],
        "clusterrolebindings": [
            {"metadata": {"name": "cluster-admin", "labels": {"kubernetes.io/bootstrapping": "rbac-defaults"}},
             "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"},
             "subjects": [{"kind": "Group", "name": "system:masters"}]},
            {"metadata": {"name": "system:controller:namespace-controller", "labels": {"kubernetes.io/bootstrapping": "rbac-defaults"}},
             "roleRef": {"kind": "ClusterRole", "name": "system:controller:namespace-controller"},
             "subjects": [{"kind": "ServiceAccount", "name": "namespace-controller", "namespace": "kube-system"}]},
            {"metadata": {"name": "devs-edit"}, "roleRef": {"kind": "ClusterRole", "name": "edit"},
             "subjects": [{"kind": "Group", "name": "developers"}]},
            {"metadata": {"name": "deployer"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"},
             "subjects": [{"kind": "User", "name": "deployer"}]},
            {"metadata": {"name": "viewers"}, "roleRef": {"kind": "ClusterRole", "name": "view"},
             "subjects": [{"kind": "Group", "name": "system:authenticated"}]},
            # named like a default, without the API server's label: a chart's own binding
            {"metadata": {"name": "system:helper"}, "roleRef": {"kind": "ClusterRole", "name": "system:helper"},
             "subjects": [{"kind": "ServiceAccount", "name": "helper", "namespace": "tools"}]},
            {"metadata": {"name": "node-debug"}, "roleRef": {"kind": "ClusterRole", "name": "node-proxy"},
             "subjects": [{"kind": "User", "name": "node-debugger"}]},
        ],
    }

    def test_readers(self):
        r = nc.secret_readers("of-server", **{k: self.RBAC[k] for k in
                                              ("roles", "clusterroles", "rolebindings", "clusterrolebindings")})
        subjects = {s: (how, boot) for s, how, _, boot in r}
        self.assertIn("Group:developers", subjects)
        self.assertTrue(subjects["ServiceAccount:of-server/ci"][0].startswith("starts pods"))
        self.assertTrue(subjects["User:oncall"][0].startswith("runs code in pods"))
        self.assertFalse(subjects["ServiceAccount:tools/helper"][1])   # a system: name without the label
        self.assertIn("nodes/proxy", subjects["User:node-debugger"][0])
        self.assertNotIn("User:local-only", subjects)
        self.assertTrue(subjects["Group:system:masters"][1])
        self.assertTrue(subjects["ServiceAccount:kube-system/namespace-controller"][1])
        self.assertNotIn("User:far", subjects)                 # bound in another namespace
        self.assertNotIn("Group:system:authenticated", subjects)  # view reads no Secrets

    def test_debug_and_port_forward(self):
        r = nc.secret_readers("clickhouse", **{k: self.RBAC[k] for k in
                                               ("roles", "clusterroles", "rolebindings", "clusterrolebindings")})
        subjects = {s: how for s, how, _, _ in r}
        self.assertTrue(subjects["User:debugger"].startswith("runs code in pods"))
        self.assertIn("port-forward", subjects["User:forwarder"])

    def test_rule_access_edges(self):
        self.assertIsNotNone(nc.rule_access({"apiGroups": [""], "resources": ["pods/ephemeralcontainers"],
                                             "verbs": ["update"]}))
        self.assertIsNone(nc.rule_access({"apiGroups": [""], "resources": ["nodes/proxy"], "verbs": ["get"]},
                                         cluster=False))
        self.assertIsNone(nc.rule_access({"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]}))
        self.assertIsNotNone(nc.rule_access({"apiGroups": [""], "resources": ["services/proxy"], "verbs": ["get"]}))

    def test_connector_namespace_is_checked(self):
        accept = ["Group:developers", "ServiceAccount:of-server/ci", "User:oncall", "ServiceAccount:tools/helper",
                  "User:node-debugger", "User:debugger", "User:forwarder"]
        argv = ["--no-exec"] + [x for a in accept for x in ("--accept", a)]
        code, out = run(FakeCluster(rbac=self.RBAC), *argv)
        self.assertEqual(code, 0, out)
        code, out = run(FakeCluster(rbac=self.RBAC), *argv, "--connector-namespace", "cloudflared")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  cloudflared: User:tunnel-ops starts pods", out)

    def test_unexpected_reader_fails_and_accept_passes(self):
        code, out = run(FakeCluster(rbac=self.RBAC), "--no-exec")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  of-server: Group:developers reads Secrets", out)
        self.assertIn("PASS  of-server: User:deployer", out)
        self.assertNotIn("FAIL  of-server: Group:system:masters", out)
        self.assertIn("FAIL  of-server: ServiceAccount:tools/helper reads Secrets", out)
        code, out = run(FakeCluster(rbac=self.RBAC), "--no-exec", "--accept", "Group:developers",
                        "--accept", "ServiceAccount:of-server/ci", "--accept", "User:oncall",
                        "--accept", "ServiceAccount:tools/helper", "--accept", "User:node-debugger",
                        "--accept", "User:debugger", "--accept", "User:forwarder")
        self.assertEqual(code, 0, out)


@unittest.skipUnless(shutil.which("helm"), "helm is not installed")
class ChartPolicyTest(unittest.TestCase):
    CHART = os.path.join(ROOT, "deploy", "helm", "of-server")

    def render(self, values):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(values)
        try:
            return subprocess.run(["helm", "template", "t", self.CHART, "-f", f.name,
                                   "-s", "templates/networkpolicy.yaml"], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(f.name)

    def test_namespace_alone_is_refused(self):
        p = self.render("networkPolicy:\n  ingressFrom:\n"
                        "    - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: cloudflared}}\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("a namespaceSelector alone, or with an empty podSelector, admits every pod", p.stderr)

    def test_empty_pod_selector_is_refused(self):
        for sel in ("{}", "{matchLabels: {}}"):
            p = self.render("networkPolicy:\n  ingressFrom:\n"
                            "    - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: cloudflared}}\n"
                            f"      podSelector: {sel}\n")
            self.assertNotEqual(p.returncode, 0, sel)
            self.assertIn("an empty podSelector", p.stderr)

    def test_defaults_name_pods_and_dns(self):
        import yaml
        p = self.render("{}\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        spec = yaml.safe_load(p.stdout)["spec"]
        peer = spec["ingress"][0]["from"][0]
        self.assertIn("podSelector", peer)
        self.assertIn("namespaceSelector", peer)
        dns = [e for e in spec["egress"] if {"port": 53, "protocol": "UDP"} in e["ports"]][0]
        self.assertEqual(dns["to"][0]["podSelector"], {"matchLabels": {"k8s-app": "kube-dns"}})
        self.assertEqual(len(spec["egress"]), 2)               # ClickHouse and DNS; no git-sync rule

    def test_node_local_dns_and_git_sync(self):
        import yaml
        p = self.render("networkPolicy:\n  dnsTo:\n    - ipBlock: {cidr: 169.254.20.10/32}\n"
                        "grants:\n  gitSync: {enabled: true, repo: x, sshKeySecret: k}\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        spec = yaml.safe_load(p.stdout)["spec"]
        self.assertEqual(spec["egress"][1]["to"], [{"ipBlock": {"cidr": "169.254.20.10/32"}}])
        git = spec["egress"][2]["to"][0]["ipBlock"]
        self.assertEqual(git["cidr"], "0.0.0.0/0")
        self.assertIn("169.254.0.0/16", git["except"])


if __name__ == "__main__":
    unittest.main()
