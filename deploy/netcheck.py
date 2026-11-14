#!/usr/bin/env python3
"""Prove that no other pod reaches of-server or its ClickHouse (deploy/RUNBOOK.md §4.1). The organization's cluster
keeper runs it with kubectl after an install or a policy change; an OF position never runs it on an organization's
cluster (AGENTS.md, Framework and apps).

  python3 deploy/netcheck.py --namespace of-server --release of-server --clickhouse-namespace clickhouse \\
      --connector-namespace cloudflared --deployer User:<who deploys>

1. Policies select the pods: a NetworkPolicy with Ingress selects the server's pods, and one ClickHouse's; the
   names do not matter, so a keeper's own manifests count as well as deploy/clickhouse/clickhouse.yaml. Each of
   them admits only named pods: no rule without `from`, no empty pod or namespace selector, no /0 block.
2. The cluster enforces NetworkPolicy: in a scratch namespace a probe reaches an open target and is refused by a
   target behind a deny-all policy. A cluster whose network plugin ignores policies fails here, and then nothing
   below holds, whatever it prints.
3. A probe in the scratch namespace, and one in the server's own namespace, are refused by the server (its Service and
   its Ready pods) and by ClickHouse (its Service, and its Ready pods on every port ClickHouse listens on). A refusal
   counts only as the deny the control showed: where the plugin drops, a reset means the connection reached the pod.
   A probe in the scratch namespace carrying the pod labels the server's policy admits is refused too, so labels
   alone, outside the connector's namespace, get nobody in. In --connector-namespace (required), a pod that is not
   the connector is refused.
4. From inside the server's container: the server answers on its own loopback (it listens), ClickHouse answers; the
   scratch target and the API server do not.
5. Who RBAC lets read the Secrets of the server's, ClickHouse's and the connector's namespaces, or reach their pods
   past NetworkPolicy: a get, list or watch on Secrets; starting a pod (it mounts any Secret of its namespace, and in
   the connector's namespace it can carry the connector's labels); exec, attach or an ephemeral container in a pod
   (`kubectl debug`), which holds its Secrets mounted; nodes/proxy, bound cluster-wide (the kubelet runs commands in
   any pod); port-forward or a proxy through the API server. Every subject outside the API server's own default
   bindings (label kubernetes.io/bootstrapping: rbac-defaults) must be named with --deployer or --accept, or the check
   fails. Impersonation and role escalation are not followed.

Each probe is a TCP connect from a pod of the server's own image (python3), or of --image. Nothing secret is read or
printed: no Secret is fetched, only RBAC objects. Exit 0 when every line passes."""
import argparse
import json
import secrets
import subprocess
import sys

CLICKHOUSE_PORTS = (8123, 9000, 9004, 9005, 9009)
SERVER_PORT = 18200
TARGET_PORT = 8080

# Runs in the probe pods and in the server's container: one JSON line per target, never an exception.
PROBE = r"""
import json, socket, sys
timeout = float(sys.argv[2])
for label, host, port in json.loads(sys.argv[1]):
    try:
        socket.create_connection((host, int(port)), timeout).close()
        r = "open"
    except ConnectionRefusedError:
        r = "refused"
    except socket.gaierror:
        r = "unresolved"
    except (socket.timeout, TimeoutError):
        r = "timeout"
    except OSError as e:
        r = "error:" + type(e).__name__
    print(json.dumps({"label": label, "result": r}), flush=True)
"""

LISTEN = r"""
import socket
try:
    s = socket.create_server(("::", %d), family=socket.AF_INET6, dualstack_ipv6=True)
except OSError:
    s = socket.create_server(("", %d))
while True:
    c, _ = s.accept()
    c.close()
""" % (TARGET_PORT, TARGET_PORT)


