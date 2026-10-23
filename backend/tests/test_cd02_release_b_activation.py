"""Offline CD-02 contracts for controlled DeepSeek production activation."""

from __future__ import annotations

import importlib.util
import copy
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_deepseek_activation_task_definition.py"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-release-b-ai-activation.yml"
OBSOLETE_OLLAMA_WORKFLOWS = (
    REPOSITORY_ROOT / ".github" / "workflows" / "backend-ai-runtime-config.yml",
    REPOSITORY_ROOT / ".github" / "workflows" / "backend-ai-secret-provision.yml",
)


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
    image = _container(source["taskDefinition"])["image"]
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


def test_activation_requires_the_exact_release_a_image_and_never_performs_an_image_upgrade():
    helper = _load_helper()
    source = _release_a_task_definition()
    release_a_image = _container(source["taskDefinition"])["image"]

    prepared = helper.prepare_activation_task_definition(
        source,
        release_a_image,
        "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/deepseek-api-key",
    )
    assert _container(prepared)["image"] == release_a_image

    with pytest.raises(helper.ContractError, match="retain the exact current Release A backend image"):
        helper.prepare_activation_task_definition(
            _release_a_task_definition(),
            "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "b" * 64,
            "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/deepseek-api-key",
        )


def test_release_a_baseline_requires_an_approved_digest_pinned_backend_image():
    helper = _load_helper()
    payload = _release_a_task_definition()
    _container(payload["taskDefinition"])["image"] = (
        "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend:latest"
    )

    with pytest.raises(helper.ContractError, match="approved backend ECR repository pinned by sha256"):
        helper.validate_release_a_baseline(payload)


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


@pytest.mark.parametrize("provider_credential", ["OPENAI_KEY", "ANTHROPIC_KEY", "MISTRAL_API_KEY"])
def test_activation_rejects_other_provider_credentials_and_plaintext_collisions(provider_credential):
    helper = _load_helper()
    provider_payload = _release_a_task_definition()
    provider_payload["taskDefinition"]["containerDefinitions"][0]["secrets"].append(
        {
            "name": provider_credential,
            "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/other-provider",
        }
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


def test_activation_strips_legacy_ollama_namespace_and_provider_selectors_without_mutating_release_a():
    helper = _load_helper()
    source = _release_a_task_definition()
    container = _container(source["taskDefinition"])
    container["environment"].extend(
        [
            {"name": "OLLAMA_CLOUD_BASE_URL", "value": "https://ollama.example/v1"},
            {"name": "OLLAMA_CLOUD_MODEL", "value": "legacy-cloud-model"},
            {"name": "OLLAMA_LOCAL_FALLBACK_MODE", "value": "enabled"},
            {"name": "CHAT_AI_PROVIDER", "value": "ollama_cloud"},
            {"name": "PLANNER_AI_PROVIDER", "value": "ollama_local"},
            {"name": "EXPLAINER_AI_PROVIDER", "value": "deepseek"},
        ]
    )
    container["secrets"].append(
        {
            "name": "OLLAMA_CLOUD_FALLBACK_REFERENCE",
            "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/ollama-fallback-reference",
        }
    )
    before = copy.deepcopy(source)

    prepared = helper.prepare_activation_task_definition(
        source,
        _container(source["taskDefinition"])["image"],
        "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/deepseek-api-key",
    )
    prepared_container = _container(prepared)
    environment = {entry["name"]: entry["value"] for entry in prepared_container["environment"]}
    secret_names = {entry["name"] for entry in prepared_container["secrets"]}

    assert source == before
    assert all(not name.upper().startswith("OLLAMA_") for name in environment)
    assert all(not name.upper().endswith("_AI_PROVIDER") for name in environment)
    assert all("ollama" not in str(value).casefold() for value in environment.values())
    assert all(not name.upper().startswith("OLLAMA_") for name in secret_names)
    assert secret_names == {
        "DATABASE_URL",
        "CONVERSATION_SUBMISSION_HMAC_KEY",
        "GEOAPIFY_API_KEY",
        "DEEPSEEK_API_KEY",
    }
    helper.validate_activation_task_definition(prepared)

    prepared_container["secrets"].append(
        {
            "name": "OLLAMA_CLOUD_API_KEY",
            "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/ollama-key",
        }
    )
    with pytest.raises(helper.ContractError, match="Retired Ollama secret reference"):
        helper.validate_activation_task_definition(prepared)


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
    assert all(not workflow_path.exists() for workflow_path in OBSOLETE_OLLAMA_WORKFLOWS)


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
        "TOOLING_SHA",
        "RELEASE_A_IMAGE_SOURCE_SHA",
        "release_a_image",
        "test \"${release_a_image##*@}\" = \"${RUNTIME_DIGEST}\"",
    ):
        assert required in script


def test_workflow_separates_reviewed_tooling_identity_from_release_a_image_provenance():
    workflow = _workflow()
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert {"tooling_sha", "runtime_image_digest", "release_a_image_source_sha"} <= set(inputs)
    assert workflow["jobs"]["execute"]["steps"][0]["with"]["ref"] == "${{ inputs.tooling_sha }}"

    script = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    assert '--arg tag "${RELEASE_A_IMAGE_SOURCE_SHA}"' in script
    assert '--arg tag "${TOOLING_SHA}"' not in script
    assert 'test "${TOOLING_SHA}" = "${RELEASE_A_IMAGE_SOURCE_SHA}"' not in script


def test_workflow_ecr_provenance_check_keeps_the_ecr_response_root_for_both_assertions():
    """The digest lookup must validate its tag without piping away imageDetails."""
    workflow = _workflow()
    script = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )

    assert '(.imageDetails | length == 1) and (.imageDetails[0].imageTags | index($tag))' in script
    assert '.imageDetails | length == 1 and (.imageDetails[0].imageTags | index($tag))' not in script
