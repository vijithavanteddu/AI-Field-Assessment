"""Task 2"""
import json
import os
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="MCP Authorization Gateway")
DOWNSTREAM = os.getenv("MCP_DOWNSTREAM_URL", "http://127.0.0.1:9009/mcp")


def rpc_error(request_id: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def role_from_bearer(header: str | None) -> str | None:
    if not header or not header.startswith("Bearer "):
        return None
    secret = os.getenv("GATEWAY_JWT_SECRET")
    if not secret:
        return None  # fail closed until configured
    try:
        claims = jwt.decode(
            header[7:], secret, algorithms=["HS256"],
            audience="mcp-gateway", issuer="assessment-gateway",
            options={"require": ["exp", "role", "aud", "iss"]},
        )
        return claims["role"] if claims["role"] in ("admin", "viewer") else None
    except (jwt.PyJWTError, KeyError, TypeError):
        return None


async def forward_downstream(payload: dict, request: Request) -> httpx.Response:
    # Do not forward the user's raw token to an untrusted service.
    pass_headers = {"content-type": "application/json"}
    for name in ("mcp-session-id", "mcp-protocol-version", "accept"):
        if name in request.headers:
            pass_headers[name] = request.headers[name]
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        return await client.post(DOWNSTREAM, json=payload, headers=pass_headers)


@app.post("/mcp")
async def mcp_proxy(request: Request, authorization: str | None = Header(default=None)):
    role = role_from_bearer(authorization)
    if role is None:
        return rpc_error(None, -32000, "Invalid or missing authentication", 401)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return rpc_error(None, -32700, "Parse error", 400)
    if not isinstance(payload, dict):
        return rpc_error(None, -32600, "Invalid request", 400)
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        return rpc_error(request_id, -32600, "Invalid request", 400)

    if payload["method"] == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return rpc_error(request_id, -32602, "Invalid params")
        if params["name"].startswith("admin_") and role != "admin":
            # Exactly the custom code requested in the assessment.
            return rpc_error(request_id, -32001, "Unauthorized Tool Call")
    # tools/list and other well-formed methods are passed downstream unchanged.
    try:
        downstream = await forward_downstream(payload, request)
        return Response(
            content=downstream.content,
            status_code=downstream.status_code,
            media_type=downstream.headers.get("content-type", "application/json"),
        )
    except httpx.HTTPError:
        return rpc_error(request_id, -32002, "Downstream server unavailable", 502)
