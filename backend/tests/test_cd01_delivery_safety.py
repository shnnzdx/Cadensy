"""Offline contracts for the CD-01 release helper and GitHub workflows."""

from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path
import sys

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_ecs_task_definition.py"
BUILD_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-build-publish.yml"
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-production-release.yml"
CAPACITY_CONTRACT_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "capacity_recovery_contract.py"
ROLLBACK_THREAT_MODEL = (
    REPOSITORY_ROOT
    / "AI_enhanced"
    / "PR-06C_Production-Telemetry-Validation"
    / "05_PR06C_ROLLBACK_THREAT_MODEL.md"
)

spec = importlib.util.spec_from_file_location("cd01_prepare_task_definition", HELPER_PATH)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

capacity_spec = importlib.util.spec_from_file_location("cd01_capacity_recovery", CAPACITY_CONTRACT_PATH)
assert capacity_spec and capacity_spec.loader
capacity = importlib.util.module_from_spec(capacity_spec)
sys.modules[capacity_spec.name] = capacity
capacity_spec.loader.exec_module(capacity)


def _task_definition() -> dict:
    return {
        "taskDefinition": {
            "taskDefinitionArn": "arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:19",
            "family": "tripsync-backend",
            "revision": 19,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "taskRoleArn": "arn:aws:iam::448678332746:role/tripsync-backend-task",
            "executionRoleArn": "arn:aws:iam::448678332746:role/tripsync-backend-execution",
            "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
            "containerDefinitions": [
                {
                    "name": "tripsync-backend",
                    "image": "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "a" * 64,
                    "environment": [
                        {"name": "APP_ENV", "value": "production"},
                        {"name": "DISABLE_SCHEDULER", "value": "1"},
                        {"name": "MOCK_AI", "value": "0"},
                        {"name": "CADENSY_B3_PROTECTED_PREVIEW_APPLY", "value": "1"},
                    ],
                    "secrets": [{"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/database-url"}],
                    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/tripsync-backend"}},
                }
            ],
        }
    }


def _workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _workflow_script(workflow: dict) -> str:
    return "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    ).casefold()


def test_task_definition_helper_preserves_live_configuration_and_forces_safe_posture():
    original = _task_definition()
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "b" * 64

    prepared = helper.prepare_task_definition(original, image)
    container = prepared["containerDefinitions"][0]
    environment = {entry["name"]: entry["value"] for entry in container["environment"]}

    assert container["image"] == image
    assert prepared["taskRoleArn"] == original["taskDefinition"]["taskRoleArn"]
    assert prepared["executionRoleArn"] == original["taskDefinition"]["executionRoleArn"]
    assert prepared["networkMode"] == "awsvpc"
    assert prepared["cpu"] == "256"
    assert prepared["memory"] == "512"
    assert container["secrets"] == original["taskDefinition"]["containerDefinitions"][0]["secrets"]
    assert container["logConfiguration"] == original["taskDefinition"]["containerDefinitions"][0]["logConfiguration"]
    assert environment["APP_ENV"] == "production"
    assert environment["DISABLE_SCHEDULER"] == "1"
    assert environment["DEV_ALLOW_MEMBERSHIP_HEADER"] == "0"
    assert environment["CADENSY_B3_PROTECTED_PREVIEW_APPLY"] == "0"
    assert all(environment[gate] == "0" for gate in helper.DEFAULT_OFF_GATES)
    assert "taskDefinitionArn" not in prepared
    assert "revision" not in prepared


def _recovery_inputs(**overrides: str) -> dict[str, str]:
    values = {
        "release_mode": "zero_capacity_recovery",
        "expected_current_task_definition": (
            "arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:19"
        ),
        "expected_current_image_digest": "sha256:" + "a" * 64,
        "expected_current_desired_count": "0",
        "recovery_desired_count": "1",
        "rollback_evidence_sha": "d" * 40,
    }
    values.update(overrides)
    return values


def _service_baseline(**overrides: object) -> dict:
    service = {
        "services": [
            {
                "status": "ACTIVE",
                "taskDefinition": _recovery_inputs()["expected_current_task_definition"],
                "desiredCount": 0,
                "runningCount": 0,
                "pendingCount": 0,
                "deployments": [
                    {
                        "status": "PRIMARY",
                        "rolloutState": "COMPLETED",
                        "taskDefinition": _recovery_inputs()["expected_current_task_definition"],
                    }
                ],
                "deploymentConfiguration": {
                    "minimumHealthyPercent": 100,
                    "maximumPercent": 200,
                    "deploymentCircuitBreaker": {"enable": True, "rollback": True},
                },
            }
        ]
    }
    service["services"][0].update(overrides)
    return service


