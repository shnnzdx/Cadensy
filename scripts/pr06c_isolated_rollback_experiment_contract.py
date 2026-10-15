"""Pure offline validation for PR-06C.1C isolated ECS rollback evidence.

This module validates a completed evidence package only. It has no AWS SDK,
subprocess, socket, or network dependency and cannot create, update, or delete
AWS resources or change the production recovery acceptance Manifest.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any


_SCHEMA_VERSION = "pr06c-isolated-rollback-evidence/v1"
_PRODUCTION_ACCOUNT_ID = "448678332746"
_APPROVED_REGION = "us-east-1"
_TARGET_ALEMBIC_REVISION = "c2a4b6d8e0f1"
_MAX_BUDGET_CAP_USD = 25
_MAX_DURATION_MINUTES = 180
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")


class IsolatedExperimentContractError(ValueError):
    """Raised when an evidence package cannot prove the planned safety boundary."""


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IsolatedExperimentContractError(f"{name} must be an object")
    return value


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IsolatedExperimentContractError(f"{name} must be a non-empty string")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IsolatedExperimentContractError(f"{name} must be a positive integer")
    return value


def _exact_int(value: object, expected: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise IsolatedExperimentContractError(f"{name} must be the integer {expected}")


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise IsolatedExperimentContractError(f"{name} must be a sha256 image digest")
    return value


def _evidence_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        raise IsolatedExperimentContractError(f"{name} must be a lowercase sha256 value")
    return value


def _ecs_arn_in_environment(
    value: object,
    name: str,
    account_id: str,
    resource_prefix: str,
) -> str:
    if not isinstance(value, str):
        raise IsolatedExperimentContractError(f"{name} must be an ECS ARN")
    parts = value.split(":", 5)
    if (
        len(parts) != 6
        or parts[:3] != ["arn", "aws", "ecs"]
        or parts[3] != _APPROVED_REGION
        or parts[4] != account_id
        or not parts[5].startswith(resource_prefix)
    ):
        raise IsolatedExperimentContractError(
            f"{name} must be a {resource_prefix.rstrip('/')} ARN in the approved experiment account and region"
        )
    return value


def _task_arn_in_environment(value: object, name: str, account_id: str) -> str:
    return _ecs_arn_in_environment(value, name, account_id, "task/")


def _audit_refs(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise IsolatedExperimentContractError(f"{name} must contain immutable audit evidence")
    if any(
        not isinstance(ref, str) or not ref.startswith("s3://") or "?versionId=" not in ref
        for ref in value
    ):
        raise IsolatedExperimentContractError(f"{name} must contain versioned S3 evidence references")
    if len(set(value)) != len(value):
        raise IsolatedExperimentContractError(f"{name} must not duplicate evidence references")
    return value


def _utc_instant(value: object, name: str) -> datetime:
    text = _nonempty_string(value, name)
    if not text.endswith("Z"):
        raise IsolatedExperimentContractError(f"{name} must be a UTC ISO-8601 timestamp")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IsolatedExperimentContractError(f"{name} must be a UTC ISO-8601 timestamp") from exc


def _final_counts(value: object, name: str) -> None:
    counts = _mapping(value, name)
    if set(counts) != {"desired", "running", "pending"}:
        raise IsolatedExperimentContractError(f"{name} fields are incomplete")
    for field in counts:
        _exact_int(counts[field], 0, f"{name}.{field}")


def _validate_environment(evidence: dict[str, Any]) -> str:
    environment = _mapping(evidence.get("environment"), "environment")
    experiment_account = environment.get("experiment_account_id")
    if not isinstance(experiment_account, str) or not _AWS_ACCOUNT_ID.fullmatch(experiment_account):
        raise IsolatedExperimentContractError("experiment account ID is invalid")
    if environment.get("approved_region") != _APPROVED_REGION:
        raise IsolatedExperimentContractError("experiment region is outside the approved boundary")
    if environment.get("production_account_id") != _PRODUCTION_ACCOUNT_ID:
        raise IsolatedExperimentContractError("production account identity does not match the reviewed boundary")
    if experiment_account == _PRODUCTION_ACCOUNT_ID:
        raise IsolatedExperimentContractError("experiment must not run in the production account")
    if environment.get("classification") != "dedicated-non-production":
        raise IsolatedExperimentContractError("experiment environment is not dedicated non-production")
    if environment.get("database") != "dedicated-isolated-postgresql":
        raise IsolatedExperimentContractError("experiment database is not dedicated and isolated")
    if environment.get("data_classification") != "synthetic-only":
        raise IsolatedExperimentContractError("experiment data is not synthetic-only")
    if environment.get("paid_ai_provider") != "disabled":
        raise IsolatedExperimentContractError("paid AI provider must remain disabled")
    return experiment_account


def _validate_cost_and_cleanup(evidence: dict[str, Any]) -> None:
    controls = _mapping(evidence.get("cost_and_cleanup"), "cost_and_cleanup")
    budget = _positive_int(controls.get("budget_cap_usd"), "budget cap")
    if budget > _MAX_BUDGET_CAP_USD:
        raise IsolatedExperimentContractError("budget cap exceeds the reviewed USD 25 authorization boundary")
    duration = _positive_int(controls.get("max_duration_minutes"), "maximum duration")
    if duration > _MAX_DURATION_MINUTES:
        raise IsolatedExperimentContractError("maximum experiment duration exceeds the reviewed three-hour bound")
    if controls.get("budget_alarm_armed") is not True:
        raise IsolatedExperimentContractError("budget alarm was not armed")
    if controls.get("automatic_cleanup_completed") is not True:
        raise IsolatedExperimentContractError("automatic cleanup was not completed")

    watchdog = _mapping(controls.get("cleanup_watchdog"), "cleanup watchdog")
    if watchdog.get("independent_principal") != "PR06CCleanupWatchdog":
        raise IsolatedExperimentContractError("cleanup watchdog must use its independent execution identity")
    if watchdog.get("trigger") != "expiry-or-stop-condition":
        raise IsolatedExperimentContractError("cleanup watchdog trigger is incomplete")
    if watchdog.get("idempotent_cleanup") is not True:
        raise IsolatedExperimentContractError("cleanup watchdog is not idempotent")
    if watchdog.get("cleanup_failure_escalation") != "page-cost-owner-and-security-owner":
        raise IsolatedExperimentContractError("cleanup watchdog escalation is incomplete")
    _audit_refs(watchdog.get("audit_evidence_refs"), "cleanup watchdog audit evidence")

    inventory = _mapping(controls.get("post_cleanup_tagged_inventory"), "post-cleanup tagged inventory")
    if inventory.get("status") != "NO_TAGGED_RUNTIME_RESOURCES_REMAIN":
        raise IsolatedExperimentContractError("post-cleanup inventory does not prove tagged runtime resources were removed")
    _utc_instant(inventory.get("collected_at_utc"), "post-cleanup inventory timestamp")
    _audit_refs(inventory.get("audit_evidence_refs"), "post-cleanup inventory audit evidence")


def _validate_forward_schema(evidence: dict[str, Any]) -> str:
    compatibility = _mapping(evidence.get("old_runtime_forward_schema"), "old_runtime_forward_schema")
    if compatibility.get("status") != "COMPATIBLE":
        raise IsolatedExperimentContractError("old runtime forward-schema compatibility is not accepted")
    if compatibility.get("target_alembic_revision") != _TARGET_ALEMBIC_REVISION:
        raise IsolatedExperimentContractError("forward-schema target revision is not approved")
    if compatibility.get("database_principal_mode") != "read-only":
        raise IsolatedExperimentContractError("forward-schema validation did not use a read-only database principal")
    if compatibility.get("schema_mutation_attempted") is not False:
        raise IsolatedExperimentContractError("forward-schema validation attempted schema mutation")
    if compatibility.get("production_data_used") is not False:
        raise IsolatedExperimentContractError("forward-schema validation used production data")
    image = _sha256(compatibility.get("legacy_runtime_image_digest"), "legacy runtime image")
    _audit_refs(compatibility.get("audit_evidence_refs"), "forward-schema audit evidence")
    return image


def _validate_production_equivalence(evidence: dict[str, Any]) -> int:
    equivalence = _mapping(evidence.get("production_equivalence"), "production equivalence")
    if equivalence.get("match_status") != "MATCHED_TO_READ_ONLY_PRODUCTION_SNAPSHOT":
        raise IsolatedExperimentContractError("sandbox deployment settings were not matched to the reviewed production snapshot")
    if equivalence.get("deployment_controller") != "ECS":
        raise IsolatedExperimentContractError("deployment controller differs from the production target")
    circuit_breaker = _mapping(equivalence.get("deployment_circuit_breaker"), "deployment circuit breaker")
    if circuit_breaker != {"enable": True, "rollback": True}:
        raise IsolatedExperimentContractError("deployment circuit breaker does not exactly preserve the required safety settings")
    _positive_int(equivalence.get("minimum_healthy_percent"), "minimum healthy percent")
    _positive_int(equivalence.get("maximum_percent"), "maximum percent")
    _evidence_sha256(equivalence.get("production_snapshot_sha256"), "production configuration snapshot")
    _evidence_sha256(equivalence.get("health_check_configuration_sha256"), "health-check configuration snapshot")

    threshold = _mapping(equivalence.get("failure_threshold"), "failure threshold")
    if threshold.get("source") not in {"ECS_DEFAULT", "PRODUCTION_SNAPSHOT"}:
        raise IsolatedExperimentContractError("failure threshold source is not declared")
    _exact_int(threshold.get("desired_count"), 1, "failure threshold desired count")
    failures = _positive_int(threshold.get("expected_failure_count"), "expected failure count")
    if failures < 3:
        raise IsolatedExperimentContractError("desired-count-one experiment must budget for at least three failures")
    _audit_refs(equivalence.get("audit_evidence_refs"), "production-equivalence audit evidence")
    return failures


def _validate_candidate_binding(evidence: dict[str, Any], expected_scenario: str) -> None:
    binding = _mapping(evidence.get("candidate_binding"), "candidate binding")
    required = {
        "scenario": expected_scenario,
        "image_digest": evidence.get("candidate_image_digest"),
        "task_definition_arn": evidence.get("candidate_task_definition_arn"),
        "service_revision_arn": evidence.get("target_service_revision_arn"),
    }
    if binding != required:
        raise IsolatedExperimentContractError("candidate image, task definition, service revision, and scenario are not bound together")


def _validate_task_observation(observation: object, account_id: str, name: str) -> dict[str, Any]:
    item = _mapping(observation, name)
    _task_arn_in_environment(item.get("task_arn"), f"{name} task ARN", account_id)
    _ecs_arn_in_environment(item.get("task_definition_arn"), f"{name} task definition ARN", account_id, "task-definition/")
    _sha256(item.get("image_digest"), f"{name} image digest")
    states = item.get("last_status_sequence")
    if not isinstance(states, list) or not states or any(not isinstance(state, str) for state in states):
        raise IsolatedExperimentContractError(f"{name} must record task status transitions")
    _utc_instant(item.get("observed_at_utc"), f"{name} timestamp")
    return item


def _validate_old_runtime_observation(evidence: dict[str, Any], account_id: str, old_image: str) -> None:
    observation = _mapping(evidence.get("old_runtime_observation"), "old runtime observation")
    if observation.get("status") != "NOT_OBSERVED_WITH_COMPLETE_EVIDENCE":
        raise IsolatedExperimentContractError("old runtime restart status is unknown or not proven complete")
    started_at = _utc_instant(observation.get("window_started_at_utc"), "old runtime observation start")
    ended_at = _utc_instant(observation.get("window_ended_at_utc"), "old runtime observation end")
    if ended_at <= started_at:
        raise IsolatedExperimentContractError("old runtime observation window is invalid")
    if observation.get("ecs_task_state_events_complete") is not True:
        raise IsolatedExperimentContractError("ECS task-state event coverage is incomplete")
    if observation.get("service_deployment_events_complete") is not True:
        raise IsolatedExperimentContractError("service-deployment event coverage is incomplete")
    task_observations = observation.get("task_observations")
    if not isinstance(task_observations, list) or not task_observations:
        raise IsolatedExperimentContractError("old runtime observation must include time-bounded task observations")
    for index, task in enumerate(task_observations):
        item = _validate_task_observation(task, account_id, f"old runtime observation[{index}]")
        if item["image_digest"] == old_image and "RUNNING" in item["last_status_sequence"]:
            raise IsolatedExperimentContractError("old image restarted during the observed rollback window")
    service_events = observation.get("service_deployment_events")
    if not isinstance(service_events, list) or not service_events:
        raise IsolatedExperimentContractError("old runtime observation must include service deployment events")
    for index, event in enumerate(service_events):
        item = _mapping(event, f"service deployment event[{index}]")
        _nonempty_string(item.get("event_id"), f"service deployment event[{index}] ID")
        _utc_instant(item.get("observed_at_utc"), f"service deployment event[{index}] timestamp")
    _audit_refs(observation.get("audit_evidence_refs"), "old runtime observation audit evidence")


def _validate_stage_evidence(
    evidence: dict[str, Any],
    expected_scenario: str,
    account_id: str,
    expected_failure_count: int,
) -> None:
    stage = _mapping(evidence.get("stage_evidence"), "stage evidence")
    if stage.get("ecs_task_state_events_complete") is not True:
        raise IsolatedExperimentContractError("candidate task-state evidence is incomplete")
    if stage.get("service_deployment_events_complete") is not True:
        raise IsolatedExperimentContractError("candidate service-deployment evidence is incomplete")
    _nonempty_string(stage.get("service_deployment_failure_reason"), "service deployment failure reason")
    actual_failure_count = _positive_int(stage.get("actual_failed_task_count"), "actual failed task count")
    attempts = stage.get("failed_task_attempts")
    if actual_failure_count != expected_failure_count:
        raise IsolatedExperimentContractError("actual failed task count does not match the approved failure threshold")
    if not isinstance(attempts, list) or len(attempts) != actual_failure_count:
        raise IsolatedExperimentContractError("every actual failed task requires one evidence-bearing attempt")
    observed = []
    task_arns: set[str] = set()
    for index, attempt in enumerate(attempts, start=1):
        item = _mapping(attempt, f"failed task attempt[{index}]")
        _exact_int(item.get("attempt_number"), index, f"failed task attempt[{index}] number")
        _nonempty_string(item.get("attempt_id"), f"failed task attempt[{index}] ID")
        observed_task = _validate_task_observation(item, account_id, f"failed task attempt[{index}]")
        if observed_task["task_arn"] in task_arns:
            raise IsolatedExperimentContractError("each failed attempt must have a unique ECS task ARN")
        task_arns.add(observed_task["task_arn"])
        _nonempty_string(item.get("failure_reason"), f"failed task attempt[{index}] failure reason")
        _audit_refs(item.get("audit_evidence_refs"), f"failed task attempt[{index}] audit evidence")
        observed.append(observed_task)
    candidate_image = evidence.get("candidate_image_digest")
    candidate_task_definition = evidence.get("candidate_task_definition_arn")
    if any(item["image_digest"] != candidate_image or item["task_definition_arn"] != candidate_task_definition for item in observed):
        raise IsolatedExperimentContractError("candidate task transition does not match the bound candidate identity")
    if any(item["last_status_sequence"][-1] != "STOPPED" for item in observed):
        raise IsolatedExperimentContractError("every observed failed task must end in STOPPED")
    if expected_scenario == "startup_failure":
        if evidence.get("circuit_breaker_stage") != "STAGE_1":
            raise IsolatedExperimentContractError("startup failure must prove circuit-breaker Stage 1")
        if evidence.get("candidate_reached_running") is not False:
            raise IsolatedExperimentContractError("startup-failure candidate unexpectedly reached RUNNING")
        if stage.get("health_checks_evaluated") is not False:
            raise IsolatedExperimentContractError("Stage 1 cannot claim ALB health-check evaluation")
        if any("RUNNING" in item["last_status_sequence"] for item in observed):
            raise IsolatedExperimentContractError("startup failure reached RUNNING and is not Stage 1 evidence")
        if any(not _nonempty_string(item.get("stopped_reason"), "candidate stopped reason") for item in observed):
            raise IsolatedExperimentContractError("Stage 1 requires candidate stopped-task reasons")
    else:
        if evidence.get("circuit_breaker_stage") != "STAGE_2":
            raise IsolatedExperimentContractError("ALB health failure must prove circuit-breaker Stage 2")
        if evidence.get("candidate_reached_running") is not True:
            raise IsolatedExperimentContractError("ALB-failure candidate did not reach RUNNING")
        if stage.get("health_checks_evaluated") is not True:
            raise IsolatedExperimentContractError("Stage 2 requires health-check evaluation evidence")
        if not all("RUNNING" in item["last_status_sequence"] for item in observed):
            raise IsolatedExperimentContractError("Stage 2 lacks a RUNNING transition for an observed failed task")
    _audit_refs(stage.get("audit_evidence_refs"), "stage-evidence audit evidence")


def _validate_case(
    case: object,
    expected_scenario: str,
    account_id: str,
    experiment_run_id: str,
    legacy_image: str,
    expected_failure_count: int,
) -> None:
    evidence = _mapping(case, "test case")
    if evidence.get("scenario") != expected_scenario:
        raise IsolatedExperimentContractError("test cases are missing a required failure scenario")
    if evidence.get("experiment_run_id") != experiment_run_id:
        raise IsolatedExperimentContractError("test-case evidence belongs to a different experiment run")
    if evidence.get("single_update_changed_task_definition_and_desired_count") is not True:
        raise IsolatedExperimentContractError("test case did not use one combined task-definition/count update")
    _ecs_arn_in_environment(evidence.get("source_service_revision_arn"), "source service revision", account_id, "service-revision/")
    _ecs_arn_in_environment(evidence.get("target_service_revision_arn"), "target service revision", account_id, "service-revision/")
    _ecs_arn_in_environment(evidence.get("source_task_definition_arn"), "source task definition", account_id, "task-definition/")
    _ecs_arn_in_environment(evidence.get("candidate_task_definition_arn"), "candidate task definition", account_id, "task-definition/")
    old_image = _sha256(evidence.get("old_image_digest"), "old image")
    candidate_image = _sha256(evidence.get("candidate_image_digest"), "candidate image")
    if old_image != legacy_image:
        raise IsolatedExperimentContractError("test-case old image does not match the forward-schema legacy image")
    if old_image == candidate_image:
        raise IsolatedExperimentContractError("candidate image must differ from the old image")
    _validate_candidate_binding(evidence, expected_scenario)
    if evidence.get("alb_target_health_collected") is not True:
        raise IsolatedExperimentContractError("ALB target health was not collected")
    target_states = evidence.get("alb_target_states")
    if not isinstance(target_states, list) or any(not isinstance(state, str) for state in target_states):
        raise IsolatedExperimentContractError("ALB target-state evidence is malformed")
    if expected_scenario == "startup_failure":
        if evidence.get("candidate_failure_observed") != "startup":
            raise IsolatedExperimentContractError("startup failure was not observed")
        if target_states:
            raise IsolatedExperimentContractError("startup failure should not claim an ALB target state")
    else:
        if evidence.get("candidate_failure_observed") != "alb_health":
            raise IsolatedExperimentContractError("ALB health failure was not observed")
        if "unhealthy" not in target_states:
            raise IsolatedExperimentContractError("ALB health-failure evidence lacks an unhealthy target")
    _validate_stage_evidence(evidence, expected_scenario, account_id, expected_failure_count)
    _validate_old_runtime_observation(evidence, account_id, old_image)
    _final_counts(evidence.get("final_counts"), "final counts")
    if evidence.get("deployment_terminal_status") != "ROLLBACK_SUCCESSFUL":
        raise IsolatedExperimentContractError("deployment rollback did not complete successfully")
    _audit_refs(evidence.get("audit_evidence_refs"), "test-case audit evidence")


def _validate_approvals(evidence: dict[str, Any]) -> None:
    approvals = _mapping(evidence.get("human_approvals"), "human approvals")
    required = {"experiment_reviewer", "database_owner", "cost_owner"}
    if set(approvals) != required or any(approvals[name] != "APPROVED" for name in required):
        raise IsolatedExperimentContractError("all independent human approvals are required")


def validate_completed_isolated_experiment(evidence_payload: dict[str, Any]) -> dict[str, Any]:
    """Validate declared evidence, never its real-world authenticity.

    An independent reviewer must still inspect every referenced artifact. A
    parser pass is only a structural prerequisite and never changes the PENDING
    production recovery Manifest or authorizes an AWS operation.
    """

    evidence = _mapping(evidence_payload, "isolated experiment evidence")
    if evidence.get("schema_version") != _SCHEMA_VERSION:
        raise IsolatedExperimentContractError("isolated experiment evidence schema is not approved")
    if evidence.get("status") != "COMPLETED_SAFE":
        raise IsolatedExperimentContractError("isolated experiment is not complete and safe")
    experiment_run_id = _nonempty_string(evidence.get("experiment_run_id"), "experiment run ID")
    account_id = _validate_environment(evidence)
    _validate_cost_and_cleanup(evidence)
    legacy_image = _validate_forward_schema(evidence)
    expected_failure_count = _validate_production_equivalence(evidence)
    cases = evidence.get("test_cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise IsolatedExperimentContractError("exactly two rollback failure cases are required")
    scenarios = [case.get("scenario") if isinstance(case, dict) else None for case in cases]
    if set(scenarios) != {"startup_failure", "alb_health_failure"}:
        raise IsolatedExperimentContractError("required rollback failure scenarios are incomplete")
    for scenario in ("startup_failure", "alb_health_failure"):
        _validate_case(
            next(case for case in cases if case["scenario"] == scenario),
            scenario,
            account_id,
            experiment_run_id,
            legacy_image,
            expected_failure_count,
        )
    _validate_approvals(evidence)
    return evidence
