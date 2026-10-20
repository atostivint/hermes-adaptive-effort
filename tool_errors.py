"""Local tool-error signal for ``tool_error_escalation`` (issue #34).

Pure and stdlib-only: no I/O, no Hermes import, no scorer. It reads the tool results already
present in a request and reports which error fingerprints repeated after an edit attempt.
Error text never leaves this module: callers only ever see SHA-256 hex digests.

Hermes result shapes this reads (pinned ref in ``.github/workflows/ci.yml``):

* a tool result is a string, normally JSON (``role: "tool"`` ``content`` in Chat Completions,
  ``function_call_output.output`` in Responses; a list of text parts is flattened);
* error: a non-empty ``error`` key (``tools/terminal_tool.py::_error_json`` and the file tools),
  or a non-zero ``exit_code`` (terminal; the failure text is then ``output``, with ``error``
  when present) — ``agent/display.py::_detect_tool_failure``;
* never an error: a harness refusal (``guardrail_refusal: true``,
  ``agent/tool_result_classification.py::is_guardrail_refusal``);
* Anthropic Messages: a ``tool_result`` block with ``is_error: true``.

File-editing tools are Hermes' ``FILE_MUTATING_TOOL_NAMES`` (``write_file``, ``patch``).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from typing import Any, List, Optional, Tuple

#: Hermes' own file-mutating tools (``agent/tool_result_classification.py``).
EDIT_TOOL_NAMES = frozenset({"write_file", "patch"})
#: Only the most recent tool results of the current user turn are compared.
LOOKBACK_RESULTS = 20
#: Normalised error text is cut to this many characters (the tail) before hashing.
FINGERPRINT_CHARS = 2000

_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"                  # CSI sequences (colours, cursor)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"      # OSC sequences
    r"|\x1b[@-Z\\-_]"                           # two-character escapes
)
_TEMP_PATH = re.compile(
    r"(?:/private)?/(?:var/folders|var/tmp|tmp)/[^\s'\"<>)\],;]*"
    r"|[A-Za-z]:[\\/](?:[^\s'\"<>\\/]+[\\/])*?(?:Temp|tmp)[\\/][^\s'\"<>)\],;]*",
    re.IGNORECASE,
)
_HEX_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]+\b")
_FILE_POSITION = re.compile(r"(\.\w{1,8}):\d+(?::\d+)?\b")
_BARE_POSITION = re.compile(r"(?<![\w:]):\d+:\d+\b")
_LINE_NUMBER = re.compile(r"\b(lines?)\s+\d+(?:\s*[-–]\s*\d+)?", re.IGNORECASE)
_CLOCK = re.compile(r"\b\d+:\d{2}:\d{2}(?:\.\d+)?\b")
_DURATION = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s?(?:ms|µs|us|ns|seconds?|secs?|s)\b", re.IGNORECASE)


def normalize_error_text(text: str) -> str:
    """Strip ANSI, unify line endings, replace volatile tokens; keep messages and values."""
    value = _ANSI.sub("", text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _TEMP_PATH.sub("<tmp>", value)
    value = _HEX_ADDRESS.sub("<hex>", value)
    value = _FILE_POSITION.sub(r"\1:<n>", value)
    value = _BARE_POSITION.sub(":<n>:<n>", value)
    value = _LINE_NUMBER.sub(r"\1 <n>", value)
    value = _CLOCK.sub("<dur>", value)
    value = _DURATION.sub("<dur>", value)
    return value.strip()


def fingerprint(text: Any) -> Optional[str]:
    """SHA-256 of the capped, normalised error text; ``None`` when nothing is left."""
    if not isinstance(text, str):
        return None
    normalized = normalize_error_text(text)
    if not normalized:
        return None
    return hashlib.sha256(normalized[-FINGERPRINT_CHARS:].encode("utf-8", "replace")).hexdigest()


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, bool):
        return ""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def result_error_text(content: Any, flagged: bool = False) -> Optional[str]:
    """The error text of one tool result, or ``None`` when the result is not an error."""
    text = _flatten(content)
    if flagged:
        return text or None
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("guardrail_refusal") is True:
        return None
    parts: List[str] = []
    error = _text_value(data.get("error")).strip()
    if error:
        parts.append(error)
    exit_code = data.get("exit_code")
    if (isinstance(exit_code, (int, float)) and not isinstance(exit_code, bool)
            and exit_code != 0):
        output = _text_value(data.get("output")).strip()
        if output:
            parts.append(output)
        if not parts:
            parts.append(f"exit {exit_code}")
    return "\n".join(parts) if parts else None


_EDIT = "edit"
_RESULT = "result"

#: Fingerprint per tool-result text, keyed by the SHA-256 of that text (never the text itself),
#: so a tool loop that re-sends the same results does not re-parse and re-normalise them.
_FINGERPRINT_MEMO: "OrderedDict[str, Optional[str]]" = OrderedDict()
_FINGERPRINT_MEMO_LIMIT = 256
_memo_lock = threading.Lock()


def _result_fingerprint(content: Any, flagged: bool = False) -> Optional[str]:
    """``fingerprint(result_error_text(content, flagged))``, memoised on the text's SHA-256."""
    text = _flatten(content)
    key = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest() + ("!" if flagged else "")
    with _memo_lock:
        if key in _FINGERPRINT_MEMO:
            _FINGERPRINT_MEMO.move_to_end(key)
            return _FINGERPRINT_MEMO[key]
    digest = fingerprint(result_error_text(text, flagged))
    with _memo_lock:
        _FINGERPRINT_MEMO[key] = digest
        while len(_FINGERPRINT_MEMO) > _FINGERPRINT_MEMO_LIMIT:
            _FINGERPRINT_MEMO.popitem(last=False)
    return digest


