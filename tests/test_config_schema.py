"""config_schema: the Desktop settings form contract for hermes-adaptive-effort."""

from __future__ import annotations

from conftest import PLUGIN_DIR, import_plugin

middleware = import_plugin("middleware")


def _load_manifest() -> dict:
    text = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    from ruamel.yaml import YAML
    data = YAML(typ="safe").load(text)
    assert isinstance(data, dict), "plugin.yaml must parse as a YAML mapping"
    return data


def test_config_schema_covers_every_default():
    schema = _load_manifest().get("config_schema")
    assert isinstance(schema, dict), "config_schema drives the Desktop settings form"
    assert set(schema) == set(middleware.DEFAULTS), (
        f"schema keys {sorted(schema)} != DEFAULTS {sorted(middleware.DEFAULTS)}")


def test_config_schema_modes_match_valid_modes():
    schema = _load_manifest()["config_schema"]
    assert middleware.VALID_MODES == ("auto", "once", "always", "off")
    description = schema["mode"]["description"].lower()
    assert "every message" in description
    assert "one decision per model and route" in description
    assert "every new user message" in description
    assert "never scores or changes effort" in description
    for key in ("mode", "subagent_mode"):
        entry = schema[key]
        assert set(entry.get("choices") or []) == set(middleware.VALID_MODES)
        assert entry.get("default") == middleware.DEFAULTS[key]
        assert entry.get("type") == "str"


def test_effort_model_setting_replaces_the_injection_setting():
    schema = _load_manifest()["config_schema"]
    assert "effort_models" in schema
    assert "force_injection_models" not in schema
    assert "you assert" in schema["effort_models"]["description"]


def test_scorer_schema_keeps_jev_default_and_requires_model_for_openrouter():
    schema = _load_manifest()["config_schema"]
    assert set(schema["scorer_provider"].get("choices") or []) == {
        "jev", "openrouter", "cloudflare", "custom", "openai_decision", "nace"}
    assert schema["scorer_provider"].get("default") == middleware.DEFAULTS["scorer_provider"]
    assert schema["jev_model"].get("default") == middleware.DEFAULTS["jev_model"]
    assert schema["jev_model"].get("type") == "str"
    assert "Jev" in schema["jev_model"].get("label", "")
    assert schema["scorer_model"].get("default") == middleware.DEFAULTS["scorer_model"]
    assert "OPENROUTER_API_KEY" in schema["scorer_provider"].get("description", "")
    assert "OPENAI_API_KEY" in schema["scorer_provider"].get("description", "")
    assert "gpt-6-luna" in schema["scorer_provider"].get("description", "")
    assert "required when" in schema["scorer_model"].get("description", "")
    assert "Optional for OpenAI Decisions" in schema["scorer_model"].get("description", "")
    assert "NACE_API_KEY" in schema["scorer_provider"].get("description", "")
    assert schema["nace_model"].get("type") == "str"
    assert schema["nace_model"].get("default") == middleware.DEFAULTS["nace_model"] == "drex-v2.0"


def test_custom_scorer_schema_defaults_and_choices_match_settings():
    schema = _load_manifest()["config_schema"]
    for key, expected_type in (
        ("custom_endpoint", "str"),
        ("custom_api_format", "str"),
        ("custom_auth", "str"),
    ):
        assert schema[key]["type"] == expected_type
        assert schema[key]["default"] == middleware.DEFAULTS[key]
    assert set(schema["custom_api_format"].get("choices") or []) == {
        "systemone"}
    assert set(schema["custom_auth"].get("choices") or []) == {"none", "bearer"}


def test_config_schema_types_and_defaults_match_middleware():
    schema = _load_manifest()["config_schema"]
    assert schema["timeout_s"]["type"] == "float"
    assert float(schema["timeout_s"]["default"]) == float(middleware.DEFAULTS["timeout_s"]) == 0
    assert schema["max_turns"]["type"] == "int"
    assert int(schema["max_turns"]["default"]) == int(middleware.DEFAULTS["max_turns"])
    assert schema["scorer_pause_failures"]["type"] == "int"
    assert int(schema["scorer_pause_failures"]["default"]) == \
        int(middleware.DEFAULTS["scorer_pause_failures"]) == 3
    assert schema["scorer_pause_s"]["type"] == "float"
    assert float(schema["scorer_pause_s"]["default"]) == \
        float(middleware.DEFAULTS["scorer_pause_s"]) == 60.0
    assert schema["prompt_chars"]["type"] == "int"
    assert int(schema["prompt_chars"]["default"]) == int(middleware.DEFAULTS["prompt_chars"])
    assert schema["endpoint"]["type"] == "str"
    assert schema["cloudflare_account_id"]["type"] == "str"
    cloudflare_client = import_plugin("cloudflare_client")
    assert set(schema["cloudflare_model"]["choices"]) == set(cloudflare_client.MODEL_PATHS)
    assert schema["cloudflare_model"]["default"] == cloudflare_client.DEFAULT_MODEL_SELECTOR
    assert str(schema["endpoint"]["default"]) == str(middleware.DEFAULTS["endpoint"])
    assert schema["classification_instructions"]["default"] == ""
    assert "2000" in schema["classification_instructions"]["description"]
    for key in ("show_tui_status", "show_desktop_popup"):
        assert schema[key]["type"] == "bool"
        assert schema[key]["default"] is middleware.DEFAULTS[key] is True
    assert schema["debug"]["type"] == "bool"
    assert schema["debug"]["default"] is middleware.DEFAULTS["debug"] is False
    assert schema["use_target_model_context"]["type"] == "bool"
    assert schema["use_target_model_context"]["default"] is False
    assert "observed effort" in schema["use_target_model_context"]["description"]
    assert schema["dialogue_context_messages"]["type"] == "int"
    assert schema["dialogue_context_messages"]["default"] == 4
    assert middleware.DEFAULTS["dialogue_context_messages"] == 4
    assert "1200" in schema["dialogue_context_messages"]["description"]
    assert schema["continuation_downgrade_votes"]["type"] == "int"
    assert schema["continuation_downgrade_votes"]["default"] == 2
    assert middleware.DEFAULTS["continuation_downgrade_votes"] == 2
    assert "dialogue_context_messages" in schema["continuation_downgrade_votes"]["description"]
    for key in ("effort_floor", "effort_ceiling"):
        assert schema[key]["type"] == "str"
        assert schema[key]["default"] == middleware.DEFAULTS[key] == ""
    assert schema["tool_error_escalation"]["type"] == "bool"
    assert schema["tool_error_escalation"]["default"] is True
    assert middleware.DEFAULTS["tool_error_escalation"] is True
    assert schema["goal_continuation_escalation"]["type"] == "bool"
    assert schema["goal_continuation_escalation"]["default"] is False
    assert middleware.DEFAULTS["goal_continuation_escalation"] is False
    assert "goal" in schema["goal_continuation_escalation"]["description"]
    assert schema["dynamic_trial"]["type"] == "bool"
    assert schema["dynamic_trial"]["default"] is True
    assert middleware.DEFAULTS["dynamic_trial"] is True
    assert "unmeasured" in schema["dynamic_trial"]["description"]


def test_config_schema_declares_no_secret():
    schema = _load_manifest()["config_schema"]
    types = {key: entry.get("type") for key, entry in schema.items()}
    assert "secret" not in set(types.values()), (
        f"credentials stay in TYPESAFE_API_KEY or OPENROUTER_API_KEY/.env, never config.yaml: {types}")
