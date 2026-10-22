"""Providers: request shape, error mapping, audit records. No network.

The Anthropic SDK client is replaced by a recording stub, so these tests assert
exactly what would be sent (model id, output_config, cache_control, no prefill)
without opening a socket.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import BaseModel
from sabra_domain.errors import ModelError
from sabra_domain.settings import Settings
from sabra_llm.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from sabra_llm.base import CallRecord, NullProvider, get_provider
from sabra_llm.errors import (
    LLMError,
    LLMInvalidOutput,
    LLMRateLimited,
    LLMRefused,
    LLMTruncated,
    LLMUnavailable,
)
from sabra_llm.fake import FakeProvider
from sabra_llm.openai_compatible import OpenAICompatibleProvider


class Verdict(BaseModel):
    ok: bool
    note: str


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'p.db'}",
        data_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_dir=tmp_path / "evidence",
        automation_screenshot_dir=tmp_path / "automation",
        source_registry_path=tmp_path / "sources.yaml",
        candidate_dir=tmp_path / "candidates",
    )
    base.update(overrides)
    return Settings(**base)


def message(text: str, *, stop: str = "end_turn", **usage: int) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=usage.get("input", 321),
            output_tokens=usage.get("output", 45),
            cache_read_input_tokens=usage.get("cache_read", 300),
            cache_creation_input_tokens=usage.get("cache_write", 0),
        ),
    )


class StubClient:
    """Records every `messages.create` call and replays queued responses."""

    def __init__(self, *responses: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(tmp_path, *responses: Any, **settings: Any) -> tuple[AnthropicProvider, StubClient]:
    client = StubClient(*responses)
    return AnthropicProvider(make_settings(tmp_path, **settings), client=client), client


# ---------------------------------------------------------------------------
# AnthropicProvider request shape
# ---------------------------------------------------------------------------


def test_request_shape_for_structured_output(tmp_path) -> None:
    llm, client = provider(tmp_path, message('{"ok": true, "note": "fine"}'))
    result = llm.complete_structured("SYSTEM TEXT", "USER TEXT", Verdict, purpose="unit")

    assert result == Verdict(ok=True, note="fine")
    (call,) = client.calls
    assert call["model"] == "claude-opus-5-5" == DEFAULT_MODEL
    assert call["max_tokens"] > 0
    assert call["output_config"]["effort"] == "medium"
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["properties"]) == {"ok", "note"}
    assert fmt["schema"]["additionalProperties"] is False  # the SDK's transform_schema ran
    # the stable system prompt is cached
    assert call["system"] == [
        {"type": "text", "text": "SYSTEM TEXT", "cache_control": {"type": "ephemeral"}}
    ]
    # exactly one user turn, never an assistant prefill
    assert call["messages"] == [{"role": "user", "content": "USER TEXT"}]
    assert all(m["role"] != "assistant" for m in call["messages"])
    # thinking is left at the model default; no sampling knobs are sent
    for forbidden in ("thinking", "temperature", "top_p", "top_k"):
        assert forbidden not in call
    assert "budget_tokens" not in json.dumps(call, default=str)


def test_model_is_overridable_and_the_key_comes_from_settings(tmp_path, monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class RecordingAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            self.messages = SimpleNamespace(
                create=lambda **kw: seen.setdefault("create", kw) and message('{"ok": true, "note": ""}')
            )

    monkeypatch.setattr(anthropic, "Anthropic", RecordingAnthropic)
    settings = make_settings(
        tmp_path,
        llm_provider="anthropic",
        llm_model="claude-test-override",
        llm_api_key="sk-test-not-real",
        llm_timeout_seconds=17.5,
    )
    llm = get_provider(settings)
    assert isinstance(llm, AnthropicProvider)
    llm.complete_structured("s", "u", Verdict, purpose="unit")

    assert seen["max_retries"] == 2
    assert seen["timeout"] == 17.5
    assert seen["api_key"] == "sk-test-not-real"
    assert seen["create"]["model"] == "claude-test-override"


def test_without_a_key_the_sdk_resolves_its_own_environment(tmp_path, monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class RecordingAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            self.messages = SimpleNamespace(create=lambda **kw: message('{"ok": true, "note": ""}'))

    monkeypatch.setattr(anthropic, "Anthropic", RecordingAnthropic)
    AnthropicProvider(make_settings(tmp_path)).complete_structured("s", "u", Verdict, purpose="p")
    assert "api_key" not in seen


@pytest.mark.parametrize("name", ["anthropic", "claude", "Claude", " ANTHROPIC "])
def test_get_provider_selects_anthropic(tmp_path, name) -> None:
    assert isinstance(get_provider(make_settings(tmp_path, llm_provider=name)), AnthropicProvider)


def test_get_provider_leaves_existing_values_unchanged(tmp_path) -> None:
    assert isinstance(get_provider(make_settings(tmp_path)), NullProvider)
    assert isinstance(get_provider(make_settings(tmp_path, llm_provider="none")), NullProvider)
    openai = get_provider(make_settings(tmp_path, llm_provider="openai", llm_model="m"))
    assert isinstance(openai, OpenAICompatibleProvider)
    openai.close()
    with pytest.raises(ModelError):
        get_provider(make_settings(tmp_path, llm_provider="bogus"))


def test_plain_completion_uses_the_same_safe_request_shape(tmp_path) -> None:
    llm, client = provider(tmp_path, message("hello there"))
    response = llm.complete("hi", system="be brief", purpose="unit")
    assert response.text == "hello there"
    assert response.tokens_prompt == 321 and response.tokens_completion == 45
    (call,) = client.calls
    assert call["output_config"] == {"effort": "medium"}
    assert call["messages"] == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Stop reasons and errors
# ---------------------------------------------------------------------------


def test_refusal_and_truncation_are_explicit(tmp_path) -> None:
    llm, _ = provider(tmp_path, message("", stop="refusal"))
    with pytest.raises(LLMRefused):
        llm.complete_structured("s", "u", Verdict, purpose="unit")

    llm, _ = provider(tmp_path, message('{"ok": tr', stop="max_tokens"))
    with pytest.raises(LLMTruncated):
        llm.complete_structured("s", "u", Verdict, purpose="unit")

    llm, _ = provider(tmp_path, message("{}", stop="model_context_window_exceeded"))
    with pytest.raises(LLMTruncated):
        llm.complete_structured("s", "u", Verdict, purpose="unit")

    llm, _ = provider(tmp_path, message("{}", stop="pause_turn"))
    with pytest.raises(LLMError):
        llm.complete_structured("s", "u", Verdict, purpose="unit")


def test_unusable_output_is_an_invalid_output_error(tmp_path) -> None:
    llm, _ = provider(tmp_path, message("not json at all"))
    with pytest.raises(LLMInvalidOutput):
        llm.complete_structured("s", "u", Verdict, purpose="unit")
    llm, _ = provider(tmp_path, message('{"ok": "maybe"}'))
    with pytest.raises(LLMInvalidOutput):
        llm.complete_structured("s", "u", Verdict, purpose="unit")


def _api_error(cls, status: int, **kwargs: Any) -> Exception:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom sk-secret-key", response=httpx.Response(status, request=request), body=None)


def test_sdk_exceptions_map_to_one_error_family(tmp_path) -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    cases = [
        (_api_error(anthropic.RateLimitError, 429), LLMRateLimited, True),
        (_api_error(anthropic.InternalServerError, 500), LLMError, True),
        (_api_error(anthropic.OverloadedError, 529), LLMError, True),
        (anthropic.APITimeoutError(request), LLMError, True),
        (anthropic.APIConnectionError(request=request), LLMError, True),
        (_api_error(anthropic.AuthenticationError, 401), LLMError, False),
        (_api_error(anthropic.BadRequestError, 400), LLMError, False),
        (RuntimeError("something else"), LLMError, False),
    ]
    for exc, expected, retryable in cases:
        llm, _ = provider(tmp_path, exc)
        with pytest.raises(expected) as caught:
            llm.complete_structured("s", "u", Verdict, purpose="unit")
        assert caught.value.retryable is retryable, type(exc).__name__
        assert isinstance(caught.value, ModelError)
        assert "sk-secret-key" not in str(caught.value)


def test_null_provider_raises_unavailable() -> None:
    null = NullProvider()
    assert not null.available
    with pytest.raises(LLMUnavailable):
        null.complete_structured("s", "u", Verdict, purpose="unit")
    with pytest.raises(LLMUnavailable):
        null.complete("hi")
    assert issubclass(LLMUnavailable, ModelError)


# ---------------------------------------------------------------------------
# Audit records
# ---------------------------------------------------------------------------


def test_every_call_is_reported_with_usage_and_latency(tmp_path) -> None:
    llm, _ = provider(tmp_path, message('{"ok": true, "note": "x"}', cache_read=900, cache_write=12))
    records: list[CallRecord] = []
    llm.complete_structured(
        "s", "u", Verdict, purpose="tailoring", entity=("opportunity", "opp_9"), recorder=records.append
    )
    (record,) = records
    assert record.ok is True and record.error is None
    assert (record.provider, record.model, record.purpose) == ("anthropic", DEFAULT_MODEL, "tailoring")
    assert (record.input_tokens, record.output_tokens) == (321, 45)
    assert record.cache_read_tokens == 900 and record.cache_write_tokens == 12
    assert record.latency_ms is not None and record.latency_ms >= 0
    assert (record.entity_type, record.entity_id) == ("opportunity", "opp_9")
    assert len(record.prompt_hash) == 64 and len(record.response_hash) == 64
    assert "u" not in {record.prompt_hash}  # hashes only, never the prompt


def test_a_failed_call_is_reported_too(tmp_path) -> None:
    llm, _ = provider(tmp_path, message("", stop="refusal"))
    records: list[CallRecord] = []
    sink: list[CallRecord] = []
    llm.call_sink = sink.append
    with pytest.raises(LLMRefused):
        llm.complete_structured("s", "u", Verdict, purpose="unit", recorder=records.append)
    assert records[0].ok is False and "declined" in records[0].error
    assert sink and sink[0] is records[0]


def test_fake_provider_reports_like_a_real_one() -> None:
    fake = FakeProvider().script("unit", Verdict(ok=True, note="n"), LLMRateLimited("slow down"))
    records: list[CallRecord] = []
    assert fake.complete_structured("s", "u", Verdict, purpose="unit", recorder=records.append).ok
    with pytest.raises(LLMRateLimited):
        fake.complete_structured("s", "u", Verdict, purpose="unit", recorder=records.append)
    assert [r.ok for r in records] == [True, False]
    assert records[1].retryable is True


# ---------------------------------------------------------------------------
# OpenAI-compatible structured output
# ---------------------------------------------------------------------------


def openai_provider(tmp_path, handler) -> OpenAICompatibleProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = make_settings(
        tmp_path, llm_provider="openai", llm_model="gpt-test", llm_base_url="http://llm.test/v1"
    )
    return OpenAICompatibleProvider(settings, client=client)


def chat(content: str, finish: str = "stop", **extra: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content, **extra}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 9},
    }


def test_openai_compatible_structured_uses_json_mode_and_validates(tmp_path) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=chat('{"ok": true, "note": "hi"}'))

    llm = openai_provider(tmp_path, handler)
    records: list[CallRecord] = []
    result = llm.complete_structured("SYS", "USR", Verdict, purpose="unit", recorder=records.append)
    assert result == Verdict(ok=True, note="hi")
    body = sent[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "SYS" in body["messages"][0]["content"]
    assert "JSON Schema" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "USR"}
    assert (records[0].input_tokens, records[0].output_tokens) == (50, 9)


def test_openai_compatible_error_handling(tmp_path) -> None:
    replies = iter(
        [
            httpx.Response(200, json=chat("not json")),
            httpx.Response(200, json=chat('{"ok": tru', finish="length")),
            httpx.Response(200, json=chat("", refusal="no")),
            httpx.Response(429, text="slow"),
            httpx.Response(503, text="down"),
            httpx.Response(401, text="bad key"),
        ]
    )
    llm = openai_provider(tmp_path, lambda request: next(replies))
    expected = [LLMInvalidOutput, LLMTruncated, LLMRefused, LLMRateLimited, LLMError, LLMError]
    retryable = [False, False, False, True, True, False]
    for cls, flag in zip(expected, retryable, strict=True):
        with pytest.raises(cls) as caught:
            llm.complete_structured("s", "u", Verdict, purpose="unit")
        assert caught.value.retryable is flag


def test_openai_compatible_plain_completion_still_works(tmp_path) -> None:
    llm = openai_provider(tmp_path, lambda request: httpx.Response(200, json=chat("done")))
    response = llm.complete("hi", system="s")
    assert response.text == "done" and response.tokens_prompt == 50
