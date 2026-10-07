#!/usr/bin/env python3
"""Prepare and locally check a hermetic model-effort validation manifest.

The real-call path intentionally fails closed until the runner can prove a hard
per-send cost bound, reconcile provider counters, and observe the actual Hermes
transport send. This module never makes network requests. Decimal ledger
helpers are pure accounting utilities and are not connected to any transport.
The CLI's run command remains blocked and cannot invoke them for a send.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as _datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "hermes-adaptive-effort.validation-manifest.v1"
RESULT_SCHEMA = "hermes-adaptive-effort.validation-result.v1"
LEDGER_SCHEMA = "hermes-adaptive-effort.validation-ledger.v1"
CASE_VERSION = "2026-10-07"
CAPS = {
    "openrouter": {"hard_cap": "2.00", "working_cap": "1.80", "currency": "USD"},
    "opencode-go": {"hard_cap": "3.00", "working_cap": "2.70", "currency": "USD"},
}
CODEX_USAGE_POLICY = {
    "window_minutes": 300,
    "max_increase_percentage_points": 30,
    "api_mode": "codex_responses",
}
CODEX_MODEL_IDS = [
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
]
CODEX_CATALOG_SOURCE = "hermes_codex_authenticated_live_catalog"
CODEX_BASELINE_USED_PERCENT = 31
CODEX_OBSERVED_USED_PERCENT = 34
SCORE_LABELS = ((0, "low"), (1, "medium"), (2, "high"))
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CODEX_WIRE_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def manifest_hash(manifest: dict[str, Any]) -> str:
    """Hash canonical manifest JSON without its self-referential hash field."""
    value = dict(manifest)
    value.pop("manifest_hash", None)
    return _digest(value)


def stable_case_id(provider: str, exact_model: str, api_mode: str,
                   target_effort: str, case_version: str = CASE_VERSION) -> str:
    """Stable identifier for a (route, plugin output) case."""
    return _digest([provider, exact_model, api_mode, target_effort, case_version])


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _hermes_source() -> dict[str, Any]:
    candidates = [os.environ.get("HERMES_SOURCE_ROOT"),
                  os.environ.get("HERMES_HOME") and str(Path(os.environ["HERMES_HOME"]) / "hermes-agent"),
                  str(ROOT.parent / "hermes-agent"),
                  os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "hermes"),
                  "/usr/local/lib/hermes-agent",
                  str(Path.home() / ".hermes" / "hermes-agent")]
    for raw in candidates:
        if raw:
            path = Path(raw).expanduser().resolve()
            if (path / "agent" / "reasoning_effort.py").is_file():
                commit = None
                try:
                    commit = subprocess.run(
                        ["git", "-C", str(path), "rev-parse", "HEAD"],
                        check=True, capture_output=True, text=True, timeout=5,
                    ).stdout.strip() or None
                except (OSError, subprocess.SubprocessError):
                    pass
                digest = hashlib.sha256()
                source_files = (
                    Path("agent/reasoning_effort.py"),
                    Path("agent/turn_api_request.py"),
                    Path("hermes_cli/middleware.py"),
                )
                hashed = []
                for relative in source_files:
                    source_file = path / relative
                    if source_file.is_file():
                        digest.update(relative.as_posix().encode("utf-8") + b"\0")
                        digest.update(source_file.read_bytes())
                        digest.update(b"\0")
                        hashed.append(relative.as_posix())
                fingerprint = digest.hexdigest() if hashed else None
                verified = bool(commit or fingerprint)
                return {"path": str(path), "version": commit,
                        "source_commit": commit, "source_sha256": fingerprint,
                        "fingerprinted_files": hashed,
                        "status": "verified" if verified else "blocked",
                        "reason": None if verified else "hermes_source_fingerprint_unavailable"}
    return {"path": None, "version": None, "source_commit": None,
            "source_sha256": None, "fingerprinted_files": [],
            "status": "blocked", "reason": "hermes_source_unavailable"}


def _source_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _plugin_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(ROOT.glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_effort_module():
    # Mirror tests/conftest.py's package loading without importing plugin startup.
    import importlib.machinery
    import importlib.util

    hermes = _hermes_source()
    core_path = hermes.get("path")
    if core_path and core_path not in sys.path:
        sys.path.insert(0, core_path)

    package_name = "hermes_plugin_adaptive_effort"
    if package_name not in sys.modules:
        package = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(package_name, None, is_package=True))
        package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
    full_name = package_name + ".effort"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, ROOT / "effort.py")
    if not spec or not spec.loader:
        raise RuntimeError("effort_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _go_cases() -> list[dict[str, Any]]:
    effort = _load_effort_module()
    cases: list[dict[str, Any]] = []
    for api_mode, routes in effort.OPEN_CODE_GO_INJECTION_ROUTES.items():
        for model in sorted(routes):
            path, _vocabulary = routes[model]
            field = "reasoning.effort" if path in ("reasoning", "paired_effort") else path
            if path == "paired_effort":
                field = "reasoning_effort"
            for score, label in SCORE_LABELS:
                mapped = effort.map_effort(label, "opencode-go", model, supported=_vocabulary)
                case: dict[str, Any] = {
                    "case_id": stable_case_id("opencode-go", model, api_mode, label),
                    "provider": "opencode-go",
                    "exact_model": model,
                    "api_mode": api_mode,
                    "target_effort": label,
                    "score": score,
                    "effort_field": field,
                    "expected_wire_effort": mapped,
                    "response_oracle": {"kind": "synthetic_answer", "expected": "4"},
                    "max_output_tokens": 1024,
                    "mapping_status": "ready" if mapped else "blocked",
                    "live_status": "blocked",
                    "live_reason": "price_and_enforceable_max_cost_bound_unavailable",
                    "pricing_status": "unavailable",
                    "maximum_cost_bound_usd": None,
                    "reason": None if mapped else "local_mapping_unavailable",
                }
                if path == "paired_effort":
                    case["thinking_prerequisite"] = "already_enabled"
                cases.append(case)
    return cases


def _codex_cases() -> list[dict[str, Any]]:
    """Catalog availability rows; only the exact registered route gets a wire map."""
    effort = _load_effort_module()
    cases = []
    for model in CODEX_MODEL_IDS:
        registered = model == "gpt-6.1-sol"
        mapped = (effort.map_effort("high", "openai-codex", model)
                  if registered else None)
        cases.append({
            "case_id": stable_case_id(
                "openai-codex", model, "codex_responses", "high"),
            "provider": "openai-codex",
            "exact_model": model,
            "api_mode": "codex_responses",
            "target_effort": "high",
            "score": 2,
            "effort_field": "reasoning.effort" if mapped else None,
            "expected_wire_effort": mapped,
            "response_oracle": {"kind": "synthetic_answer", "expected": "4"},
            "max_output_tokens": 1024,
            "mapping_status": "ready" if mapped else "blocked",
            "live_status": "blocked",
            "live_reason": "codex_per_request_quota_bound_and_transport_observer_unavailable",
            "pricing_status": "not_applicable_codex_quota",
            "maximum_cost_bound_usd": None,
            "reason": None if mapped else "no_exact_dynamic_effort_registration",
        })
    return cases


def _safe_config_models(path: Path) -> list[dict[str, Any]]:
    """Extract exact OpenRouter model IDs while ignoring credential-like fields."""
    try:
        from ruamel.yaml import YAML
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    found: set[tuple[str, str]] = set()

    def walk(value: Any, provider: str | None = None, api: str | None = None) -> None:
        if isinstance(value, dict):
            local_provider = provider
            local_api = api
            for key, child in value.items():
                k = str(key).lower()
                if any(word in k for word in ("key", "token", "secret", "password", "auth")):
                    continue
                if k in {"provider", "provider_name"} and isinstance(child, str):
                    local_provider = child.lower()
                elif k in {"api_mode", "api", "mode"} and isinstance(child, str):
                    local_api = child.lower()
            if local_provider and "openrouter" in local_provider:
                for key, child in value.items():
                    k = str(key).lower()
                    if k in {"model", "model_id", "id"} and isinstance(child, str):
                        found.add((child.strip(), local_api or "unknown"))
            for key, child in value.items():
                k = str(key).lower()
                if not any(word in k for word in ("key", "token", "secret", "password", "auth")):
                    walk(child, local_provider, local_api)
        elif isinstance(value, list):
            for child in value:
                walk(child, provider, api)

    walk(data)
    # Current plugin source does not register OpenRouter injection routes. A
    # configured ID alone cannot prove an effort carrier is eligible.
    return [{"provider": "openrouter", "exact_model": model, "api_mode": api,
             "mapping_status": "blocked", "live_status": "blocked",
             "live_reason": "price_and_enforceable_max_cost_bound_unavailable",
             "pricing_status": "unavailable", "maximum_cost_bound_usd": None,
             "reason": "no_verified_injection_route"}
            for model, api in sorted(found)]


def build_manifest(config_path: str | None = None) -> dict[str, Any]:
    cases = _go_cases()
    cases.extend(_codex_cases())
    configured = _safe_config_models(Path(config_path).expanduser()) if config_path else []
    for route in configured:
        for score, label in SCORE_LABELS:
            cases.append({
                "case_id": stable_case_id("openrouter", route["exact_model"],
                                           route["api_mode"], label),
                "provider": "openrouter", "exact_model": route["exact_model"],
                "api_mode": route["api_mode"], "target_effort": label, "score": score,
                "expected_wire_effort": None, "mapping_status": "blocked",
                "live_status": "blocked",
                "live_reason": "price_and_enforceable_max_cost_bound_unavailable",
                "pricing_status": "unavailable", "maximum_cost_bound_usd": None,
                "response_oracle": {"kind": "synthetic_answer", "expected": "4"},
                "max_output_tokens": 1024,
                "reason": route["reason"],
            })
    campaign_id = str(uuid.uuid4())
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "created_at": _now(),
        "source_commit": _source_commit(),
        "plugin_sha256": _plugin_hash(),
        "hermes_source": _hermes_source(),
        "limits": {**{name: dict(values) for name, values in CAPS.items()},
                   "openai-codex-usage": dict(CODEX_USAGE_POLICY)},
        "cases": cases,
        "score_origin": "controlled",
        "real_scorer_calls": 0,
        "inventory": {"openrouter_config_read": bool(config_path),
                      "openrouter_routes": len(configured),
                      "openai_codex_catalog": {
                          "status": "available",
                          "source": CODEX_CATALOG_SOURCE,
                          "model_ids": list(CODEX_MODEL_IDS),
                          "catalog_fingerprint": _digest(CODEX_MODEL_IDS),
                          "identity_matches_codex_app": True,
                          "observed_at": _now(),
                      },
                      "network_requests": 0},
    }
    manifest["manifest_hash"] = manifest_hash(manifest)
    return manifest


def validate_manifest(manifest: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return ["manifest_schema_invalid"]
    if manifest.get("manifest_hash") != manifest_hash(manifest):
        errors.append("manifest_hash_mismatch")
    if not isinstance(manifest.get("campaign_id"), str) or not manifest.get("campaign_id"):
        errors.append("campaign_id_invalid")
    hermes_source = manifest.get("hermes_source")
    current_hermes_source = _hermes_source()
    if (not isinstance(hermes_source, dict)
            or hermes_source.get("status") != "verified"
            or hermes_source != current_hermes_source):
        errors.append("hermes_source_fingerprint_mismatch")
    if manifest.get("plugin_sha256") != _plugin_hash():
        errors.append("plugin_source_mismatch")
    if manifest.get("score_origin") != "controlled" or manifest.get("real_scorer_calls") != 0:
        errors.append("score_origin_invalid")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        errors.append("cases_invalid")
        return errors
    ids: set[str] = set()
    go_cases: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            errors.append("case_invalid")
            continue
        cid = case.get("case_id")
        if not isinstance(cid, str) or cid in ids:
            errors.append("case_id_invalid")
        ids.add(cid)
        if case.get("score") not in (0, 1, 2) or case.get("target_effort") not in {"low", "medium", "high"}:
            errors.append("case_rubric_invalid")
        if case.get("provider") not in {*CAPS, "openai-codex"}:
            errors.append("case_provider_invalid")
        if case.get("mapping_status") not in {"ready", "blocked"}:
            errors.append("case_status_invalid")
        if (case.get("live_status") != "blocked"
                or case.get("maximum_cost_bound_usd") is not None):
            errors.append("case_live_status_not_blocked")
        if case.get("provider") in CAPS:
            if (case.get("live_reason")
                    != "price_and_enforceable_max_cost_bound_unavailable"
                    or case.get("pricing_status") != "unavailable"):
                errors.append("case_live_status_not_blocked")
        elif case.get("provider") == "openai-codex":
            if (case.get("live_reason")
                    != "codex_per_request_quota_bound_and_transport_observer_unavailable"
                    or case.get("pricing_status") != "not_applicable_codex_quota"):
                errors.append("case_live_status_not_blocked")
        if case.get("provider") == "opencode-go":
            go_cases.append(case)
        elif case.get("provider") == "openrouter":
            expected_id = stable_case_id("openrouter", case.get("exact_model", ""),
                                         case.get("api_mode", ""),
                                         case.get("target_effort", ""))
            if (case.get("case_id") != expected_id or case.get("mapping_status") != "blocked"
                    or case.get("live_status") != "blocked"
                    or case.get("pricing_status") != "unavailable"
                    or case.get("maximum_cost_bound_usd") is not None
                    or case.get("reason") != "no_verified_injection_route"
                    or case.get("expected_wire_effort") is not None):
                errors.append("openrouter_case_not_fail_closed")
        elif case.get("provider") == "openai-codex":
            if (case.get("case_id") != stable_case_id(
                    "openai-codex", case.get("exact_model", ""),
                    case.get("api_mode", ""), case.get("target_effort", ""))
                    or case.get("exact_model") not in CODEX_MODEL_IDS
                    or case.get("api_mode") != "codex_responses"
                    or case.get("target_effort") != "high"
                    or case.get("score") != 2
                    or case.get("live_status") != "blocked"
                    or case.get("maximum_cost_bound_usd") is not None):
                errors.append("codex_case_invalid")
        else:
            errors.append("case_provider_invalid")
    try:
        expected_go = _go_cases()
    except Exception:
        expected_go = []
        errors.append("local_route_registry_unavailable")
    if go_cases != expected_go:
        errors.append("go_cases_do_not_match_local_registry")
    codex_cases = [case for case in cases if isinstance(case, dict)
                   and case.get("provider") == "openai-codex"]
    try:
        expected_codex = _codex_cases()
    except Exception:
        expected_codex = []
        errors.append("codex_mapping_registry_unavailable")
    if codex_cases != expected_codex:
        errors.append("codex_cases_do_not_match_catalog")
    limits = manifest.get("limits")
    expected_limits = {**CAPS, "openai-codex-usage": CODEX_USAGE_POLICY}
    if limits != expected_limits:
        errors.append("budget_limits_invalid")
    inventory = manifest.get("inventory")
    catalog = inventory.get("openai_codex_catalog") if isinstance(inventory, dict) else None
    if (not isinstance(catalog, dict)
            or catalog.get("status") != "available"
            or catalog.get("source") != CODEX_CATALOG_SOURCE
            or catalog.get("model_ids") != CODEX_MODEL_IDS
            or catalog.get("catalog_fingerprint") != _digest(CODEX_MODEL_IDS)
            or catalog.get("identity_matches_codex_app") is not True
            or not isinstance(catalog.get("observed_at"), str)):
        errors.append("codex_catalog_status_invalid")
    else:
        try:
            observed_at = _datetime.datetime.fromisoformat(catalog["observed_at"])
        except ValueError:
            observed_at = None
        if (observed_at is None or observed_at.utcoffset() is None
                or observed_at.utcoffset().total_seconds() != 0):
            errors.append("codex_catalog_status_invalid")
    return errors


def check_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors = validate_manifest(manifest)
    go = [c for c in manifest.get("cases", []) if c.get("provider") == "opencode-go"]
    go_pairs = {(c.get("exact_model"), c.get("expected_wire_effort")) for c in go
                if c.get("expected_wire_effort")}
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict):
        inventory = {}
    catalog = inventory.get("openai_codex_catalog")
    if not isinstance(catalog, dict):
        catalog = {}
    hermes_source = manifest.get("hermes_source")
    if not isinstance(hermes_source, dict):
        hermes_source = {}
    return {"schema": "hermes-adaptive-effort.validation-check.v1",
            "manifest_hash": manifest.get("manifest_hash"),
            "valid": not errors, "errors": errors,
            "go_score_mappings": len(go), "go_distinct_wire_pairs": len(go_pairs),
            "openai_codex_catalog_models": len(catalog.get("model_ids", [])),
            "network_requests": 0, "run_ready": False,
            "hermes_source_fingerprint_verified":
                hermes_source.get("status") == "verified",
            "codex_usage_policy": dict(CODEX_USAGE_POLICY),
            "openai_codex_catalog_status": catalog.get("status"),
            "openai_codex_catalog_reason": catalog.get("reason"),
            "run_block_reason": "hard_cost_guard_and_real_transport_observer_unavailable"}


def codex_stop_threshold(baseline_used_percent: int,
                         max_increase_percentage_points: int) -> int:
    """Return the preventive stop one point before the inclusive hard ceiling."""
    if (isinstance(baseline_used_percent, bool)
            or isinstance(max_increase_percentage_points, bool)
            or not isinstance(baseline_used_percent, int)
            or not isinstance(max_increase_percentage_points, int)
            or not 0 <= baseline_used_percent <= 100
            or not 0 <= max_increase_percentage_points <= 100):
        raise ValueError("codex_quota_policy_invalid")
    hard_ceiling = min(100, baseline_used_percent + max_increase_percentage_points)
    return max(0, hard_ceiling - 1)


def record_send(ledger: dict[str, Any], case_id: str, send_id: str,
                outgoing_model: str, effort_field: str, effort_value: str,
                gate_allows_send: bool = True) -> dict[str, Any]:
    """Record one observed physical Codex send without performing transport I/O.

    Persist the returned snapshot with persist_ledger_transition before allowing
    the corresponding HTTP request to continue.
    """
    if gate_allows_send is not True:
        raise ValueError("codex_send_gate_closed")
    if (not isinstance(case_id, str) or not _SAFE_ID_RE.fullmatch(case_id)
            or not isinstance(send_id, str) or not _SAFE_ID_RE.fullmatch(send_id)
            or not isinstance(outgoing_model, str)
            or not isinstance(effort_field, str)
            or not isinstance(effort_value, str)):
        raise ValueError("codex_send_metadata_invalid")
    if outgoing_model not in CODEX_MODEL_IDS:
        raise ValueError("codex_model_not_in_authenticated_catalog")
    if effort_field != "reasoning.effort" or effort_value not in _CODEX_WIRE_EFFORTS:
        raise ValueError("codex_effort_observation_invalid")
    updated = copy.deepcopy(ledger)
    usage = updated.get("codex_usage")
    if not isinstance(usage, dict) or usage.get("status") != "available":
        raise ValueError("codex_usage_snapshot_unavailable")
    if usage.get("identity_matches_codex_app") is not True:
        raise ValueError("codex_usage_account_mismatch")
    events = usage.setdefault("send_events", [])
    if any(event.get("send_id") == send_id for event in events):
        raise ValueError("codex_send_id_already_recorded")
    observed = usage.get("observed_used_percent")
    stop = usage.get("stop_threshold_percent")
    if (isinstance(observed, bool) or not isinstance(observed, (int, float))
            or isinstance(stop, bool) or not isinstance(stop, (int, float))
            or observed >= stop):
        raise ValueError("codex_usage_stop_threshold_reached")
    events.append({
        "send_id": send_id, "case_id": case_id,
        "outgoing_model": outgoing_model, "effort_field": effort_field,
        "effort_value": effort_value, "state": "sent",
    })
    usage["physical_requests"] = int(usage.get("physical_requests", 0)) + 1
    return updated


def validate_compatibility_result(row: dict[str, Any], expected_model: str,
                                  expected_effort_field: str,
                                  expected_effort: str) -> list[str]:
    """Require exact outgoing route/control evidence and a completed response."""
    if not isinstance(row, dict):
        return ["result_invalid"]
    errors = []
    if row.get("observed_model") != expected_model:
        errors.append("outgoing_model_mismatch")
    if row.get("observed_effort_field") != expected_effort_field:
        errors.append("outgoing_effort_field_mismatch")
    if row.get("observed_effort") != expected_effort:
        errors.append("outgoing_effort_mismatch")
    if row.get("response_complete") is not True:
        errors.append("response_incomplete")
    return errors


@contextlib.contextmanager
def exclusive_ledger_lock(path: str | Path) -> Iterator[None]:
    """Acquire a single-writer OS lock suitable for future persistent ledger use."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if stream.read(1) == b"":
                stream.seek(0)
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_replace(path, payload.encode("utf-8"))


