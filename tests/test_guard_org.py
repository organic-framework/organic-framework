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
        self.assertEqual(self.kinds("kubectl logs --tail 50 -n x deploy/api"), ("logs", ["deployments"]))
        self.assertEqual(self.kinds("kubectl -n x exec -it p -- sh"), ("exec", ["pods"]))
        self.assertEqual(self.kinds("kubectl apply -f k.yaml"), ("apply", ["(from file)"]))
        self.assertEqual(self.kinds("kubectl rollout restart deploy/x -n y"), ("rollout restart", ["deployments"]))
        self.assertEqual(self.kinds("kubectl get $R"), ("get", ["unparsed"]))
        self.assertEqual(self.kinds("kubectl drain node1"), ("drain", ["nodes"]))
        self.assertEqual(self.kinds("kubectl wait --for=condition=complete job/x"), ("wait", ["jobs"]))
        self.assertEqual(self.kinds("kubectl auth can-i get pods"), ("auth can-i", ["(none)"]))
        self.assertEqual(self.kinds("kubectl get jobs.batch -n z"), ("get", ["jobs.batch"]))
        self.assertTrue(of_guard.kubectl_calls("kubectl top pods -A")[0]["all_namespaces"])


class OrgRuleTest(unittest.TestCase):
    def test_exec_into_a_runner_pod(self):
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml exec -it runner-7 -- sh"),
                         ["runner-exec"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml cp ./job.json runners/runner-7:/tmp/"),
                         ["runner-exec"])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml logs runner-7"), [])
        self.assertEqual(hits("kubectl --kubeconfig /k/own.yaml exec -it db-0 -- psql"), [])

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
