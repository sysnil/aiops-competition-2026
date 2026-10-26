from __future__ import annotations

import tempfile
import unittest

import httpx

from client.public_api import PublicAPI


class APIRetryTests(unittest.TestCase):
    def test_429_has_bounded_retry(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "limited"})
            return httpx.Response(200, json={"items": [{"ok": True}]})

        with tempfile.TemporaryDirectory() as directory:
            api = PublicAPI(
                "http://test", directory, "unit-test", request_budget=3,
                max_retries=2, transport=httpx.MockTransport(handler),
            )
            result = api.get("/v1/cases")
            api.close()
        self.assertEqual(2, calls)
        self.assertEqual(200, 200 if result.payload["items"][0]["ok"] else 500)


if __name__ == "__main__":
    unittest.main()