def _atomic_replace(path: Path, payload: bytes) -> None:
    """Atomically replace one JSON artifact and durably flush file and directory."""
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _read_ledger(path: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("ledger_event_invalid")
                events.append(event)
    if not events:
        raise ValueError("ledger_empty")
    latest = events[-1]
    if latest.get("schema") != LEDGER_SCHEMA:
        raise ValueError("ledger_schema_invalid")
    if latest.get("manifest_hash") != manifest["manifest_hash"]:
        raise ValueError("ledger_manifest_mismatch")
    if latest.get("campaign_id") != manifest["campaign_id"]:
        raise ValueError("ledger_campaign_mismatch")
    if any(event.get("manifest_hash") != manifest["manifest_hash"] for event in events):
        raise ValueError("ledger_history_mismatch")
    return latest


def _append_ledger_event(path: Path, event: dict[str, Any]) -> None:
    """Atomically append one auditable JSONL snapshot and fsync it to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_bytes() if path.exists() else b""
    if previous and not previous.endswith(b"\n"):
        raise ValueError("ledger_partial_line")
    line = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(previous)
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _empty_ledger(manifest: dict[str, Any]) -> dict[str, Any]:
    accounts = {}
    for provider, caps in CAPS.items():
        accounts[provider] = {**caps, "reconciled_spend": "0.00", "reserved": "0.00",
                              "unresolved": "0.00", "attempts": [],
                              "blocked": False, "block_reason": None}
    return {"schema": LEDGER_SCHEMA, "manifest_hash": manifest["manifest_hash"],
            "campaign_id": manifest["campaign_id"],
            "event": "initialized_zero_send", "created_at": _now(),
            "network_requests": 0, "accounts": accounts,
            "codex_usage": {
                "status": "available",
                "identity_matches_codex_app": True,
                "window_minutes": CODEX_USAGE_POLICY["window_minutes"],
                "baseline_used_percent": CODEX_BASELINE_USED_PERCENT,
                "max_increase_percentage_points":
                    CODEX_USAGE_POLICY["max_increase_percentage_points"],
                "observed_used_percent": CODEX_OBSERVED_USED_PERCENT,
                "stop_threshold_percent": codex_stop_threshold(
                    CODEX_BASELINE_USED_PERCENT,
                    CODEX_USAGE_POLICY["max_increase_percentage_points"]),
                "hard_ceiling_percent": min(
                    100, CODEX_BASELINE_USED_PERCENT
                    + CODEX_USAGE_POLICY["max_increase_percentage_points"]),
                "physical_requests": 0,
                "send_events": [],
            },
            "results": [], "run_history": []}


def _decimal_money(value: Any) -> Decimal:
    """Parse an exact nonnegative decimal; binary floats are refused."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("money_must_be_exact_decimal")
    if not isinstance(value, (str, int, Decimal)):
        raise ValueError("money_must_be_exact_decimal")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("money_invalid") from None
    if not result.is_finite() or result < 0:
        raise ValueError("money_invalid")
    return result


def _money_text(value: Any) -> str:
    """Serialize exact USD amounts without exponent notation, to at least cents."""
    rendered = format(_decimal_money(value), "f")
    if "." not in rendered:
        return rendered + ".00"
    whole, fraction = rendered.split(".", 1)
    return whole + "." + fraction.rstrip("0").ljust(2, "0")


def reserve_attempt(ledger: dict[str, Any], provider: str, case_id: str,
                    reservation: str | Decimal) -> dict[str, Any]:
    """Pure accounting helper. It reserves within caps and never sends a request."""
    if provider not in CAPS or not isinstance(case_id, str) or not case_id:
        raise ValueError("reservation_identity_invalid")
    amount = _decimal_money(reservation)
    if amount <= 0:
        raise ValueError("reservation_must_be_positive")
    updated = copy.deepcopy(ledger)
    if updated.get("schema") != LEDGER_SCHEMA:
        raise ValueError("ledger_schema_invalid")
    account = updated.get("accounts", {}).get(provider)
    if not isinstance(account, dict) or not isinstance(account.get("attempts"), list):
        raise ValueError("ledger_account_invalid")
    if account.get("blocked"):
        raise ValueError("provider_budget_blocked")
    prior = [row for row in account["attempts"] if row.get("case_id") == case_id]
    if prior and prior[-1].get("state") not in {"completed", "reconciled"}:
        raise ValueError("case_attempt_not_reconciled")
    spent = _decimal_money(account.get("reconciled_spend", "0.00"))
    reserved = _decimal_money(account.get("reserved", "0.00"))
    working = _decimal_money(account.get("working_cap"))
    hard = _decimal_money(account.get("hard_cap"))
    if spent + reserved + amount > working or spent + reserved + amount > hard:
        raise ValueError("reservation_exceeds_provider_cap")
    attempt = {"case_id": case_id, "attempt_id": f"{len(prior) + 1:04d}",
               "state": "reserved", "reservation": _money_text(amount),
               "provider_cost": None, "created_at": _now(), "updated_at": _now()}
    account["attempts"].append(attempt)
    account["reserved"] = _money_text(reserved + amount)
    updated.update(event="attempt_reserved", created_at=_now())
    return updated


def _release_attempt_reservation(account: dict[str, Any], attempt: dict[str, Any]) -> None:
    reserved = _decimal_money(account.get("reserved", "0.00"))
    amount = _decimal_money(attempt.get("reservation"))
    if amount > reserved:
        raise ValueError("ledger_reservation_inconsistent")
    account["reserved"] = _money_text(reserved - amount)


def _commit_attempt_cost(account: dict[str, Any], attempt: dict[str, Any],
                         provider_cost: str | Decimal) -> bool:
    cost = _decimal_money(provider_cost)
    spent = _decimal_money(account.get("reconciled_spend", "0.00"))
    reservation = _decimal_money(attempt.get("reservation"))
    previous_state = attempt.get("state")
    _release_attempt_reservation(account, attempt)
    if previous_state == "unknown":
        unresolved = _decimal_money(account.get("unresolved", "0.00"))
        if reservation > unresolved:
            raise ValueError("ledger_unresolved_inconsistent")
        account["unresolved"] = _money_text(unresolved - reservation)
    updated_spend = spent + cost
    account["reconciled_spend"] = _money_text(updated_spend)
    over_reservation = cost > reservation
    over_cap = (updated_spend > _decimal_money(account["working_cap"])
                or updated_spend > _decimal_money(account["hard_cap"]))
    if over_reservation or over_cap:
        account["blocked"] = True
        account["block_reason"] = ("provider_cost_exceeded_reservation" if over_reservation
                                   else "provider_budget_cap_exceeded")
        attempt["cost_discrepancy"] = {
            "reserved": _money_text(reservation), "actual": _money_text(cost),
            "over_by": _money_text(max(Decimal("0"), cost - reservation)),
            "over_cap": over_cap,
        }
        return True
    return False


def transition_attempt(ledger: dict[str, Any], provider: str, case_id: str,
                       attempt_id: str, state: str,
                       provider_cost: str | Decimal | None = None) -> dict[str, Any]:
    """Advance one attempt; missing cost becomes unknown and retains its reserve."""
    if state not in {"sent", "completed", "unknown", "blocked"}:
        raise ValueError("attempt_transition_invalid")
    updated = copy.deepcopy(ledger)
    account = updated.get("accounts", {}).get(provider)
    if not isinstance(account, dict):
        raise ValueError("ledger_account_invalid")
    matches = [row for row in account.get("attempts", [])
               if row.get("case_id") == case_id and row.get("attempt_id") == attempt_id]
    if len(matches) != 1:
        raise ValueError("attempt_not_found")
    attempt = matches[0]
    current = attempt.get("state")
    if current == "reserved" and state == "sent":
        attempt["state"] = "sent"
        updated["network_requests"] = int(updated.get("network_requests", 0)) + 1
    elif current == "reserved" and state == "blocked":
        _release_attempt_reservation(account, attempt)
        attempt["state"] = "blocked"
    elif current == "sent" and state in {"completed", "unknown"}:
        if state == "unknown" or provider_cost is None:
            attempt["state"] = "unknown"
            account["unresolved"] = _money_text(
                _decimal_money(account.get("unresolved", "0.00"))
                + _decimal_money(attempt["reservation"]))
        else:
            breach = _commit_attempt_cost(account, attempt, provider_cost)
            attempt["state"] = "blocked" if breach else "completed"
            attempt["provider_cost"] = _money_text(provider_cost)
    else:
        raise ValueError("attempt_replay_or_transition_invalid")
    attempt["updated_at"] = _now()
    updated.update(event="attempt_" + attempt["state"], created_at=_now())
    return updated


def reconcile_attempt(ledger: dict[str, Any], provider: str, case_id: str,
                      attempt_id: str, provider_cost: str | Decimal | None) -> dict[str, Any]:
    """Release an unknown/sent reservation only after exact cost evidence arrives."""
    updated = copy.deepcopy(ledger)
    account = updated.get("accounts", {}).get(provider)
    if not isinstance(account, dict):
        raise ValueError("ledger_account_invalid")
    matches = [row for row in account.get("attempts", [])
               if row.get("case_id") == case_id and row.get("attempt_id") == attempt_id]
    if len(matches) != 1:
        raise ValueError("attempt_not_found")
    attempt = matches[0]
    if attempt.get("state") not in {"sent", "unknown"}:
        raise ValueError("attempt_not_reconcilable")
    if provider_cost is None:
        if attempt.get("state") == "sent":
            account["unresolved"] = _money_text(
                _decimal_money(account.get("unresolved", "0.00"))
                + _decimal_money(attempt["reservation"]))
        attempt["state"] = "unknown"
    else:
        breach = _commit_attempt_cost(account, attempt, provider_cost)
        attempt["provider_cost"] = _money_text(provider_cost)
        attempt["state"] = "blocked" if breach else "reconciled"
    attempt["updated_at"] = _now()
    updated.update(event="attempt_" + attempt["state"], created_at=_now())
    return updated


def _read_latest_ledger(path: Path, expected_manifest_hash: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    if not events or any(not isinstance(item, dict) for item in events):
        raise ValueError("ledger_event_invalid")
    if any(item.get("schema") != LEDGER_SCHEMA
           or item.get("manifest_hash") != expected_manifest_hash for item in events):
        raise ValueError("ledger_history_mismatch")
    return events[-1]


def persist_ledger_transition(path: str | Path, expected_manifest_hash: str,
                              transition: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Apply one pure ledger helper under the lock and append/fsync its snapshot.

    This function has no transport dependency and is not used by the blocked run
    command. A future send guard must integrate it before every physical send.
    """
    if transition not in {
        reserve_attempt, transition_attempt, reconcile_attempt, record_send,
    }:
        raise ValueError("ledger_transition_callback_invalid")
    ledger_path = Path(path)
    with exclusive_ledger_lock(ledger_path):
        ledger = _read_latest_ledger(ledger_path, expected_manifest_hash)
        if ledger is None:
            raise ValueError("ledger_missing_initialization")
        updated = transition(ledger, *args, **kwargs)
        _append_ledger_event(ledger_path, updated)
        return updated


def _cmd_inventory(args: argparse.Namespace) -> int:
    manifest = build_manifest(args.config)
    _write_json(Path(args.output), manifest)
    print(json.dumps({"manifest_hash": manifest["manifest_hash"],
                      "cases": len(manifest["cases"]), "network_requests": 0,
                      "output": str(Path(args.output).resolve())}, sort_keys=True))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report = check_manifest(manifest)
    if args.output:
        _write_json(Path(args.output), report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["valid"] else 2


def _cmd_run(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report = check_manifest(manifest)
    if not report["valid"]:
        print(json.dumps({"state": "blocked", "reason": "manifest_invalid",
                          "errors": report["errors"], "network_requests": 0}, sort_keys=True))
        return 2
    blocked_rows = _blocked_result_rows(manifest)
    ledger_path = Path(args.ledger)
    try:
        with exclusive_ledger_lock(ledger_path):
            prior = _read_ledger(ledger_path, manifest)
            if prior is not None and not args.resume:
                raise ValueError("ledger_exists_use_resume")
            if prior is None:
                event = _empty_ledger(manifest)
                event["results"] = blocked_rows
                # The persisted zero-send schema has no physical-send event
                # fields until the first observer transition is durably recorded.
                event["codex_usage"].pop("physical_requests", None)
                event["codex_usage"].pop("send_events", None)
            else:
                # Resume is read-only for spend state: this fail-closed runner
                # has no physical attempts to reconcile or retry.
                event = copy.deepcopy(prior)
                event["event"] = "resumed_zero_send"
                event["created_at"] = _now()
                event.setdefault("results", blocked_rows)
                event.setdefault("run_history", [])
            run_history = list(event.get("run_history", []))
            run_history.append({
                "campaign_id": manifest["campaign_id"],
                "invocation": len(run_history) + 1,
                "event": "blocked_zero_send",
                "created_at": _now(),
            })
            event["run_history"] = run_history
            # Carry forward the historical counter exactly; this invocation
            # contributes zero because it never enters a transport.
            _append_ledger_event(ledger_path, event)
    except (OSError, ValueError, json.JSONDecodeError, BlockingIOError) as exc:
        print(json.dumps({"state": "blocked", "reason": "ledger_unavailable",
                          "detail": str(exc) if str(exc).isidentifier() else "ledger_error",
                          "network_requests": 0}, sort_keys=True))
        return 3
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = event["results"]
    _write_json(outdir / "results.json", {
        "manifest_hash": manifest["manifest_hash"],
        "campaign_id": manifest["campaign_id"],
        "results": rows, "run_history": event["run_history"],
        "network_requests": event["network_requests"],
    })
    print(json.dumps({"state": "blocked", "reason": "hard_cost_guard_and_real_transport_observer_unavailable",
                      "results": len(rows), "network_requests": 0,
                      "ledger_network_requests": event["network_requests"],
                      "campaign_id": manifest["campaign_id"],
                      "output": str((outdir / "results.json").resolve())}, sort_keys=True))
    return 3


def _blocked_result_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe cases blocked before any attempt; they have no attempt ordinal."""
    created = _now()
    return [{
        "schema": RESULT_SCHEMA, "case_id": case["case_id"], "attempt_id": None,
        "manifest_hash": manifest["manifest_hash"], "state": "blocked",
        "provider": case["provider"], "exact_model": case["exact_model"],
        "api_mode": case["api_mode"], "http_status": None,
        "before_effort": None, "middleware_effort": None, "wire_effort": None,
        "response_complete": False, "synthetic_answer": False,
        "input_tokens": None, "output_tokens": None,
        "provider_cost": None, "reservation": None,
        # No provider route reaches transport in this runner. Keep one stable
        # blocked result code; the check report carries the more specific
        # Codex quota and Hermes transport limitations.
        "failure_code": "hard_cost_guard_unavailable",
        "created_at": created, "finished_at": created,
    } for case in manifest["cases"]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory", help="write a redacted local manifest")
    inventory.add_argument("--output", required=True)
    inventory.add_argument("--config", help="optional Hermes YAML config for route IDs only")
    inventory.set_defaults(func=_cmd_inventory)
    check = sub.add_parser("check", help="validate manifest locally; no HTTP")
    check.add_argument("--manifest", required=True)
    check.add_argument("--output")
    check.set_defaults(func=_cmd_check)
    run = sub.add_parser("run", help="produce blocked rows unless campaign guards exist")
    run.add_argument("--manifest", required=True)
    run.add_argument("--ledger", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--resume", action="store_true")
    run.set_defaults(func=_cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
