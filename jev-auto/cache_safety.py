"""Per-route cache safety for an effort rewrite.

Prompt caches are keyed on the concrete model and the exact rendered prefix, so
the question is not "does effort belong to the request" but "does changing it
change the PREFIX". Two outcomes were verified against this Hermes install:

* ``codex_responses`` (and the OpenAI-compatible chat family) pass effort as a
  request field. Verified live: ``ResponsesApiTransport.build_kwargs`` returns an
  identical ``prompt_cache_key`` for ``low`` and ``high``, and the effort string
  never appears in ``input`` or ``instructions``. The prefix is untouched, so a
  per-turn effort costs nothing.
* The Anthropic family renders its thinking configuration into the prompt, and
  the provider documents that changing it invalidates message blocks. Its
  transport carries ``thinking.budget_tokens`` rather than an effort level, so an
  effort rewrite there is not merely cache-hostile, it is meaningless.

Hermes ships no ``cache_safe`` helper, so this table lives next to the code that
does the rewriting. The default is ``False``: an unrecognised route is treated as
unsafe, because guessing wrong silently costs cache reads.
"""

from __future__ import annotations

from typing import Optional

#: ``api_mode`` values verified in agent/agent_init.py.
CODEX_RESPONSES = "codex_responses"
CHAT_COMPLETIONS = "chat_completions"
ANTHROPIC_MESSAGES = "anthropic_messages"

#: Routes where effort is a request parameter, never rendered into the prompt.
#: Evidence: the transport builds it top-level / in the body, and the rendered
#: input is byte-identical whatever the level.
CACHE_SAFE_API_MODES: frozenset = frozenset({CODEX_RESPONSES, CHAT_COMPLETIONS})

#: Routes that render thinking configuration into the prompt. An effort change
#: invalidates the cached message blocks there (provider-documented).
CACHE_UNSAFE_API_MODES: frozenset = frozenset({ANTHROPIC_MESSAGES})


def effort_is_cache_safe(provider: Optional[str] = None, model: Optional[str] = None,
                         api_mode: Optional[str] = None) -> bool:
    """May the effort change every turn, or must it be pinned for the session?

    ``False`` for anything unrecognised. The cost of a wrong ``True`` is a
    silently degraded cache, which is invisible in the response and only shows up
    in the bill; the cost of a wrong ``False`` is one extra pinned level.
    """
    mode = str(api_mode or "").strip().lower()
    if mode in CACHE_UNSAFE_API_MODES:
        return False
    if mode in CACHE_SAFE_API_MODES:
        return True
    return False


def explain(provider: Optional[str] = None, model: Optional[str] = None,
            api_mode: Optional[str] = None) -> str:
    """One short, human-readable reason for a ``/jev-auto status`` line."""
    mode = str(api_mode or "").strip().lower()
    if mode in CACHE_SAFE_API_MODES:
        return f"cache-safe on {mode}: effort is a request field, not prompt text"
    if mode in CACHE_UNSAFE_API_MODES:
        return f"cache-hostile on {mode}: thinking config is rendered into the prompt"
    return f"unknown route ({mode or 'no api_mode'}): treated as cache-hostile"
