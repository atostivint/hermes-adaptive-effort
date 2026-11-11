"""All configured scorer protocols carry and strictly validate named effort choices."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

jev = import_plugin("jev_client")
decisions = import_plugin("openai_decision_client")
cloudflare = import_plugin("cloudflare_client")
nace = import_plugin("nace_client")
openrouter = import_plugin("openrouter_client")
custom = import_plugin("custom_client")

CHOICES = ("low", "high", "max")
ACCOUNT = "0123456789abcdef0123456789abcdef"


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(request)
        return self.payload


def response(provider, value):
    if provider in {"jev", "nace", "openrouter", "custom-systemone"}:
        return {"answers": {"effort": {"type": "choice", "choice": value}}}
    if provider == "cloudflare":
        return {"success": True, "errors": [], "result": {
            "answers": {"effort": {"type": "choice", "choice": value}}}}
    if provider == "decisions":
        return {"answers": [{"name": "effort", "type": "choice", "choice": value}]}


def make_client(provider, transport):
    if provider == "jev":
        return jev.JevClient(api_key="key", transport=transport)
    if provider == "nace":
        return nace.NaceClient(api_key="key", transport=transport)
    if provider == "decisions":
        return decisions.OpenAIDecisionClient(api_key="key", transport=transport)
    if provider == "cloudflare":
        return cloudflare.CloudflareClient(account_id=ACCOUNT, api_key="key", transport=transport)
    if provider == "openrouter":
        return openrouter.OpenRouterClient(model="scorer/model", api_key="key",
                                           transport=transport)
    return custom.CustomClient(endpoint="https://scorer.example/score", model="scorer",
                               api_key="", transport=transport)


@pytest.mark.parametrize("provider", [
    "jev", "nace", "decisions", "cloudflare", "openrouter", "custom-systemone",
])
def test_provider_sends_only_route_choices_and_reads_named_response(provider):
    transport = FakeTransport(response(provider, "max"))
    client = make_client(provider, transport)

    assert client.classify_effort_detail("bounded task", CHOICES) == ("max", None)
    body = json.loads(transport.calls[0].data.decode("utf-8"))
    if provider in {"jev", "nace", "cloudflare", "openrouter", "custom-systemone"}:
        question = body["questions"]["effort"]
        assert question["type"] == "choice"
        assert tuple(question["criteria"]) == CHOICES
    elif provider == "decisions":
        question = body["questions"][0]
        assert question["type"] == "choice"
        assert tuple(option["value"] for option in question["choices"]) == CHOICES


@pytest.mark.parametrize("provider", [
    "jev", "nace", "decisions", "cloudflare", "openrouter", "custom-systemone",
])
def test_provider_rejects_a_choice_outside_the_route_vocabulary(provider):
    transport = FakeTransport(response(provider, "medium"))
    client = make_client(provider, transport)

    assert client.classify_effort_detail("bounded task", CHOICES) == (
        None, "malformed_response")
    assert len(transport.calls) == 1


NONE_CHOICES = ("none", "low", "high")
DIRECTSDK_CHOICES = ("low", "medium", "high", "xhigh", "max")


@pytest.mark.parametrize("provider", [
    "jev", "nace", "decisions", "cloudflare", "openrouter", "custom-systemone",
])
def test_provider_carries_none_only_when_the_route_offered_it(provider):
    transport = FakeTransport(response(provider, "none"))
    client = make_client(provider, transport)

    assert client.classify_effort_detail("mechanical task", NONE_CHOICES) == ("none", None)
    body = json.loads(transport.calls[0].data.decode("utf-8"))
    if provider in {"jev", "nace", "cloudflare", "openrouter", "custom-systemone"}:
        assert tuple(body["questions"]["effort"]["criteria"]) == NONE_CHOICES
    else:
        assert tuple(option["value"] for option in body["questions"][0]["choices"]) == \
            NONE_CHOICES


@pytest.mark.parametrize("provider", [
    "jev", "nace", "decisions", "cloudflare", "openrouter", "custom-systemone",
])
def test_directsdk_five_level_vocabulary_reaches_every_scorer_and_is_read_back(provider):
    for level in DIRECTSDK_CHOICES:
        transport = FakeTransport(response(provider, level))
        client = make_client(provider, transport)
        assert client.classify_effort_detail("bounded task", DIRECTSDK_CHOICES) == (level, None)
        body = json.loads(transport.calls[0].data.decode("utf-8"))
        if provider == "decisions":
            sent = tuple(option["value"] for option in body["questions"][0]["choices"])
        else:
            sent = tuple(body["questions"]["effort"]["criteria"])
        assert sent == DIRECTSDK_CHOICES


@pytest.mark.parametrize("provider", [
    "jev", "nace", "decisions", "cloudflare", "openrouter", "custom-systemone",
])
def test_provider_rejects_none_when_the_route_did_not_offer_it(provider):
    transport = FakeTransport(response(provider, "none"))
    client = make_client(provider, transport)

    assert client.classify_effort_detail("task", CHOICES) == (None, "malformed_response")
    assert len(transport.calls) == 1


def test_none_stays_out_of_the_score_contract_and_is_the_lowest_choice_level():
    effort = import_plugin("effort")
    rubric = import_plugin("rubric")

    # The numeric score contract, used by /hae probe and unknown vocabularies, is unchanged.
    assert rubric.questions_for(None, None)["effort"]["type"] == "score"
    assert rubric.normalize_effort_choices(("none", "low")) == ("none", "low")
    assert rubric.normalize_effort_choices(("ultra", "low")) == ("low",)
    assert rubric.normalize_effort_choices(None) == ()
    assert effort.score_to_label(0.1) == "low"

    # none is only legal where the route lists it, and only as the lowest level.
    assert effort.map_named_choice("none", ("low", "high")) is None
    assert effort.map_named_choice("none", ("none", "low", "high")) == "none"
    assert effort.map_effort("none", "opencode-go", "kimi-k3") is None
    assert effort.bound_label("none", None, None) == "none"
    assert effort.bound_label("none", "low", "high") == "low"
    assert effort.bound_label("none", None, "high") == "none"
    assert effort.raise_choice_level("none", ("none", "low", "high"), 1) == "low"
    assert effort.raise_choice_level("none", ("low", "high"), 1) == "none"
    assert effort.raise_choice_level("none", ("low", "high"), 5) == "none"


@pytest.mark.parametrize("provider", [
    "jev", "nace", "decisions", "cloudflare", "openrouter", "custom-systemone",
])
@pytest.mark.parametrize("value", ["none", "ultra", "minimal", "MAX "])
def test_directsdk_rejects_out_of_list_answers(provider, value):
    client = make_client(provider, FakeTransport(response(provider, value)))
    assert client.classify_effort_detail("bounded task", DIRECTSDK_CHOICES) == (
        None, "malformed_response")


# ── Codex Responses and CommandCode DeepSeek named vocabularies (issue #100) ──────────────

effort = import_plugin("effort")

CODEX_MODELS = (
    "gpt-6.1-sol", "gpt-6.1-sol-900k",
    "gpt-6-astra", "gpt-6-astra-900k",
    "gpt-6.1-terra",
    "gpt-6-sol", "gpt-6-sol-900k",
    "gpt-6-luna", "gpt-6-luna-900k",
    "gpt-5.6-sol", "gpt-5.6-sol-900k",
    "gpt-5.6-terra", "gpt-5.6-terra-900k",
    "gpt-5.6-luna", "gpt-5.6-luna-900k",
)
CODEX_FIVE = ("low", "medium", "high", "xhigh", "max")
COMMANDCODE_MODEL = "deepseek/deepseek-v4.1-flash"
COMMANDCODE_CHOICES = ("low", "high", "max")
SCORER_PROVIDERS = ["jev", "nace", "decisions", "cloudflare", "openrouter",
                    "custom-systemone"]


def test_codex_routes_are_exactly_the_fifteen_listed_ids():
    assert set(effort.CODEX_CHOICE_MODELS) == set(CODEX_MODELS)
    assert len(effort.CODEX_CHOICE_MODELS) == 15
    assert effort.CODEX_PROVIDER == "openai-codex"


def test_codex_routes_take_their_vocabulary_from_the_host_model_table():
    from agent.reasoning_effort import codex_supported_efforts

    for model in CODEX_MODELS:
        levels = effort.route_choice_levels(
            "openai-codex", model, "codex_responses")
        expected = tuple(level for level in codex_supported_efforts(model)
                         if level in effort.CHOICE_LEVELS)
        # The declared list is the host's own ladder, filtered to the level names this
        # plugin can choose; never a second hand-written table.
        assert levels == expected, model
        # No route advertises ``none`` as a choice, whatever its ladder declares.
        assert "none" not in levels, model
        assert "minimal" not in levels and "ultra" not in levels, model
    # ``gpt-6.1-terra`` sits on the legacy ladder: no ``max`` tier there.
    assert effort.route_choice_levels(
        "openai-codex", "gpt-6.1-terra", "codex_responses") == (
        "low", "medium", "high", "xhigh")
    for model in ("gpt-6.1-sol", "gpt-6.1-sol-900k", "gpt-6-astra", "gpt-6-astra-900k",
                  "gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna"):
        assert effort.route_choice_levels(
            "openai-codex", model, "codex_responses") == CODEX_FIVE, model


def test_commandcode_deepseek_route_declares_low_high_max():
    assert set(effort.COMMANDCODE_CHOICE_ROUTES) == {COMMANDCODE_MODEL}
    assert effort.route_choice_levels(
        "commandcode", COMMANDCODE_MODEL, "chat_completions") == COMMANDCODE_CHOICES
    # DeepSeek maps ``medium`` onto ``high``; it is never offered, and the shared
    # vocabulary override is what turns a bounded label into the wire value.
    assert "medium" not in effort.route_choice_levels(
        "commandcode", COMMANDCODE_MODEL, "chat_completions")
    assert effort.map_named_choice("medium", COMMANDCODE_CHOICES) == "high"
    assert effort.map_named_choice("high", COMMANDCODE_CHOICES) == "high"
    assert effort.map_effort("max", "commandcode", COMMANDCODE_MODEL) == "max"


def test_codex_vocabulary_requires_exact_provider_model_and_api_mode():
    for api in ("chat_completions", "anthropic_messages", "", None):
        assert effort.route_choice_levels("openai-codex", "gpt-6-luna", api) == (), api
    for provider in ("openai", "opencode-zen", "codex", "openai-codex-zen",
                     "claude-subscription-directsdk-experimental", "", None):
        assert effort.route_choice_levels(
            provider, "gpt-6-luna", "codex_responses") == (), provider
    for model in (
            "gpt-6.1-terra-900k",  # does not exist
            "gpt-6.1-sol-preview", "gpt-6.1-sol-1m", "gpt-6.1-sol-900k-2026",
            "gpt-6-luna-1m", "gpt-6-luna-preview", "gpt-6-astra-900",
            "gpt-6-daybreak-blue-latest", "gpt-daybreak-blue-latest-900k",
            "gpt-5.6", "gpt-6", "gpt-5", "gpt", "", None):
        assert effort.route_choice_levels(
            "openai-codex", model, "codex_responses") == (), model
    # Inherited host normalisation: ``codex_supported_efforts`` strips a vendor prefix,
    # exactly as it did for ``gpt-6.1-sol`` before this change.
    assert effort.route_choice_levels(
        "openai-codex", "openai-codex/gpt-6-luna", "codex_responses") == CODEX_FIVE


def test_commandcode_vocabulary_requires_exact_provider_model_and_api_mode():
    for api in ("codex_responses", "anthropic_messages", "", None):
        assert effort.route_choice_levels(
            "commandcode", COMMANDCODE_MODEL, api) == (), api
    for provider in ("command-code", "commandcode-zen", "opencode-zen", "openai",
                     "deepseek", "", None):
        assert effort.route_choice_levels(
            provider, COMMANDCODE_MODEL, "chat_completions") == (), provider
    for model in (
            "deepseek-v4.1-flash",  # bare slug is a different id
            "deepseek/deepseek-v4.1-flash-preview",
            "deepseek/deepseek-v4.1-flash-900k",
            "deepseek/deepseek-v4-pro",
            "deepseek/deepseek-v4-flash",
            "commandcode/deepseek/deepseek-v4.1-flash",
            "deepseek", "", None):
        assert effort.route_choice_levels(
            "commandcode", model, "chat_completions") == (), model


def test_codex_choice_list_is_the_only_thing_named_choices_change():
    middleware = import_plugin("middleware")
    route_trial = import_plugin("route_trial")

    # Only ``gpt-6.1-sol`` was dynamic before and stays dynamic; the other fourteen keep a
    # retained decision (invariant 1), so naming their vocabulary changes no cache behaviour.
    for model in CODEX_MODELS:
        expected = model == "gpt-6.1-sol"
        assert middleware._dynamic_effort_route(
            "openai-codex", model, "codex_responses") is expected, model
    # A registered vocabulary takes the route out of the automatic trial, which is the only
    # other way to dynamic status: it never grants it.
    for model in CODEX_MODELS:
        choices = effort.route_choice_levels("openai-codex", model, "codex_responses")
        assert not route_trial.eligible_route(
            ("openai-codex", model, "codex_responses"), choices), model
    for api in ("codex_responses", "chat_completions"):
        assert middleware._dynamic_effort_route(
            "commandcode", COMMANDCODE_MODEL, api) is False, api
    assert not route_trial.eligible_route(
        ("commandcode", COMMANDCODE_MODEL, "chat_completions"), COMMANDCODE_CHOICES)


def test_allow_none_gate_stays_closed_on_every_newly_registered_route():
    # The #83 gate offers ``none`` only on an exact registered route whose own declared
    # vocabulary lists it. ``gpt-6.1-sol`` sits on the host's no-disable ladder (``none``
    # 400s there, live 2026-09-29), so registering it does not open the gate on the one
    # dynamic Codex route.
    assert effort.route_declares_none(
        "openai-codex", "gpt-6.1-sol", "codex_responses") is False
    assert effort.route_declares_none(
        "openai-codex", "gpt-6-astra", "codex_responses") is False
    # The GPT-5.6 and legacy ladders do declare ``none`` at the host, but none of those
    # routes is dynamic and ``none`` is never a named choice, so the gate stays shut and
    # ``none`` is never offered or written.
    for model in ("gpt-6-luna", "gpt-6-sol", "gpt-5.6-sol", "gpt-6.1-terra"):
        assert effort.route_declares_none(
            "openai-codex", model, "codex_responses") is True, model
    assert effort.route_declares_none(
        "commandcode", COMMANDCODE_MODEL, "chat_completions") is False


@pytest.mark.parametrize("provider", SCORER_PROVIDERS)
@pytest.mark.parametrize("value", ["minimal", "ultra", "none"])
def test_codex_vocabulary_rejects_answers_outside_the_route_list(provider, value):
    transport = FakeTransport(response(provider, value))
    client = make_client(provider, transport)
    assert client.classify_effort_detail("bounded task", CODEX_FIVE) == (
        None, "malformed_response")
    # A received answer is never retried.
    assert len(transport.calls) == 1


@pytest.mark.parametrize("provider", SCORER_PROVIDERS)
@pytest.mark.parametrize("value", ["minimal", "medium", "xhigh", "ultra", "none"])
def test_commandcode_vocabulary_rejects_answers_outside_the_route_list(provider, value):
    client = make_client(provider, FakeTransport(response(provider, value)))
    assert client.classify_effort_detail("bounded task", COMMANDCODE_CHOICES) == (
        None, "malformed_response")


@pytest.mark.parametrize("provider", SCORER_PROVIDERS)
@pytest.mark.parametrize("value", COMMANDCODE_CHOICES)
def test_commandcode_vocabulary_reaches_every_scorer_and_is_read_back(provider, value):
    transport = FakeTransport(response(provider, value))
    client = make_client(provider, transport)
    assert client.classify_effort_detail("bounded task", COMMANDCODE_CHOICES) == (value, None)
    body = json.loads(transport.calls[0].data.decode("utf-8"))
    if provider == "decisions":
        sent = tuple(option["value"] for option in body["questions"][0]["choices"])
    else:
        sent = tuple(body["questions"]["effort"]["criteria"])
    assert sent == COMMANDCODE_CHOICES


# ── OpenCode Go routes measured on 2026-11-11 (acceptance, not intensity) ────────────────

#: One live POST per level against https://opencode.ai/zen/go/v1 answered 200 for each
#: list below. A 200 means the gateway took the value; it is not evidence that the level
#: changes how much the model reasons.
GO_MEASURED_ROUTES = {
    "minimax-m3.1-flash": ("low", "medium", "high", "xhigh", "max"),
    "step-5-preview": ("low", "medium", "high"),
    "glm-5.3-flash": ("low", "high", "max"),
}


@pytest.mark.parametrize(("model", "levels"), sorted(GO_MEASURED_ROUTES.items()))
def test_measured_go_chat_routes_resolve_to_their_declared_vocabulary(model, levels):
    assert effort.route_choice_levels("opencode-go", model, "chat_completions") == levels
    assert effort.wire_efforts("opencode-go", model) == levels
    assert effort.OPEN_CODE_GO_INJECTION_ROUTES["chat_completions"][model] == (
        "reasoning_effort", levels)
    # Acceptance never widens the vocabulary beyond the route's own list, and no
    # measured route declares ``none`` (the #83 gate stays shut).
    assert "none" not in levels
    assert effort.route_declares_none(
        "opencode-go", model, "chat_completions") is False
    for level in levels:
        assert effort.map_effort(level, "opencode-go", model) == level, level


def test_measurement_confirms_the_three_already_registered_go_entries():
    # The probe confirms these rather than changing them. The OpenCode CLI catalog lists
    # only ``low/high/max`` for deepseek-v4.1-flash; the live 200 on ``medium`` is the
    # deciding evidence, so the four-level list is pinned.
    assert effort.OPEN_CODE_GO_INJECTION_ROUTES["chat_completions"] == {
        "glm-5.2": ("reasoning_effort", ("high", "max")),
        "glm-5.3": ("paired_effort", ("low", "high", "max")),
        "glm-5.3-flash": ("reasoning_effort", ("low", "high", "max")),
        "kimi-k3": ("reasoning_effort", ("low", "high", "max")),
        "mimo-v2.6-flash": ("reasoning_effort", ("low", "medium", "high")),
        "deepseek-v4-pro": ("paired_effort", ("low", "high", "max")),
        "deepseek-v4-flash": ("paired_effort", ("low", "high", "max")),
        "deepseek-v4.1-flash": ("paired_effort", ("low", "medium", "high", "max")),
        "minimax-m3.1-flash": ("reasoning_effort", ("low", "medium", "high", "xhigh",
                                                    "max")),
        "step-5-preview": ("reasoning_effort", ("low", "medium", "high")),
    }
    assert effort.route_choice_levels(
        "opencode-go", "deepseek-v4.1-flash", "chat_completions") == (
        "low", "medium", "high", "max")
    assert effort.OPEN_CODE_GO_INJECTION_ROUTES["codex_responses"]["grok-4.7"] == (
        "reasoning", ("low", "medium", "high", "xhigh"))


def test_registry_never_infers_a_neighbouring_go_model():
    # ``glm-5.3-flash`` is a different id from ``glm-5.3``: same levels, different carrier.
    assert effort.OPEN_CODE_GO_INJECTION_ROUTES["chat_completions"]["glm-5.3"] == (
        "paired_effort", ("low", "high", "max"))
    assert effort.route_choice_levels("opencode-go", "glm-5.3", "chat_completions") == (
        "low", "high", "max")
    for model in ("minimax-m3.1", "minimax-m3.1-flash-preview", "minimax-m3-flash",
                  "step-5", "step-5-preview-mini", "glm-5.3-flash-2026",
                  "glm-5.3-preview", "glm-5.2-flash", "beam-free",
                  "muse-spark-1.3-contributor", "deepseek-v4.1"):
        assert effort.route_choice_levels(
            "opencode-go", model, "chat_completions") == (), model


@pytest.mark.parametrize("model", sorted(GO_MEASURED_ROUTES))
def test_measured_go_routes_require_the_chat_completions_api_mode(model):
    for api in ("codex_responses", "anthropic_messages", "openai_responses", "", None):
        assert effort.route_choice_levels("opencode-go", model, api) == (), api


@pytest.mark.parametrize(("model", "levels"), sorted(GO_MEASURED_ROUTES.items()))
def test_measured_go_routes_require_the_opencode_go_provider(model, levels):
    for provider in ("openai", "opencode-zen", "zai", "openrouter", "anthropic", "", None):
        assert effort.route_choice_levels(
            provider, model, "chat_completions") == (), provider
    # The inherited "last path segment" normalisation is unchanged: a prefixed *exact*
    # slug resolves, a prefixed neighbouring id never does.
    assert effort.route_choice_levels(
        "opencode-go", "minimax/" + model, "chat_completions") == levels
    assert effort.route_choice_levels(
        "opencode-go", model.rsplit("-", 1)[0] + "/preview", "chat_completions") == ()


@pytest.mark.parametrize("provider", SCORER_PROVIDERS)
@pytest.mark.parametrize(("model", "value"), [
    ("minimax-m3.1-flash", "minimal"),
    ("step-5-preview", "max"),
    ("glm-5.3-flash", "medium"),
])
def test_measured_go_vocabulary_rejects_an_answer_outside_the_route_list(provider, model,
                                                                        value):
    transport = FakeTransport(response(provider, value))
    client = make_client(provider, transport)
    # Fail open: no rewrite, and a received answer is never retried.
    assert client.classify_effort_detail(
        "bounded task", GO_MEASURED_ROUTES[model]) == (None, "malformed_response")
    assert len(transport.calls) == 1