def _safe_live_task_definition() -> dict:
    payload = _task_definition()
    environment = payload["taskDefinition"]["containerDefinitions"][0]["environment"]
    for entry in environment:
        if entry["name"] == "CADENSY_B3_PROTECTED_PREVIEW_APPLY":
            entry["value"] = "0"
    existing_names = {entry["name"] for entry in environment}
    for gate in helper.DEFAULT_OFF_GATES:
        if gate not in existing_names:
            environment.append({"name": gate, "value": "0"})
    environment.append({"name": "DEV_ALLOW_MEMBERSHIP_HEADER", "value": "0"})
    return payload


def test_zero_capacity_recovery_requires_explicit_mode_and_exactly_one_task():
    request = capacity.validate_recovery_inputs(_recovery_inputs())

    assert request.release_desired_count == 1

    for overrides in (
        {"release_mode": "standard"},
        {"recovery_desired_count": "2"},
        {"recovery_desired_count": "0"},
        {"rollback_evidence_sha": ""},
    ):
        with pytest.raises(capacity.CapacityRecoveryContractError):
            capacity.validate_recovery_inputs(_recovery_inputs(**overrides))


def test_capacity_contract_cli_outputs_only_the_derived_recovery_count(capsys):
    result = capacity.main(
        [
            "--release-mode",
            "zero_capacity_recovery",
            "--expected-current-task-definition",
            _recovery_inputs()["expected_current_task_definition"],
            "--expected-current-image-digest",
            _recovery_inputs()["expected_current_image_digest"],
            "--expected-current-desired-count",
            "0",
            "--recovery-desired-count",
            "1",
            "--rollback-evidence-sha",
            _recovery_inputs()["rollback_evidence_sha"],
        ]
    )

    assert result == 0
    assert capsys.readouterr().out == "1\n"


def test_zero_capacity_recovery_rejects_nonzero_or_drifting_live_baseline():
    request = capacity.validate_recovery_inputs(_recovery_inputs())
    task = _safe_live_task_definition()

    capacity.validate_live_baseline(_service_baseline(), task, request)

    for service_overrides in (
        {"desiredCount": 1, "runningCount": 1},
        {"taskDefinition": "arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:20"},
        {"pendingCount": 1},
    ):
        with pytest.raises(capacity.CapacityRecoveryContractError):
            capacity.validate_live_baseline(_service_baseline(**service_overrides), task, request)


def test_zero_capacity_recovery_rejects_running_image_and_gate_drift():
    request = capacity.validate_recovery_inputs(_recovery_inputs())

    bad_image = _safe_live_task_definition()
    bad_image["taskDefinition"]["containerDefinitions"][0]["image"] = (
        "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "e" * 64
    )
    with pytest.raises(capacity.CapacityRecoveryContractError):
        capacity.validate_live_baseline(_service_baseline(), bad_image, request)

    bad_gate = copy.deepcopy(_safe_live_task_definition())
    bad_gate["taskDefinition"]["containerDefinitions"][0]["environment"].append(
        {"name": "CADENSY_B3_PROTECTED_PREVIEW_APPLY", "value": "1"}
    )
    with pytest.raises(capacity.CapacityRecoveryContractError):
        capacity.validate_live_baseline(_service_baseline(), bad_gate, request)