class Kubectl:
    def __init__(self, context=None):
        self.base = ["kubectl"] + (["--context", context] if context else [])

    def run(self, args, stdin=None):
        """(returncode, stdout, stderr)."""
        p = subprocess.run(self.base + list(args), input=stdin, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def json(self, *args):
        code, out, err = self.run(list(args) + ["-o", "json"])
        if code:
            raise LookupError(err.strip().splitlines()[-1] if err.strip() else "kubectl failed")
        return json.loads(out)

    def ok(self, *args):
        return self.run(list(args))[0] == 0


class Report:
    def __init__(self, out=sys.stdout):
        self.out, self.failed = out, 0

    def line(self, passed, what, why=""):
        self.failed += 0 if passed else 1
        print(("PASS  " if passed else "FAIL  ") + what + (f": {why}" if why and not passed else ""), file=self.out)

    def note(self, text):
        print("      " + text, file=self.out)


DENY = ("timeout", "refused")


def judge(expect, result, deny=DENY):
    """A target meant to be open must answer; one meant to be closed must give DENY, how this cluster's plugin denies
    (the deny-all control's result: a drop times out, a reject is refused). Where the plugin drops, a refusal means the
    connection reached the pod and found nothing listening, which proves nothing about the policy (of_review on
    organic-framework PR 169). An unresolved name or another error proves nothing either way, so it fails."""
    if expect == "open":
        return result == "open"
    return result in deny


def probe_pod(name, ns, image, labels, command, ready_port=None):
    """A pod that passes the restricted Pod Security level, so it runs in a namespace that enforces it."""
    c = {"name": "probe", "image": image, "command": command,
         "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                             "runAsNonRoot": True, "runAsUser": 10001, "capabilities": {"drop": ["ALL"]},
                             "seccompProfile": {"type": "RuntimeDefault"}},
         "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"cpu": "200m", "memory": "96Mi"}}}
    if ready_port:
        c["readinessProbe"] = {"tcpSocket": {"port": ready_port}, "periodSeconds": 2}
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "labels": dict(labels, **{"of-netcheck": "probe"})},
            "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False, "enableServiceLinks": False,
                     "terminationGracePeriodSeconds": 1, "containers": [c]}}


def scratch_objects(scratch, image):
    """The scratch namespace, an open target, a target behind a deny-all policy, and that policy."""
    listen = ["python3", "-I", "-c", LISTEN]
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": scratch, "labels": {"of-netcheck": "scratch"}}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": "deny-closed", "namespace": scratch},
         "spec": {"podSelector": {"matchLabels": {"role": "closed"}}, "policyTypes": ["Ingress"]}},
        probe_pod("target-open", scratch, image, {"role": "open"}, listen, TARGET_PORT),
        probe_pod("target-closed", scratch, image, {"role": "closed"}, listen, TARGET_PORT),
    ]


def parse_results(log):
    out = {}
    for line in log.splitlines():
        try:
            d = json.loads(line)
            out[d["label"]] = d["result"]
        except (ValueError, KeyError, TypeError):
            continue
    return out


def selects(selector, labels):
    """Does a LabelSelector select a pod with LABELS? An empty selector selects every pod."""
    selector = selector or {}
    if any(labels.get(key) != v for key, v in (selector.get("matchLabels") or {}).items()):
        return False
    for e in selector.get("matchExpressions") or []:
        key, op, vals = e.get("key"), e.get("operator"), e.get("values") or []
        if (op == "In" and labels.get(key) not in vals) or (op == "NotIn" and labels.get(key) in vals) or \
                (op == "Exists" and key not in labels) or (op == "DoesNotExist" and key in labels):
            return False
    return True


def policies_for(k, ns, selector):
    """([NetworkPolicies with Ingress that select the pods SELECTOR names], number of pods)."""
    pods = k.json("get", "pods", "-n", ns, "-l", selector).get("items") or []
    pols = k.json("get", "networkpolicy", "-n", ns).get("items") or []
    chosen = {p["metadata"]["name"]: p for p in pols for pod in pods
              if "Ingress" in (p["spec"].get("policyTypes") or ["Ingress"])
              and selects(p["spec"].get("podSelector"), pod["metadata"].get("labels") or {})}
    return [chosen[n] for n in sorted(chosen)], len(pods)


