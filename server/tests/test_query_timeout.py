from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.settings import load_settings
from app.storage import CaseStorage, QueryTimeout


def test_long_duckdb_query_is_interrupted():
    settings = replace(load_settings(), query_timeout_seconds=0.01)
    storage = CaseStorage(settings)

    async def run():
        await storage._query("SELECT sum(random()) FROM range(1000000000)", [])

    with pytest.raises(QueryTimeout):
        asyncio.run(run())

