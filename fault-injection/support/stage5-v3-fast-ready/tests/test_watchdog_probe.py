import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/watchdog_probe.py"
spec = importlib.util.spec_from_file_location("watchdog_probe", SOURCE)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogProbeTests(unittest.TestCase):
    def setUp(self):
        self.registration = {"object_uid": "test-uid"}
        self.object = {"metadata": {"name": watchdog.NAME,
            "namespace": watchdog.NAMESPACE, "uid": "test-uid",
            "resourceVersion": "5", "labels": {watchdog.LABEL_KEY:
            watchdog.LABEL_VALUE}}, "data": dict(watchdog.BASELINE)}

    def test_registered_baseline_is_accepted(self):
        watchdog.validate_object(self.object, self.registration)

    def test_candidate_is_accepted_for_recovery(self):
        self.object["data"] = dict(watchdog.CANDIDATE)
        watchdog.validate_object(self.object, self.registration)

    def test_wrong_uid_fails_closed(self):
        self.object["metadata"]["uid"] = "other"
        with self.assertRaises(ValueError):
            watchdog.validate_object(self.object, self.registration)

    def test_other_data_fails_closed(self):
        self.object["data"]["answer"] = "unregistered"
        with self.assertRaises(ValueError):
            watchdog.validate_object(self.object, self.registration)

    def test_owner_reference_fails_closed(self):
        self.object["metadata"]["ownerReferences"] = [{"name": "deployment"}]
        with self.assertRaises(ValueError):
            watchdog.validate_object(self.object, self.registration)

    def test_data_hash_distinguishes_test_states(self):
        self.assertNotEqual(watchdog.digest(watchdog.BASELINE),
                            watchdog.digest(watchdog.CANDIDATE))


if __name__ == "__main__":
    unittest.main()
