import os

from fastapi.testclient import TestClient
from task4 import router


def test_sqlite_sliding_window(tmp_path, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_DB", str(tmp_path / "test.sqlite"))
    router.setup_db()
    assert router.reserve_sync("tenant-abcdefgh", 40_000, now=1000.0)
    assert router.reserve_sync("tenant-abcdefgh", 11_000, now=1010.0) is None
    assert router.reserve_sync("tenant-other-ijkl", 50_000, now=1010.0)
    assert router.reserve_sync("tenant-abcdefgh", 50_000, now=1061.0)


def test_backup_on_primary_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_DB", str(tmp_path / "test2.sqlite"))
    monkeypatch.setenv("PRIMARY_MODEL_URL", "https://primary.invalid")
    monkeypatch.setenv("BACKUP_MODEL_URL", "https://backup.invalid")
    router.setup_db()
    monkeypatch.setattr(router, "input_token_count", lambda s: 2)
    targets = []

    async def fake_model(client, url, body, timeout):
        targets.append(url)
        if "primary" in url:
            raise router.RetryOnBackup()
        return {"choices": [{"text": "hello"}], "usage": {"total_tokens": 4}}

    monkeypatch.setattr(router, "request_model", fake_model)
    client = TestClient(router.app)
    response = client.post("/v1/completions", headers={"X-Tenant-Key": "tenant-abcdefgh"}, json={"prompt": "hello", "max_tokens": 100})
    assert response.status_code == 200
    assert response.json()["provider"] == "backup"
    assert len(targets) == 2
    with router.connection() as con:
        assert con.execute("SELECT tokens FROM usage").fetchone()[0] == 4


def test_request_model_429_causes_fallback_signal():
    import asyncio
    import httpx
    async def run():
        transport = httpx.MockTransport(lambda req: httpx.Response(429, json={"error": "too many"}))
        async with httpx.AsyncClient(transport=transport) as client:
            try:
                await router.request_model(client, "https://primary.invalid", {"prompt": "Hi"}, timeout=1.0)
            except router.RetryOnBackup:
                return True
            return False
    assert asyncio.run(run())


def test_request_model_wallclock_timeout_causes_fallback_signal():
    import asyncio
    import httpx
    async def slow(req):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"choices": []})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            try:
                await router.request_model(client, "https://primary.invalid", {"prompt": "Hi"}, timeout=0.001)
            except router.RetryOnBackup:
                return True
            return False
    assert asyncio.run(run())
