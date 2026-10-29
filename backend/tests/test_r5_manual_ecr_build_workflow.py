"""Static safety contract retained for the archived R5 image build evidence."""

from __future__ import annotations

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = (
    REPOSITORY_ROOT
    / "AWS"
    / "archive"
    / "workflows"
    / "retired-release"
    / "r5-manual-ecr-build.yml"
)
RUNNER_DOCKERFILE = REPOSITORY_ROOT / "backend" / "Dockerfile.r5-migration-runner"
RELEASE_SHA = "6daf90ed4f1130171ff6e84c11241f374dc5aeeb"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _run_text() -> str:
    workflow = _workflow()
    return "\n".join(
        step.get("run", "")
        for step in workflow["jobs"]["build-and-push"]["steps"]
    ).casefold()


def test_r5_ecr_workflow_is_manual_main_only_and_uses_the_main_environment():
    workflow = _workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert workflow["jobs"]["build-and-push"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["build-and-push"]["environment"] == "Main"
    assert workflow["env"]["RELEASE_SHA"] == RELEASE_SHA
    assert workflow["env"]["APPROVED_AWS_ACCOUNT_ID"] == "448678332746"
    assert workflow["env"]["APPROVED_AWS_REGION"] == "us-east-1"


def test_r5_ecr_workflow_pins_application_source_and_records_independent_tooling_identity():
    workflow = _workflow()
    steps = workflow["jobs"]["build-and-push"]["steps"]
    release_checkout = next(
        step
        for step in steps
        if step["name"] == "Check out the approved immutable application release"
    )
    tooling_step = next(
        step for step in steps if step["name"] == "Enforce main-only dispatch and record tooling identity"
    )
    source_step = next(
        step for step in steps if step["name"] == "Verify release source and assemble isolated build context"
    )

    assert release_checkout["with"]["ref"] == "${{ env.RELEASE_SHA }}"
    assert release_checkout["with"]["path"] == "release"
    assert "git -C release rev-parse HEAD" in source_step["run"]
    assert "test \"${actual_release_sha}\" = \"${RELEASE_SHA}\"" in source_step["run"]
    assert "git -C tooling rev-parse HEAD" in tooling_step["run"]
    assert "migration=${release_sha}-migration-${{ steps.tooling.outputs.sha }}" in _run_text()
    assert "Dockerfile.r5-migration-runner" in source_step["run"]


def test_r5_ecr_workflow_only_permits_ecr_build_and_push_actions():
    script = _run_text()
    assert "aws sts get-caller-identity" in script
    assert "aws ecr describe-repositories" in script
    assert "aws ecr describe-images" in script
    assert "aws ecr get-login-password" in script
    assert "docker build" in script
    assert "docker push" in script
    assert "imagenotfoundexception" in script
    assert "refusing to overwrite existing immutable ecr tag" in script
    for forbidden in (
        "aws ecs",
        "register-task-definition",
        "run-task",
        "alembic stamp",
        "alembic upgrade",
        "aws rds",
        "aws ssm",
        "aws secretsmanager",
        "aws iam",
        "aws ec2",
        "aws elbv2",
        "create-repository",
    ):
        assert forbidden not in script


def test_r5_ecr_workflow_builds_distinct_immutable_tags_and_offline_checks_runner():
    script = _run_text()
    assert "runtime=${release_sha}" in script
    assert "migration=${release_sha}-migration-" in script
    assert "docker build --file release/backend/dockerfile" in script
    assert "docker build --file release/backend/dockerfile.r5-migration-runner" in script
    assert "docker run --rm --network none" in script
    assert "c2a4b6d8e0f1" in script
    assert "database_url=" in script
    assert "docker push \"${ecr_uri}:${runtime_tag}\"" in script
    assert "docker push \"${ecr_uri}:${migration_tag}\"" in script


def test_r5_migration_runner_is_safe_by_default_and_contains_complete_release_contract():
    dockerfile = RUNNER_DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY requirements.txt" in dockerfile
    assert "COPY alembic.ini" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile
    assert "COPY app ./app" in dockerfile
    assert "COPY data ./data" in dockerfile
    assert "alembic upgrade" not in dockerfile
    assert "alembic stamp" not in dockerfile
    assert "legacy_schema_compat" not in dockerfile
    assert "R5 migration runner ready" in dockerfile
