"""Regression tests for the two contract bugs found in the 2026-09-29 review.

B1 — on ``codex_responses`` the middleware sees the payload *after*
     ``preflight_kwargs()`` replaced ``messages`` with ``input``. Reading only
     ``messages`` made every normal Codex session a silent no-op.
B2 — ``map_effort`` clamped onto Hermes' *entry* vocabulary (the widest
     OpenAI-compatible set), so a level the vendor rejects was written verbatim
     (Moonshot K3 / Ox Alpha reject ``medium``; GLM-5.2 rejects low and medium).
     The middleware runs after the transport clamp, so it must know the wire set.
"""

from __future__ import annotations

import pytest

from tests.conftest import import_plugin

middleware = import_plugin("middleware")
effort = import_plugin("effort")


class FakeClassifier:
    def __init__(self, score=1.0):
        self.score = score
        self.calls = []

    def classify(self, prompt):
        self.calls.append(prompt)
        return self.score


class RecordingClassifierFactory:
    def __init__(self, score=1.0):
        self.score = score
        self.instances = []

    def __call__(self, timeout=None):
        client = FakeClassifier(self.score)
        self.instances.append(client)
        return client


@pytest.fixture(autouse=True)
def clean_middleware(monkeypatch):
    middleware.reset_state()
    monkeypatch.setattr(middleware, "_settings_provider", None)
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    yield
    middleware.reset_state()


def use_settings(monkeypatch, settings):
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: settings.get(key, default))


def use_classifier(monkeypatch, factory):
    monkeypatch.setattr(middleware, "_classifier_factory", factory)


# ── B1: the preflighted codex payload has no ``messages`` ────────────────────

def codex_request(**extra):
    """The exact shape ``preflight_kwargs`` leaves behind: model/instructions/input."""
    req = {
        "model": "gpt-5.6-terra",
        "instructions": "You are Hermes.",
        "input": [{"role": "user",
                   "content": [{"type": "input_text", "text": "Refactor the parser."}]}],
        "reasoning": {"effort": "medium", "summary": "auto"},
        "store": False,
    }
    req.update(extra)
    return req


def ctx(request, session, provider="openai-codex", model="gpt-5.6-terra",
        api_mode="codex_responses"):
    return {
        "request": request, "session_id": session, "provider": provider, "model": model,
        "task_id": "t", "turn_id": "turn", "api_request_id": "r1", "api_mode": api_mode,
        "telemetry_schema_version": "hermes.observer.v1",
        "middleware_schema_version": "hermes.middleware.v1",
    }


def test_codex_normal_session_is_classified_from_input(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=0.0)  # -> "low"
    use_classifier(monkeypatch, factory)

    out = middleware.on_llm_request(**ctx(codex_request(), "codex-blocks"))

    assert factory.instances[0].calls == ["Refactor the parser."]
    assert out is not None
    assert out["request"]["reasoning"]["effort"] == "low"


def test_codex_string_content_is_read(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.5)  # -> "high"
    use_classifier(monkeypatch, factory)

    req = codex_request(input=[{"role": "user", "content": "plain string prompt"}])
    out = middleware.on_llm_request(**ctx(req, "codex-string"))

    assert factory.instances[0].calls == ["plain string prompt"]
    assert out["request"]["reasoning"]["effort"] == "high"


def test_chat_shape_still_wins_when_messages_are_present(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.5)
    use_classifier(monkeypatch, factory)

    req = {"model": "gpt-5.6-terra",
           "messages": [{"role": "user", "content": "chat shape prompt"}],
           "input": [{"role": "user", "content": "stale input"}],
           "reasoning": {"effort": "medium"}}
    out = middleware.on_llm_request(**ctx(req, "codex-both"))

    assert factory.instances[0].calls == ["chat shape prompt"]
    assert out["request"]["reasoning"]["effort"] == "high"


# ── B2: the wire vocabulary, not the entry vocabulary ────────────────────────

def test_kimi_k3_never_receives_medium():
    # K3's declared set is low/high/max, and medium rounds UP to its middle (high).
    assert effort.map_effort("medium", "moonshot", "kimi-k3") == "high"
    assert effort.map_effort("medium", "moonshot", "kimi-k3-256k") == "high"
    assert effort.map_effort("low", "moonshot", "kimi-k3") == "low"
    assert effort.map_effort("high", "moonshot", "kimi-k3") == "high"


def test_kimi_k2_era_keeps_medium():
    assert effort.map_effort("medium", "moonshot", "kimi-k2.6") == "medium"


def test_glm52_floors_to_its_declared_knob():
    # GLM-5.2 accepts exactly high/max: low and medium clamp up to its floor.
    assert effort.map_effort("low", "zai", "glm-5.2") == "high"
    assert effort.map_effort("medium", "zai", "glm-5.2") == "high"
    assert effort.map_effort("high", "zai", "glm-5.2") == "high"


def test_wide_routes_are_unchanged():
    assert effort.map_effort("medium", "openrouter", "x/y") == "medium"
    assert effort.map_effort("low", "opencode-go", "deepseek-v4.1-flash") == "low"
    assert effort.map_effort("high", "opencode-go", "mimo-v2.6-pro") == "high"
    # Codex keeps using Hermes' own route table (none/low/medium/high/xhigh/max).
    assert effort.map_effort("medium", "openai-codex", "gpt-5.6-terra") == "medium"
    assert effort.map_effort("medium", "openai-codex", "gpt-5.6") == "medium"


def test_supported_efforts_reports_the_wire_set():
    assert effort.supported_efforts("moonshot", "kimi-k3") == ("low", "high", "max")
    assert effort.supported_efforts("zai", "glm-5.2") == ("high", "max")
    # An explicit ``supported`` still wins (provider-profile callers).
    assert effort.supported_efforts("moonshot", "kimi-k3", supported=("low", "medium")) == \
        ("low", "medium")
