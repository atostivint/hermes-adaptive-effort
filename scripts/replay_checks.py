"""Pure answer checks for the effort replay corpus (stdlib only, executes nothing).

Four check types: ``exact`` (normalised string), ``regex`` (``re.search``, case-insensitive),
``json_schema`` (a small validator for a documented subset) and ``numeric`` (last number in
the answer, optional absolute tolerance). A model answer is data: it is never evaluated.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List

CHECK_TYPES = ("exact", "regex", "json_schema", "numeric")
_NUMBER = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?|-?\.\d+")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def normalise(text: str) -> str:
    """Trim, drop wrapping quotes/backticks and a final full stop, collapse spaces, casefold."""
    value = " ".join(str(text).split()).strip()
    if value.endswith(".") and not value.endswith(".."):
        value = value[:-1].rstrip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        value = value[1:-1].strip()
    return value.casefold()


def last_number(text: str) -> "float | None":
    found = _NUMBER.findall(str(text))
    if not found:
        return None
    try:
        return float(found[-1].replace(",", ""))
    except ValueError:
        return None


def extract_json(text: str) -> Any:
    """First JSON value in *text* (fenced block, whole text, or first ``{``/``[``); else raises."""
    body = str(text).strip()
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        return json.loads(body)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(body):
        if char in "{[":
            try:
                return decoder.raw_decode(body[index:])[0]
            except ValueError:
                continue
    raise ValueError("no json")


def schema_errors(value: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Violations of the supported subset: type, enum, const, required, properties,
    additionalProperties (bool), items, minimum, maximum. Unknown keywords are rejected."""
    known = {"type", "enum", "const", "required", "properties", "additionalProperties",
             "items", "minimum", "maximum"}
    errors: List[str] = [f"{path}: unsupported keyword {key}" for key in schema
                         if key not in known]
    expected = schema.get("type")
    if expected is not None and not _TYPES.get(expected, lambda _v: False)(value):
        return errors + [f"{path}: type"]
    if "const" in schema and (value != schema["const"]
                              or isinstance(value, bool) != isinstance(schema["const"], bool)):
        errors.append(f"{path}: const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: maximum")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, sub in props.items():
            if key in value:
                errors.extend(schema_errors(value[key], sub, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            errors.extend(f"{path}.{key}: additional" for key in value if key not in props)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, element in enumerate(value):
            errors.extend(schema_errors(element, schema["items"], f"{path}[{index}]"))
    return errors


def validate_check(check: Any) -> List[str]:
    """Problems with a corpus ``check`` definition (empty when usable)."""
    if not isinstance(check, dict) or check.get("type") not in CHECK_TYPES:
        return ["check type unknown"]
    kind = check["type"]
    if kind == "exact":
        return [] if isinstance(check.get("answer"), str) and check["answer"] else ["exact answer"]
    if kind == "regex":
        try:
            re.compile(check.get("pattern"))
        except Exception:
            return ["regex pattern"]
        return []
    if kind == "numeric":
        answer, tol = check.get("answer"), check.get("tolerance", 0)
        ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                 for v in (answer, tol)) and tol >= 0
        return [] if ok else ["numeric answer or tolerance"]
    schema = check.get("schema")
    if not isinstance(schema, dict):
        return ["json_schema schema"]
    return [p for p in schema_errors({}, schema) if "unsupported keyword" in p]


def run_check(check: Dict[str, Any], text: Any) -> bool:
    """``True`` when *text* satisfies *check*; any malformed input is a failed check."""
    if not isinstance(text, str) or not text.strip() or validate_check(check):
        return False
    kind = check["type"]
    if kind == "exact":
        return normalise(text) == normalise(check["answer"])
    if kind == "regex":
        return re.search(check["pattern"], text.strip(), re.I | re.S) is not None
    if kind == "numeric":
        found = last_number(text)
        return found is not None and abs(found - check["answer"]) <= check.get("tolerance", 0) + 1e-9
    try:
        return not schema_errors(extract_json(text), check["schema"])
    except ValueError:
        return False