def clear_memo() -> None:
    with _memo_lock:
        _FINGERPRINT_MEMO.clear()


def _is_turn_boundary(message: dict) -> bool:
    """A user message that is not purely tool-result blocks starts a new turn."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, list) and content:
        return not all(isinstance(block, dict) and block.get("type") == "tool_result"
                       for block in content)
    return True


def _message_events(message: dict) -> List[Tuple[str, Any, bool]]:
    """``(kind, raw content, flagged)`` for one message, in the order the forward scan used."""
    events: List[Tuple[str, Any, bool]] = []
    role = message.get("role")
    if role == "tool":
        return [(_RESULT, message.get("content"), False)]
    if role == "assistant":
        calls = message.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            name = (function or {}).get("name") if isinstance(function, dict) else None
            if name in EDIT_TOOL_NAMES:
                events.append((_EDIT, None, False))
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in EDIT_TOOL_NAMES:
                events.append((_EDIT, None, False))
            elif block.get("type") == "tool_result":
                events.append((_RESULT, block.get("content"), block.get("is_error") is True))
    return events


def _scan_backwards(items: List[Any], is_boundary, item_events) -> List[Tuple[str, Optional[str]]]:
    """The last ``LOOKBACK_RESULTS`` results of the current turn (plus the edits between them).

    Walks from the newest item and stops at the turn boundary or once the 20th result from the
    end is in hand, so a long conversation costs only its current tail. Error text is turned
    into a fingerprint only for the results that are kept.
    """
    chunks: List[List[Tuple[str, Any, bool]]] = []
    results = 0
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if not isinstance(item, dict):
            continue
        if is_boundary(item):
            break
        events = item_events(item)
        if not events:
            continue
        chunks.append(events)
        results += sum(1 for event in events if event[0] == _RESULT)
        if results >= LOOKBACK_RESULTS:
            break
    if results < 2:
        return []  # a repeat needs two results
    flat = [event for chunk in reversed(chunks) for event in chunk]
    positions = [i for i, event in enumerate(flat) if event[0] == _RESULT]
    if len(positions) > LOOKBACK_RESULTS:
        flat = flat[positions[-LOOKBACK_RESULTS]:]
    return [(kind, _result_fingerprint(content, flagged) if kind == _RESULT else None)
            for kind, content, flagged in flat]


def _events_from_messages(messages: List[Any]) -> List[Tuple[str, Optional[str]]]:
    return _scan_backwards(messages, _is_turn_boundary, _message_events)


def _input_events(item: dict) -> List[Tuple[str, Any, bool]]:
    kind = item.get("type")
    if kind == "function_call" and item.get("name") in EDIT_TOOL_NAMES:
        return [(_EDIT, None, False)]
    if kind == "function_call_output":
        return [(_RESULT, item.get("output"), False)]
    return []


def _events_from_input(items: List[Any]) -> List[Tuple[str, Optional[str]]]:
    return _scan_backwards(items, lambda item: item.get("role") == "user", _input_events)


def repeated_error_fingerprints(request: Any) -> List[str]:
    """Fingerprints that repeated with an edit attempt in between, oldest first.

    Looks only at the current user turn and its last ``LOOKBACK_RESULTS`` tool results.
    """
    if not isinstance(request, dict):
        return []
    messages = request.get("messages")
    if isinstance(messages, list) and messages:
        events = _events_from_messages(messages)
    elif isinstance(request.get("input"), list):
        events = _events_from_input(request["input"])
    else:
        return []
    last_seen: dict = {}
    last_edit = -1
    repeated: List[str] = []
    for index, (kind, digest) in enumerate(events):
        if kind == _EDIT:
            last_edit = index
            continue
        if digest is None:
            continue
        previous = last_seen.get(digest)
        if previous is not None and last_edit > previous and digest not in repeated:
            repeated.append(digest)
        last_seen[digest] = index
    return repeated
