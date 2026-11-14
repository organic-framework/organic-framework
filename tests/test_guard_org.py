"""Organization rules on kubectl (docs/guard.md, O1): which cluster a kubectl call reaches, with which credential and
into which pod, and an organization's rule over it. A user organization asked on 2026-11-08 (msg 01a1bd17)
after a position used the cluster-admin default kubeconfig to exec job starts into a CI runner pod."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_guard  # noqa: E402

ADMIN = {"configs": ["~/.kube/config"], "contexts": ["acme-k8s"], "users": ["admin"]}
RUNNER = {"id": "runner-exec", "mode": "warn", "reason": "runner work goes through the runner gate",
          "except": ["runners_keeper", "runners_coordinator"],
          "kubectl": {"verbs": ["exec", "attach", "cp", "debug", "port-forward"], "pods": ["runner-*"]}}
KUBE_ADMIN = {"id": "kube-admin", "mode": "warn", "reason": "a position uses its own kubeconfig",
              "except": ["runners_keeper", "runners_coordinator"], "kubectl": {"admin": ADMIN}}


def calls(cmd, env=None):
    return [(c["verb"], c["targets"], c["config_from"]) for c in of_guard.kubectl_calls(cmd, env)]


def hits(cmd, position="returns_keeper", env=None):
    return [r["id"] for r, _ in of_guard.org_rule_hits("Bash", {"command": cmd}, [RUNNER, KUBE_ADMIN], position, env)]


class KubectlCallsTest(unittest.TestCase):
    def test_credential_in_effect(self):
        self.assertEqual(calls("kubectl get pods"), [("get", [], "default")])
        self.assertEqual(calls("kubectl --kubeconfig ~/k/own.yaml get pods"), [("get", [], "--kubeconfig")])
        self.assertEqual(calls("kubectl --kubeconfig=~/k/own.yaml get pods"), [("get", [], "--kubeconfig")])
        self.assertEqual(calls("KUBECONFIG=~/k/own.yaml kubectl get jobs -w"), [("get", [], "KUBECONFIG")])
        self.assertEqual(calls("env KUBECONFIG=~/k/own.yaml timeout 30 kubectl get jobs"), [("get", [], "KUBECONFIG")])
        self.assertEqual(calls("export KUBECONFIG=$HOME/k/own.yaml && kubectl get ns; kubectl get pods"),
                         [("get", [], "KUBECONFIG")] * 2)
        self.assertEqual(calls("kubectl get ns", env={"KUBECONFIG": "/k/own.yaml"}), [("get", [], "KUBECONFIG")])
        self.assertEqual(calls("unset KUBECONFIG; kubectl get ns", env={"KUBECONFIG": "/k/own.yaml"}),
                         [("get", [], "default")])
        self.assertEqual(calls("KUBECONFIG=/k/own.yaml; kubectl get ns"), [("get", [], "default")])  # not exported

    def test_targets(self):
        self.assertEqual(calls("kubectl -n runners exec -it runner-7 -c main -- sh")[0][1], ["runner-7"])
        self.assertEqual(calls("kubectl exec pod/runner-7 -- ls")[0][1], ["runner-7"])
        self.assertEqual(calls("kubectl cp runners/runner-1:/tmp/x ./x")[0][1], ["runner-1"])
        self.assertEqual(calls("kubectl port-forward pod/runner-2 8080:80")[0][1], ["runner-2"])
        self.assertEqual(calls("kubectl logs runner-2")[0][1], [])

    def test_what_runs_locally_is_followed_and_what_runs_elsewhere_is_not(self):
        self.assertEqual(calls('bash -c "kubectl get ns"'), [("get", [], "default")])
        self.assertEqual(calls("for p in $(kubectl get pods -o name); do kubectl exec $p -- ls; done"),
                         [("get", [], "default"), ("exec", ["$p"], "default")])
        self.assertEqual(calls("bash <<'EOF'\nkubectl get ns\nEOF"), [("get", [], "default")])
        self.assertEqual(calls("ssh gw kubectl get pods"), [])
        self.assertEqual(calls('kubectl --kubeconfig /k/own.yaml exec runner-1 -- kubectl get pods'),
                         [("exec", ["runner-1"], "--kubeconfig")])
        self.assertEqual(calls('of send acme_org "kubectl exec runner-1 -- sh"'), [])
        self.assertEqual(calls('git commit -m "kubectl --context acme-k8s get ns"'), [])
        self.assertEqual(calls("cat > notes.md <<'EOF'\nkubectl exec runner-1\nEOF"), [])


class KindsTest(unittest.TestCase):
    """The verb and resource kinds of a call, to size a narrow Role (asked for a k8s_keeper, 2026-11-09)."""

    def kinds(self, cmd):
        c = of_guard.kubectl_calls(cmd)[0]
        return c["verb_label"], c["kinds"]

    def test_kinds(self):
        self.assertEqual(self.kinds("kubectl -n evm get pods,svc"), ("get", ["pods", "services"]))
        self.assertEqual(self.kinds("kubectl logs --tail 50 -n x deploy/api"), ("logs", ["deployments", "pods"]))
        self.assertEqual(self.kinds("kubectl -n x exec -it p -- sh"), ("exec", ["pods"]))
        self.assertEqual(self.kinds("kubectl apply -f k.yaml"), ("apply", ["(from file)"]))
        self.assertEqual(self.kinds("kubectl rollout restart deploy/x -n y"), ("rollout restart", ["deployments"]))
        self.assertEqual(self.kinds("kubectl get $R"), ("get", ["unparsed"]))
        self.assertEqual(self.kinds("kubectl drain node1"), ("drain", ["nodes"]))
        self.assertEqual(self.kinds("kubectl wait --for=condition=complete job/x"), ("wait", ["jobs"]))
        self.assertEqual(self.kinds("kubectl auth can-i get pods"), ("auth can-i", ["(none)"]))
        self.assertEqual(self.kinds("kubectl get jobs.batch -n z"), ("get", ["jobs"]))
        self.assertTrue(of_guard.kubectl_calls("kubectl top pods -A")[0]["all_namespaces"])


SECRETS = {"id": "signing-secrets", "mode": "deny", "reason": "signing keys", "except": [],
           "kubectl": {"verbs": ["get", "describe"], "kinds": ["Secret"], "namespaces": ["signer"], "admin": ADMIN}}


class KindsRuleTest(unittest.TestCase):
    """O1's `kinds` and namespaces read as a reader would (a user organization's signing-key Secrets, msg
    01a1d7ac-c34b; of_architect's six conditions, msg 01a1d7b2-a99a)."""

    def hit(self, cmd, rule=SECRETS):
        return [r["id"] for r, _ in of_guard.org_rule_hits("Bash", {"command": cmd}, [rule], "x")]

    def test_kinds_are_folded_on_both_sides(self):
        for k in ("secret", "Secret", "secrets", "secrets.v1", "secrets.v1.", "SECRETS"):
            self.assertEqual(of_guard.fold_kind(k), ("secrets", True), k)
        self.assertEqual(of_guard.fold_kind("deployments.v1.apps"), ("deployments", True))
        self.assertEqual(of_guard.fold_kind("externalsecrets.external-secrets.io"),
                         ("externalsecrets.external-secrets.io", True))  # a custom resource keeps its group
        self.assertFalse(of_guard.fold_kind("secertss")[1])               # a typo: registry check warns
        self.assertEqual(self.hit("kubectl get secrets.v1 -n signer"), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl get externalsecrets -n signer"), [])
        crd = dict(SECRETS, kubectl=dict(SECRETS["kubectl"], kinds=["externalsecrets.external-secrets.io"]))
        self.assertEqual(self.hit("kubectl get externalsecrets -n signer", crd), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl get secrets -n signer", crd), [])

    def test_the_kind_must_match(self):
        self.assertEqual(self.hit("kubectl get secrets -n signer"), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl describe secret k -n signer"), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl get pods -n signer"), [])
        self.assertEqual(self.hit("kubectl get secrets -n other"), [])
        self.assertEqual(self.hit("kubectl get $KIND -n signer"), ["signing-secrets"])      # a false positive over a miss
        self.assertEqual(self.hit("kubectl get --raw /api/v1/namespaces/signer/secrets"), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl get --raw /api/v1/namespaces/other/secrets"), [])
        for path in ("/api/v1/namespaces/signer/%73ecrets/k", "/api/v1/namespaces/signer/secrets%2Fk",
                     "/api/v1/watch/namespaces/signer/secrets"):
            self.assertEqual(self.hit(f"kubectl get --raw {path}"), ["signing-secrets"], path)
        for cmd in ('P=/api/v1/namespaces/signer/secrets/k; kubectl get --raw "$P"', 'kubectl get --raw "$P"'):
            self.assertEqual(self.hit(cmd), ["signing-secrets"], cmd)               # unparsed: a match
        self.assertEqual(self.hit("kubectl get --raw /healthz"), [])                 # names no object

    def test_every_namespace_and_none_named(self):
        self.assertEqual(self.hit("kubectl get secrets -A"), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl get secrets"), ["signing-secrets"])               # the kubeconfig's namespace
        nodes = dict(SECRETS, kubectl={"verbs": ["get"], "namespaces": ["signer"], "admin": ADMIN})
        self.assertEqual(self.hit("kubectl get nodes", nodes), [])                            # cluster-scoped only
        self.assertEqual(self.hit("kubectl get pods", nodes), ["signing-secrets"])
        said = of_guard.org_rule_hits("Bash", {"command": "kubectl get secrets"}, [SECRETS], "x")[0][1]
        self.assertIn("give -n", said)

    def test_what_reaches_pods_and_service_accounts(self):
        pods = dict(SECRETS, kubectl={"kinds": ["pods"], "namespaces": ["signer"]})
        for cmd in ("kubectl logs -n signer deploy/x", "kubectl exec -n signer deploy/x -- id",
                    "kubectl port-forward -n signer svc/x 8080", "kubectl run t -n signer --image=busybox",
                    "kubectl get all -n signer", "kubectl get --raw /api/v1/namespaces/signer/pods/p/log"):
            self.assertEqual(self.hit(cmd, pods), ["signing-secrets"], cmd)
        sa = dict(SECRETS, kubectl={"kinds": ["serviceaccounts"], "namespaces": ["signer"]})
        self.assertEqual(self.hit("kubectl create token signer-sa -n signer", sa), ["signing-secrets"])
        self.assertEqual(self.hit("kubectl get all -n signer"), [])                           # all holds no Secrets


SSH = {"id": "shared-key", "mode": "deny", "reason": "the shared agent key", "except": ["hosts_keeper"],
       "ssh": {"hosts": ["us", "192.0.2.5", "gw*", "fra.example.net"]}}


class SshRuleTest(unittest.TestCase):
    """O3: an ssh destination scope (a user organization's shared agent key, msg 01a1d7b4-ac9f; of_architect's five
    conditions, msg 01a1d7bd-cd8c): a false positive over a miss, aliases folded from the ssh config."""

    ALIASES = {"us-alias": "192.0.2.5", "fra": "fra.example.net"}

    def hit(self, cmd, position="returns_keeper", resolver=None):
        return [m for _, m in of_guard.org_rule_hits(
            "Bash", {"command": cmd}, [SSH], position,
            resolver=resolver or (lambda cfg: lambda n: self.ALIASES.get(n)))]

    def test_what_reaches_a_denied_host(self):
        for cmd in ("ssh US", "ssh -J gw2 other", "ssh -J other,gw7 x", "ssh -o HostName=192.0.2.5 anything",
                    "ssh -oProxyJump=gw1,x y", "ssh -o 'ProxyCommand ssh -W %h:%p gw3' inner", "ssh -W us:22 other",
                    "scp f us:/tmp/", "scp -r ./a [192.0.2.5]:/x", "scp ./a scp://u@us:2222/x",
                    "rsync -av -e 'ssh -J gw2' ./d other:/d", "rsync -av ./d us::mod", "rsync rsync://us/mod ./x",
                    "sftp sftp://u@US:2222/home", "mosh --ssh='ssh -J gw2' other", "sshfs us:/data /mnt/x",
                    "autossh -M 0 -N us", "/usr/bin/ssh -p 22 us", "timeout 5 ssh -tt us", "bash -c 'ssh us'"):
            self.assertTrue(self.hit(cmd), cmd)

    def test_option_clusters_are_read_the_getopt_way(self):
        """of_review and of_secrets-review on organic-framework PR 152: `-vp 22 us` took 22 for the destination."""
        for cmd in ("ssh -vp 22 us", "ssh -vl root us", "ssh -vi ~/.ssh/k us", "ssh -4vJ other us", "ssh -P tag us",
                    "autossh -M 0 -vp 22 us", "sftp -vP 22 us", "ssh -4p 22 us", "ssh -Ap 2222 us",
                    "rsync -avze 'ssh -J us' f ok.example.org:/tmp", "ssh us."):
            self.assertTrue(self.hit(cmd), cmd)
        self.assertEqual(self.hit("ssh -4vJ gw1 other"), ["ssh via the jump host gw1"])
        self.assertEqual(self.hit("ssh -vp 22 other"), [])

    def test_the_configs_own_jumps_and_what_hides_them(self):
        """of_secrets-review on PR 152: a bastion alias's ProxyJump reached a denied host; a FIFO given to -F hung the
        hook; a Match block can send an alias anywhere."""
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as t:
            cfg = os.path.join(t, "config")
            with open(cfg, "w") as fh:
                fh.write("Host bastion-alias\n  HostName ok.example.org\n  ProxyJump us\n"
                         "Host viacmd\n  ProxyCommand ssh -W %h:%p gw7\n")
            hit = lambda cmd: self.hit(cmd, resolver=of_guard.ssh_resolver)  # noqa: E731
            self.assertEqual(hit(f"ssh -F {cfg} bastion-alias"), ["ssh to bastion-alias (via the jump host us)"])
            self.assertEqual(hit(f"ssh -F {cfg} viacmd"), ["ssh to viacmd (in its ProxyCommand gw7)"])
            self.assertEqual(hit(f"ssh -F {cfg} plain.example.org"), [])
            match = os.path.join(t, "match")
            with open(match, "w") as fh:
                fh.write("Match host mm\n  HostName us\n")
            self.assertIn("a Match block sets hostname", hit(f"ssh -F {match} mm")[0])
            fifo = os.path.join(t, "fifo")
            os.mkfifo(fifo)
            t0 = time.time()
            self.assertIn("not a plain file", hit(f"ssh -F {fifo} anywhere")[0])
            self.assertLess(time.time() - t0, 2)                                         # never blocks

    def test_an_alias_is_folded_to_its_hostname(self):
        self.assertEqual(self.hit("ssh us-alias"), ["ssh to us-alias (HostName 192.0.2.5)"])
        self.assertEqual(self.hit("ssh fra uptime"), ["ssh to fra (HostName fra.example.net)"])

    def test_a_variable_counts_as_a_match_where_its_literal_parts_fit(self):
        """A bare variable can be any host; `agent-$h-node` only what fits agent-*-node (a user organization, msg
        01a1d7c9-9bc8: its loops over two hosts were denied by every other host group's rule)."""
        self.assertEqual(self.hit("ssh $HOST uptime"), ["ssh to *, a destination held in a variable that may be us"])
        self.assertTrue(self.hit("scp f \"$H\":/tmp/"))
        self.assertTrue(self.hit("ssh $(cat h) uptime"))
        self.assertEqual(self.hit("for h in a b; do ssh agent-$h-node uptime; done"), [])   # never us, gw*, fra…
        self.assertTrue(self.hit("ssh u@gw-${N}.lan"))                                       # fits gw*
        self.assertTrue(self.hit("ssh 192.0.2.$i"))                                           # fits 192.0.2.5
        self.assertEqual(self.hit("ssh 198.51.100.$i"), [])

    def test_an_alias_a_variable_can_fit_is_folded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            cfg = os.path.join(t, "config")
            with open(cfg, "w") as fh:
                fh.write("Host agent-us-node\n  HostName us.example.net\nHost agent-x-node\n  HostName 192.0.2.5\n")
            got = self.hit(f"ssh -F {cfg} agent-$h-node", resolver=of_guard.ssh_resolver)
        self.assertEqual(got, ["ssh to agent-*-node, a destination held in a variable that may be 192.0.2.5 "
                               "(HostName 192.0.2.5)"])

    def test_a_long_word_never_loses_the_lines_rules(self):
        """of_review and of_secrets-review on PR 152: _globs_meet recursed per character, a 1,200-character word with a
        variable raised RecursionError, and every organization rule of that line was lost."""
        long = "gw" + "x" * 5000 + "$h"
        self.assertTrue(self.hit(f"ssh {long} id"))
        self.assertTrue(self.hit(f'scp f "{"y" * 3000}$H:/tmp"'))                   # past the limit: a match
        both = [r["id"] for r, _ in of_guard.org_rule_hits(
            "Bash", {"command": f"ssh {long}; kubectl get secrets -n signer"}, [SSH, SECRETS], "x",
            resolver=lambda cfg: lambda n: None)]
        self.assertEqual(both, ["shared-key", "signing-secrets"])
        boom = dict(SSH, ssh={"hosts": 5})                                            # a rule that raises
        said = of_guard.org_rule_hits("Bash", {"command": "ssh us"}, [boom, SECRETS], "x")
        self.assertIn("counted as a match", said[0][1])

    def test_globs_meet(self):
        for a, b, want in (("agent-*-node", "agent-us-node", True), ("agent-*-node", "bastion-node", False),
                           ("*", "anything", True), ("gw*", "gw-*.lan", True), ("a?c", "abc", True),
                           ("a*c", "b*", False), ("192.0.2.*", "192.0.2.5", True), ("x*y", "*z", False),
                           ("a**b", "a*b", True), ("", "", True), ("", "*", True), ("*", "", True), ("?", "", False),
                           ("a" * 3000 + "*", "b", True)):                             # past the limit
            self.assertEqual(of_guard._globs_meet(a, b), want, (a, b))

    def test_what_does_not(self):
        for cmd in ("ssh other", "ssh other 'ssh us'", "scp ./a:b ./c", "git push origin main", "echo ssh us",
                    "of send x 'ssh us later'", "cat ~/.ssh/config"):
            self.assertEqual(self.hit(cmd), [], cmd)
        self.assertEqual(self.hit("ssh us", position="hosts_keeper"), [])                  # excepted

    def test_the_config_in_effect_is_read_never_run(self):
        """Host and HostName, Include followed, Match skipped (its exec runs a command; ssh -G would run it)."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            inc = os.path.join(t, "agents.conf")
            with open(inc, "w") as fh:
                fh.write("Host US us-east\n  HostName 192.0.2.5\n  IdentityFile ~/.ssh/shared\n")
            cfg = os.path.join(t, "config")
            with open(cfg, "w") as fh:
                fh.write(f"Include {inc}\nMatch exec \"touch {t}/ran\"\n  User nobody\n"
                         "Host !secret *.corp\n  HostName %h.example.net\nHost *\n  ServerAliveInterval 30\n")
            resolve = of_guard.ssh_resolver(cfg)
            self.assertEqual(resolve("us-east"), "192.0.2.5")
            self.assertEqual(resolve("fra.corp"), "fra.corp.example.net")
            self.assertIsNone(resolve("secret"))
            self.assertFalse(os.path.exists(os.path.join(t, "ran")))                        # nothing was run
            self.assertEqual(self.hit(f"ssh -F {cfg} us-east", resolver=of_guard.ssh_resolver),
                             ["ssh to us-east (HostName 192.0.2.5)"])


class OrgRuleTest(unittest.TestCase):
    def test_exec_into_a_runner_pod(self):
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml exec -it runner-7 -- sh"),
                         ["runner-exec"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml cp ./job.json runners/runner-7:/tmp/"),
                         ["runner-exec"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml logs runner-7"), [])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml exec -it db-0 -- psql"), [])

    def test_allowed_execs_are_out_of_the_rule(self):
        """A user organization, 2026-11-11: exactly two typed execs into its runners stay allowed."""
        rule = dict(RUNNER, kubectl={"verbs": ["exec", "cp"], "namespaces": ["runners-jobs"], "allow_exec": [
            {"argv": ["/root/opt/gate/bin/sshd-i"], "pods": ["runner-w*"]},
            {"argv": ["/usr/sbin/sshd", "-i", "-e", "-f", "/opt/gate/sshd_config"], "pods": ["runner-0"]}]})
        ids = lambda cmd: [r["id"] for r, _ in of_guard.org_rule_hits("Bash", {"command": cmd}, [rule], "x")]  # noqa
        self.assertEqual(ids("kubectl -n runners-jobs exec -i runner-w3 -- /root/opt/gate/bin/sshd-i"), [])
        self.assertEqual(ids("kubectl -n runners-jobs exec -i runner-0 -- /usr/sbin/sshd -i -e -f "
                             "/opt/gate/sshd_config"), [])
        for flagged in ("kubectl -n runners-jobs exec -i runner-0 -- /root/opt/gate/bin/sshd-i",   # wrong pod
                        "kubectl -n runners-jobs exec -i runner-w3 -- /root/opt/gate/bin/sshd-i -o x",  # more args
                        "kubectl -n runners-jobs exec -i runner-w3 -- sh -c /root/opt/gate/bin/sshd-i",
                        "kubectl -n runners-jobs exec -i $POD -- /root/opt/gate/bin/sshd-i",     # pod unknown
                        "kubectl -n runners-jobs exec -i runner-w3 -- $GATE",
                        "kubectl -n runners-jobs cp ./f runner-w3:/tmp/"):
            self.assertEqual(ids(flagged), ["runner-exec"], flagged)
        import of_grants
        g = {"effect": "deny", "subject": "*", "issuer": "tenant", "scope": {"kubectl": rule["kubectl"]}}
        self.assertEqual(of_grants.shape_problems(g), [])
        bad = dict(g, scope={"kubectl": {"verbs": ["exec"], "allow_exec": [{"argv": "/usr/sbin/sshd -i"}]}})
        self.assertIn("scope kubectl.allow_exec is a list of {argv: [strings], pods?: [globs]}",
                      of_grants.shape_problems(bad))

    def test_a_namespace_sees_a_pod_held_in_a_variable(self):
        rule = dict(RUNNER, kubectl={"verbs": ["exec", "cp"], "namespaces": ["runners-jobs"]})
        hit = of_guard.org_rule_hits("Bash", {"command": "POD=$(kubectl -n runners-jobs get pod -o name | head -1); "
                                                         "kubectl -n runners-jobs exec $POD -- sh"}, [rule], "x")
        self.assertEqual([said for _, said in hit], ["kubectl exec in namespace runners-jobs"])
        self.assertEqual(of_guard.org_rule_hits("Bash", {"command": "kubectl -n evm exec $POD -- sh"}, [rule], "x"), [])
        self.assertEqual(of_guard.org_rule_hits("Bash", {"command": "kubectl -n runners-jobs get pods"}, [rule], "x"),
                         [])

    def test_the_admin_credential(self):
        self.assertEqual(hits("kubectl get jobs"), ["kube-admin"])
        self.assertEqual(hits("kubectl --kubeconfig ~/.kube/config get jobs"), ["kube-admin"])
        self.assertEqual(hits("KUBECONFIG=$HOME/.kube/config kubectl get jobs"), ["kube-admin"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml --context acme-k8s get jobs"), ["kube-admin"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml --user admin get jobs"), ["kube-admin"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml get jobs"), [])
        self.assertEqual(hits("kubectl get jobs", env={"KUBECONFIG": "/k/own.yaml"}), [])
        self.assertEqual(hits("kubectl exec runner-7 -- sh"), ["runner-exec", "kube-admin"])

    def test_local_verbs_reach_no_cluster(self):
        for cmd in ("kubectl config get-contexts", "kubectl version --client", "kubectl get pods --help",
                    "kubectl completion zsh", "kubectl kustomize ./overlay"):
            self.assertEqual(hits(cmd), [], cmd)
        self.assertEqual(hits("kubectl version"), ["kube-admin"])  # asks the server for its version

    def test_excepted_positions_and_the_reason(self):
        self.assertEqual(hits("kubectl exec runner-7 -- sh", position="runners_coordinator"), [])
        self.assertEqual(hits("kubectl exec runner-7 -- sh", position=None),
                         ["runner-exec", "kube-admin"])  # an unbound session gets no exception
        (_, said), = of_guard.org_rule_hits("Bash", {"command": "kubectl get ns"}, [KUBE_ADMIN], "acme_dispatcher")
        self.assertIn("default kubeconfig", said)
        self.assertEqual(of_guard.org_rule_hits("Read", {"file_path": "/x"}, [KUBE_ADMIN]), [])

    def test_a_parse_failure_matches_nothing(self):
        self.assertEqual(hits('kubectl exec "runner-7 -- sh'), [])


if __name__ == "__main__":
    unittest.main()
