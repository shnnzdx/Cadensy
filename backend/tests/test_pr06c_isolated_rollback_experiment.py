"""Offline acceptance contract for PR-06C.1C isolated rollback evidence."""

from __future__ import annotations

import copy
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


OLD_IMAGE = "sha256:" + "a" * 64
CANDIDATE_IMAGE = "sha256:" + "b" * 64


def _case(scenario: str) -> dict:
    failed_before_running = scenario == "startup_failure"
    return {
        "scenario": scenario,
        "single_update_changed_task_definition_and_desired_count": True,
        "source_service_revision_arn": "arn:aws:ecs:us-east-1:999999999999:service-revision/isolated/old",
        "target_service_revision_arn": "arn:aws:ecs:us-east-1:999999999999:service-revision/isolated/candidate",
        "source_task_definition_arn": "arn:aws:ecs:us-east-1:999999999999:task-definition/cadensy-isolated-old:1",
        "candidate_task_definition_arn": "arn:aws:ecs:us-east-1:999999999999:task-definition/cadensy-isolated-candidate:1",
        "old_image_digest": OLD_IMAGE,
        "candidate_image_digest": CANDIDATE_IMAGE,
        "candidate_reached_running": not failed_before_running,
        "candidate_failure_observed": "startup" if failed_before_running else "alb_health",
        "alb_target_health_collected": True,
        "alb_target_states": [] if failed_before_running else ["unhealthy"],
        "final_counts": {"desired": 0, "running": 0, "pending": 0},
        "old_image_restarted": False,
        "deployment_terminal_status": "ROLLBACK_SUCCESSFUL",
        "audit_evidence_refs": [f"s3://cadensy-isolated-audit/{scenario}/evidence.json?versionId=1"],
    }


def _completed_safe_evidence() -> dict:
    return {
        "schema_version": "pr06c-isolated-rollback-evidence/v1",
        "status": "COMPLETED_SAFE",
        "environment": {
            "experiment_account_id": "999999999999",
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
        "test_cases": [_case("startup_failure"), _case("alb_health_failure")],
        "human_approvals": {
            "experiment_reviewer": "APPROVED",
            "database_owner": "APPROVED",
            "cost_owner": "APPROVED",
        },
    }


def test_completed_isolated_evidence_accepts_two_failed_candidate_paths_without_old_restart():
    evidence = _completed_safe_evidence()

    assert experiment.validate_completed_isolated_experiment(evidence) == evidence


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence["test_cases"][0].__setitem__("old_image_restarted", True),
        lambda evidence: evidence["test_cases"][1].__setitem__("alb_target_states", ["healthy"]),
        lambda evidence: evidence["old_runtime_forward_schema"].__setitem__("database_principal_mode", "read-write"),
        lambda evidence: evidence["environment"].__setitem__("experiment_account_id", "448678332746"),
        lambda evidence: evidence["cost_and_cleanup"].__setitem__("automatic_cleanup_completed", False),
        lambda evidence: evidence["human_approvals"].__setitem__("database_owner", "PENDING"),
    ],
)
def test_completed_evidence_fails_closed_for_unsafe_or_unapproved_experiment(mutate):
    evidence = _completed_safe_evidence()
    mutate(evidence)

    with pytest.raises(experiment.IsolatedExperimentContractError):
        experiment.validate_completed_isolated_experiment(evidence)
