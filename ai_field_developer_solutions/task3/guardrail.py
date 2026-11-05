"""Task """
import json
import os
import re
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="LLM Streaming Guardrail")

# Bounded practical patterns, not a full DLP solution.
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}(?![A-Za-z0-9-])")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

MAX_PENDING = 384
EMAIL_BOUNDARY = re.compile(r"[\s,;:<>(){}\[\]\"'!?]")
DIGIT_TAIL = re.compile(r"(?<!\d)\d[\d -]*$")


def luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        n = int(digit)
        if index % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact(text: str) -> str:
    text = EMAIL.sub("[REDACTED]", text)
    text = SSN.sub("[REDACTED]", text)
    return CARD.sub(lambda m: "[REDACTED]" if luhn_ok(re.sub(r"\D", "", m.group())) else m.group(), text)


class StreamingPIIRedactor:
    

    def __init__(self) -> None:
        self.pending = ""

    def feed(self, fragment: str, final: bool = False) -> str:
        if not isinstance(fragment, str):
            raise TypeError("Expected text fragment")
        self.pending += fragment
        if final:
            output = redact(self.pending)
            self.pending = ""
            return output
        cut = len(self.pending)
        delimiters = list(EMAIL_BOUNDARY.finditer(self.pending))
        email_tail_start = delimiters[-1].end() if delimiters else 0
        if email_tail_start < len(self.pending):
            cut = min(cut, email_tail_start)
        digit_tail = DIGIT_TAIL.search(self.pending)
        if digit_tail:
            cut = min(cut, digit_tail.start())
        
        for pattern in (EMAIL, SSN, CARD):
            for match in pattern.finditer(self.pending):
                if match.start() < cut < match.end():
                    cut = match.start()
        
        cut = max(cut, len(self.pending) - MAX_PENDING)
        safe, self.pending = self.pending[:cut], self.pending[cut:]
        return redact(safe)


def sse_data(value: dict | str) -> bytes:
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return ("data: " + value + "\n\n").encode("utf-8")


def content_frame(template: dict, content: str) -> dict:
    base = {k: v for k, v in template.items() if k != "choices"}
    base["choices"] = [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    return base


async def rewrite_openai_sse(lines: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """Adapter for one-choice, OpenAI-style chat-completion SSE deltas."""
    cleaner = StreamingPIIRedactor()
    last_template: dict = {"object": "chat.completion.chunk"}
    record: list[str] = []

    async def consume(event_lines: list[str]) -> AsyncIterator[bytes]:
        nonlocal last_template
        items = [line[5:].lstrip() for line in event_lines if line.startswith("data:")]
        if not items:
            return
        data = "\n".join(items)
        if data == "[DONE]":
            tail = cleaner.feed("", final=True)
            if tail:
                yield sse_data(content_frame(last_template, tail))
            yield sse_data("[DONE]")
            return
        try:
            obj = json.loads(data)
            if not isinstance(obj, dict) or len(obj.get("choices", [])) != 1:
                raise ValueError("Unsupported stream event")
            choice = obj["choices"][0]
            delta = choice["delta"]
            if not isinstance(delta, dict) or set(delta) - {"content", "role"}:
                raise ValueError("Unsupported delta fields")
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("Unsupported delta type")
        except (ValueError, KeyError, TypeError):
            
            yield sse_data({"error": {"code": "UNSUPPORTED_STREAM", "message": "Unsupported provider stream event"}})
            return
        last_template = obj
        if content:
            safe = cleaner.feed(content)
            if safe:
                yield sse_data(content_frame(obj, safe))
        if not content:
            if choice.get("finish_reason") is not None:
                tail = cleaner.feed("", final=True)
                if tail:
                    yield sse_data(content_frame(obj, tail))
            yield sse_data(obj)

    async for line in lines:
        if line == "":
            if record:
                async for chunk in consume(record):
                    yield chunk
                record = []
        else:
            record.append(line)
    if record:
        async for chunk in consume(record):
            yield chunk
    tail = cleaner.feed("", final=True)
    if tail:
        yield sse_data(content_frame(last_template, tail))


@app.post("/v1/chat/completions")
async def stream_gateway(request: Request):
    provider_url = os.getenv("MODEL_PROVIDER_URL")
    if not provider_url:
        raise HTTPException(status_code=503, detail="Provider not configured")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    payload["stream"] = True
    headers = {"accept": "text/event-stream"}
    if os.getenv("MODEL_PROVIDER_API_KEY"):
        headers["authorization"] = "Bearer " + os.environ["MODEL_PROVIDER_API_KEY"]
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0), follow_redirects=False)
    try:
        upstream_request = client.build_request("POST", provider_url, json=payload, headers=headers)
        upstream = await client.send(upstream_request, stream=True)
        if upstream.status_code != 200:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail="Upstream LLM request failed")
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Upstream LLM unavailable") from None

    async def events() -> AsyncIterator[bytes]:
        try:
            async for piece in rewrite_openai_sse(upstream.aiter_lines()):
                yield piece
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
