"""Offline contracts for the CD-01 release helper and GitHub workflows."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_ecs_task_definition.py"
BUILD_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-build-publish.yml"
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-production-release.yml"

spec = importlib.util.spec_from_file_location("cd01_prepare_task_definition", HELPER_PATH)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


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


def test_pr06b_build_artifacts_require_the_reviewed_head_without_authorizing_release():
    """Building a PR-06B image is not a database migration or release approval."""
    build = _workflow(BUILD_WORKFLOW)
    release = _workflow(RELEASE_WORKFLOW)
    build_job = build["jobs"]["build-and-push"]
    build_script = _workflow_script(build)

    assert build_job["env"]["BUILD_ALEMBIC_HEAD"] == "b7d3a1e9c642"
    assert 'backend/alembic/versions/b7d3a1e9c642_pr06b1b_conversation_schema.py' in build_script
    assert "docker run --rm --network none --env build_alembic_head" in build_script
    assert '"${build_alembic_head}"' in build_script
    # Production still fails closed until a separately approved operation has
    # migrated the live schema and supplied the required recovery evidence.
    assert release["env"]["APPROVED_ALEMBIC_HEAD"] == "c2a4b6d8e0f1"


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