def named(selector):
    """A LabelSelector that names something: an empty or missing one selects everything."""
    return isinstance(selector, dict) and bool(selector.get("matchLabels") or selector.get("matchExpressions"))


def peer_problems(policy):
    """Why a NetworkPolicy's ingress admits more than named pods (the chart refuses these at render; a keeper's own
    manifests are read here)."""
    out = []
    for i, rule in enumerate(policy["spec"].get("ingress") or []):
        peers = (rule or {}).get("from")
        if not peers:
            out.append(f"ingress[{i}] has no from: it admits every source")
            continue
        for peer in peers:
            if "ipBlock" in peer:
                cidr = str((peer.get("ipBlock") or {}).get("cidr") or "")
                if cidr.endswith("/0"):
                    out.append(f"ingress[{i}] admits every address ({cidr})")
            elif not named(peer.get("podSelector")):
                out.append(f"ingress[{i}] admits every pod of " + ("every namespace" if "namespaceSelector" in peer and
                           not named(peer.get("namespaceSelector")) else "a namespace"))
            elif "namespaceSelector" in peer and not named(peer.get("namespaceSelector")):
                out.append(f"ingress[{i}] admits pods with those labels in every namespace")
    return out


def admitted_labels(policies):
    """The pod labels the policies' ingress peers name, one dict per peer that names pods by labels a pod can carry."""
    out = []
    for p in policies:
        for rule in p["spec"].get("ingress") or []:
            for peer in (rule or {}).get("from") or []:
                sel = peer.get("podSelector")
                if not named(sel):
                    continue
                labels = dict(sel.get("matchLabels") or {})
                for e in sel.get("matchExpressions") or []:
                    if e.get("operator") == "In" and e.get("values"):
                        labels[e["key"]] = e["values"][0]
                    elif e.get("operator") == "Exists":
                        labels[e["key"]] = "x"
                if labels and labels not in out:
                    out.append(labels)
    return out


def ready(pod):
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in (pod.get("status") or {}).get("conditions") or [])


def pod_ips(k, ns, selector, rep=None, what=None):
    """[(name, ip)] of the Ready pods SELECTOR names. With REP, a pod that is not Ready fails a line: a probe refused
    by it would prove nothing (of_review on organic-framework PR 169)."""
    items = k.json("get", "pods", "-n", ns, "-l", selector).get("items") or []
    for p in items:
        if rep is not None and not ready(p):
            rep.line(False, f"{what} pod {ns}/{p['metadata']['name']} is Ready", "not Ready: a refusal from it proves "
                     "nothing; run the check again once it is")
    return [(p["metadata"]["name"], p["status"].get("podIP")) for p in items
            if p.get("status", {}).get("podIP") and ready(p)]


