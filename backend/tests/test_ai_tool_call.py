"""Phase 1 tool-calling seam: ``model_for`` tiering + the ``tool_call`` helper.

Hermetic throughout — the OpenAI client is faked at the ``_tool_call_client``
seam, so nothing reaches DeepInfra. ``conftest`` blanks the key and points the
base URL at an unroutable port; every test that exercises ``tool_call`` sets a
fake key with ``monkeypatch`` and never truly connects.
"""
from __future__ import annotations

import json
import types

import pytest

from app.core.config import settings
from app.services import ai as ai_service
from app.services.ai_models import AITask, model_for


# --- Fake OpenAI client ------------------------------------------------------


def _response(
    *, arguments=None, reasoning=None, tool_calls_present=True, model="fake/model-x",
    finish_reason="tool_calls",
):
    """A minimal chat.completions response shaped like the SDK objects."""
    tool_calls = []
    if tool_calls_present and arguments is not None:
        fn = types.SimpleNamespace(name="record_budget_proposal", arguments=arguments)
        tool_calls.append(types.SimpleNamespace(function=fn))
    message = types.SimpleNamespace(
        content="",
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    choice = types.SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = types.SimpleNamespace(prompt_tokens=42, completion_tokens=17)
    return types.SimpleNamespace(choices=[choice], usage=usage, model=model)


class _FakeCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        assert self._script, "the fake client ran out of scripted responses"
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _FakeClient:
    def __init__(self, script):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(script))


@pytest.fixture()
def fake_client(monkeypatch):
    """Install a fake OpenAI client and a valid key; return a factory."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    # Never actually sleep during backoff.
    monkeypatch.setattr(ai_service.time, "sleep", lambda *_a, **_k: None)

    holder: dict = {}

    def _install(script):
        client = _FakeClient(script)
        holder["client"] = client
        monkeypatch.setattr(ai_service, "_tool_call_client", lambda *, timeout: client)
        return client

    return _install


_TOOL = {"name": "record_budget_proposal", "parameters": {"type": "object"}}


# --- model_for tiering -------------------------------------------------------


def test_model_for_defaults_every_task_to_ai_model(monkeypatch):
    monkeypatch.setattr(settings, "AI_MODEL", "zai-org/GLM-5.3-Flash")
    monkeypatch.setattr(settings, "AI_MODEL_TIERS", "")
    for task in AITask:
        assert model_for(task) == "zai-org/GLM-5.3-Flash"
    # String names work too, case-insensitively.
    assert model_for("extract") == "zai-org/GLM-5.3-Flash"


def test_model_for_applies_override_and_ignores_garbage(monkeypatch):
    monkeypatch.setattr(settings, "AI_MODEL", "zai-org/GLM-5.3-Flash")
    monkeypatch.setattr(
        settings,
        "AI_MODEL_TIERS",
        "COMPARE=zai-org/GLM-4.6 , BOGUS=x , PROPUESTA=",  # blank + unknown ignored
    )
    assert model_for(AITask.COMPARE) == "zai-org/GLM-4.6"
    assert model_for(AITask.PROPUESTA) == "zai-org/GLM-5.3-Flash"  # blank value ignored
    assert model_for(AITask.EXTRACT) == "zai-org/GLM-5.3-Flash"  # untouched default


# --- tool_call success -------------------------------------------------------


def test_tool_call_parses_and_validates(fake_client):
    from app.schemas.extraction.budget_proposal import BudgetProposalExtraction

    args = json.dumps(
        {"document_type": "budget_proposal", "total_premium_uf": "UF 1.234,56", "facets": []}
    )
    fake_client([_response(arguments=args, reasoning="pensando...")])

    result = ai_service.tool_call(
        messages=[{"role": "user", "content": "hola"}],
        tool_schema=_TOOL,
        model="fake/model",
        timeout=30,
        schema=BudgetProposalExtraction,
    )

    assert result.payload is not None
    assert str(result.payload.total_premium_uf) == "1234.56"
    assert result.parsed["document_type"] == "budget_proposal"
    assert result.reasoning == "pensando..."  # kept, never folded into the payload
    assert result.usage == {"prompt_tokens": 42, "completion_tokens": 17}
    assert result.model == "fake/model-x"


def test_tool_call_truncation_finish_reason_length_blocks(fake_client):
    """A ``finish_reason=length`` answer is TRUNCATED — never trusted, always
    blocked as a provider error after the retries are exhausted."""
    args = json.dumps({"dimensions": []})  # what a truncated tool arg looks like
    fake_client(
        [
            _response(arguments=args, finish_reason="length"),
            _response(arguments=args, finish_reason="length"),
        ]
    )
    with pytest.raises(ai_service.AIProviderError):
        ai_service.tool_call(
            messages=[{"role": "user", "content": "x"}],
            tool_schema=_TOOL,
            model="m",
            timeout=10,
            max_retries=1,
        )


def test_tool_call_passes_through_max_tokens(fake_client):
    """The caller's ``max_tokens`` reaches the provider request (comparison budget)."""
    client = fake_client([_response(arguments=json.dumps({"ok": 1}))])
    seen: dict = {}
    original = client.chat.completions.create

    def _spy(**kwargs):
        seen.update(kwargs)
        return original(**kwargs)

    client.chat.completions.create = _spy
    ai_service.tool_call(
        messages=[{"role": "user", "content": "x"}],
        tool_schema=_TOOL,
        model="m",
        timeout=10,
        max_tokens=24000,
    )
    assert seen["max_tokens"] == 24000


