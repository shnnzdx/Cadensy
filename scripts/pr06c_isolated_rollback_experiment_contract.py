"""Pure offline validation for PR-06C.1C isolated ECS rollback evidence.

This module validates a completed evidence package only. It has no AWS SDK,
subprocess, socket, or network dependency and cannot create, update, or delete
AWS resources or change the production recovery acceptance Manifest.
"""

from __future__ import annotations

import re
from typing import Any


_SCHEMA_VERSION = "pr06c-isolated-rollback-evidence/v1"
_PRODUCTION_ACCOUNT_ID = "448678332746"
_TARGET_ALEMBIC_REVISION = "c2a4b6d8e0f1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")


class IsolatedExperimentContractError(ValueError):
    """Raised when an evidence package cannot prove the planned safety boundary."""


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IsolatedExperimentContractError(f"{name} must be an object")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IsolatedExperimentContractError(f"{name} must be a positive integer")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise IsolatedExperimentContractError(f"{name} must be a sha256 image digest")
    return value


def _ecs_arn(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("arn:aws:ecs:"):
        raise IsolatedExperimentContractError(f"{name} must be an ECS ARN")
    return value


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


def _final_counts(value: object, name: str) -> None:
    counts = _mapping(value, name)
    if set(counts) != {"desired", "running", "pending"}:
        raise IsolatedExperimentContractError(f"{name} fields are incomplete")
    if any(counts[field] != 0 for field in counts):
        raise IsolatedExperimentContractError(f"{name} must prove zero desired, running, and pending tasks")


def _validate_environment(evidence: dict[str, Any]) -> None:
    environment = _mapping(evidence.get("environment"), "environment")
    experiment_account = environment.get("experiment_account_id")
    if not isinstance(experiment_account, str) or not _AWS_ACCOUNT_ID.fullmatch(experiment_account):
        raise IsolatedExperimentContractError("experiment account ID is invalid")
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


def _validate_cost_and_cleanup(evidence: dict[str, Any]) -> None:
    controls = _mapping(evidence.get("cost_and_cleanup"), "cost_and_cleanup")
    _positive_int(controls.get("budget_cap_usd"), "budget cap")
    duration = _positive_int(controls.get("max_duration_minutes"), "maximum duration")
    if duration > 180:
        raise IsolatedExperimentContractError("maximum experiment duration exceeds the reviewed three-hour bound")
    if controls.get("budget_alarm_armed") is not True:
        raise IsolatedExperimentContractError("budget alarm was not armed")
    if controls.get("automatic_cleanup_completed") is not True:
        raise IsolatedExperimentContractError("automatic cleanup was not completed")


def _validate_forward_schema(evidence: dict[str, Any]) -> None:
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
    _sha256(compatibility.get("legacy_runtime_image_digest"), "legacy runtime image")
    _audit_refs(compatibility.get("audit_evidence_refs"), "forward-schema audit evidence")


def _validate_case(case: object, expected_scenario: str) -> None:
    evidence = _mapping(case, "test case")
    if evidence.get("scenario") != expected_scenario:
        raise IsolatedExperimentContractError("test cases are missing a required failure scenario")
    if evidence.get("single_update_changed_task_definition_and_desired_count") is not True:
        raise IsolatedExperimentContractError("test case did not use one combined task-definition/count update")
    _ecs_arn(evidence.get("source_service_revision_arn"), "source service revision")
    _ecs_arn(evidence.get("target_service_revision_arn"), "target service revision")
    _ecs_arn(evidence.get("source_task_definition_arn"), "source task definition")
    _ecs_arn(evidence.get("candidate_task_definition_arn"), "candidate task definition")
    old_image = _sha256(evidence.get("old_image_digest"), "old image")
    candidate_image = _sha256(evidence.get("candidate_image_digest"), "candidate image")
    if old_image == candidate_image:
        raise IsolatedExperimentContractError("candidate image must differ from the old image")
    if evidence.get("alb_target_health_collected") is not True:
        raise IsolatedExperimentContractError("ALB target health was not collected")
    target_states = evidence.get("alb_target_states")
    if not isinstance(target_states, list) or any(not isinstance(state, str) for state in target_states):
        raise IsolatedExperimentContractError("ALB target-state evidence is malformed")

    if expected_scenario == "startup_failure":
        if evidence.get("candidate_reached_running") is not False:
            raise IsolatedExperimentContractError("startup-failure candidate unexpectedly reached RUNNING")
        if evidence.get("candidate_failure_observed") != "startup":
            raise IsolatedExperimentContractError("startup failure was not observed")
        if target_states:
            raise IsolatedExperimentContractError("startup failure should not claim an ALB target state")
    else:
        if evidence.get("candidate_reached_running") is not True:
            raise IsolatedExperimentContractError("ALB-failure candidate did not reach RUNNING")
        if evidence.get("candidate_failure_observed") != "alb_health":
            raise IsolatedExperimentContractError("ALB health failure was not observed")
        if "unhealthy" not in target_states:
            raise IsolatedExperimentContractError("ALB health-failure evidence lacks an unhealthy target")

    _final_counts(evidence.get("final_counts"), "final counts")
    if evidence.get("old_image_restarted") is not False:
        raise IsolatedExperimentContractError("old image restarted; recovery safety result is negative")
    if evidence.get("deployment_terminal_status") != "ROLLBACK_SUCCESSFUL":
        raise IsolatedExperimentContractError("deployment rollback did not complete successfully")
    _audit_refs(evidence.get("audit_evidence_refs"), "test-case audit evidence")


def _validate_approvals(evidence: dict[str, Any]) -> None:
    approvals = _mapping(evidence.get("human_approvals"), "human_approvals")
    required = {"experiment_reviewer", "database_owner", "cost_owner"}
    if set(approvals) != required or any(approvals[name] != "APPROVED" for name in required):
        raise IsolatedExperimentContractError("all independent human approvals are required")


def validate_completed_isolated_experiment(evidence_payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an evidence package without asserting that AWS evidence is genuine.

    Human reviewers must independently inspect referenced artifacts. Passing this
    parser is only a structural prerequisite and never changes the PENDING
    production recovery Manifest.
    """

    evidence = _mapping(evidence_payload, "isolated experiment evidence")
    if evidence.get("schema_version") != _SCHEMA_VERSION:
        raise IsolatedExperimentContractError("isolated experiment evidence schema is not approved")
    if evidence.get("status") != "COMPLETED_SAFE":
        raise IsolatedExperimentContractError("isolated experiment is not complete and safe")
    _validate_environment(evidence)
    _validate_cost_and_cleanup(evidence)
    _validate_forward_schema(evidence)

    cases = evidence.get("test_cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise IsolatedExperimentContractError("exactly two rollback failure cases are required")
    scenarios = [case.get("scenario") if isinstance(case, dict) else None for case in cases]
    if set(scenarios) != {"startup_failure", "alb_health_failure"}:
        raise IsolatedExperimentContractError("required rollback failure scenarios are incomplete")
    for scenario in ("startup_failure", "alb_health_failure"):
        _validate_case(next(case for case in cases if case["scenario"] == scenario), scenario)
    _validate_approvals(evidence)
    return evidence
