"""Pure rubric-score -> effort mapping.

No I/O, no Hermes imports at module import time (core is imported lazily so the
unit tests run without a Hermes install). Rules:

* Scorers answer an ordered 3-level score rubric: 0 = low, 1 = medium, 2 = high,
  with deterministic thresholds <0.5, 0.5..<1.5, >=1.5.
* Anything outside 0..2, non-finite, or non-numeric is invalid -> ``None``.
* The target level is always clamped onto the route's own declared vocabulary
  (``agent.reasoning_effort.clamp_effort``): never an unsupported wire value.
"""

from __future__ import annotations

import math
from urllib.parse import urlsplit
from typing import Optional, Sequence

#: Normalized labels Hermes Adaptive Effort may select. Never a fourth value.
EFFORT_LABELS: tuple[str, ...] = ("low", "medium", "high")
CHOICE_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

#: Rubric boundaries (documented in README.md).
LOW_MAX = 0.5
MEDIUM_MAX = 1.5

# Exact Contributor Free slug; other Muse tiers can have different ceilings.
MUSE_CONTRIBUTOR_FREE = "muse-spark-1.3-contributor-free"
MUSE_CONTRIBUTOR_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
MUSE_STANDARD_13_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
MUSE_STANDARD_12_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
MUSE_INJECTION_EFFORTS = {
    "muse-spark-1.3": MUSE_STANDARD_13_EFFORTS,
    "muse-spark-1.2": MUSE_STANDARD_12_EFFORTS,
    "muse-spark-1.3-contributor-free": MUSE_CONTRIBUTOR_EFFORTS,
    "muse-spark-1.3-contributor": MUSE_CONTRIBUTOR_EFFORTS,
    "muse-spark-1.2-contributor": MUSE_CONTRIBUTOR_EFFORTS,
}

# Deliberately explicit Go route evidence. Each entry binds an exact model to
# the field/container and wire vocabulary published for that route; generic
# OpenAI-compatible fallback support is never sufficient to add a field.
OPEN_CODE_GO_INJECTION_ROUTES = {
    "codex_responses": {
        "gpt-6-luna": ("reasoning", ("low", "medium", "high", "xhigh")),
        "gpt-5.6-luna": ("reasoning", ("low", "medium", "high", "xhigh", "max")),
        "grok-4.7": ("reasoning", ("low", "medium", "high", "xhigh")),
        "grok-4.6": ("reasoning", ("low", "medium", "high", "xhigh")),
        "grok-4.5": ("reasoning", ("low", "medium", "high")),
        **{model: ("reasoning", values) for model, values in MUSE_INJECTION_EFFORTS.items()
           if model.endswith("-contributor")},
    },
    "chat_completions": {
        "glm-5.2": ("reasoning_effort", ("high", "max")),
        "glm-5.3": ("paired_effort", ("low", "high", "max")),
        "kimi-k3": ("reasoning_effort", ("low", "high", "max")),
        # Exact Go Chat route: live calls accepted all three values. Xiaomi does
        # not currently promise distinct reasoning intensity among them.
        "mimo-v2.6-flash": ("reasoning_effort", ("low", "medium", "high")),
        "deepseek-v4-pro": ("paired_effort", ("low", "high", "max")),
        "deepseek-v4-flash": ("paired_effort", ("low", "high", "max")),
        "deepseek-v4.1-flash": ("paired_effort", ("low", "medium", "high", "max")),
    },
}
_OPEN_CODE_GO_PROVIDERS = frozenset({"opencode-go", "opencode_go", "go", "opencode-go-sub"})

# Exact Anthropic API model registry. Profiles are documentation only; this
# table is the plugin's explicit assertion that the native Messages route can
# carry output_config.effort for these exact model ids.
ANTHROPIC_EFFORT_ROUTES = {
    "claude-fable-5-1": ("low", "medium", "high", "xhigh", "max"),
    "claude-mythos-5-1": ("low", "medium", "high", "xhigh", "max"),
    "claude-opus-5-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-opus-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-sonnet-5-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-fable-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-mythos-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-opus-4-8": ("low", "medium", "high", "xhigh", "max"),
    "claude-opus-4-7": ("low", "medium", "high", "xhigh", "max"),
    "claude-opus-4-6": ("low", "medium", "high", "max"),
    "claude-sonnet-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-sonnet-4-6": ("low", "medium", "high", "max"),
}

