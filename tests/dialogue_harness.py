"""Drive the real adapters end to end with fake transports and record every request they build.

Shared by ``test_dialogue_context.py`` and ``scripts/capture_scorer_bodies.py`` so the golden
fixture is produced by exactly the code path the tests then exercise.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from conftest import import_plugin

middleware = import_plugin("middleware")
_ADAPTER_MODULES = tuple(import_plugin(name) for name in (
    "jev_client", "nace_client", "openrouter_client", "openai_decision_client",
    "cloudflare_client", "custom_client"))

ACCOUNT_ID = "0123456789abcdef0123456789abcdef"

#: One entry per adapter configuration. Each is a full settings dict for ``_settings_provider``.
ADAPTERS: Dict[str, Dict[str, Any]] = {
    "jev": {"scorer_provider": "jev"},
    "nace": {"scorer_provider": "nace"},
    "openrouter": {"scorer_provider": "openrouter", "scorer_model": "vendor/scorer"},
    "openai_decision": {"scorer_provider": "openai_decision"},
    "cloudflare": {"scorer_provider": "cloudflare", "cloudflare_account_id": ACCOUNT_ID,
                   "cloudflare_model": "clef"},
    "custom_systemone": {"scorer_provider": "custom", "scorer_model": "rubric-v1",
                         "custom_endpoint": "https://scorer.example/v1/score",
                         "custom_api_format": "systemone"},
    "custom_chat": {"scorer_provider": "custom", "scorer_model": "rubric-v1",
                    "custom_endpoint": "https://scorer.example/v1/chat/completions",
                    "custom_api_format": "chat_completions"},
}

#: Route kinds: ``score`` is an unknown vocabulary (numeric 0..2 contract), ``choice`` an exact
#: registered route (named-choice contract).
ROUTES: Dict[str, Dict[str, Any]] = {
    "score": {
        "provider": "openrouter", "model": "openrouter/x/y", "api_mode": "chat",
        "body": lambda messages: {
            "model": "openrouter/x/y", "messages": messages,
            "extra_body": {"reasoning": {"enabled": True, "effort": "medium"}}},
    },
    "choice": {
        "provider": "opencode-go", "model": "kimi-k3", "api_mode": "chat_completions",
        "body": lambda messages: {
            "model": "kimi-k3", "messages": messages, "reasoning_effort": "high"},
    },
}

HISTORY: List[Dict[str, Any]] = [
    {"role": "system", "content": "SYSTEM-SECRET-INSTRUCTIONS"},
    {"role": "user", "content": "HISTORY-USER-1 refactor the parser"},
    {"role": "assistant", "content": "HISTORY-ASSISTANT-1 plan: split the tokenizer"},
    {"role": "user", "content": "HISTORY-USER-2 fine, continue"},
    {"role": "assistant", "content": "HISTORY-ASSISTANT-2 done, tests pass"},
    {"role": "user", "content": "LATEST-USER now fix the failing integration tests"},
]


class Recorder:
    """Fake HTTP transport shared by every adapter; stores what each one would have sent."""

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request, timeout):
        self.requests.append({
            "url": request.full_url,
            "headers": {key.lower(): value for key, value in request.header_items()},
            "body": request.data.decode("utf-8") if request.data else "",
        })
        # An empty payload parses as malformed: the request is captured, the call fails open.
        return {}


def install(monkeypatch, settings: Dict[str, Any]) -> Recorder:
    """Route every adapter through one recorder and make the middleware read *settings*."""
    recorder = Recorder()
    for module in _ADAPTER_MODULES:
        monkeypatch.setattr(module, "_default_transport", recorder)
        monkeypatch.setattr(module, "_default_key_reader", lambda: "test-key")
    middleware.reset_state()
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: settings.get(key, default))
    return recorder


def run(monkeypatch, adapter: str, route: str, *, messages: Optional[List[Dict[str, Any]]] = None,
        extra_settings: Optional[Dict[str, Any]] = None, session: str = "golden",
        **context: Any) -> Recorder:
    """One active ``llm_request`` through the real adapter; returns what the scorer received."""
    settings = {"mode": "always"}
    settings.update(ADAPTERS[adapter])
    settings.update(extra_settings or {})
    recorder = install(monkeypatch, settings)
    spec = ROUTES[route]
    middleware.on_llm_request(
        request=spec["body"](messages if messages is not None else HISTORY),
        session_id=session, turn_id="turn-1", provider=spec["provider"], model=spec["model"],
        api_mode=spec["api_mode"], **context)
    return recorder


def capture_all(monkeypatch) -> Dict[str, Any]:
    """Every adapter x route body for the standard history, plus two composed-feature variants."""
    captured: Dict[str, Any] = {}
    for adapter in ADAPTERS:
        for route in ROUTES:
            captured[f"{adapter}/{route}"] = run(monkeypatch, adapter, route).requests
    composed = {"classification_instructions": "Prefer low for chores.",
                "use_target_model_context": True}
    for route in ROUTES:
        captured[f"jev/{route}/composed"] = run(
            monkeypatch, "jev", route, extra_settings=composed).requests
        captured[f"custom_chat/{route}/composed"] = run(
            monkeypatch, "custom_chat", route, extra_settings=composed).requests
    return json.loads(json.dumps(captured))
