from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from launch_openrca_from_tty import chat_endpoint


class ArkLauncherTests(unittest.TestCase):
    def test_base_url_appends_chat_completion_path(self):
        self.assertEqual(chat_endpoint('https://ark.cn-beijing.volces.com/api/plan/v3'),
                         'https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions')
        self.assertEqual(chat_endpoint('https://yyt.s-flag.com/v1\u00a0'),
                         'https://yyt.s-flag.com/v1/chat/completions')

    def test_url_cannot_carry_credentials(self):
        with self.assertRaisesRegex(ValueError, 'credential-free'):
            chat_endpoint('https://example.com/api/plan/v3?api_key=secret')


if __name__ == '__main__':
    unittest.main()
