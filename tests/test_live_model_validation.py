"""Hermetic contract tests for the local model-validation runner.

These tests exercise inventory, validation, and blocked-run behavior only. The
runner's real-call path is intentionally disabled, so no scorer or target API
is contacted by this module.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from conftest import PLUGIN_DIR, import_plugin


RUNNER_PATH = PLUGIN_DIR / "scripts" / "run_model_validation.py"
_SPEC = importlib.util.spec_from_file_location("run_model_validation", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)

effort = import_plugin("effort")


def canonical_hash(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def find_mappings_with_values(value, expected):
    if isinstance(value, dict):
        if all(value.get(key) == expected_value for key, expected_value in expected.items()):
            yield value
        for child in value.values():
            yield from find_mappings_with_values(child, expected)
    elif isinstance(value, list):
        for child in value:
            yield from find_mappings_with_values(child, expected)


CODEX_USAGE_POLICY = {
    "window_minutes": 300,
    "max_increase_percentage_points": 30,
    "api_mode": "codex_responses",
}

AUTHENTICATED_CODEX_MODEL_IDS = [
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
]


def test_go_inventory_is_exact_registry_cross_product_with_local_mapping():
    manifest = runner.build_manifest()
    routes = effort.OPEN_CODE_GO_INJECTION_ROUTES
    cases = [case for case in manifest["cases"] if case["provider"] == "opencode-go"]

    expected_routes = {
        (api_mode, model)
        for api_mode, route_models in routes.items()
        for model in route_models
    }
    assert {(case["api_mode"], case["exact_model"]) for case in cases} == expected_routes
    assert len(cases) == len(expected_routes) * 3
    assert len({case["case_id"] for case in cases}) == len(cases)

    for api_mode, route_models in routes.items():
        for model, (path, _vocabulary) in route_models.items():
            for score, label in runner.SCORE_LABELS:
                [case] = [case for case in cases if case["api_mode"] == api_mode
                          and case["exact_model"] == model
                          and case["target_effort"] == label]
                expected_field = {
                    "reasoning": "reasoning.effort",
                    "paired_effort": "reasoning_effort",
                }.get(path, path)
                mapped = effort.map_effort(
                    label, "opencode-go", model, supported=_vocabulary)
                assert case["score"] == score
                assert case["effort_field"] == expected_field
                assert case["expected_wire_effort"] == mapped
                assert case["mapping_status"] == ("ready" if mapped else "blocked")
                assert case["live_status"] == "blocked"
                assert case["live_reason"] == (
                    "price_and_enforceable_max_cost_bound_unavailable")
                assert case["pricing_status"] == "unavailable"
                assert case["maximum_cost_bound_usd"] is None
                assert case["reason"] == (None if mapped else "local_mapping_unavailable")
                assert case["case_id"] == runner.stable_case_id(
                    "opencode-go", model, api_mode, label)
                if path == "paired_effort":
                    assert case["thinking_prerequisite"] == "already_enabled"
                else:
                    assert "thinking_prerequisite" not in case


def test_stable_case_id_and_manifest_hash_use_canonical_json():
    item = ["opencode-go", "gpt-6-luna", "codex_responses", "medium", runner.CASE_VERSION]
    expected_id = canonical_hash(item)
    assert runner.stable_case_id(*item[:4]) == expected_id
    assert runner.stable_case_id(*item[:4]) == runner.stable_case_id(*item[:4])

    manifest = runner.build_manifest()
    assert manifest["manifest_hash"] == canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"})
    assert runner.manifest_hash(manifest) == manifest["manifest_hash"]


def test_manifest_schema_budget_and_blocked_reason_are_explicit():
    manifest = runner.build_manifest()
    assert runner.validate_manifest(manifest) == []
    report = runner.check_manifest(manifest)

    assert manifest["schema"] == "hermes-adaptive-effort.validation-manifest.v1"
    assert manifest["campaign_id"]
    assert manifest["score_origin"] == "controlled"
    assert manifest["real_scorer_calls"] == 0
    assert manifest["inventory"]["network_requests"] == 0
    assert manifest["limits"] == {
        "openrouter": {"hard_cap": "2.00", "working_cap": "1.80", "currency": "USD"},
        "opencode-go": {"hard_cap": "3.00", "working_cap": "2.70", "currency": "USD"},
        "openai-codex-usage": CODEX_USAGE_POLICY,
    }
    assert set(runner.CAPS) == {"openrouter", "opencode-go"}
    catalog = manifest["inventory"]["openai_codex_catalog"]
    assert catalog == {
        "status": "available",
        "source": "hermes_codex_authenticated_live_catalog",
        "model_ids": AUTHENTICATED_CODEX_MODEL_IDS,
        "catalog_fingerprint": canonical_hash(AUTHENTICATED_CODEX_MODEL_IDS),
        "identity_matches_codex_app": True,
        "observed_at": catalog["observed_at"],
    }
    assert len(catalog["model_ids"]) == 7
    assert catalog["model_ids"] == sorted(set(catalog["model_ids"]))
    assert datetime.fromisoformat(catalog["observed_at"]).utcoffset().total_seconds() == 0
    assert "account_id" not in catalog
    assert report["schema"] == "hermes-adaptive-effort.validation-check.v1"
    assert report["valid"] is True
    assert report["network_requests"] == 0
    assert report["go_score_mappings"] == 39
    assert report["go_distinct_wire_pairs"] == 33
    assert report["run_ready"] is False
    assert list(find_mappings_with_values(report, CODEX_USAGE_POLICY))
    assert report["run_block_reason"] == (
        "hard_cost_guard_and_real_transport_observer_unavailable")


def test_manifest_rejects_changed_or_missing_hermes_source_fingerprint(monkeypatch):
    manifest = runner.build_manifest()
    assert manifest["hermes_source"]["status"] == "verified"

    changed_manifest = json.loads(json.dumps(manifest))
    changed_manifest["hermes_source"]["source_sha256"] = "0" * 64
    changed_manifest["manifest_hash"] = runner.manifest_hash(changed_manifest)
    assert "hermes_source_fingerprint_mismatch" in runner.validate_manifest(changed_manifest)

    monkeypatch.setattr(runner, "_hermes_source", lambda: {
        "path": None, "version": None, "source_commit": None,
        "source_sha256": None, "fingerprinted_files": [],
        "status": "blocked", "reason": "hermes_source_unavailable",
    })
    assert "hermes_source_fingerprint_mismatch" in runner.validate_manifest(manifest)


def test_authenticated_codex_catalog_snapshot_is_exact_and_account_matched():
    manifest = runner.build_manifest()
    catalog = manifest["inventory"]["openai_codex_catalog"]
    model_ids = catalog["model_ids"]

    assert len(model_ids) == 7
    assert model_ids == AUTHENTICATED_CODEX_MODEL_IDS
    assert catalog["catalog_fingerprint"] == canonical_hash(model_ids)
    assert catalog["identity_matches_codex_app"] is True
    assert datetime.fromisoformat(catalog["observed_at"]).utcoffset().total_seconds() == 0
    assert runner.validate_manifest(manifest) == []
    assert "account_id" not in catalog

    mismatch = json.loads(json.dumps(manifest))
    mismatch["inventory"]["openai_codex_catalog"]["identity_matches_codex_app"] = False
    mismatch["manifest_hash"] = runner.manifest_hash(mismatch)
    assert "codex_catalog_status_invalid" in runner.validate_manifest(mismatch)

    assert manifest["limits"]["openai-codex-usage"] == CODEX_USAGE_POLICY
    assert set(runner.CAPS) == {"openrouter", "opencode-go"}


def test_codex_quota_snapshot_is_separate_and_stops_at_60_before_hard_61():
    manifest = runner.build_manifest()
    ledger = runner._empty_ledger(manifest)
    usage = ledger["codex_usage"]

    assert runner.codex_stop_threshold(31, 30) == 60
    assert usage["window_minutes"] == 300
    assert usage["baseline_used_percent"] == 31
    assert usage["observed_used_percent"] == 34
    assert usage["max_increase_percentage_points"] == 30
    assert usage["stop_threshold_percent"] == 60
    assert usage["hard_ceiling_percent"] == 61
    assert set(ledger["accounts"]) == {"openrouter", "opencode-go"}
    assert "openai-codex" not in ledger["accounts"]
    assert {provider: (account["hard_cap"], account["working_cap"])
            for provider, account in ledger["accounts"].items()} == {
                "openrouter": ("2.00", "1.80"),
                "opencode-go": ("3.00", "2.70"),
            }


def test_send_observer_counts_physical_retries_and_refuses_closed_gate():
    ledger = runner._empty_ledger(runner.build_manifest())
    first = runner.record_send(
        ledger, "case-1", "send-1", "gpt-6.1-sol", "reasoning.effort", "high")
    assert ledger["codex_usage"]["physical_requests"] == 0
    assert first["codex_usage"]["physical_requests"] == 1
    assert first["codex_usage"]["send_events"] == [{
        "send_id": "send-1", "case_id": "case-1", "outgoing_model": "gpt-6.1-sol",
        "effort_field": "reasoning.effort", "effort_value": "high", "state": "sent",
    }]

    # Same logical case with a new send ID is an additional physical attempt.
    retry = runner.record_send(
        first, "case-1", "send-2", "gpt-6.1-sol", "reasoning.effort", "high")
    assert retry["codex_usage"]["physical_requests"] == 2
    assert [event["send_id"] for event in retry["codex_usage"]["send_events"]] == [
        "send-1", "send-2"]
    with pytest.raises(ValueError):
        runner.record_send(
            retry, "case-1", "send-2", "gpt-6.1-sol", "reasoning.effort", "high")
    with pytest.raises(ValueError):
        runner.record_send(
            retry, "case-2", "send-3", "gpt-6.1-sol", "reasoning.effort", "high",
            gate_allows_send=False)
    assert retry["codex_usage"]["physical_requests"] == 2
    assert len(retry["codex_usage"]["send_events"]) == 2


def test_compatibility_accepts_exact_observation_and_completed_response():
    row = {
        "observed_model": "gpt-6.1-sol",
        "observed_effort_field": "reasoning.effort",
        "observed_effort": "high",
        "response_complete": True,
    }
    assert runner.validate_compatibility_result(
        row, "gpt-6.1-sol", "reasoning.effort", "high") == []


@pytest.mark.parametrize("changes", [
    {"observed_model": "gpt-6-luna"},
    {"observed_effort_field": "reasoning_effort"},
    {"observed_effort": "medium"},
    {"response_complete": False},
])
def test_compatibility_requires_exact_observed_effort_and_completed_response(changes):
    row = {
        "observed_model": "gpt-6.1-sol",
        "observed_effort_field": "reasoning.effort",
        "observed_effort": "high",
        "response_complete": True,
    }
    row.update(changes)
    errors = runner.validate_compatibility_result(
        row, "gpt-6.1-sol", "reasoning.effort", "high")
    assert errors


def test_inventory_and_check_cli_are_local_and_report_zero_requests(tmp_path, capsys):
    inventory_path = tmp_path / "manifest.json"
    assert runner.main(["inventory", "--output", str(inventory_path)]) == 0
    inventory_line = json.loads(capsys.readouterr().out)
    manifest = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert inventory_line["manifest_hash"] == manifest["manifest_hash"]
    assert inventory_line["network_requests"] == 0
    assert manifest["inventory"]["network_requests"] == 0
    assert manifest["inventory"]["openai_codex_catalog"]["status"] == "available"

    check_line = runner.main(["check", "--manifest", str(inventory_path)])
    check_report = json.loads(capsys.readouterr().out)
    assert check_line == 0
    assert check_report["valid"] is True
    assert check_report["network_requests"] == 0
    assert check_report["codex_usage_policy"] == CODEX_USAGE_POLICY
    assert check_report["openai_codex_catalog_status"] == "available"
    assert check_report["run_ready"] is False


def test_config_inventory_ignores_credentials_and_blocks_unregistered_routes(tmp_path):
    marker = "TEST_ONLY_SECRET_MUST_NOT_ESCAPE_72f4"
    config = tmp_path / "hermes.yaml"
    config.write_text(
        "providers:\n"
        "  openrouter:\n"
        "    provider: openrouter\n"
        "    api_mode: chat_completions\n"
        "    models:\n"
        "      - model: example/private-model\n"
        "        api_key: TEST_ONLY_SECRET_MUST_NOT_ESCAPE_72f4\n"
        "secrets:\n"
        "  credential: TEST_ONLY_SECRET_MUST_NOT_ESCAPE_72f4\n",
        encoding="utf-8",
    )
    manifest = runner.build_manifest(str(config))
    payload = json.dumps(manifest)

    assert marker not in payload
    assert manifest["inventory"]["openrouter_config_read"] is True
    assert manifest["inventory"]["openrouter_routes"] == 1
    openrouter_cases = [case for case in manifest["cases"]
                        if case["provider"] == "openrouter"]
    assert len(openrouter_cases) == 3
    assert {case["exact_model"] for case in openrouter_cases} == {"example/private-model"}
    assert {case["api_mode"] for case in openrouter_cases} == {"chat_completions"}
    assert all(case["mapping_status"] == "blocked" for case in openrouter_cases)
    assert all(case["live_status"] == "blocked" for case in openrouter_cases)
    assert {case["reason"] for case in openrouter_cases} == {"no_verified_injection_route"}
    assert runner.validate_manifest(manifest) == []


def test_run_initializes_and_resumes_zero_send_ledger_without_real_requests(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "results-first"
    resumed_output_path = tmp_path / "results-resumed"
    ledger_path = tmp_path / "ledger.jsonl"
    manifest = runner.build_manifest()
    write_manifest(manifest_path, manifest)

    exit_code = runner.main([
        "run", "--manifest", str(manifest_path), "--ledger", str(ledger_path),
        "--output", str(output_path),
    ])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 3, report
    assert report["state"] == "blocked", report
    result_file = output_path / "results.json"
    results = json.loads(result_file.read_text(encoding="utf-8"))
    [initialized] = [json.loads(line) for line in ledger_path.read_text(
        encoding="utf-8").splitlines()]

    assert report["reason"] == "hard_cost_guard_and_real_transport_observer_unavailable"
    assert report["network_requests"] == 0
    assert results["network_requests"] == 0
    assert results["manifest_hash"] == manifest["manifest_hash"]
    assert results["campaign_id"] == manifest["campaign_id"]
    assert len(results["results"]) == len(manifest["cases"])
    assert all(row["schema"] == runner.RESULT_SCHEMA for row in results["results"])
    assert all(row["state"] == "blocked" for row in results["results"])
    assert all(row["failure_code"] == "hard_cost_guard_unavailable"
               for row in results["results"])
    assert all(row["attempt_id"] is None for row in results["results"])
    assert all(row["wire_effort"] is None for row in results["results"])
    assert all(row["response_complete"] is False for row in results["results"])
    assert all(row["state"] != "compatible" for row in results["results"])
    assert initialized["schema"] == runner.LEDGER_SCHEMA
    assert initialized["campaign_id"] == manifest["campaign_id"]
    assert initialized["manifest_hash"] == manifest["manifest_hash"]
    assert initialized["network_requests"] == 0
    assert set(initialized["accounts"]) == {"openrouter", "opencode-go"}
    assert "openai-codex-usage" not in initialized["accounts"]
    assert initialized["codex_usage"] == {
        "status": "available",
        "identity_matches_codex_app": True,
        "window_minutes": 300,
        "baseline_used_percent": 31,
        "max_increase_percentage_points": 30,
        "observed_used_percent": 34,
        "stop_threshold_percent": 60,
        "hard_ceiling_percent": 61,
    }
    for provider, cap in runner.CAPS.items():
        account = initialized["accounts"][provider]
        assert account["hard_cap"] == cap["hard_cap"]
        assert account["working_cap"] == cap["working_cap"]
        assert account["reconciled_spend"] == account["reserved"] == account["unresolved"] == "0.00"
        assert account["attempts"] == []
    first_results = results["results"]
    first_run_history = results["run_history"]

    # Simulate a historical physical request with an unresolved cost. This is
    # seeded through pure ledger helpers; no request is issued by this test.
    historical = runner.persist_ledger_transition(
        ledger_path, manifest["manifest_hash"], runner.reserve_attempt,
        "opencode-go", "historical-case", "0.40")
    historical = runner.persist_ledger_transition(
        ledger_path, manifest["manifest_hash"], runner.transition_attempt,
        "opencode-go", "historical-case", "0001", "sent")
    historical = runner.persist_ledger_transition(
        ledger_path, manifest["manifest_hash"], runner.transition_attempt,
        "opencode-go", "historical-case", "0001", "unknown")
    prior_results = historical["results"]
    prior_history = historical["run_history"]

    resume_exit = runner.main([
        "run", "--manifest", str(manifest_path), "--ledger", str(ledger_path),
        "--output", str(resumed_output_path), "--resume",
    ])
    resume_report = json.loads(capsys.readouterr().out)
    events = [json.loads(line) for line in ledger_path.read_text(
        encoding="utf-8").splitlines()]
    assert resume_exit == 3
    assert resume_report["state"] == "blocked"
    assert resume_report["network_requests"] == 0
    assert resume_report["ledger_network_requests"] == 1
    assert events[-1]["event"] == "resumed_zero_send"
    assert events[-1]["manifest_hash"] == manifest["manifest_hash"]
    assert events[-1]["campaign_id"] == manifest["campaign_id"]
    assert events[-1]["network_requests"] == 1
    assert events[-1]["accounts"] == historical["accounts"]
    assert (resumed_output_path / "results.json").is_file()
    resumed_results = json.loads((resumed_output_path / "results.json").read_text(
        encoding="utf-8"))
    assert resumed_results["results"] == prior_results == first_results
    assert resumed_results["run_history"][:1] == prior_history == first_run_history
    assert [item["invocation"] for item in resumed_results["run_history"]] == [1, 2]
    historical_attempt = events[-1]["accounts"]["opencode-go"]["attempts"][0]
    assert historical_attempt["state"] == "unknown"
    assert events[-1]["accounts"]["opencode-go"]["reserved"] == "0.40"
    assert events[-1]["accounts"]["opencode-go"]["unresolved"] == "0.40"
    assert resumed_results["campaign_id"] == manifest["campaign_id"]

    # A second run without the explicit resume flag refuses the existing ledger.
    refused_exit = runner.main([
        "run", "--manifest", str(manifest_path), "--ledger", str(ledger_path),
        "--output", str(tmp_path / "results-refused"),
    ])
    refused_report = json.loads(capsys.readouterr().out)
    assert refused_exit == 3
    assert refused_report["reason"] == "ledger_unavailable"
    assert refused_report["detail"] == "ledger_exists_use_resume"
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 5
    assert not (tmp_path / "results-refused").exists()


def test_run_rejects_tampered_manifest_before_emitting_results(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "results"
    manifest = runner.build_manifest()
    manifest["cases"][0]["target_effort"] = "xhigh"
    write_manifest(manifest_path, manifest)

    exit_code = runner.main([
        "run", "--manifest", str(manifest_path), "--ledger", str(tmp_path / "ledger"),
        "--output", str(output_path),
    ])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["state"] == "blocked"
    assert report["reason"] == "manifest_invalid"
    assert "manifest_hash_mismatch" in report["errors"]
    assert "case_rubric_invalid" in report["errors"]
    assert report["network_requests"] == 0
    assert not output_path.exists()


def test_lock_context_releases_and_reacquires_single_writer_ledger_lock(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    with runner.exclusive_ledger_lock(ledger):
        assert Path(str(ledger) + ".lock").exists()
        with pytest.raises((OSError, BlockingIOError)):
            with runner.exclusive_ledger_lock(ledger):
                pass

    # The lock can be acquired again after leaving the first context.
    with runner.exclusive_ledger_lock(ledger):
        pass


def test_decimal_reservations_are_independent_and_fail_before_exceeding_caps():
    manifest = runner.build_manifest()
    ledger = runner._empty_ledger(manifest)
    original = json.loads(json.dumps(ledger))

    openrouter = runner.reserve_attempt(ledger, "openrouter", "or-case", "1.80")
    go = runner.reserve_attempt(openrouter, "opencode-go", "go-case", "2.70")
    assert go["accounts"]["openrouter"]["reserved"] == "1.80"
    assert go["accounts"]["opencode-go"]["reserved"] == "2.70"
    assert ledger == original  # helpers return a new snapshot rather than mutate input

    with pytest.raises(ValueError, match="reservation_exceeds_provider_cap"):
        runner.reserve_attempt(go, "openrouter", "another-or-case", "0.01")
    # Exhausting one provider's allowance does not borrow the other provider's balance.
    independent_go = runner.reserve_attempt(
        openrouter, "opencode-go", "independent-go-case", "0.01")
    assert independent_go["accounts"]["openrouter"]["reserved"] == "1.80"
    assert independent_go["accounts"]["opencode-go"]["reserved"] == "0.01"


@pytest.mark.parametrize("invalid", [0, "0", "NaN", "Infinity", "-0.01", 0.5, True])
def test_reservation_rejects_invalid_or_inexact_amounts(invalid):
    ledger = runner._empty_ledger(runner.build_manifest())
    with pytest.raises(ValueError):
        runner.reserve_attempt(ledger, "openrouter", "case", invalid)


def test_known_cost_releases_reservation_commits_spend_and_allows_reconciled_retry():
    ledger = runner._empty_ledger(runner.build_manifest())
    reserved = runner.reserve_attempt(ledger, "openrouter", "case-1", "0.75")
    sent = runner.transition_attempt(reserved, "openrouter", "case-1", "0001", "sent")
    assert sent["network_requests"] == 1
    completed = runner.transition_attempt(
        sent, "openrouter", "case-1", "0001", "completed", "0.60")
    account = completed["accounts"]["openrouter"]
    assert account["reserved"] == "0.00"
    assert account["unresolved"] == "0.00"
    assert account["reconciled_spend"] == "0.60"
    assert account["attempts"][0]["state"] == "completed"
    assert account["attempts"][0]["provider_cost"] == "0.60"

    retry = runner.reserve_attempt(completed, "openrouter", "case-1", "0.25")
    assert retry["accounts"]["openrouter"]["attempts"][-1]["attempt_id"] == "0002"
    assert retry["accounts"]["openrouter"]["reserved"] == "0.25"


def test_unknown_cost_retains_reservation_until_exact_reconciliation():
    ledger = runner._empty_ledger(runner.build_manifest())
    reserved = runner.reserve_attempt(ledger, "opencode-go", "uncertain", "1.20")
    sent = runner.transition_attempt(reserved, "opencode-go", "uncertain", "0001", "sent")
    unknown = runner.transition_attempt(
        sent, "opencode-go", "uncertain", "0001", "completed", None)
    account = unknown["accounts"]["opencode-go"]
    assert account["reserved"] == "1.20"
    assert account["unresolved"] == "1.20"
    assert account["reconciled_spend"] == "0.00"
    assert account["attempts"][0]["state"] == "unknown"
    with pytest.raises(ValueError, match="case_attempt_not_reconciled"):
        runner.reserve_attempt(unknown, "opencode-go", "uncertain", "0.10")

    reconciled = runner.reconcile_attempt(
        unknown, "opencode-go", "uncertain", "0001", "0.90")
    account = reconciled["accounts"]["opencode-go"]
    assert account["reserved"] == account["unresolved"] == "0.00"
    assert account["reconciled_spend"] == "0.90"
    assert account["attempts"][0]["state"] == "reconciled"
    assert account["attempts"][0]["provider_cost"] == "0.90"
    retry = runner.reserve_attempt(reconciled, "opencode-go", "uncertain", "0.10")
    assert retry["accounts"]["opencode-go"]["attempts"][-1]["attempt_id"] == "0002"
    with pytest.raises(ValueError, match="attempt_not_reconcilable"):
        runner.reconcile_attempt(reconciled, "opencode-go", "uncertain", "0001", "0.90")


def test_cost_overrun_records_discrepancy_and_blocks_provider():
    ledger = runner._empty_ledger(runner.build_manifest())
    reserved = runner.reserve_attempt(ledger, "openrouter", "overrun", "0.50")
    sent = runner.transition_attempt(reserved, "openrouter", "overrun", "0001", "sent")
    overrun = runner.transition_attempt(
        sent, "openrouter", "overrun", "0001", "completed", "0.65")
    account = overrun["accounts"]["openrouter"]
    attempt = account["attempts"][0]
    assert attempt["state"] == "blocked"
    assert attempt["cost_discrepancy"] == {
        "reserved": "0.50", "actual": "0.65", "over_by": "0.15", "over_cap": False,
    }
    assert account["reserved"] == "0.00"
    assert account["reconciled_spend"] == "0.65"
    assert account["blocked"] is True
    assert account["block_reason"] == "provider_cost_exceeded_reservation"
    with pytest.raises(ValueError, match="provider_budget_blocked"):
        runner.reserve_attempt(overrun, "openrouter", "later", "0.10")
    # The provider's block does not disable the independent other account.
    assert runner.reserve_attempt(overrun, "opencode-go", "later-go", "0.10")[
        "accounts"]["opencode-go"]["reserved"] == "0.10"


def test_hard_working_budget_overrun_is_retained_and_blocks_provider():
    ledger = runner._empty_ledger(runner.build_manifest())
    reserved = runner.reserve_attempt(ledger, "openrouter", "cap", "1.80")
    sent = runner.transition_attempt(reserved, "openrouter", "cap", "0001", "sent")
    over_cap = runner.transition_attempt(
        sent, "openrouter", "cap", "0001", "completed", "1.81")
    account = over_cap["accounts"]["openrouter"]
    assert account["reconciled_spend"] == "1.81"
    assert account["attempts"][0]["cost_discrepancy"]["over_cap"] is True
    assert account["block_reason"] == "provider_cost_exceeded_reservation"


def test_attempt_transitions_reject_replay_and_invalid_order():
    ledger = runner._empty_ledger(runner.build_manifest())
    reserved = runner.reserve_attempt(ledger, "openrouter", "replay", "0.20")
    sent = runner.transition_attempt(reserved, "openrouter", "replay", "0001", "sent")
    with pytest.raises(ValueError, match="attempt_replay_or_transition_invalid"):
        runner.transition_attempt(sent, "openrouter", "replay", "0001", "sent")
    completed = runner.transition_attempt(
        sent, "openrouter", "replay", "0001", "completed", "0.20")
    with pytest.raises(ValueError, match="attempt_replay_or_transition_invalid"):
        runner.transition_attempt(completed, "openrouter", "replay", "0001", "completed")
    with pytest.raises(ValueError, match="attempt_not_found"):
        runner.transition_attempt(completed, "openrouter", "missing", "0001", "sent")


def test_persisted_ledger_transitions_are_locked_append_only_and_recoverable(tmp_path):
    manifest = runner.build_manifest()
    ledger_path = tmp_path / "ledger.jsonl"
    initial = runner._empty_ledger(manifest)
    runner._append_ledger_event(ledger_path, initial)

    reserved = runner.persist_ledger_transition(
        ledger_path, manifest["manifest_hash"], runner.reserve_attempt,
        "openrouter", "persisted-case", "0.40")
    sent = runner.persist_ledger_transition(
        ledger_path, manifest["manifest_hash"], runner.transition_attempt,
        "openrouter", "persisted-case", "0001", "sent")
    completed = runner.persist_ledger_transition(
        ledger_path, manifest["manifest_hash"], runner.transition_attempt,
        "openrouter", "persisted-case", "0001", "completed", "0.30")

    snapshots = [json.loads(line) for line in ledger_path.read_text(
        encoding="utf-8").splitlines()]
    assert len(snapshots) == 4
    assert reserved["accounts"]["openrouter"]["reserved"] == "0.40"
    assert sent["network_requests"] == 1
    assert completed["accounts"]["openrouter"]["reconciled_spend"] == "0.30"
    recovered = runner._read_latest_ledger(ledger_path, manifest["manifest_hash"])
    assert recovered == completed

    with pytest.raises(ValueError, match="ledger_history_mismatch"):
        runner.persist_ledger_transition(
            ledger_path, "wrong-manifest-hash", runner.reserve_attempt,
            "openrouter", "other-case", "0.10")
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 4
