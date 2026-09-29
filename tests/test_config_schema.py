"""config_schema: the Desktop settings form contract for jev-auto-effort."""

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
    for key in ("mode", "subagent_mode"):
        entry = schema[key]
        assert set(entry.get("choices") or []) == set(middleware.VALID_MODES)
        assert entry.get("default") == "off"
        assert entry.get("type") == "str"


def test_config_schema_types_and_defaults_match_middleware():
    schema = _load_manifest()["config_schema"]
    assert schema["timeout_s"]["type"] == "float"
    assert float(schema["timeout_s"]["default"]) == float(middleware.DEFAULTS["timeout_s"])
    assert schema["max_turns"]["type"] == "int"
    assert int(schema["max_turns"]["default"]) == int(middleware.DEFAULTS["max_turns"])
    assert schema["prompt_chars"]["type"] == "int"
    assert int(schema["prompt_chars"]["default"]) == int(middleware.DEFAULTS["prompt_chars"])
    assert schema["endpoint"]["type"] == "str"
    assert str(schema["endpoint"]["default"]) == str(middleware.DEFAULTS["endpoint"])


def test_config_schema_declares_no_secret():
    schema = _load_manifest()["config_schema"]
    types = {key: entry.get("type") for key, entry in schema.items()}
    assert "secret" not in set(types.values()), (
        f"credential stays in TYPESAFE_API_KEY/.env, never config.yaml: {types}")
