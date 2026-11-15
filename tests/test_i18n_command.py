"""Localized ``/hae`` output: shipped catalogs, key parity, fallback and registration.

The English default stays at every ``_t`` call site in ``command.py``, so that source
is the reference here: the three catalogs must cover exactly its keys with the same
``{placeholders}`` and line breaks, and a host without ``register_locale`` — or with
one broken catalog — must keep rendering the English text instead of failing.
"""

from __future__ import annotations

import ast
import string
import sys
import types

import pytest

from conftest import PLUGIN_DIR, import_plugin

i18n_text = import_plugin("i18n_text")
LANGUAGES = tuple(i18n_text.CATALOG_LANGUAGES)


# ── helpers ─────────────────────────────────────────────────────────────────

def _raw_catalog(path):
    """Parse a catalog without the host's flattening, so nesting stays visible."""
    from ruamel.yaml import YAML
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def _placeholders(text):
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name})


def _module_strings(tree):
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                continue
    return consts


#: ``_t(*entry)`` call sites unpack these tables instead of naming their key.
UNPACKED_TABLES = ("_DYNAMIC_REASON_KEYS", "_CACHE_EXCLUSION_KEYS")


def _command_defaults():
    """``{key: English default}`` exactly as ``command.py`` passes them to ``_t``."""
    tree = ast.parse((PLUGIN_DIR / "command.py").read_text(encoding="utf-8"))
    consts = _module_strings(tree)
    table = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_t = ((isinstance(func, ast.Name) and func.id == "_t")
                or (isinstance(func, ast.Attribute) and func.attr == "_t"))
        if not is_t:
            continue
        if len(node.args) < 2:
            assert node.args and isinstance(node.args[0], ast.Starred), (
                f"_t at command.py:{node.lineno} has no literal key and does not "
                "unpack one of the known tables")
            continue
        key = ast.literal_eval(node.args[0])
        default_node = node.args[1]
        default = consts[default_node.id] if isinstance(default_node, ast.Name) \
            else ast.literal_eval(default_node)
        assert isinstance(key, str) and isinstance(default, str), node.lineno
        assert table.get(key, default) == default, f"{key} has two English defaults"
        table[key] = default
    for name in UNPACKED_TABLES:
        pairs = consts.get(name)
        assert isinstance(pairs, dict), f"{name} must stay a literal key/default table"
        for key, default in pairs.values():
            assert table.get(key, default) == default, f"{key} has two English defaults"
            table.setdefault(key, default)
    return table


def _fake_host(monkeypatch, translate):
    """Install a fake ``agent.i18n`` whose ``t`` behaves like *translate*."""
    module = types.ModuleType("agent")
    module.i18n = types.SimpleNamespace(t=translate)
    monkeypatch.setitem(sys.modules, "agent", module)


class _Host:
    """Minimal stand-in for ``PluginContext``: validates a catalog like the host does."""

    def __init__(self, unparseable=()):
        self.unparseable = set(unparseable)
        self.registered = []

    def register_locale(self, lang, source):
        if lang in self.unparseable:
            raise ValueError(f"malformed catalog for {lang}")
        self.registered.append((lang, str(source)))
        return object()


# ── catalogs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", LANGUAGES)
def test_each_catalog_parses_and_is_flat(lang):
    path = i18n_text.catalog_path(lang)
    assert path.is_file(), f"missing catalog {path}"
    raw = _raw_catalog(path)
    assert isinstance(raw, dict) and raw, f"{lang}: a catalog is a non-empty mapping"
    for key, value in raw.items():
        assert isinstance(key, str) and "." in key and key == key.strip(), key
        assert " " not in key and "\t" not in key, f"{lang}: undocumented key shape {key!r}"
        assert isinstance(value, str), f"{lang}:{key} must be a string, not a nested value"
        assert value.strip(), f"{lang}:{key} is empty"
        assert "\t" not in value, f"{lang}:{key} carries a literal tab"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_host_loader_accepts_each_catalog_unchanged(lang):
    path = i18n_text.catalog_path(lang)
    try:
        from agent.i18n_layers import load_locale_source
    except Exception:  # pragma: no cover - no Hermes source tree
        return
    assert load_locale_source(str(path)) == _raw_catalog(path)


