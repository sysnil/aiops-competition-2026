import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import collector_change


class CollectorChangeTests(unittest.TestCase):
    def make_backup(self, root, memory="400Mi"):
        root.mkdir()
        relay = yaml.safe_dump({"processors": {"memory_limiter": {
            "check_interval": "5s", "limit_percentage": 80,
            "spike_limit_percentage": 25}},
            "service": {"pipelines": {"traces": {"processors": ["memory_limiter"]}}}})
        objects = {
            "configmap": {"metadata": {"uid": "config-uid"}, "data": {"relay": relay}},
            "deployment": {"metadata": {"uid": "deploy-uid"},
                "spec": {"template": {"metadata": {"annotations": {"existing": "keep"}},
                    "spec": {"containers": [{"name": "opentelemetry-collector",
                        "resources": {"limits": {"memory": memory}},
                        "env": [{"name": "GOMEMLIMIT", "value": "320MiB"}]}]}}},
                "status": {"readyReplicas": 1}},
            "pod": {"metadata": {"uid": "old-pod"}},
        }
        hashes = {}
        for name, value in objects.items():
            path = root / (name + ".json")
            path.write_text(json.dumps(value))
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / "manifest.json").write_text(json.dumps({"sha256": hashes,
            "pod_uid": "old-pod"}))
        return relay

    def test_plan_changes_only_one_limiter_field(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "backup"
            self.make_backup(source)
            prereg = root / "prereg.yaml"
            prereg.write_text(yaml.safe_dump({"status": "PREREGISTERED_BEFORE_COLLECTOR_CHANGE",
                "single_candidate": {"old": 25, "new": 15}}))
            output = root / "plan.json"
            collector_change.plan(source, prereg, output)
            plan = json.loads(output.read_text())
            old = yaml.safe_load(plan["original_relay"])
            new = yaml.safe_load(plan["new_relay"])
            self.assertEqual(old["processors"]["memory_limiter"]["spike_limit_percentage"], 25)
            self.assertEqual(new["processors"]["memory_limiter"]["spike_limit_percentage"], 15)
            new["processors"]["memory_limiter"]["spike_limit_percentage"] = 25
            self.assertEqual(new, old)

    def test_plan_rejects_unregistered_resource_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "backup"
            self.make_backup(source, memory="512Mi")
            prereg = root / "prereg.yaml"
            prereg.write_text(yaml.safe_dump({"status": "PREREGISTERED_BEFORE_COLLECTOR_CHANGE",
                "single_candidate": {"old": 25, "new": 15}}))
            with self.assertRaises(ValueError):
                collector_change.plan(source, prereg, root / "plan.json")

    def test_original_state_rollback_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "backup"
            relay = self.make_backup(source)
            cm = {"data": {"relay": relay}, "metadata": {"uid": "config-uid",
                  "resourceVersion": "1"}}
            dep = json.loads((source / "deployment.json").read_text())
            dep["metadata"]["resourceVersion"] = "1"
            pod = {"metadata": {"uid": "old-pod", "name": "collector-1"},
                   "status": {"containerStatuses": [{"restartCount": 0}], "podIP": "10.0.0.1"},
                   "spec": {"nodeName": "worker3"}}
            plan = {"new_relay": "changed", "original_relay": relay,
                    "original_relay_sha256": hashlib.sha256(relay.encode()).hexdigest(),
                    "new_relay_sha256": "ab" * 32, "original_pod_uid": "old-pod",
                    "original_deployment_uid": "deploy-uid"}
            dep["status"] = {"readyReplicas": 1}
            with patch.object(collector_change, "object_json", side_effect=[cm, dep, cm, dep]), \
                    patch.object(collector_change, "gateway_pod", return_value=pod):
                result = collector_change.rollback(plan, root / "rollback.json")
            self.assertEqual(result["status"], "COLLECTOR_ORIGINAL_CONFIG_RESTORED")


if __name__ == "__main__":
    unittest.main()
