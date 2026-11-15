"""Localized text for the ``/hae`` command's own output.

The English string stays at the call site (``_t("hae.status.mode", "Mode: ...")``),
so the command reads correctly when no catalog is installed. A catalog only
overrides it, key by key, when the host's ``agent.i18n.t`` resolves the key: the
bare key that ``t()`` returns on a miss is the "nothing matched" signal, never a
translation. Only human wording is keyed here — reason codes, schema ids, field
names, effort level names and provider ids stay in the code, untranslated.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Catalogs shipped with the payload, as lowercase host language ids.
LOCALES_DIR = Path(__file__).resolve().parent / "locales"
CATALOG_LANGUAGES = ("fr", "de", "es")
#: The Python ``t()`` surface only; the Desktop popover is a separate surface.
SURFACE = "core"


def _translate(key: str) -> Optional[str]:
    """Return the host's rendering of *key*, or ``None`` when nothing matched."""
    try:
        from agent import i18n  # lazy: the plugin must import without Hermes
    except Exception:  # pragma: no cover - no Hermes source tree
        return None
    try:
        value = i18n.t(key)
    except Exception:  # pragma: no cover - t() is total, but stay fail-open
        return None
    if not isinstance(value, str) or not value or value == key:
        return None
    return value


def _t(key: str, default: str, **fmt: Any) -> str:
    """Translate *key*, else keep the English *default* that sits at the call site.

    ``fmt`` is applied with ``str.format``. A translation that lost a placeholder
    falls back to the English default rather than raising into the command.
    """
    text = _translate(key)
    text = default if text is None else text
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        logger.debug("i18n placeholder mismatch for key %r; using the English default", key)
        try:
            return default.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return default


def register_catalogs(ctx: Any) -> int:
    """Register every shipped catalog on the core surface; return how many landed.

    Guarded on purpose: an older host without ``register_locale`` keeps the
    English defaults unchanged, and one malformed catalog is skipped rather than
    failing plugin load.
    """
    register = getattr(ctx, "register_locale", None)
    if not callable(register):
        return 0
    registered = 0
    for lang in CATALOG_LANGUAGES:
        try:
            register(lang, str(LOCALES_DIR / f"{lang}.yaml"))
        except Exception as exc:
            logger.debug("hermes-adaptive-effort: locale %s skipped: %s", lang, exc)
            continue
        registered += 1
    return registered


def catalog_path(lang: str) -> Path:
    """Path of the shipped catalog for *lang* (used by the tests)."""
    return LOCALES_DIR / f"{lang}.yaml"
