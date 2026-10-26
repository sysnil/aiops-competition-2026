import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from run_openrca_isolated import (can_read, failure_class, prediction_outcome,
                                  profile_for, run)


class OpenRCAIsolationTests(unittest.TestCase):
    def test_real_clean_abstention_is_distinct_from_nonempty_smoke(self):
        prediction = {'status': 'NO_PREDICTION', 'ranking': [],
                      'root_layer': None, 'root_entity': None,
                      'input_sha256': 'hash',
                      'llm_metadata': {'transport': 'https',
                                       'endpoint_host': 'independent.test'}}
        self.assertEqual(prediction_outcome(prediction, 'hash', 'independent.test'),
                         'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_VALID_ABSTENTION')
        with self.assertRaisesRegex(ValueError, 'identity'):
            prediction_outcome(prediction, 'wrong', 'independent.test')

    def test_failure_diagnostics_do_not_store_response_or_credentials(self):
        self.assertEqual(failure_class('urllib.error.HTTPError: HTTP Error 400: Bad Request'),
                         {'kind': 'HTTP_ERROR', 'http_status': 400})
        self.assertEqual(failure_class('ValueError: Endpoint HTTP 400 code=InvalidParam '
                                       'param=response_format category=response_format'),
                         {'kind': 'HTTP_ERROR', 'http_status': 400,
                          'error_code': 'InvalidParam', 'error_param': 'response_format',
                          'error_category': 'response_format'})

    def test_real_endpoint_configuration_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Independent HTTPS endpoint'):
                run(Path('/missing-public.json'), Path('/missing-prediction.json'),
                    Path('/missing-audit.json'))

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS sandbox-exec is required')
    def test_public_read_allowed_private_reads_denied(self):
        with tempfile.TemporaryDirectory(prefix='stage4f-openrca-public.',
                                         dir='/private/tmp') as temp:
            directory = Path(temp)
            public = directory / 'bundle.json'
            public.write_text('{"public_only":true}\n')
            env = {'PATH': '/usr/bin:/bin', 'HOME': temp, 'TMPDIR': temp,
                   'PYTHONNOUSERSITE': '1', 'PYTHONPATH': '',
                   'PYTHONDONTWRITEBYTECODE': '1'}
            profile = profile_for(directory)
            self.assertTrue(can_read(profile, env, public, directory))
            self.assertFalse(can_read(profile, env, Path(__file__), directory))
            with tempfile.NamedTemporaryFile(dir='/private/tmp') as other:
                self.assertFalse(can_read(profile, env, other.name, directory))


if __name__ == '__main__':
    unittest.main()
