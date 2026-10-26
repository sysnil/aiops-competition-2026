from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class ConcurrencyLimitError(Exception):
    pass


@dataclass(frozen=True)
class TeamIdentity:
    token_id: str
    team_id: str
    mock_only: bool
    team_name: str = ""
    assigned_ip: str | None = None
    session: str = "UNASSIGNED"
    allowed_modes: tuple[str, ...] = ()
    is_organizer_test: bool = False


def derive_hash(secret: str, salt_hex: str) -> str:
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    ).hex()


class TokenStore:
    def __init__(self, path: Path):
        self.path = path
        self._mtime_ns = -1
        self._records: dict[str, dict] = {}

    def _reload(self) -> None:
        stat = self.path.stat()
        if stat.st_mtime_ns == self._mtime_ns:
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self._records = {record["token_id"]: record for record in payload.get("tokens", [])}
        self._mtime_ns = stat.st_mtime_ns

    def verify(self, token: str) -> TeamIdentity:
        self._reload()
        parts = token.split(".", 2)
        if len(parts) != 3 or parts[0] not in {"mock", "team"}:
            raise AuthenticationError("invalid token")
        _, token_id, secret = parts
        record = self._records.get(token_id)
        if not record or not record.get("enabled", False):
            raise AuthenticationError("invalid token")
        candidate = derive_hash(secret, record["salt"])
        if not hmac.compare_digest(candidate, record["hash"]):
            raise AuthenticationError("invalid token")
        return TeamIdentity(
            token_id=token_id,
            team_id=record["team_id"],
            mock_only=bool(record.get("mock_only", False)),
        )


class FairLimiter:
    def __init__(self, requests_per_minute: int, per_team_concurrency: int):
        self.requests_per_minute = requests_per_minute
        self.per_team_concurrency = per_team_concurrency
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._active: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def check_rate(self, team_id: str) -> None:
        now = time.monotonic()
        async with self._lock:
            bucket = self._requests[team_id]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.requests_per_minute:
                raise RateLimitError("team request rate exceeded")
            bucket.append(now)

    @asynccontextmanager
    async def slot(self, team_id: str):
        async with self._lock:
            if self._active[team_id] >= self.per_team_concurrency:
                raise ConcurrencyLimitError("team concurrent request limit exceeded")
            self._active[team_id] += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active[team_id] -= 1
