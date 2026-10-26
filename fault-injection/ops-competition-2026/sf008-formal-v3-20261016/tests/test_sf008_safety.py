import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf008_safety.py'
SPEC = importlib.util.spec_from_file_location('sf008_safety', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Usage:
    def __init__(self, free):
        self.free = free


class Device:
    def __init__(self, value):
        self.st_dev = value


class SafetyStorage(unittest.TestCase):
    def test_five_gb_floor_includes_root_and_data(self):
        names = iter(['/dev/sdb1', MODULE.EXPECTED_UUID, 'rw,nosuid,nodev,noexec'])
        with patch.object(MODULE.subprocess, 'check_output', side_effect=lambda *a, **k: next(names)), \
             patch.object(MODULE.os, 'stat', side_effect=[Device(2), Device(1)]), \
             patch.object(MODULE.shutil, 'disk_usage', side_effect=[Usage(5_000_000_000),
                                                                     Usage(5_000_000_000)]):
            self.assertEqual(MODULE.storage()['absolute_floor_bytes'], 5_000_000_000)

    def test_below_floor_fails_closed(self):
        names = iter(['/dev/sdb1', MODULE.EXPECTED_UUID, 'rw'])
        with patch.object(MODULE.subprocess, 'check_output', side_effect=lambda *a, **k: next(names)), \
             patch.object(MODULE.os, 'stat', side_effect=[Device(2), Device(1)]), \
             patch.object(MODULE.shutil, 'disk_usage', side_effect=[Usage(5_000_000_000),
                                                                     Usage(4_999_999_999)]):
            with self.assertRaises(RuntimeError):
                MODULE.storage()


if __name__ == '__main__':
    unittest.main()
