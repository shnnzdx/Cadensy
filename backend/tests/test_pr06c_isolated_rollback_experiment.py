"""Offline acceptance contract for PR-06C.1C isolated rollback evidence."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "scripts" / "pr06c_isolated_rollback_experiment_contract.py"

spec = importlib.util.spec_from_file_location("pr06c_isolated_rollback_experiment", CONTRACT_PATH)
assert spec and spec.loader
experiment = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = experiment
spec.loader.exec_module(experiment)


ACCOUNT = "999999999999"
REGION = "us-east-1"
RUN_ID = "pr06c1c-isolated-run-001"
OLD_IMAGE = "sha256:" + "a" * 64
CANDIDATE_IMAGE = "sha256:" + "b" * 64
SNAPSHOT_HASH = "c" * 64
HEALTH_CHECK_HASH = "d" * 64


def _task_observation(image: str, task_definition: str, statuses: list[str], suffix: str) -> dict:
    return {
        "task_arn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/isolated/{suffix}",
        "task_definition_arn": task_definition,
        "image_digest": image,
        "last_status_sequence": statuses,
        "observed_at_utc": "2026-10-15T12:00:00Z",
    }


def _case(scenario: str) -> dict:
    is_stage_one = scenario == "startup_failure"
    candidate_task_definition = (
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/cadensy-isolated-{scenario}:1"
    )
    target_revision = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service-revision/isolated/{scenario}-candidate"
    candidate_transition = _task_observation(
        CANDIDATE_IMAGE,
        candidate_task_definition,
        ["PENDING", "STOPPED"] if is_stage_one else ["PENDING", "RUNNING", "STOPPED"],
        f"{scenario}-candidate",
    )
    if is_stage_one:
        candidate_transition["stopped_reason"] = "Essential container in task exited"
    return {
        "scenario": scenario,
        "experiment_run_id": RUN_ID,
        "single_update_changed_task_definition_and_desired_count": True,
        "source_service_revision_arn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:service-revision/isolated/old",
        "target_service_revision_arn": target_revision,
        "source_task_definition_arn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/cadensy-isolated-old:1",
        "candidate_task_definition_arn": candidate_task_definition,
        "old_image_digest": OLD_IMAGE,
        "candidate_image_digest": CANDIDATE_IMAGE,
        "candidate_binding": {
            "scenario": scenario,
            "image_digest": CANDIDATE_IMAGE,
            "task_definition_arn": candidate_task_definition,
            "service_revision_arn": target_revision,
        },
        "circuit_breaker_stage": "STAGE_1" if is_stage_one else "STAGE_2",
        "candidate_reached_running": not is_stage_one,
        "candidate_failure_observed": "startup" if is_stage_one else "alb_health",
        "alb_target_health_collected": True,
        "alb_target_states": [] if is_stage_one else ["unhealthy"],
        "stage_evidence": {
            "ecs_task_state_events_complete": True,
            "service_deployment_events_complete": True,
            "service_deployment_failure_reason": "deployment circuit breaker triggered",
            "health_checks_evaluated": not is_stage_one,
            "candidate_task_transitions": [candidate_transition],
            "audit_evidence_refs": [f"s3://cadensy-isolated-audit/{scenario}/stage.json?versionId=1"],
        },
        "old_runtime_observation": {
            "status": "NOT_OBSERVED_WITH_COMPLETE_EVIDENCE",
            "window_started_at_utc": "2026-10-15T12:00:00Z",
            "window_ended_at_utc": "2026-10-15T12:20:00Z",
            "ecs_task_state_events_complete": True,
            "service_deployment_events_complete": True,
            "task_observations": [candidate_transition],
            "service_deployment_events": [
                {"event_id": f"{scenario}-rollback-complete", "observed_at_utc": "2026-10-15T12:20:00Z"}
            ],
            "audit_evidence_refs": [f"s3://cadensy-isolated-audit/{scenario}/old-runtime.json?versionId=1"],
        },
        "final_counts": {"desired": 0, "running": 0, "pending": 0},
        "deployment_terminal_status": "ROLLBACK_SUCCESSFUL",
        "audit_evidence_refs": [f"s3://cadensy-isolated-audit/{scenario}/evidence.json?versionId=1"],
    }


def _completed_safe_evidence() -> dict:
    return {
        "schema_version": "pr06c-isolated-rollback-evidence/v1",
        "status": "COMPLETED_SAFE",
        "experiment_run_id": RUN_ID,
        "environment": {
            "experiment_account_id": ACCOUNT,
            "approved_region": REGION,
            "production_account_id": "448678332746",
            "classification": "dedicated-non-production",
            "database": "dedicated-isolated-postgresql",
            "data_classification": "synthetic-only",
            "paid_ai_provider": "disabled",
        },
        "cost_and_cleanup": {
            "budget_cap_usd": 25,
            "max_duration_minutes": 180,
            "budget_alarm_armed": True,
            "automatic_cleanup_completed": True,
            "cleanup_watchdog": {
                "independent_principal": "PR06CCleanupWatchdog",
                "trigger": "expiry-or-stop-condition",
                "idempotent_cleanup": True,
                "cleanup_failure_escalation": "page-cost-owner-and-security-owner",
                "audit_evidence_refs": ["s3://cadensy-isolated-audit/cleanup/watchdog.json?versionId=1"],
            },
            "post_cleanup_tagged_inventory": {
                "status": "NO_TAGGED_RUNTIME_RESOURCES_REMAIN",
                "collected_at_utc": "2026-10-15T13:00:00Z",
                "audit_evidence_refs": ["s3://cadensy-isolated-audit/cleanup/inventory.json?versionId=1"],
            },
        },
        "old_runtime_forward_schema": {
            "status": "COMPATIBLE",
            "target_alembic_revision": "c2a4b6d8e0f1",
            "database_principal_mode": "read-only",
            "schema_mutation_attempted": False,
            "production_data_used": False,
            "legacy_runtime_image_digest": OLD_IMAGE,
            "audit_evidence_refs": ["s3://cadensy-isolated-audit/schema/evidence.json?versionId=1"],
        },
        "production_equivalence": {
            "match_status": "MATCHED_TO_READ_ONLY_PRODUCTION_SNAPSHOT",
            "deployment_controller": "ECS",
            "deployment_circuit_breaker": {"enable": True, "rollback": True},
            "minimum_healthy_percent": 100,
            "maximum_percent": 200,
            "production_snapshot_sha256": SNAPSHOT_HASH,
            "health_check_configuration_sha256": HEALTH_CHECK_HASH,
            "failure_threshold": {
                "source": "ECS_DEFAULT",
                "desired_count": 1,
                "expected_failure_count": 3,
            },
            "audit_evidence_refs": ["s3://cadensy-isolated-audit/equivalence/evidence.json?versionId=1"],
        },
        "test_cases": [_case("startup_failure"), _case("alb_health_failure")],
        "human_approvals": {
            "experiment_reviewer": "APPROVED",
            "database_owner": "APPROVED",
            "cost_owner": "APPROVED",
        },
    }


def _rejects(mutator) -> None:
    evidence = _completed_safe_evidence()
    mutator(evidence)
    with pytest.raises(experiment.IsolatedExperimentContractError):
        experiment.validate_completed_isolated_experiment(evidence)


def test_completed_isolated_evidence_accepts_bound_two_stage_failure_paths_without_old_restart():
    evidence = _completed_safe_evidence()

    assert experiment.validate_completed_isolated_experiment(evidence) == evidence


@pytest.mark.parametrize("budget_cap", [0, 26, 100, True, "25"])
def test_completed_evidence_rejects_out_of_contract_or_non_integer_budget_caps(budget_cap):
    _rejects(lambda evidence: evidence["cost_and_cleanup"].__setitem__("budget_cap_usd", budget_cap))


@pytest.mark.parametrize("duration", [0, 181, True, "180"])
def test_completed_evidence_rejects_invalid_duration_values(duration):
    _rejects(lambda evidence: evidence["cost_and_cleanup"].__setitem__("max_duration_minutes", duration))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence["test_cases"][0]["old_runtime_observation"].__setitem__("status", "UNKNOWN"),
        lambda evidence: evidence["test_cases"][0]["old_runtime_observation"]["task_observations"].append(
            _task_observation(
                OLD_IMAGE,
                evidence["test_cases"][0]["source_task_definition_arn"],
                ["PENDING", "RUNNING", "STOPPED"],
                "unexpected-old-runtime",
            )
        ),
        lambda evidence: evidence["test_cases"][1].__setitem__("alb_target_states", ["healthy"]),
        lambda evidence: evidence["old_runtime_forward_schema"].__setitem__("database_principal_mode", "read-write"),
        lambda evidence: evidence["environment"].__setitem__("experiment_account_id", "448678332746"),
        lambda evidence: evidence["cost_and_cleanup"].__setitem__("automatic_cleanup_completed", False),
        lambda evidence: evidence["human_approvals"].__setitem__("database_owner", "PENDING"),
        lambda evidence: evidence["test_cases"][0]["final_counts"].__setitem__("running", False),
    ],
)
def test_completed_evidence_fails_closed_for_unsafe_or_unapproved_experiment(mutate):
    _rejects(mutate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence["test_cases"][0].__setitem__(
            "source_task_definition_arn", "arn:aws:ecs:us-east-1:448678332746:task-definition/production:19"
        ),
        lambda evidence: evidence["test_cases"][1].__setitem__(
            "target_service_revision_arn", "arn:aws:ecs:us-west-2:999999999999:service-revision/isolated/wrong-region"
        ),
        lambda evidence: evidence["environment"].__setitem__("approved_region", "us-west-2"),
    ],
)
def test_completed_evidence_rejects_production_account_or_region_drift(mutate):
    _rejects(mutate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence["test_cases"][1].__setitem__("old_image_digest", "sha256:" + "e" * 64),
        lambda evidence: evidence["test_cases"][1]["candidate_binding"].__setitem__("scenario", "startup_failure"),
        lambda evidence: evidence["test_cases"][1].__setitem__("experiment_run_id", "other-run"),
    ],
)
def test_completed_evidence_rejects_cross_case_identity_splicing(mutate):
    _rejects(mutate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence["test_cases"][0].__setitem__("circuit_breaker_stage", "STAGE_2"),
        lambda evidence: evidence["test_cases"][0]["stage_evidence"]["candidate_task_transitions"][0].__setitem__(
            "last_status_sequence", ["PENDING", "RUNNING", "STOPPED"]
        ),
        lambda evidence: evidence["test_cases"][1]["stage_evidence"]["candidate_task_transitions"][0].__setitem__(
            "last_status_sequence", ["PENDING", "STOPPED"]
        ),
        lambda evidence: evidence["production_equivalence"]["failure_threshold"].__setitem__("expected_failure_count", 2),
    ],
)
def test_completed_evidence_rejects_inconclusive_or_misclassified_circuit_breaker_stages(mutate):
    _rejects(mutate)
