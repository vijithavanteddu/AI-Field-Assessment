import json
import time

import httpx
import jwt
from fastapi.testclient import TestClient
from task2 import gateway


def token(role: str, secret: str) -> str:
    return jwt.encode({"role": role, "exp": int(time.time()) + 3600,
                       "aud": "mcp-gateway", "iss": "assessment-gateway"}, secret, algorithm="HS256")


def test_gateway_auth_and_forward(monkeypatch):
    secret = "unit-test-secret-do-not-use-in-production"
    monkeypatch.setenv("GATEWAY_JWT_SECRET", secret)
    calls = []

    async def fake_forward(payload, request):
        calls.append(payload)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}})

    monkeypatch.setattr(gateway, "forward_downstream", fake_forward)
    client = TestClient(gateway.app)
    admin_tool = {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "admin_reset_key", "arguments": {}}}
    viewer = {"authorization": "Bearer " + token("viewer", secret)}
    admin = {"authorization": "Bearer " + token("admin", secret)}

    denied = client.post("/mcp", headers=viewer, json=admin_tool)
    assert denied.json()["error"]["code"] == -32001
    assert calls == []
    ok = client.post("/mcp", headers=admin, json=admin_tool)
    assert ok.json()["result"]["ok"] is True
    assert len(calls) == 1
    listed = client.post("/mcp", headers=viewer, json={"jsonrpc": "2.0", "id": 12, "method": "tools/list"})
    assert listed.status_code == 200 and len(calls) == 2
    missing = client.post("/mcp", json=admin_tool)
    assert missing.status_code == 401
    assert len(calls) == 2
    invalid = client.post("/mcp", headers=admin, json={"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {}})
    assert invalid.json()["error"]["code"] == -32602
    assert len(calls) == 2
    malformed = client.post("/mcp", headers=admin, data="{not-json")
    assert malformed.json()["error"]["code"] == -32700
