"""OpenRouter client behaviour with a fake client: skipping, retrying, reporting."""

from types import SimpleNamespace

import pytest

from atlas.llm import OpenRouterModel, model_chain


def reply(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


class FakeClient:
    """Each model maps to a list of outcomes, consumed one call at a time."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, **kw):
        self.calls.append((model, kw))
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def model(script, models):
    return OpenRouterModel(models=models, client=FakeClient(script), sleep=lambda s: None)


def test_first_working_model_answers_and_reasoning_is_capped():
    m = model({"a": [reply("{}")]}, ["a", "b"])
    assert m.complete([]) == "{}" and m.name == "a"
    assert m.client.calls[0][1]["extra_body"] == {"reasoning": {"max_tokens": 4000}}


def test_reply_used_up_by_reasoning_moves_on_and_says_why():
    m = model({"a": [reply("", "length")], "b": [reply("{}")]}, ["a", "b"])
    assert m.complete([]) == "{}" and m.name == "b"
    assert "ran out of tokens" in m.failures["a"]


def test_rate_limit_is_retried_once_then_succeeds():
    m = model({"a": [Exception("Error code: 429 - slow down"), reply("{}")]}, ["a"])
    assert m.complete([]) == "{}"
    assert len(m.client.calls) == 2


def test_model_no_longer_free_is_skipped_and_not_retried():
    gone = Exception("Error code: 404 - This model is unavailable for free.")
    m = model({"a": [gone], "b": [reply("{}"), reply("{}")]}, ["a", "b"])
    m.complete([])
    m.complete([])
    assert [c[0] for c in m.client.calls] == ["a", "b", "b"]


def test_every_failure_is_reported():
    m = model({"a": [Exception("Error code: 404 - unavailable for free")],
               "b": [Exception("Request timed out.")]}, ["a", "b"])
    with pytest.raises(RuntimeError) as e:
        m.complete([])
    assert "a: no longer free" in str(e.value) and "b: timed out" in str(e.value)


def test_bad_key_stops_immediately():
    m = model({"a": [Exception("Error code: 401 - unauthorized")], "b": [reply("{}")]}, ["a", "b"])
    with pytest.raises(RuntimeError, match="rejected the API key"):
        m.complete([])


def test_model_chain_order(monkeypatch):
    monkeypatch.delenv("EXTRACTION_MODELS", raising=False)
    monkeypatch.setenv("EXTRACTION_MODEL", "x")
    monkeypatch.setenv("FALLBACK_MODEL", "y")
    assert model_chain("EXTRACTION_MODELS", ("EXTRACTION_MODEL", "FALLBACK_MODEL"), ["y", "z"]) == ["x", "y", "z"]
    monkeypatch.setenv("EXTRACTION_MODELS", "p, q")
    assert model_chain("EXTRACTION_MODELS", ("EXTRACTION_MODEL",), ["z"]) == ["p", "q"]
