"""Offline contracts for the manually approved Pydantic production release."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = (
    REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_pydantic_release_task_definition.py"
)
WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "cd03-pydantic-production-release.yml"
)
IMAGE = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "a" * 64
PYDANTIC_IMAGE = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "b" * 64
TASK_DEFINITION_ARN = "arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:26"
DEEPSEEK_SECRET = (
    "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/deepseek-api-key"
)


def _load_helper():
    spec = importlib.util.spec_from_file_location("cd03_pydantic_release_helper", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _legacy_task_definition() -> dict:
    environment = [
        {"name": "APP_ENV", "value": "production"},
        {"name": "DISABLE_SCHEDULER", "value": "1"},
        {"name": "DEV_ALLOW_MEMBERSHIP_HEADER", "value": "0"},
        {"name": "MOCK_AI", "value": "0"},
        {"name": "CADENSY_E2E_FAKE_RUNTIME", "value": "0"},
        {"name": "CHAT_AGENT_RUNTIME", "value": "legacy"},
        {"name": "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "value": "1"},
        {"name": "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED", "value": "1"},
        {"name": "DEEPSEEK_BASE_URL", "value": "https://api.deepseek.com"},
        {"name": "DEEPSEEK_MODEL", "value": "deepseek-flash"},
        {"name": "DEEPSEEK_THINKING", "value": "disabled"},
        {"name": "CORS_ORIGINS", "value": "https://app.cadensy.top"},
        {"name": "CADENSY_B3_PROTECTED_PREVIEW_APPLY", "value": "0"},
    ]
    return {
        "taskDefinition": {
            "taskDefinitionArn": TASK_DEFINITION_ARN,
            "family": "tripsync-backend",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "taskRoleArn": "arn:aws:iam::448678332746:role/tripsync-backend-task-role",
            "executionRoleArn": "arn:aws:iam::448678332746:role/tripsync-ecs-execution-role",
            "containerDefinitions": [
                {
                    "name": "tripsync-backend",
                    "image": IMAGE,
                    "environment": environment,
                    "secrets": [
                        {"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/database-url"},
                        {"name": "CONVERSATION_SUBMISSION_HMAC_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/conversation-submission-hmac"},
                        {"name": "GEOAPIFY_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/geoapify-api-key"},
                        {"name": "UNSPLASH_ACCESS_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/unsplash-access-key"},
                        {"name": "DEEPSEEK_API_KEY", "valueFrom": DEEPSEEK_SECRET},
                    ],
                    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
                    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/tripsync-backend"}},
                }
            ],
        }
    }


def _container(payload: dict) -> dict:
    return payload.get("taskDefinition", payload)["containerDefinitions"][0]


def _environment(payload: dict) -> dict[str, str]:
    return {entry["name"]: entry["value"] for entry in _container(payload)["environment"]}


def test_prepare_pydantic_profile_changes_only_image_and_runtime_selector():
    helper = _load_helper()
    source = _legacy_task_definition()
    before = copy.deepcopy(source)

    prepared = helper.prepare_pydantic_task_definition(
        source, PYDANTIC_IMAGE, expected_baseline_arn=TASK_DEFINITION_ARN
    )

    assert source == before
    assert _container(prepared)["image"] == PYDANTIC_IMAGE
    assert _environment(prepared)["CHAT_AGENT_RUNTIME"] == "pydantic"
    assert {
        name: value for name, value in _environment(prepared).items() if name != "CHAT_AGENT_RUNTIME"
    } == {
        name: value for name, value in _environment(source).items() if name != "CHAT_AGENT_RUNTIME"
    }
    assert _container(prepared)["secrets"] == _container(source)["secrets"]
    assert _container(prepared)["portMappings"] == _container(source)["portMappings"]
    assert _container(prepared)["logConfiguration"] == _container(source)["logConfiguration"]
    assert prepared["taskRoleArn"] == source["taskDefinition"]["taskRoleArn"]
    assert prepared["executionRoleArn"] == source["taskDefinition"]["executionRoleArn"]
    assert prepared["networkMode"] == source["taskDefinition"]["networkMode"]
    assert prepared["requiresCompatibilities"] == source["taskDefinition"]["requiresCompatibilities"]
    assert prepared["cpu"] == source["taskDefinition"]["cpu"]
    assert prepared["memory"] == source["taskDefinition"]["memory"]
    helper.validate_pydantic_task_definition(prepared)
    helper.validate_pydantic_transition(source, prepared, PYDANTIC_IMAGE)


def test_rejects_baseline_mismatch_and_unapproved_image():
    helper = _load_helper()
    with pytest.raises(helper.ContractError, match="expected current baseline"):
        helper.prepare_pydantic_task_definition(
            _legacy_task_definition(), PYDANTIC_IMAGE, expected_baseline_arn=TASK_DEFINITION_ARN + "9"
        )
    with pytest.raises(helper.ContractError, match="approved backend ECR repository"):
        helper.prepare_pydantic_task_definition(
            _legacy_task_definition(), "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend:latest"
        )


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda payload: _environment_entry(payload, "CHAT_AGENT_RUNTIME", "legacy"), "CHAT_AGENT_RUNTIME=pydantic"),
        (lambda payload: _environment_entry(payload, "MOCK_AI", "1"), "MOCK_AI=0"),
        (lambda payload: _environment_entry(payload, "DEEPSEEK_BASE_URL", "https://example.invalid"), "DEEPSEEK_BASE_URL"),
        (lambda payload: _environment_entry(payload, "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "0"), "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=1"),
    ],
)
def test_rejects_invalid_pydantic_runtime_profile(mutator, message):
    helper = _load_helper()
    prepared = helper.prepare_pydantic_task_definition(_legacy_task_definition(), PYDANTIC_IMAGE)
    mutator(prepared)
    with pytest.raises(helper.ContractError, match=message):
        helper.validate_pydantic_task_definition(prepared)


def _environment_entry(payload: dict, name: str, value: str) -> None:
    for entry in _container(payload)["environment"]:
        if entry["name"] == name:
            entry["value"] = value
            return
    raise AssertionError(f"missing expected environment entry {name}")


def test_rejects_provider_secret_drift_plaintext_secret_and_duplicate_environment():
    helper = _load_helper()
    prepared = helper.prepare_pydantic_task_definition(_legacy_task_definition(), PYDANTIC_IMAGE)
    _container(prepared)["secrets"].append(
        {"name": "OPENAI_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/openai"}
    )
    with pytest.raises(helper.ContractError, match="Non-DeepSeek provider credential"):
        helper.validate_pydantic_task_definition(prepared)

    plaintext = helper.prepare_pydantic_task_definition(_legacy_task_definition(), PYDANTIC_IMAGE)
    _container(plaintext)["environment"].append({"name": "DEEPSEEK_API_KEY", "value": "forbidden"})
    with pytest.raises(helper.ContractError, match="both environment and secrets"):
        helper.validate_pydantic_task_definition(plaintext)

    duplicate = helper.prepare_pydantic_task_definition(_legacy_task_definition(), PYDANTIC_IMAGE)
    duplicate["containerDefinitions"][0]["environment"].append({"name": "CORS_ORIGINS", "value": "duplicate"})
    with pytest.raises(helper.ContractError, match="Duplicate environment variable"):
        helper.validate_pydantic_task_definition(duplicate)

    invalid_secret = helper.prepare_pydantic_task_definition(_legacy_task_definition(), PYDANTIC_IMAGE)
    next(entry for entry in _container(invalid_secret)["secrets"] if entry["name"] == "DEEPSEEK_API_KEY")["valueFrom"] = "not-an-approved-secret-reference"
    with pytest.raises(helper.ContractError, match="approved-account"):
        helper.validate_pydantic_task_definition(invalid_secret)

    unexpected_provider = helper.prepare_pydantic_task_definition(_legacy_task_definition(), PYDANTIC_IMAGE)
    unexpected_provider["containerDefinitions"][0]["environment"].append(
        {"name": "OPENAI_BASE_URL", "value": "https://example.invalid"}
    )
    with pytest.raises(helper.ContractError, match="Non-DeepSeek provider configuration"):
        helper.validate_pydantic_task_definition(unexpected_provider)


def test_transition_rejects_unrelated_environment_secret_and_task_configuration_drift():
    helper = _load_helper()
    source = _legacy_task_definition()
    prepared = helper.prepare_pydantic_task_definition(source, PYDANTIC_IMAGE)
    _environment_entry(prepared, "CORS_ORIGINS", "https://other.invalid")
    with pytest.raises(helper.ContractError, match="unapproved environment change"):
        helper.validate_pydantic_transition(source, prepared, PYDANTIC_IMAGE)

    prepared = helper.prepare_pydantic_task_definition(source, PYDANTIC_IMAGE)
    prepared["cpu"] = "512"
    with pytest.raises(helper.ContractError, match="outside the approved image/runtime changes"):
        helper.validate_pydantic_transition(source, prepared, PYDANTIC_IMAGE)


def test_legacy_rollback_profile_is_validated_without_aws_access():
    helper = _load_helper()
    source = _legacy_task_definition()
    helper.validate_legacy_rollback_task_definition(source, expected_baseline_arn=TASK_DEFINITION_ARN)

    _environment_entry(source, "CHAT_AGENT_RUNTIME", "pydantic")
    with pytest.raises(helper.ContractError, match="CHAT_AGENT_RUNTIME=legacy"):
        helper.validate_legacy_rollback_task_definition(source, expected_baseline_arn=TASK_DEFINITION_ARN)

    source_text = HELPER_PATH.read_text(encoding="utf-8").casefold()
    for forbidden in ("boto3", "subprocess", "aws ecs", "get-secret-value", "get-parameter"):
        assert forbidden not in source_text


def test_workflow_is_manual_protected_and_has_required_safety_contracts():
    workflow = _workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert {"operation", "tooling_sha", "source_sha", "runtime_image_digest", "expected_current_task_definition_arn", "rollback_task_definition_arn", "confirmation"} <= set(inputs)
    assert set(inputs["operation"]["options"]) == {"validate", "activate", "rollback"}
    assert workflow["jobs"]["assert-approval-gate"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["execute"]["environment"] == "Main"

    script = "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])
    ).casefold()
    for required in (
        "git merge-base --is-ancestor",
        "imagedigest=${runtime_digest}",
        "describe-services",
        "deploymentcircuitbreaker.enable == true",
        "minimumhealthypercent == 100",
        "maximumpercent == 200",
        "--validate-legacy-profile",
        "--validate-pydantic-profile",
        "--validate-transition",
        "register-task-definition",
        "update-service",
        "describe-target-health",
        "baseline-target-health.json",
        "/api/health",
        "real pydantic provider acceptance: not executed",
        "rollback_on_error",
        "automatic legacy rollback could not be fully verified",
    ):
        assert required in script
    for forbidden in ("get-secret-value", "get-parameter", "--with-decryption", "alembic upgrade", "aws rds", "aws ecs update-service --cluster ${ecs_cluster} --services"):
        assert forbidden not in script


def test_activation_post_deployment_verification_is_covered_by_legacy_rollback_trap():
    """Every Pydantic acceptance check must run inside the activation ERR trap."""
    workflow = _workflow()
    steps = workflow["jobs"]["execute"]["steps"]
    activate = next(step for step in steps if step.get("id") == "activate")
    activate_script = activate["run"].casefold()

    assert activate["env"]["RUNTIME_DIGEST"] == "${{ inputs.runtime_image_digest }}"
    assert "trap rollback_on_error err" in activate_script
    for required_post_deploy_check in (
        "describe-services",
        "taskdefinition' /tmp/final-service.json",
        "runningcount >= 1",
        "pendingcount == 0",
        "describe-target-health",
        "/api/health",
        "--validate-pydantic-profile",
        "runtime_digest",
    ):
        assert required_post_deploy_check in activate_script

    rollback_verification = next(
        step
        for step in steps
        if step.get("name") == "Verify service, target health, and runtime selector"
    )
    assert rollback_verification["if"] == "inputs.operation == 'rollback'"
