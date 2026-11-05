import asyncio
import json

from task3.guardrail import StreamingPIIRedactor, luhn_ok, rewrite_openai_sse


def feed_fragments(parts):
    guard = StreamingPIIRedactor()
    return "".join(guard.feed(part) for part in parts) + guard.feed("", final=True)


def test_multi_chunk_email_ssn_card():
    original = "Email john@example.com. SSN 123-45-6789. Card 4111 1111 1111 1111."
    for block in [1, 2, 3, 7, 16, 64]:
        out = feed_fragments([original[i:i + block] for i in range(0, len(original), block)])
        assert out == "Email [REDACTED]. SSN [REDACTED]. Card [REDACTED]."


def test_non_luhn_number_preserved():
    assert not luhn_ok("4111111111111112")
    assert "4111111111111112" in feed_fragments(["4111111", "111111112 ."])


def test_memory_bounded_and_fast_normal_text():
    guard = StreamingPIIRedactor()
    early = guard.feed("Hello world. Good morning ")
    assert early.startswith("Hello world.")
    guard.feed("x" * 50_000)
    assert len(guard.pending) <= 384


def test_sse_rewrite_split_across_delta_events():
    async def line_stream():
        for word in ["Email: john", "@example", ".com", " is sensitive."]:
            yield 'data: '+ json.dumps({"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}]})
            yield ""
        yield 'data: '+ json.dumps({"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield ""
        yield "data: [DONE]"
        yield ""

    async def run():
        return [line.decode() async for line in rewrite_openai_sse(line_stream())]

    frames = asyncio.run(run())
    full = "".join(json.loads(f[6:].strip())["choices"][0]["delta"].get("content", "")
                   for f in frames if not f.startswith("data: [DONE]"))
    assert "john@example.com" not in full
    assert "[REDACTED]" in full