def test_the_three_catalogs_and_the_code_agree_on_one_key_set():
    code = _command_defaults()
    catalogs = {lang: set(_raw_catalog(i18n_text.catalog_path(lang))) for lang in LANGUAGES}

    assert len(LANGUAGES) == 3, "fr, de and es are the shipped languages"
    for lang, keys in catalogs.items():
        missing = sorted(code.keys() - keys)
        extra = sorted(keys - code.keys())
        assert not missing, f"{lang}.yaml is missing {missing}"
        assert not extra, f"{lang}.yaml has keys command.py never uses: {extra}"
    assert catalogs == {lang: catalogs[LANGUAGES[0]] for lang in LANGUAGES}, (
        "the three catalogs must carry identical key sets")
    # Pinned so removing a key from the code and all catalogs still fails here.
    assert len(code) == 116


@pytest.mark.parametrize("lang", LANGUAGES)
def test_placeholders_and_line_breaks_match_the_english_default(lang):
    catalog = _raw_catalog(i18n_text.catalog_path(lang))
    for key, english in _command_defaults().items():
        translation = catalog[key]
        assert _placeholders(translation) == _placeholders(english), (
            f"{lang}.yaml:{key} placeholders {_placeholders(translation)} "
            f"!= {_placeholders(english)}")
        assert translation.count("\n") == english.count("\n"), (
            f"{lang}.yaml:{key} changed the number of line breaks")


# ── fallback ────────────────────────────────────────────────────────────────

def test_without_a_catalog_the_english_default_is_used(monkeypatch):
    # An unregistered key is exactly what the host answers with: the bare key.
    _fake_host(monkeypatch, lambda key, lang=None, **kw: key)
    assert i18n_text._t("hae.test.missing", "Default") == "Default"
    # A host with no agent.i18n at all must read the same.
    monkeypatch.setitem(sys.modules, "agent", None)
    assert i18n_text._t("hae.test.missing", "Default") == "Default"


def test_t_uses_a_translation_and_falls_back_on_bad_answers(monkeypatch):
    _fake_host(monkeypatch, lambda key, lang=None, **kw: "Traduit")
    assert i18n_text._t("hae.test.key", "Default") == "Traduit"

    _fake_host(monkeypatch, lambda key, lang=None, **kw: "")
    assert i18n_text._t("hae.test.key", "Default") == "Default"

    _fake_host(monkeypatch, lambda key, lang=None, **kw: None)
    assert i18n_text._t("hae.test.key", "Default") == "Default"

    def _raise(key, lang=None, **kw):
        raise RuntimeError("host i18n is broken")

    _fake_host(monkeypatch, _raise)
    assert i18n_text._t("hae.test.key", "Default") == "Default"


def test_t_formats_a_translation_and_survives_a_lost_placeholder(monkeypatch):
    _fake_host(monkeypatch, lambda key, lang=None, **kw: "Salut {name}")
    assert i18n_text._t("hae.test.key", "Default {name}", name="Ana") == "Salut Ana"

    # A translation that lost the placeholder must not raise into the command.
    _fake_host(monkeypatch, lambda key, lang=None, **kw: "Salut {other}")
    assert i18n_text._t("hae.test.key", "Default {name}", name="Ana") == "Default Ana"


# ── registration ────────────────────────────────────────────────────────────

def test_register_catalogs_skips_a_host_without_register_locale():
    class OlderHost:
        pass

    assert i18n_text.register_catalogs(OlderHost()) == 0


def test_register_catalogs_registers_every_shipped_catalog():
    host = _Host()
    assert i18n_text.register_catalogs(host) == len(LANGUAGES)
    assert [lang for lang, _ in host.registered] == list(LANGUAGES)
    for lang, source in host.registered:
        assert source.endswith(f"{lang}.yaml")


def test_register_catalogs_skips_a_malformed_catalog_and_keeps_the_others(
        monkeypatch, tmp_path):
    for lang in LANGUAGES:
        (tmp_path / f"{lang}.yaml").write_text(f'hae.usage: "{lang} usage"\n',
                                               encoding="utf-8")
    (tmp_path / f"{LANGUAGES[-1]}.yaml").write_text("broken: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(i18n_text, "LOCALES_DIR", tmp_path)

    host = _Host(unparseable={LANGUAGES[-1]})
    assert i18n_text.register_catalogs(host) == len(LANGUAGES) - 1
    assert [lang for lang, _ in host.registered] == list(LANGUAGES[:-1])


def test_register_catalogs_swallows_a_file_the_host_cannot_read():
    class StrictHost:
        def register_locale(self, lang, source):
            raise FileNotFoundError(source)

    assert i18n_text.register_catalogs(StrictHost()) == 0