ANTHROPIC_PER_MESSAGE_MODELS = frozenset({
    "claude-fable-5-1", "claude-mythos-5-1", "claude-opus-5-5",
    "claude-opus-5", "claude-sonnet-5-5",
})


def score_to_label(score) -> Optional[str]:
    """Rubric score -> normalized label; ``None`` for anything invalid."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    value = float(score)
    if not math.isfinite(value) or not 0.0 <= value <= 2.0:
        return None
    if value < LOW_MAX:
        return "low"
    if value < MEDIUM_MAX:
        return "medium"
    return "high"


def wire_efforts(provider: Optional[str], model: Optional[str]) -> tuple[str, ...]:
    """Wire vocabulary the (provider, model) route *really* accepts, when it is narrow.

    ``route_supported_efforts`` is Hermes' **entry** clamp: for every route that is
    not Codex it returns the widest OpenAI-compatible vocabulary, on the assumption
    that the transport clamps again downstream. A plugin hooked into
    ``llm_request`` runs **after** that transport clamp, so writing a level the wide
    set allows but the vendor rejects makes the request fail (Moonshot K3 and Ox
    Alpha reject ``medium``; GLM-5.2 rejects ``low`` and ``medium``). Returns ``()``
    when the route has no narrower declared set — the caller then falls back to
    Hermes' route data.
    """
    bare = (model or "").strip().lower().rsplit("/", 1)[-1]
    if not bare:
        return ()
    if bare in MUSE_INJECTION_EFFORTS:
        return MUSE_INJECTION_EFFORTS[bare]
    if (provider or "").strip().lower() in _OPEN_CODE_GO_PROVIDERS:
        for route in OPEN_CODE_GO_INJECTION_ROUTES.values():
            declared = route.get(bare)
            if declared:
                return declared[1]
    try:
        from agent import reasoning_effort as _core
    except Exception:
        return ()
    try:
        if "kimi" in bare or "moonshot" in bare or _core._KIMI_K3_SLUG_RE.search(bare):
            # K3 = low/high/max, K2-era = low/medium/high (host-side detection reused).
            return tuple(_core.kimi_supported_efforts(bare))
        for prefix, attribute in (("glm-5.2", "GLM52_EFFORTS"), ("glm-5.3", "GLM53_EFFORTS")):
            if bare.startswith(prefix):
                return tuple(getattr(_core, attribute))
    except Exception:
        return ()
    return ()


def wire_overrides(vocabulary: Sequence[str]) -> Optional[dict]:
    """Declared vendor re-mapping for a level the vendor spells differently.

    Kimi K3's positional middle is ``high`` (the server default), so ``medium``
    must round **up** to ``high`` rather than down to ``low``; without this the
    clamp would silently halve the requested effort.
    """
    try:
        from agent import reasoning_effort as _core
    except Exception:
        return None
    try:
        if tuple(vocabulary) == tuple(_core.KIMI_K3_EFFORTS):
            return dict(_core.KIMI_K3_OVERRIDES)
        if tuple(vocabulary) == tuple(_core.GLM52_EFFORTS):
            return dict(_core.GLM52_OVERRIDES)
        if tuple(vocabulary) == tuple(_core.GLM53_EFFORTS):
            return dict(_core.GLM53_OVERRIDES)
        if tuple(vocabulary) == ("low", "high", "max"):
            # Several exact vendor APIs omit a literal medium tier and document
            # it as the next tier up; preserve that declared semantic mapping.
            return {"medium": "high", "xhigh": "max"}
    except Exception:
        return None
    return None


def supported_efforts(provider: Optional[str], model: Optional[str],
                      supported: Optional[Sequence[str]] = None) -> tuple[str, ...]:
    """Levels the route accepts, from Hermes' own route vocabulary table.

    ``supported`` overrides the lookup (used by tests and by callers that already
    read the provider profile). A route with a narrower *wire* vocabulary than the
    OpenAI-compatible default wins over the default. Unknown/undeclared routes fall
    back to the widest OpenAI-compatible vocabulary — the same default
    ``clamp_effort`` applies.
    """
    if supported:
        return tuple(str(level) for level in supported)
    try:
        from agent.reasoning_effort import route_supported_efforts
    except Exception:
        return ()
    try:
        if (provider or "").strip().lower() != "openai-codex":
            narrow = wire_efforts(provider, model)
            if narrow:
                return narrow
        return tuple(route_supported_efforts(provider, model))
    except Exception:
        return ()


def route_choice_levels(provider: Optional[str], model: Optional[str], api_mode: Optional[str],
                        base_url: Optional[str] = None) -> tuple[str, ...]:
    """Exact route vocabularies allowed to use native named-choice classification.

    This intentionally does not use Hermes' generic OpenAI-compatible fallback
    or model-profile documentation as route evidence.
    """
    provider_name = (provider or "").strip().lower()
    api = (api_mode or "").strip().lower()
    model_id = (model or "").strip().lower()
    bare = model_id.rsplit("/", 1)[-1]
    levels: Sequence[str] = ()
    if provider_name in _OPEN_CODE_GO_PROVIDERS:
        declared = OPEN_CODE_GO_INJECTION_ROUTES.get(api, {}).get(bare)
        if declared:
            levels = declared[1]
    elif provider_name in {"opencode", "opencode-zen", "opencode_zen", "zen"} \
            and api == "codex_responses":
        levels = MUSE_INJECTION_EFFORTS.get(bare, ())
    elif (provider_name == "openai-codex" and api == "codex_responses"
          and bare == "gpt-6.1-sol"):
        try:
            from agent.reasoning_effort import codex_supported_efforts
            levels = codex_supported_efforts(model_id)
        except Exception:
            levels = ()
    elif (provider_name == "anthropic" and api == "anthropic_messages"
          and _native_anthropic_host(base_url)):
        levels = ANTHROPIC_EFFORT_ROUTES.get(bare, ())
    return tuple(level for level in levels if level in CHOICE_LEVELS)


def _native_anthropic_host(base_url: Optional[str]) -> bool:
    try:
        parsed = urlsplit(str(base_url or ""))
        return (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower() == "api.anthropic.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except (TypeError, ValueError):
        return False


def map_effort(label: Optional[str], provider: Optional[str] = None,
               model: Optional[str] = None,
               supported: Optional[Sequence[str]] = None) -> Optional[str]:
    """Normalized label -> a wire-legal effort, or ``None`` when it cannot be mapped."""
    if not isinstance(label, str):
        return None
    level = label.strip().lower()
    if level not in CHOICE_LEVELS:
        return None
    vocabulary = supported_efforts(provider, model, supported)
    if not vocabulary:
        return None
    try:
        from agent.reasoning_effort import clamp_effort
    except Exception:
        return None
    try:
        clamped = clamp_effort(level, vocabulary, wire_overrides(vocabulary))
    except Exception:
        return None
    if not isinstance(clamped, str) or clamped.strip().lower() not in vocabulary:
        return None
    return clamped.strip().lower()


def map_named_choice(label: Optional[str], supported: Sequence[str]) -> Optional[str]:
    """Re-clamp a named choice after a route change without escalating to ``max``.

    The exact choice returned for its original route is already valid. When that
    decision crosses to another named-choice route, retain the existing explicit
    ``medium -> high`` vendor mapping, but do not reinterpret ``xhigh`` as the new
    route's maximum tier.
    """
    if not isinstance(label, str):
        return None
    level = label.strip().lower()
    vocabulary = tuple(item for item in supported if item in CHOICE_LEVELS)
    if level not in CHOICE_LEVELS or not vocabulary:
        return None
    try:
        from agent.reasoning_effort import clamp_effort
    except Exception:
        return None
    overrides = wire_overrides(vocabulary) or {}
    overrides = {key: value for key, value in overrides.items() if key != "xhigh"}
    try:
        clamped = clamp_effort(level, vocabulary, overrides)
    except Exception:
        return None
    if not isinstance(clamped, str) or clamped.strip().lower() not in vocabulary:
        return None
    return clamped.strip().lower()