def run_probe(k, rep, ns, name, image, targets, timeout, soft=False, deny=DENY, labels=None, control=None):
    """Run one probe pod in NS over TARGETS [(label, host, port, expect)] and report each line. The first target is
    the control the probe must reach; with SOFT, a probe that reaches nothing (an egress policy of its own namespace)
    only notes that its lines are skipped, since a refusal from it would prove nothing. CONTROL names the target behind
    deny-all: its result is how this cluster denies, returned for the probes after it. LABELS: the probe pod's own."""
    probe = probe_pod(name, ns, image, dict(labels or {}, role="probe"),
                      ["python3", "-I", "-c", PROBE, json.dumps([t[:3] for t in targets]), str(timeout)])
    code, _, err = k.run(["apply", "-f", "-"], stdin=json.dumps(probe))
    if code:
        rep.line(False, f"probe pod {ns}/{name}", err.strip()[-300:])
        return deny
    wait = str(int(60 + len(targets) * (timeout + 1))) + "s"
    if not k.ok("wait", "-n", ns, "--for=jsonpath={.status.phase}=Succeeded", f"pod/{name}", f"--timeout={wait}"):
        try:
            st = k.json("get", "pod", name, "-n", ns).get("status") or {}
            why = "; ".join(f"{s['name']}: {next(iter(s.get('state') or {}), '?')} "
                            f"{(next(iter((s.get('state') or {}).values()), {}) or {}).get('reason', '')}".strip()
                            for s in st.get("containerStatuses") or []) or st.get("phase", "?")
        except LookupError as e:
            why = str(e)
        rep.line(False, f"probe pod {ns}/{name} did not finish", f"{why} (an image the cluster cannot pull: --image)")
        return deny
    code, log, _ = k.run(["logs", "-n", ns, f"pod/{name}"])
    got = parse_results(log)
    if soft and got.get(targets[0][0]) != "open":
        rep.note(f"from {ns}: skipped, the probe reaches nothing ({got.get(targets[0][0], 'no result')}; an egress "
                 "policy of that namespace); the scratch namespace's lines cover the same targets")
        return deny
    if control and got.get(control) in DENY:
        deny = (got[control],)
    _report(rep, f"from {ns}" + (" with the labels " + ",".join(f"{k_}={v}" for k_, v in sorted(labels.items()))
                                 if labels else ""), targets, got, deny)
    return deny


def _report(rep, source, targets, got, deny):
    for label, host, port, expect in targets:
        r = got.get(label, "no result")
        why = r if expect == "open" or r not in DENY else (
            f"{r}, but this cluster's plugin denies with {deny[0]}: the connection reached the pod" if len(deny) == 1
            else r)
        rep.line(judge(expect, r, deny), f"{source}: {label} {'answers' if expect == 'open' else 'is refused'}", why)


def exec_probe(k, rep, ns, deploy, targets, timeout, deny=DENY):
    code, log, err = k.run(["exec", "-n", ns, f"deploy/{deploy}", "-c", "of-server", "--", "python3", "-I", "-c",
                            PROBE, json.dumps([t[:3] for t in targets]), str(timeout)])
    if code:
        rep.line(False, f"exec into {ns}/{deploy}", err.strip()[-300:])
        return
    _report(rep, "from the server", targets, parse_results(log), deny)


# ------------------------------------------------------------------ RBAC

_CORE = {"", "*"}
_WORKLOAD_GROUPS = {"", "apps", "batch", "*"}
_WORKLOADS = {"pods", "deployments", "replicasets", "statefulsets", "daemonsets", "jobs", "cronjobs",
              "replicationcontrollers"}
# A subresource of a pod that runs code inside it, with the verbs that do (`kubectl debug` patches
# pods/ephemeralcontainers; of_secrets-review on organic-framework PR 169).
_INTO_POD = {"pods/exec": {"create", "get", "*"}, "pods/attach": {"create", "get", "*"},
             "pods/ephemeralcontainers": {"create", "get", "update", "patch", "*"}}
_THROUGH_API = {"pods/portforward", "pods/proxy", "services/proxy"}


def rule_access(rule, cluster=True):
    """How a PolicyRule lets its holder read a namespace's Secrets or reach its pods past NetworkPolicy, or None.
    CLUSTER: the rule is bound cluster-wide (a ClusterRoleBinding), the only way nodes/proxy is granted."""
    groups, res, verbs = (set(rule.get(k) or []) for k in ("apiGroups", "resources", "verbs"))
    named = rule.get("resourceNames")
    core = groups & _CORE
    if core and res & {"secrets", "*"} and verbs & {"get", "list", "watch", "*"}:
        return "reads Secrets" + (f" (only {', '.join(named)})" if named else "")
    if groups & _WORKLOAD_GROUPS and res & (_WORKLOADS | {"*"}) and verbs & {"create", "update", "patch", "*"}:
        return "starts pods, which mount any Secret and carry any labels"
    if core and any(r in res and verbs & v for r, v in _INTO_POD.items()):
        return "runs code in pods (exec, attach or kubectl debug), which hold mounted Secrets"
    if cluster and core and "nodes/proxy" in res and verbs & {"get", "create", "*"}:
        return "runs code in any pod through the kubelet (nodes/proxy)"
    if core and res & _THROUGH_API and verbs:
        return "reaches pods through the API server (port-forward or proxy), past NetworkPolicy"
    return None


