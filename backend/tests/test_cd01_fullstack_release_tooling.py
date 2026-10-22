"""Offline contracts for the PR-06B coordinated full-stack release tooling."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_HELPER = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_ecs_task_definition.py"
BOOTSTRAP_HELPER = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_backend_bootstrap_task_definition.py"
FRONTEND_HELPER = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_frontend_task_definition.py"
FRONTEND_BUILD_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-frontend-build-publish.yml"
FRONTEND_RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-frontend-production-release.yml"
FRONTEND_DOCKERFILE = REPOSITORY_ROOT / "frontend" / "Dockerfile"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _backend_task_definition(*, include_hmac: bool = True) -> dict:
    secrets = [
        {"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/database-url"},
        {"name": "UNSPLASH_ACCESS_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/unsplash"},
        {"name": "GEOAPIFY_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/geoapify"},
        {"name": "DEEPSEEK_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/deepseek"},
        {"name": "OLLAMA_CLOUD_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/ollama"},
    ]
    if include_hmac:
        secrets.insert(1, {"name": "CONVERSATION_SUBMISSION_HMAC_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/conversation-hmac"})
    return {
        "taskDefinition": {
            "family": "tripsync-backend",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "taskRoleArn": "arn:aws:iam::448678332746:role/tripsync-backend-task",
            "executionRoleArn": "arn:aws:iam::448678332746:role/tripsync-backend-execution",
            "containerDefinitions": [
                {
                    "name": "tripsync-backend",
                    "image": "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend:legacy",
                    "environment": [
                        {"name": "APP_ENV", "value": "production"},
                        {"name": "DISABLE_SCHEDULER", "value": "1"},
                        {"name": "MOCK_AI", "value": "0"},
                    ],
                    "secrets": secrets,
                    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/tripsync-backend"}},
                }
            ],
        }
    }


def _frontend_task_definition() -> dict:
    return {
        "taskDefinition": {
            "family": "tripsync-frontend",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "taskRoleArn": "arn:aws:iam::448678332746:role/tripsync-frontend-task",
            "executionRoleArn": "arn:aws:iam::448678332746:role/tripsync-frontend-execution",
            "containerDefinitions": [
                {
                    "name": "tripsync-frontend",
                    "image": "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-frontend:legacy",
                    "environment": [{"name": "PORT", "value": "3000"}],
                    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/tripsync-frontend"}},
                    "healthCheck": {"command": ["CMD-SHELL", "true"]},
                }
            ],
        }
    }


def test_backend_release_profile_removes_provider_credentials_and_preserves_non_ai_secrets():
    helper = _load_module(BACKEND_HELPER, "pr06b_backend_release_helper")
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "a" * 64

    prepared = helper.prepare_task_definition(_backend_task_definition(), image)
    container = prepared["containerDefinitions"][0]
    secret_names = {entry["name"] for entry in container["secrets"]}
    environment = {entry["name"]: entry["value"] for entry in container["environment"]}

    assert secret_names == {"DATABASE_URL", "CONVERSATION_SUBMISSION_HMAC_KEY", "GEOAPIFY_API_KEY", "UNSPLASH_ACCESS_KEY"}
    assert environment["MOCK_AI"] == "0"
    assert environment["DISABLE_SCHEDULER"] == "1"
    assert environment["CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED"] == "0"
    assert environment["CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"] == "0"
    assert environment["CADENSY_E2E_FAKE_RUNTIME"] == "0"


def test_backend_bootstrap_adds_hmac_without_accepting_a_missing_reference_for_normal_release():
    bootstrap = _load_module(BOOTSTRAP_HELPER, "pr06b_backend_bootstrap_helper")
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "b" * 64
    hmac = "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/conversation-submission-hmac"

    prepared = bootstrap.prepare_bootstrap_task_definition(
        _backend_task_definition(include_hmac=False), image, hmac
    )
    container = prepared["containerDefinitions"][0]

    assert container["image"] == image
    assert {entry["name"] for entry in container["secrets"]} == {
        "DATABASE_URL", "CONVERSATION_SUBMISSION_HMAC_KEY", "GEOAPIFY_API_KEY", "UNSPLASH_ACCESS_KEY"
    }
    assert {entry["name"]: entry["value"] for entry in container["environment"]}["MOCK_AI"] == "0"


def test_frontend_task_definition_clone_changes_only_the_digest_pinned_image():
    helper = _load_module(FRONTEND_HELPER, "pr06b_frontend_release_helper")
    source = _frontend_task_definition()
    source["taskDefinition"].pop("taskRoleArn")
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-frontend@sha256:" + "c" * 64

    prepared = helper.prepare_frontend_task_definition(source, image)

    assert prepared["containerDefinitions"][0]["image"] == image
    assert prepared["containerDefinitions"][0]["environment"] == source["taskDefinition"]["containerDefinitions"][0]["environment"]
    assert prepared["containerDefinitions"][0]["logConfiguration"] == source["taskDefinition"]["containerDefinitions"][0]["logConfiguration"]
    assert prepared["containerDefinitions"][0]["healthCheck"] == source["taskDefinition"]["containerDefinitions"][0]["healthCheck"]
    assert "taskRoleArn" not in prepared
    assert prepared["executionRoleArn"] == source["taskDefinition"]["executionRoleArn"]


def test_frontend_release_workflows_are_manual_or_artifact_only_and_never_provision_infrastructure():
    build = _workflow(FRONTEND_BUILD_WORKFLOW)
    release = _workflow(FRONTEND_RELEASE_WORKFLOW)
    build_script = "\n".join(step.get("run", "") for job in build["jobs"].values() for step in job.get("steps", [])).casefold()
    release_script = "\n".join(step.get("run", "") for job in release["jobs"].values() for step in job.get("steps", [])).casefold()

    assert {"workflow_run", "workflow_dispatch"} == set(build["on"])
    assert build["jobs"]["build-and-push"]["environment"] == "Main"
    assert "npm run build:trip-preview" in FRONTEND_DOCKERFILE.read_text(encoding="utf-8")
    assert "curl --fail" in build_script
    assert "docker push" in build_script
    assert "aws ecs update-service" not in build_script
    assert ":latest" not in build_script
    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["jobs"]["promote"]["environment"] == "Main"
    assert release["jobs"]["promote"]["steps"][0]["with"]["ref"] == "${{ inputs.source_sha }}"
    assert "aws ecs update-service" in release_script
    assert "aws ecs wait services-stable" in release_script
    assert "describe-target-health" in release_script
    for forbidden in ("create-repository", "aws ec2", "aws rds", "aws iam", "phase8"):
        assert forbidden not in release_script


def test_backend_bootstrap_helper_rejects_an_invalid_hmac_parameter_reference():
    bootstrap = _load_module(BOOTSTRAP_HELPER, "pr06b_backend_bootstrap_helper_invalid_hmac")
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "d" * 64

    with pytest.raises(bootstrap.ContractError):
        bootstrap.prepare_bootstrap_task_definition(
            _backend_task_definition(include_hmac=False), image, "not-an-ssm-parameter-arn"
        )


def test_backend_release_profile_rejects_an_unclassified_api_key_secret():
    helper = _load_module(BACKEND_HELPER, "pr06b_backend_release_helper_unclassified_key")
    payload = _backend_task_definition()
    payload["taskDefinition"]["containerDefinitions"][0]["secrets"].append(
        {"name": "FUTURE_LLM_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/future-llm"}
    )
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "e" * 64

    with pytest.raises(helper.ContractError, match="Unclassified API-key secret"):
        helper.prepare_task_definition(payload, image)