def test_tool_call_without_schema_returns_parsed_only(fake_client):
    args = json.dumps({"a": 1})
    fake_client([_response(arguments=args)])
    result = ai_service.tool_call(
        messages=[{"role": "user", "content": "x"}],
        tool_schema=_TOOL,
        model="m",
        timeout=10,
    )
    assert result.parsed == {"a": 1}
    assert result.payload is None


# --- tool_call retry / typed failure -----------------------------------------


def test_tool_call_retries_transient_then_succeeds(fake_client):
    args = json.dumps({"document_type": "budget_proposal"})
    client = fake_client(
        [
            _response(arguments=None, tool_calls_present=False),  # empty -> transient
            _response(arguments="not-json-at-all"),  # malformed -> transient
            _response(arguments=args),  # finally good
        ]
    )
    result = ai_service.tool_call(
        messages=[{"role": "user", "content": "x"}],
        tool_schema=_TOOL,
        model="m",
        timeout=10,
        max_retries=2,
    )
    assert result.parsed == {"document_type": "budget_proposal"}
    assert client.chat.completions.calls == 3


def test_tool_call_raises_typed_after_exhausting_retries(fake_client):
    client = fake_client(
        [
            _response(arguments=None, tool_calls_present=False),
            _response(arguments=None, tool_calls_present=False),
            _response(arguments=None, tool_calls_present=False),
        ]
    )
    with pytest.raises(ai_service.AIProviderError):
        ai_service.tool_call(
            messages=[{"role": "user", "content": "x"}],
            tool_schema=_TOOL,
            model="m",
            timeout=10,
            max_retries=2,
        )
    assert client.chat.completions.calls == 3


def test_tool_call_malformed_json_raises_parse_error(fake_client):
    fake_client(
        [
            _response(arguments="{bad"),
            _response(arguments="{bad"),
        ]
    )
    with pytest.raises(ai_service.AIParseError):
        ai_service.tool_call(
            messages=[{"role": "user", "content": "x"}],
            tool_schema=_TOOL,
            model="m",
            timeout=10,
            max_retries=1,
        )


def test_tool_call_requires_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "")
    with pytest.raises(ai_service.AINotConfigured):
        ai_service.tool_call(
            messages=[{"role": "user", "content": "x"}],
            tool_schema=_TOOL,
            model="m",
            timeout=10,
        )


# --- optional validator seam -------------------------------------------------


def test_optional_validator_for_is_off_by_default():
    assert ai_service.optional_validator_for("todo_riesgo") is None
    assert ai_service.optional_validator_for(None) is None