def subject_key(s, binding_ns=None):
    if s.get("kind") == "ServiceAccount":
        return f"ServiceAccount:{s.get('namespace') or binding_ns}/{s.get('name')}"
    return f"{s.get('kind')}:{s.get('name')}"


def bootstrap(binding):
    """The API server's own default bindings: the label it puts on them, and only system identities bound (a name
    proves nothing, and a label can be copied; of_secrets-review and of_review on organic-framework PR 169)."""
    labels = binding["metadata"].get("labels") or {}
    subjects = binding.get("subjects") or []
    return labels.get("kubernetes.io/bootstrapping") == "rbac-defaults" and bool(subjects) and all(
        (s.get("kind") in ("User", "Group") and str(s.get("name") or "").startswith("system:"))
        or (s.get("kind") == "ServiceAccount" and s.get("namespace") == "kube-system") for s in subjects)


def secret_readers(namespace, roles, clusterroles, rolebindings, clusterrolebindings):
    """[(subject, how, binding, bootstrap)] of everyone RBAC lets read NAMESPACE's Secrets or reach its pods (rule_access)."""
    croles = {r["metadata"]["name"]: r for r in clusterroles}
    nroles = {r["metadata"]["name"]: r for r in roles if r["metadata"].get("namespace") == namespace}
    out = []

    def add(binding, role, where, cluster):
        if not role:
            return
        hows = sorted({h for h in (rule_access(r, cluster) for r in role.get("rules") or []) if h})
        for s in binding.get("subjects") or []:
            for how in hows:
                out.append((subject_key(s, binding["metadata"].get("namespace")), how, where, bootstrap(binding)))
    for b in clusterrolebindings:
        add(b, croles.get(b.get("roleRef", {}).get("name")), f"ClusterRoleBinding {b['metadata']['name']}", True)
    for b in rolebindings:
        if b["metadata"].get("namespace") != namespace:
            continue
        ref = b.get("roleRef", {})
        role = croles.get(ref.get("name")) if ref.get("kind") == "ClusterRole" else nroles.get(ref.get("name"))
        add(b, role, f"RoleBinding {namespace}/{b['metadata']['name']}", False)
    return out


def check_rbac(k, rep, namespaces, deployer, accept):
    try:
        objs = {kind: k.json("get", kind, *([] if kind.startswith("cluster") else ["-A"])).get("items") or []
                for kind in ("roles", "clusterroles", "rolebindings", "clusterrolebindings")}
    except LookupError as e:
        rep.line(False, "RBAC objects readable", str(e))
        return
    for ns in namespaces:
        readers = secret_readers(ns, objs["roles"], objs["clusterroles"], objs["rolebindings"],
                                 objs["clusterrolebindings"])
        boot = sorted({s for s, _, _, b in readers if b})
        if boot:
            rep.note(f"{ns}: the API server's default bindings also reach it: {', '.join(boot)}")
        seen = set()
        for subject, how, where, is_boot in sorted(readers):
            if is_boot or (subject, how) in seen:
                continue
            seen.add((subject, how))
            if subject in deployer:
                rep.line(True, f"{ns}: {subject} {how} (the deployer; {where})")
            elif subject in accept:
                rep.line(True, f"{ns}: {subject} {how} (accepted by the keeper; {where})")
            else:
                rep.line(False, f"{ns}: {subject} {how} ({where})",
                         "only the deployer may; remove the binding, or name the subject with --accept")
        if not any(s in deployer for s, _, _, _ in readers) and deployer:
            rep.note(f"{ns}: no RBAC binding names the deployer; it reads Secrets as a cluster administrator")


