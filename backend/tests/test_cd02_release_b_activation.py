"""Offline CD-02 contracts for controlled DeepSeek production activation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_deepseek_activation_task_definition.py"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-release-b-ai-activation.yml"


def _load_helper():
    spec = importlib.util.spec_from_file_location("cd02_activation_helper", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _release_a_task_definition() -> dict:
    runtime_off_gates = [
        "CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES",
        "CADENSY_B2C_PROTECTED_BOOKING_WRITES",
        "CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES",
        "CADENSY_B2C_PROTECTED_VOTE_WRITES",
        "CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES",
        "CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES",
        "CADENSY_B2C_PROTECTED_ROUND_SETTLEMENT_WRITES",
        "CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES",
        "CADENSY_B2C_PROTECTED_CONSTRAINT_UPDATE_WRITES",
        "CADENSY_B2C_PROTECTED_CONSTRAINT_DELETE_WRITES",
        "CADENSY_B2C_PROTECTED_PREFERENCES_WRITES",
        "CADENSY_B2D_PROTECTED_GENERATOR_WRITES",
        "CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES",
        "CADENSY_B2E_PROTECTED_SCHEDULER_WRITES",
        "CADENSY_B2E_PROTECTED_ORGANIZER_WRITES",
        "CADENSY_B3_PROTECTED_PREVIEW_APPLY",
        "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED",
        "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED",
        "CADENSY_E2E_FAKE_RUNTIME",
    ]
    environment = [
        {"name": "APP_ENV", "value": "production"},
        {"name": "DISABLE_SCHEDULER", "value": "1"},
        {"name": "DEV_ALLOW_MEMBERSHIP_HEADER", "value": "0"},
        {"name": "MOCK_AI", "value": "0"},
        *({"name": name, "value": "0"} for name in runtime_off_gates),
    ]
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
                    "image": "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "a" * 64,
                    "environment": environment,
                    "secrets": [
                        {"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/database-url"},
                        {"name": "CONVERSATION_SUBMISSION_HMAC_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/conversation-submission-hmac"},
                        {"name": "GEOAPIFY_API_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/geoapify"},
                    ],
                    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/tripsync-backend"}},
                }
            ],
        }
    }


def _container(payload: dict) -> dict:
    return payload["containerDefinitions"][0]


def test_activation_profile_is_exact_and_preserves_only_safe_existing_configuration():
    helper = _load_helper()
    source = _release_a_task_definition()
    image = "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "b" * 64
    secret_arn = "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/deepseek-api-key"

    prepared = helper.prepare_activation_task_definition(source, image, secret_arn)
    container = _container(prepared)
    environment = {entry["name"]: entry["value"] for entry in container["environment"]}
    secrets = {entry["name"]: entry["valueFrom"] for entry in container["secrets"]}

    assert container["image"] == image
    assert environment == {
        **{
            entry["name"]: entry["value"]
            for entry in _release_a_task_definition()["taskDefinition"]["containerDefinitions"][0]["environment"]
        },
        **helper.ACTIVATION_ENVIRONMENT,
    }
    assert secrets["DEEPSEEK_API_KEY"] == secret_arn
    assert set(secrets) == {"DATABASE_URL", "CONVERSATION_SUBMISSION_HMAC_KEY", "GEOAPIFY_API_KEY", "DEEPSEEK_API_KEY"}
    helper.validate_activation_task_definition(prepared)


@pytest.mark.parametrize(
    "secret_arn",
    [
        "arn:aws:ssm:us-west-2:448678332746:parameter/tripsync/backend/prod/deepseek-api-key",
        "arn:aws:ssm:us-east-1:000000000000:parameter/tripsync/backend/prod/deepseek-api-key",
        "arn:aws:secretsmanager:us-east-1:448678332746:secret:deepseek key",
        "not-an-arn",
    ],
)
def test_activation_rejects_unapproved_secret_references(secret_arn: str):
    helper = _load_helper()
    with pytest.raises(helper.ContractError):
        helper.prepare_activation_task_definition(
            _release_a_task_definition(),
            "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "c" * 64,
            secret_arn,
        )


def test_activation_rejects_other_provider_credentials_and_plaintext_collisions():
    helper = _load_helper()
    provider_payload = _release_a_task_definition()
    provider_payload["taskDefinition"]["containerDefinitions"][0]["secrets"].append(
        {"name": "OPENAI_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/openai"}
    )
    with pytest.raises(helper.ContractError, match="Provider credential"):
        helper.prepare_activation_task_definition(
            provider_payload,
            "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "d" * 64,
            "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/deepseek-api-key",
        )

    collision_payload = _release_a_task_definition()
    collision_payload["taskDefinition"]["containerDefinitions"][0]["environment"].append(
        {"name": "DATABASE_URL", "value": "plaintext-is-forbidden"}
    )
    with pytest.raises(helper.ContractError):
        helper.validate_release_a_baseline(collision_payload)


def test_activation_rejects_a_non_release_a_or_invalid_runtime_profile():
    helper = _load_helper()
    payload = _release_a_task_definition()
    payload["taskDefinition"]["containerDefinitions"][0]["environment"] = [
        entry
        for entry in payload["taskDefinition"]["containerDefinitions"][0]["environment"]
        if entry["name"] != "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED"
    ]
    with pytest.raises(helper.ContractError, match="Runtime-off contract"):
        helper.prepare_activation_task_definition(
            payload,
            "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "e" * 64,
            "arn:aws:secretsmanager:us-east-1:448678332746:secret:tripsync/deepseek-prod-a1b2c3",
        )


def test_workflow_is_main_only_manual_and_has_no_secret_value_or_schema_operations():
    workflow = _workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["jobs"]["assert-approval-gate"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["execute"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["execute"]["environment"] == "Main"

    script = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    ).casefold()
    assert "get-secret-value" not in script
    assert "get-parameter" not in script
    assert "--with-decryption" not in script
    assert "backend-ai-runtime-config" not in script
    assert "backend-ai-secret-provision" not in script
    assert "alembic" not in script
    assert "aws rds" not in script
    assert "aws iam put" not in script


def test_workflow_requires_immutable_inputs_secret_metadata_iam_simulation_and_runtime_off_rollback():
    workflow = _workflow()
    script = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    for required in (
        "git merge-base --is-ancestor",
        "imageDigest=${RUNTIME_DIGEST}",
        "describe-parameters",
        "describe-secret",
        "simulate-principal-policy",
        "kms:Decrypt",
        "deploymentCircuitBreaker.enable == true",
        "--validate-release-a-baseline",
        "--validate-activation-profile",
        "runtime_off_rollback",
        "rollback_on_error",
        "Automatic Runtime-off rollback could not be fully verified",
        "--force-new-deployment",
        "describe-target-health",
    ):
        assert required in script
