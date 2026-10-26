import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import uid_rollout


def original_env():
    return [{"name": "OTEL_SERVICE_NAME", "value": "cart"},
            {"name": "OTEL_RESOURCE_ATTRIBUTES",
             "value": "service.version=3.0.0,service.criticality=high"}]


class UidRolloutTests(unittest.TestCase):
    def test_enriched_is_uniform_and_does_not_mutate_backup(self):
        old = original_env()
        new = uid_rollout.enriched(old)
        self.assertEqual(old, original_env())
        self.assertEqual([row["name"] for row in new[:2]],
                         ["WG_POD_UID", "WG_POD_NAME"])
        self.assertEqual(new[0]["valueFrom"]["fieldRef"]["fieldPath"],
                         "metadata.uid")
        resource = next(row["value"] for row in new if row["name"] ==
                        "OTEL_RESOURCE_ATTRIBUTES")
        self.assertIn("k8s.pod.uid=$(WG_POD_UID)", resource)
        self.assertIn("k8s.pod.name=$(WG_POD_NAME)", resource)

    def test_preexisting_or_duplicate_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            uid_rollout.enriched(uid_rollout.enriched(original_env()))
        with self.assertRaises(ValueError):
            uid_rollout.enriched([*original_env(), original_env()[0]])

    def test_missing_container_status_is_not_ready(self):
        value = {"items": [{"metadata": {"name": "cart-a"},
            "status": {"phase": "Running"}, "spec": {"containers": [
                {"env": original_env()}]}},
            {"metadata": {"name": "cart-b"},
             "status": {"phase": "Running", "containerStatuses": [
                 {"ready": True}]}, "spec": {"containers": [
                     {"env": original_env()}]}}]}
        with patch.object(uid_rollout, "kube", return_value=json.dumps(value)):
            self.assertEqual([row["metadata"]["name"] for row in
                uid_rollout.ready_pods("cart", original_env())], ["cart-b"])

    def test_distroless_exec_absence_requires_later_trace_verification(self):
        old = original_env()
        new = uid_rollout.enriched(old)
        def deployed(env, version):
            return {"metadata": {"uid": "checkout-uid", "resourceVersion": version},
                "spec": {"replicas": 1, "template": {"spec": {"containers": [
                    {"name": "checkout", "env": env}]}}},
                "status": {"readyReplicas": 1, "updatedReplicas": 1}}
        pod = {"metadata": {"name": "checkout-new", "uid": "pod-uid"},
               "spec": {"nodeName": "k8s-worker3"}}
        def fake_kube(*args, **kwargs):
            if args[0] == "exec":
                raise RuntimeError("printenv unavailable")
            return ""
        with patch.object(uid_rollout, "cluster_ready"), patch.object(
                uid_rollout, "deployment", side_effect=[deployed(old, "1"),
                    deployed(new, "2")]), patch.object(uid_rollout,
                "ready_pods", return_value=[pod]), patch.object(
                uid_rollout, "kube", side_effect=fake_kube):
            result = uid_rollout.patch("checkout", old, new, "checkout-uid")
        self.assertEqual(result["runtime_env_check"],
                         "UNAVAILABLE_NO_CONTAINER_EXEC_VERIFIED_LATER_BY_TRACE")
        self.assertIsNone(result["rendered_resource_sha256"])

    def test_rollback_rejects_unregistered_intervening_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            plan.write_text(json.dumps({"services": {
                service: {"deployment_uid": service + "-uid",
                          "original_env": original_env(),
                          "new_env": uid_rollout.enriched(original_env())}
                for service in uid_rollout.SERVICES}}))
            with patch.object(uid_rollout, "ROOT", root), patch.object(
                    uid_rollout, "deployment", return_value={
                        "metadata": {"uid": "quote-uid"}, "spec": {"template": {
                            "spec": {"containers": [{"name": "quote", "env": [
                                {"name": "UNREGISTERED", "value": "x"}]}]}}}}):
                (root / "runtime").mkdir()
                with self.assertRaises(ValueError):
                    uid_rollout.rollback_all(plan, root / "result.json")
                self.assertFalse((root / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
