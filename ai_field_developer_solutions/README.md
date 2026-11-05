# AI Field Developer / Solutions Engineer Assessment 

The provided assessment contains four independent coding tasks. These examples are intentionally **mock/demo implementations**; they do not connect to a real refund processor and are not production deployments.

## Requirements

- Python 3.11 or later
- `pip install -r requirements.txt`
- Run commands from the project root; commands below assume Unix shells. On Windows PowerShell set variables using `$env:KEY="value"` instead of `export KEY=value`.

## Task 1 — official SDK MCP server (stdio)

`python task1/server.py`

This process is a stdio protocol server, so it is not a normal command-line chat program. A compatible MCP client must start it and complete MCP initialization. Both advertised tools are mock-only. Input validation uses strict Pydantic schemas. For this assignment, invalid tool arguments explicitly raise JSON-RPC `-32602`; modern MCP guidance often uses a tool result with `isError=true` for recoverable argument failures, so that assessment-specific difference is documented.

## Task 2 — MCP authorization proxy

Terminal 1: `uvicorn task2.mock_server:app --port 9009`

Terminal 2:

```sh
export GATEWAY_JWT_SECRET='replace-this-with-a-long-test-only-secret'
export MCP_DOWNSTREAM_URL='http://127.0.0.1:9009/mcp'
uvicorn task2.gateway:app --port 8002
```

Create a demo viewer token with `GATEWAY_JWT_SECRET=... python task2/make_token.py viewer` and post JSON-RPC to `http://127.0.0.1:8002/mcp` using `Authorization: Bearer <token>`. Test `tools/list`, a regular `tools/call`, and `admin_reset_key`; viewer cannot call `admin_` tools. The gateway accepts signed HS256 JWT test tokens for this demo and does **not** forward raw tokens downstream. This is not a complete MCP Streamable HTTP session implementation.

## Task 3 — LLM streaming privacy guardrail

```sh
export MODEL_PROVIDER_URL='https://your-compatible-provider.example/v1/chat/completions'
export MODEL_PROVIDER_API_KEY='test-or-real-key'
uvicorn task3.guardrail:app --port 8003
```

Post an OpenAI-compatible chat completion body to `http://127.0.0.1:8003/v1/chat/completions`. The proxy adds `stream=true` and rewrites one-choice SSE content deltas, redacting emails, SSN-like values, and Luhn-valid card-like numbers across arbitrary chunk boundaries. It does not assume whole-response buffering. It is a bounded demonstration rather than comprehensive production DLP: provider event formats vary, and input prompts are not scrubbed.

## Task 4 — SQLite token rate limiter / fallback

```sh
export PRIMARY_MODEL_URL='https://primary-provider.example/v1/completions'
export BACKUP_MODEL_URL='https://backup-provider.example/v1/completions'
export RATE_LIMIT_DB='./token_rate_limit.sqlite3'
uvicorn task4.router:app --port 8004
```

POST `{"prompt":"Hi", "max_tokens":100}` to `http://127.0.0.1:8004/v1/completions` with `X-Tenant-Key: tenant-abcdefgh`. The router counts prompt tokens using `tiktoken` cl100k_base and reserves prompt plus maximum output tokens in a SQLite sliding window. A primary 429, timeout, or network error is routed to backup; other upstream failures return sanitized errors. The two mock provider URLs must be replaced by accessible **compatible JSON APIs** to run end to end.

If the provider reports `usage.total_tokens`, the reservation is reconciled to actual usage; otherwise it remains conservative for the 60-second window. For production billing, verify the provider's tokenizer and meter timeouts/fallbacks separately. `X-Tenant-Key` is a quota identity only in this demo: production must authenticate tenant keys.

## Tests

`python -m pytest -q tests`

Tests cover Task 2 authorization and forwarding, Task 3 split-stream PII detection, Task 4 database quota, HTTP 429/timeout triggers, and failover. A Task 1 Pydantic/MCP test will execute after installing the official SDK. This offline build environment did not have the SDK and could not download packages, so an end-to-end Task 1 stdio handshake was not verified here.

## Security / applicability limits

- Demo refunds never send actual money.
- Use HTTPS, secret management, authenticated tenant identity, and audited authorization policies for production.
- Rate limits can differ by model tokenizer; prompt/output reservations are an upper bound relative to the selected tokenizer only.
- Full MCP HTTP transports require MCP session/version headers and response semantics beyond this assessment's simplified gateway.
- Streaming detection is pattern-based, not a comprehensive PII discovery engine.