# ------------------------------------------------------------------ main

def main(argv=None, k=None, out=sys.stdout):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--namespace", default="of-server", help="where of-server is released")
    ap.add_argument("--release", default="of-server", help="the Helm release name")
    ap.add_argument("--clickhouse-namespace", default="clickhouse",
                    help="ClickHouse's namespace (deploy/clickhouse/clickhouse.yaml); 'none' when it is outside")
    ap.add_argument("--clickhouse-service", default="clickhouse", help="ClickHouse's Service name")
    ap.add_argument("--clickhouse-selector", default="app=clickhouse", help="ClickHouse's pod labels, k=v,…")
    ap.add_argument("--connector-namespace", help="the namespace whose pods the server admits (the tunnel connector's "
                    "or the ingress controller's): required; a stranger pod there is refused")
    ap.add_argument("--image", help="an image with python3 for the probes (default: the server's own)")
    ap.add_argument("--context", help="the kubectl context")
    ap.add_argument("--deployer", action="append", default=[], help="Kind:name allowed to read Secrets (repeat)")
    ap.add_argument("--accept", action="append", default=[], help="another Kind:name the keeper accepts (repeat)")
    ap.add_argument("--no-exec", action="store_true", help="skip the probe from inside the server's container")
    ap.add_argument("--keep", action="store_true", help="keep the scratch namespace and probe pods")
    ap.add_argument("--timeout", type=float, default=3.0, help="seconds per connect")
    a = ap.parse_args(argv)
    k = k or Kubectl(a.context)
    rep = Report(out)
    ns, chns = a.namespace, (None if a.clickhouse_namespace == "none" else a.clickhouse_namespace)
    server = f"{a.release}-of-server"
    try:
        dep = k.json("get", "deployment", server, "-n", ns)
    except LookupError as e:
        rep.line(False, f"deployment {ns}/{server}", str(e))
        return 1
    image = a.image or next((c["image"] for c in dep["spec"]["template"]["spec"]["containers"]
                             if c["name"] == "of-server"), None)

    if not a.connector_namespace:
        rep.line(False, "--connector-namespace given", "name the namespace whose pods the server admits, so a stranger "
                 "pod there is probed")
    labels = ",".join(f"{key}={v}" for key, v in sorted(dep["spec"]["selector"]["matchLabels"].items()))
    server_policies = []
    for pns, sel, what in [(ns, labels, "the server's pods")] + ([(chns, a.clickhouse_selector, "ClickHouse's pods")]
                                                                  if chns else []):
        try:
            pols, n = policies_for(k, pns, sel)
        except LookupError as e:
            pols, n = [], 0
            rep.line(False, f"NetworkPolicies in {pns}", str(e))
        names = [p["metadata"]["name"] for p in pols]
        rep.line(bool(pols and n), f"{what} in {pns} are selected by a NetworkPolicy with Ingress"
                 + (f" ({', '.join(names)})" if names else ""), "no pods" if not n else "none")
        for p in pols:
            why = peer_problems(p)
            rep.line(not why, f"NetworkPolicy {pns}/{p['metadata']['name']} admits only named pods", "; ".join(why))
        if pns == ns:
            server_policies = pols
    denied = [(f"server Service {server}.{ns}.svc:{SERVER_PORT}", f"{server}.{ns}.svc", SERVER_PORT, "closed")]
    server_pods = pod_ips(k, ns, labels, rep, "server")
    denied += [(f"server pod {n}:{SERVER_PORT}", ip, SERVER_PORT, "closed") for n, ip in server_pods]
    if chns:
        chs = f"{a.clickhouse_service}.{chns}.svc"
        ch = [(f"ClickHouse Service {chs}:8123", chs, 8123, "closed")]
        ch_pods = pod_ips(k, chns, a.clickhouse_selector, rep, "ClickHouse")
        ch += [(f"ClickHouse pod {n}:{p}", ip, p, "closed") for n, ip in ch_pods for p in CLICKHOUSE_PORTS]
        if not ch_pods:
            rep.line(False, f"ClickHouse pods in {chns}", "none Ready")
    else:
        ch = []
    if not server_pods:
        rep.line(False, f"server pods in {ns}", "none Ready")

    scratch = "of-netcheck-" + secrets.token_hex(3)
    stranger = f"of-netcheck-{scratch[-6:]}"
    made = []
    try:
        code, _, err = k.run(["apply", "-f", "-"], stdin=json.dumps(
            {"apiVersion": "v1", "kind": "List", "items": scratch_objects(scratch, image)}))
        made.append(("namespace", scratch, None))
        if code:
            rep.line(False, f"scratch namespace {scratch}", err.strip()[-300:])
            return 1
        ready = k.ok("wait", "-n", scratch, "--for=condition=Ready", "pod/target-open", "pod/target-closed",
                     "--timeout=180s")
        ips = dict(pod_ips(k, scratch, "of-netcheck=probe"))
        if not ready or not ips.get("target-open") or not ips.get("target-closed"):
            rep.line(False, "scratch targets ready", "they did not start (an image the cluster cannot pull: --image)")
            return 1
        control = [("an open target (the probe can connect)", ips["target-open"], TARGET_PORT, "open"),
                   ("a target behind deny-all (the cluster enforces NetworkPolicy)", ips["target-closed"],
                    TARGET_PORT, "closed")]
        deny = run_probe(k, rep, scratch, "probe", image, control + denied + ch, a.timeout, control=control[1][0])
        for i, lab in enumerate(admitted_labels(server_policies)):
            # labels alone, outside the namespace the policy pins, must get nobody in (of_review on PR 169)
            run_probe(k, rep, scratch, f"probe-labels-{i}", image, control[:1] + denied, a.timeout, deny=deny,
                      labels=lab)
        made.append(("pod", stranger, ns))
        run_probe(k, rep, ns, stranger, image, control[:1] + ch + denied, a.timeout, soft=True, deny=deny)
        if a.connector_namespace:
            made.append(("pod", stranger, a.connector_namespace))
            run_probe(k, rep, a.connector_namespace, stranger, image, control[:1] + denied, a.timeout, soft=True,
                      deny=deny)
        if not a.no_exec:
            mine = [(f"the server itself 127.0.0.1:{SERVER_PORT} (it listens)", "127.0.0.1", SERVER_PORT, "open")]
            mine += [(f"ClickHouse {chs}:8123", chs, 8123, "open")] if chns else []
            mine += [("the scratch target", ips["target-open"], TARGET_PORT, "closed"),
                     ("the API server kubernetes.default.svc:443", "kubernetes.default.svc", 443, "closed")]
            exec_probe(k, rep, ns, server, mine, a.timeout, deny=deny)
    finally:
        if a.keep:
            rep.note(f"kept: namespace {scratch}" + "".join(f", pod {n}/{p}" for kind, p, n in made if kind == "pod"))
        else:
            for kind, name, pns in reversed(made):
                k.run(["delete", kind, name, "--wait=false", "--ignore-not-found"] + (["-n", pns] if pns else []))
    check_rbac(k, rep, [ns] + ([chns] if chns else []) + ([a.connector_namespace] if a.connector_namespace else []),
               set(a.deployer), set(a.accept))
    print(f"{'FAILED' if rep.failed else 'PASSED'}: {rep.failed} failing line(s)", file=out)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