def test_capacity_contract_is_offline_and_standard_mode_cannot_restore_zero_capacity():
    tree = ast.parse(CAPACITY_CONTRACT_PATH.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imports.isdisjoint({"boto3", "botocore", "subprocess", "socket", "urllib", "requests"})

    with pytest.raises(capacity.CapacityRecoveryContractError):
        capacity.validate_recovery_inputs(
            _recovery_inputs(
                release_mode="standard",
                recovery_desired_count="",
                rollback_evidence_sha="",
            )
        )


def test_cd01_zero_capacity_recovery_is_explicit_and_rechecks_before_service_update():
    release = _workflow(RELEASE_WORKFLOW)
    release_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    release_script = _workflow_script(release)
    inputs = release["on"]["workflow_dispatch"]["inputs"]

    assert set(inputs).issuperset(
        {
            "release_mode",
            "expected_current_task_definition",
            "expected_current_image_digest",
            "expected_current_desired_count",
            "recovery_desired_count",
            "rollback_evidence_sha",
        }
    )
    assert inputs["release_mode"]["default"] == "standard"
    assert "capacity_recovery_contract.py" in release_script
    assert "--desired-count" in release_script
    assert "expected_current_image_digest" in release_script
    assert "rollback evidence" in release_script
    assert "required_reviewers" in release_script
    assert "jq -e 'any(.protection_rules[]?; .type == \"required_reviewers\")'" in release_text
    assert release_text.index("Run read-only schema-head preflight") < release_text.index(
        "register-task-definition"
    ) < release_text.index("Re-check live baseline immediately before service mutation") < release_text.index(
        "aws ecs update-service"
    )
    assert "set -euo pipefail" in release_text


def test_rollback_threat_model_keeps_zero_capacity_rollback_semantics_blocked():
    threat_model = ROLLBACK_THREAT_MODEL.read_text(encoding="utf-8").casefold()

    assert "old image can run" in threat_model
    assert "unknown / blocking" in threat_model
    assert "isolated environment experiment" in threat_model
    assert "database downgrade is never an automatic option" in threat_model


@pytest.mark.parametrize(
    ("mutate", "image"),
    [
        (lambda payload: payload["taskDefinition"]["containerDefinitions"][0]["environment"].append({"name": "DATABASE_URL", "value": "plaintext"}), None),
        (lambda payload: payload["taskDefinition"]["containerDefinitions"][0]["secrets"].clear(), None),
        (lambda payload: payload["taskDefinition"]["containerDefinitions"][0]["environment"].__setitem__(0, {"name": "APP_ENV", "value": "development"}), None),
        (lambda payload: None, "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend:mutable"),
    ],
)
def test_task_definition_helper_fails_closed_for_unsafe_inputs(mutate, image):
    payload = _task_definition()
    mutate(payload)
    image = image or "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "c" * 64

    with pytest.raises(helper.ContractError):
        helper.prepare_task_definition(payload, image)


def test_cd01_workflows_are_digest_pinned_manual_for_production_and_never_migrate_implicitly():
    build = _workflow(BUILD_WORKFLOW)
    release = _workflow(RELEASE_WORKFLOW)
    release_script = _workflow_script(release)

    assert {"workflow_run", "workflow_dispatch"} == set(build["on"])
    assert build["on"]["workflow_run"]["workflows"] == ["CI Regression Gates"]
    assert build["on"]["workflow_run"]["branches"] == ["main"]
    assert "github.event.workflow_run.conclusion == 'success'" in build["jobs"]["build-and-push"]["if"]
    assert "github.event.workflow_run.event == 'push'" in build["jobs"]["build-and-push"]["if"]
    assert build["jobs"]["build-and-push"]["environment"] == "Main"
    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["jobs"]["assert-approval-gate"]["if"] == "github.ref == 'refs/heads/main'"
    assert release["jobs"]["promote"]["needs"] == "assert-approval-gate"
    assert release["jobs"]["promote"]["environment"] == "Main"
    assert release["concurrency"]["cancel-in-progress"] == "false"
    assert "@${runtime_digest}" in release_script
    assert "deploymentcircuitbreaker={enable=true,rollback=true}" in release_script
    assert "dev_allow_membership_header" in release_script
    assert "CADENSY_B3_PROTECTED_PREVIEW_APPLY" in HELPER_PATH.read_text(encoding="utf-8")
    assert "aws ecs update-service" in release_script
    assert "required_reviewers" in release_script
    assert "aws ecs wait services-stable" in release_script
    assert "describe-target-health" in release_script
    assert "alembic -c alembic.ini current" in release_script
    assert "alembic -c alembic.ini heads" in release_script
    for forbidden in ("app.db.init_schema", "alembic upgrade", "alembic stamp", "alembic downgrade"):
        assert forbidden not in release_script


def test_cd01_workflows_do_not_reuse_provisioning_or_mutable_latest_tags():
    build_script = _workflow_script(_workflow(BUILD_WORKFLOW))
    release_script = _workflow_script(_workflow(RELEASE_WORKFLOW))
    for script in (build_script, release_script):
        assert "create-repository" not in script
        assert "aws iam" not in script
        assert "aws ec2" not in script
        assert "aws rds create" not in script
        assert ":latest" not in script
        assert "aws secretsmanager get-secret-value" not in script
        assert "aws ssm get-parameter" not in script
