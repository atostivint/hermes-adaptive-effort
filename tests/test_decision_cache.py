"""Decision memory across a turn: route identity, re-clamping, and the telemetry that
reports both (acceptance criteria 3 and 6).

Criterion 3 — a stored target is only legal for the route that produced it. A provider
fallback inside one turn, or ``cache_safe`` pinning a session and then watching the
route change, keeps the decision key while the route moves underneath it; replaying
the recorded level verbatim is exactly how a narrow route receives a value its vendor
rejects. Moonshot K3 accepts exactly ``low``/``high``/``max`` (a bare ``medium`` is a
400), GLM-5.2 rejects ``low`` and ``medium``, and Ox Alpha rejects ``medium``. The
label describes the PROMPT, so on a route change it is re-clamped onto the new
route's vocabulary; a route that cannot express it at all rewrites nothing.

Criterion 6 — ``/hermes-adaptive-effort status`` names the provider and model behind each decision,
so an operator can tell what was classified and on which route, while still never
printing prompt text.
"""

from __future__ import annotations

import json


from conftest import import_plugin, settings_with_prompt_consent

command = import_plugin("command")
middleware = import_plugin("middleware")
effort = import_plugin("effort")

SESSION = "CACHE-SESSION"


def make_request(effort_level="medium", text="Design a multi-region failover plan"):
    """Recorded OpenAI-compatible shape: reasoning already on, effort pinned."""
    return {
        "model": "openrouter/x/y",
        "messages": [{"role": "user", "content": text}],
        "extra_body": {"reasoning": {"enabled": True, "effort": effort_level}},
    }


class CountingJev:
    """Fake transport: a scripted score/failure and a count of the probes made."""

    def __init__(self, score=1.0, failure=None):
        self.score = score
        self.failure = failure
        self.calls = []

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        return self.score, self.failure


def use_settings(monkeypatch, **kw):
    kw = settings_with_prompt_consent(kw)
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: kw.get(key, default))


def route(monkeypatch, jev, turn="turn-1", provider="openrouter",
          model="openrouter/x/y", api_mode="chat_completions", request=None,
          api_call_count=1):
    """One request through the plugin callback on the given transport."""
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: jev)
    return middleware.on_llm_request(
        request=make_request() if request is None else request,
        session_id=SESSION, provider=provider, model=model, api_mode=api_mode,
        task_id="t", turn_id=turn, api_request_id="r", api_call_count=api_call_count,
        middleware_schema_version="hermes.middleware.v1")


def entries():
    return list(middleware.session_state().values())


# ── criterion 3: one decision per turn, valid only for its route ────────────

