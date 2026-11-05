"""Task 4: SQLite-backed sliding-window token limiter and provider fallback."""
import asyncio
import hashlib
import os
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="LLM Rate Limit and Fallback Gateway")
TOKEN_LIMIT = 50_000
WINDOW_SECONDS = 60
PRIMARY_TIMEOUT = 3.0


class CompletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1)
    max_tokens: int = Field(ge=1, le=4096)


class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


@app.exception_handler(GatewayError)
async def exception_handler(request, error: GatewayError):
    return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})


def db_file() -> str:
    return os.getenv("RATE_LIMIT_DB", str(Path(__file__).with_name("rate_limits.sqlite3")))


def connection():
    con = sqlite3.connect(db_file(), timeout=10.0)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def setup_db() -> None:
    with connection() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS usage ("
                    "id TEXT PRIMARY KEY, tenant_hash TEXT NOT NULL, ts REAL NOT NULL, tokens INTEGER NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS usage_tenant_ts ON usage(tenant_hash, ts)")


def reserve_sync(tenant_key: str, tokens: int, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    tenant_hash = hashlib.sha256(tenant_key.encode("utf-8")).hexdigest()
    con = connection()
    try:
        con.execute("BEGIN IMMEDIATE")  # serializes simultaneous writers even across processes
        con.execute("DELETE FROM usage WHERE ts <= ?", (now - WINDOW_SECONDS,))
        spent = con.execute("SELECT COALESCE(SUM(tokens), 0) FROM usage WHERE tenant_hash=?", (tenant_hash,)).fetchone()[0]
        if spent + tokens > TOKEN_LIMIT:
            con.rollback()
            return None
        rid = uuid.uuid4().hex
        con.execute("INSERT INTO usage (id,tenant_hash,ts,tokens) VALUES (?,?,?,?)", (rid, tenant_hash, now, tokens))
        con.commit()
        return rid
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def reconcile_sync(reservation_id: str, real_tokens: int | None) -> None:
    # If provider returned no usage, keep original conservative reservation.
    if real_tokens is None:
        return
    with connection() as con:
        con.execute("UPDATE usage SET tokens=? WHERE id=?", (real_tokens, reservation_id))


def input_token_count(text: str) -> int:
    import tiktoken  # required dependency; do not silently substitute character counts
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


class RetryOnBackup(Exception):
    pass


async def request_model(client: httpx.AsyncClient, url: str, body: dict, timeout: float) -> dict:
    try:
        # Distinct credentials prevent accidental cross-provider credential reuse.
        key_name = "PRIMARY_MODEL_API_KEY" if url == os.getenv("PRIMARY_MODEL_URL") else "BACKUP_MODEL_API_KEY"
        api_key = os.getenv(key_name)
        headers = {"Authorization": "Bearer " + api_key} if api_key else {}
        reply = await asyncio.wait_for(client.post(url, json=body, headers=headers), timeout=timeout)
    except (TimeoutError, httpx.RequestError) as exc:
        raise RetryOnBackup() from exc
    if reply.status_code == 429:
        raise RetryOnBackup()
    if reply.status_code != 200:
        raise GatewayError(502, "UPSTREAM_FAILURE", "Model service returned an error")
    try:
        data = reply.json()
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object")
        return data
    except ValueError:
        raise GatewayError(502, "UPSTREAM_RESPONSE_INVALID", "Invalid model response") from None


@app.post("/v1/completions")
async def complete(body: CompletionIn, x_tenant_key: str | None = Header(default=None)):
    if not x_tenant_key or len(x_tenant_key) < 12:
        raise GatewayError(401, "INVALID_TENANT", "A valid tenant key is required")
    primary = os.getenv("PRIMARY_MODEL_URL")
    backup = os.getenv("BACKUP_MODEL_URL")
    if not primary or not backup:
        raise GatewayError(503, "NOT_CONFIGURED", "Model endpoints are not configured")
    # Reserve worst-case prompt + generated tokens, not a loose char approximation.
    try:
        predicted_tokens = input_token_count(body.prompt) + body.max_tokens
    except (ImportError, RuntimeError):
        raise GatewayError(503, "TOKENIZER_UNAVAILABLE", "Tokenizer service unavailable") from None
    if predicted_tokens > TOKEN_LIMIT:
        raise GatewayError(429, "TOKEN_RATE_LIMIT", "Request exceeds token budget")
    reservation = await asyncio.to_thread(reserve_sync, x_tenant_key, predicted_tokens)
    if reservation is None:
        raise GatewayError(429, "TOKEN_RATE_LIMIT", "Tenant token limit exceeded")

    payload = body.model_dump()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            try:
                result = await request_model(client, primary, payload, PRIMARY_TIMEOUT)
                chosen = "primary"
            except RetryOnBackup:
                result = await request_model(client, backup, payload, PRIMARY_TIMEOUT)
                chosen = "backup"
        except RetryOnBackup:
            # Do not refund a timed-out attempt; upstream may already have generated tokens.
            raise GatewayError(503, "MODELS_UNAVAILABLE", "Model services are temporarily unavailable") from None

    usage = result.get("usage")
    actual_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    if not isinstance(actual_tokens, int) or actual_tokens < 0:
        actual_tokens = None
    await asyncio.to_thread(reconcile_sync, reservation, actual_tokens)
    return {"provider": chosen, "result": result}


setup_db()