def test_a_decision_records_the_route_it_was_made_on(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    jev = CountingJev(1.9)

    out = route(monkeypatch, jev)

    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    entry = entries()[0]
    assert entry["state"] == "decided"
    assert (entry["provider"], entry["model"]) == ("openrouter", "openrouter/x/y")
    assert entry["probes"] == 1


def test_same_turn_same_route_reuses_the_decision_without_a_second_probe(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    jev = CountingJev(1.9)

    for call in (1, 2, 3):
        out = route(monkeypatch, jev, api_call_count=call)
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"

    entry = entries()[0]
    assert entry["requests"] == 3
    assert entry["probes"] == 1
    assert len(jev.calls) == 1


def test_route_change_within_a_turn_reclamps_instead_of_replaying_a_stale_level(monkeypatch):
    """Recorded ``medium`` on a wide route, then applied to Moonshot K3: a 400.

    K3's positional middle is ``high`` (its server default), so the label is
    re-clamped up — the recorded wire value is never sent to a route that does not
    declare it.
    """
    use_settings(monkeypatch, mode="auto")
    jev = CountingJev(1.0)  # -> label `medium`

    first = route(monkeypatch, jev, provider="openrouter",
                  model="openrouter/meta/llama-3.3-70b-instruct")
    # The wide route already sits at `medium`: nothing to send, but still recorded.
    assert first is None
    entry = entries()[0]
    assert (entry["label"], entry["target"]) == ("medium", "medium")

    second = route(monkeypatch, jev, provider="moonshot", model="moonshot/kimi-k3",
                   api_call_count=2)

    assert second["request"]["extra_body"]["reasoning"]["effort"] == "high"
    entry = entries()[0]
    assert entry["target"] == "high"
    assert entry["model"] == "moonshot/kimi-k3"
    assert entry["probes"] == 1          # re-clamped, never re-classified
    assert len(jev.calls) == 1


def test_route_that_cannot_express_the_label_rewrites_nothing(monkeypatch):
    """A route with no vocabulary for the label: report it, send no effort at all."""
    use_settings(monkeypatch, mode="auto")
    jev = CountingJev(1.9)
    route(monkeypatch, jev)              # label `high`, decided on openrouter

    original = effort.supported_efforts
    monkeypatch.setattr(
        effort, "supported_efforts",
        lambda provider, model, supported=None: (
            () if provider == "zai" else original(provider, model, supported)))

    request = make_request()
    out = route(monkeypatch, jev, provider="zai", model="zai/glm-5.3",
                request=request, api_call_count=2)

    assert out is None
    assert request["extra_body"]["reasoning"]["effort"] == "medium"
    entry = entries()[0]
    assert entry["state"] == "unsupported"
    assert entry["target"] == "high"     # the decision stands; it just cannot be sent
    assert entry["probes"] == 1          # and the turn is never re-classified
    assert len(jev.calls) == 1


def test_cache_safe_pins_the_session_on_a_cache_hostile_route(monkeypatch):
    """``cache_safe``: per turn where an effort change keeps the cache, else pinned.

    An ``anthropic_messages`` route is cache-hostile — the thinking configuration is
    rendered into the prompt — so the decision key drops the turn id and one probe
    serves the whole session.
    """
    use_settings(monkeypatch, mode="cache_safe")
    jev = CountingJev(1.9)

    for turn in ("turn-A", "turn-B", "turn-C"):
        out = route(monkeypatch, jev, api_mode="anthropic_messages", turn=turn)
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"

    entries_ = entries()
    assert len(entries_) == 1
    assert entries_[0]["requests"] == 3
    assert entries_[0]["probes"] == 1
    assert len(jev.calls) == 1


# ── criterion 6: the status payload names the route, and no prompt text ─────

def test_status_names_the_provider_and_model_of_each_decision(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    marker = "PROMPT-MARKER-6b1f"
    jev = CountingJev(1.9)

    route(monkeypatch, jev, request=make_request(text=marker))

    payload = json.loads(command.handle("status json"))
    # `session_id` is the decision KEY here, so it carries the turn that was classified.
    assert payload["last"]["session_id"] == f"{SESSION}/turn-1"
    assert payload["last"]["provider"] == "openrouter"
    assert payload["last"]["model"] == "openrouter/x/y"
    assert payload["last"]["target"] == "high"
    session = payload["sessions"][0]
    assert (session["provider"], session["model"]) == ("openrouter", "openrouter/x/y")
    assert payload["counts"]["sessions"] == 1

    # The text rendering carries the same two fields, and says where the mode came from.
    text = command.handle("status")
    assert "provider=openrouter" in text
    assert "model=openrouter/x/y" in text
    assert "(from config)" in text

    # The documented promise holds in both renderings: the prompt never leaves the plugin.
    assert marker not in json.dumps(payload)
    assert marker not in text


def test_status_reports_the_route_even_when_nothing_was_rewritten(monkeypatch):
    """``unsupported`` is a reportable outcome, and it must name its route too."""
    use_settings(monkeypatch, mode="auto")
    jev = CountingJev(1.9)
    request = make_request()
    request["extra_body"]["reasoning"]["enabled"] = False

    assert route(monkeypatch, jev, request=request) is None

    payload = json.loads(command.handle("status json"))
    session = payload["sessions"][0]
    assert (session["state"], session["provider"], session["model"]) == (
        "unsupported", "openrouter", "openrouter/x/y")
    assert payload["counts"]["unsupported"] == 1
    assert payload["counts"]["probes"] == 0     # no scorer call without a writable field
