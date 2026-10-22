"""Offline contracts for the CD-01 release helper and GitHub workflows."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "prepare_ecs_task_definition.py"
RELEASE_INPUT_HELPER_PATH = (
    REPOSITORY_ROOT / "scripts" / "cd01" / "validate_release_evidence.py"
)
BUILD_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-build-publish.yml"
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-production-release.yml"
MIGRATION_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-pr06b-schema-migration.yml"

spec = importlib.util.spec_from_file_location("cd01_prepare_task_definition", HELPER_PATH)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

release_input_spec = importlib.util.spec_from_file_location(
    "cd01_validate_release_evidence", RELEASE_INPUT_HELPER_PATH
)
assert release_input_spec and release_input_spec.loader
release_input_helper = importlib.util.module_from_spec(release_input_spec)
release_input_spec.loader.exec_module(release_input_helper)


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
                    "secrets": [
                        {"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/database-url"},
                        {"name": "CONVERSATION_SUBMISSION_HMAC_KEY", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/conversation-hmac"},
                    ],
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


def _application_container(payload: dict) -> dict:
    task_definition = payload.get("taskDefinition", payload)
    return next(
        container
        for container in task_definition["containerDefinitions"]
        if container["name"] == "tripsync-backend"
    )


def _prepared_release_task_definition() -> dict:
    return helper.prepare_task_definition(
        _task_definition(),
        "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "f" * 64,
    )


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
    assert environment["CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED"] == "0"
    assert environment["CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"] == "0"
    assert environment["CADENSY_E2E_FAKE_RUNTIME"] == "0"
    assert all(environment[gate] == "0" for gate in helper.DEFAULT_OFF_GATES)
    assert "taskDefinitionArn" not in prepared
    assert "revision" not in prepared


def test_release_runtime_off_contract_accepts_the_generated_dual_gate_profile():
    helper.validate_runtime_off_task_definition(
        _prepared_release_task_definition(), profile="release"
    )


def test_release_runtime_off_contract_rejects_a_missing_production_latch():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    container["environment"] = [
        entry
        for entry in container["environment"]
        if entry["name"]
        != "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"
    ]

    with pytest.raises(helper.ContractError, match="PRODUCTION_ACTIVATION_ENABLED"):
        helper.validate_runtime_off_task_definition(prepared, profile="release")


def test_release_runtime_off_contract_rejects_an_enabled_production_latch():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    next(
        entry
        for entry in container["environment"]
        if entry["name"]
        == "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"
    )["value"] = "1"

    with pytest.raises(helper.ContractError, match="PRODUCTION_ACTIVATION_ENABLED"):
        helper.validate_runtime_off_task_definition(prepared, profile="release")


def test_release_runtime_off_contract_rejects_duplicate_latch_configuration():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    container["environment"].append(
        {
            "name": "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED",
            "value": "0",
        }
    )

    with pytest.raises(helper.ContractError, match="Duplicate environment variable"):
        helper.validate_runtime_off_task_definition(prepared, profile="release")


@pytest.mark.parametrize(
    "name",
    [
        "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED",
        "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED",
        "CADENSY_E2E_FAKE_RUNTIME",
        "MOCK_AI",
    ],
)
def test_release_runtime_off_contract_rejects_environment_secret_name_collisions(name):
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    container["secrets"].append(
        {
            "name": name,
            "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/unsafe-override",
        }
    )

    with pytest.raises(helper.ContractError, match="both environment and secrets"):
        helper.validate_runtime_off_task_definition(prepared, profile="release")


@pytest.mark.parametrize("location", ["environment", "secrets"])
def test_release_runtime_off_contract_rejects_provider_credentials(location):
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    if location == "environment":
        container[location].append({"name": "DEEPSEEK_API_KEY", "value": "unsafe"})
    else:
        container[location].append(
            {
                "name": "DEEPSEEK_API_KEY",
                "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/deepseek",
            }
        )

    with pytest.raises(helper.ContractError, match="Provider credential"):
        helper.validate_runtime_off_task_definition(prepared, profile="release")


@pytest.mark.parametrize(
    "credential_name",
    [
        "DEEPSEEK_TOKEN",
        "DEEPSEEK_KEY",
        "OPENAI_KEY",
        "ANTHROPIC_KEY",
        "OLLAMA_KEY",
    ],
)
@pytest.mark.parametrize("location", ["environment", "secrets"])
def test_release_runtime_off_contract_rejects_provider_credential_aliases(
    location, credential_name
):
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    if location == "environment":
        container[location].append({"name": credential_name, "value": "unsafe"})
    else:
        container[location].append(
            {
                "name": credential_name,
                "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/deepseek-token",
            }
        )

    with pytest.raises(helper.ContractError, match="Provider credential"):
        helper.validate_runtime_off_task_definition(prepared, profile="release")


@pytest.mark.parametrize(
    "credential_name",
    [
        "DEEPSEEK_TOKEN",
        "DEEPSEEK_KEY",
        "OPENAI_KEY",
        "ANTHROPIC_KEY",
        "OLLAMA_KEY",
    ],
)
@pytest.mark.parametrize("location", ["environment", "secrets"])
def test_task_definition_generation_strips_provider_credential_aliases(
    location, credential_name
):
    payload = _task_definition()
    container = _application_container(payload)
    if location == "environment":
        container[location].append({"name": credential_name, "value": "unsafe"})
    else:
        container[location].append(
            {
                "name": credential_name,
                "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/deepseek-token",
            }
        )

    prepared = helper.prepare_task_definition(
        payload,
        "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:"
        + "e" * 64,
    )
    prepared_container = _application_container(prepared)

    assert all(
        entry["name"] != credential_name
        for entry in prepared_container["environment"]
    )
    assert all(
        entry["name"] != credential_name for entry in prepared_container["secrets"]
    )


def _valid_release_evidence_inputs(**overrides):
    values = {
        "release_mode": "code_only",
        "source_sha": "a" * 40,
        "runtime_digest": "sha256:" + "b" * 64,
        "migration_digest": "sha256:" + "c" * 64,
        "schema_migration_evidence_ref": (
            "https://github.com/shnnzdx/Cadensy/actions/runs/123456789"
        ),
        "migration_run_id": "",
        "gate_m_workflow_sha": "",
        "migration_task_arn": "",
    }
    values.update(overrides)
    return values


def test_release_evidence_contract_accepts_code_only_without_gate_m_rebinding():
    release_input_helper.validate_release_evidence_inputs(
        **_valid_release_evidence_inputs()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("migration_run_id", "123456789"),
        ("gate_m_workflow_sha", "d" * 40),
        (
            "migration_task_arn",
            "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/"
            + "e" * 32,
        ),
    ],
)
def test_release_evidence_contract_rejects_gate_m_rebinding_in_code_only(
    field, value
):
    with pytest.raises(release_input_helper.ContractError, match="must be empty"):
        release_input_helper.validate_release_evidence_inputs(
            **_valid_release_evidence_inputs(**{field: value})
        )


def test_release_evidence_contract_rejects_unstructured_historical_reference():
    with pytest.raises(release_input_helper.ContractError, match="evidence reference"):
        release_input_helper.validate_release_evidence_inputs(
            **_valid_release_evidence_inputs(
                schema_migration_evidence_ref="historical-schema-evidence"
            )
        )


def test_release_evidence_contract_accepts_complete_migration_bound_inputs():
    release_input_helper.validate_release_evidence_inputs(
        **_valid_release_evidence_inputs(
            release_mode="migration_bound",
            migration_run_id="123456789",
            gate_m_workflow_sha="d" * 40,
            migration_task_arn=(
                "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/"
                + "e" * 32
            ),
        )
    )


def test_release_evidence_contract_rejects_migration_bound_reference_for_another_run():
    with pytest.raises(release_input_helper.ContractError, match="migration_run_id"):
        release_input_helper.validate_release_evidence_inputs(
            **_valid_release_evidence_inputs(
                release_mode="migration_bound",
                migration_run_id="987654321",
                gate_m_workflow_sha="d" * 40,
                migration_task_arn=(
                    "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/"
                    + "e" * 32
                ),
            )
        )


@pytest.mark.parametrize("field", ["migration_run_id", "gate_m_workflow_sha", "migration_task_arn"])
def test_release_evidence_contract_rejects_incomplete_migration_bound_inputs(field):
    values = _valid_release_evidence_inputs(
        release_mode="migration_bound",
        migration_run_id="123456789",
        gate_m_workflow_sha="d" * 40,
        migration_task_arn=(
            "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/" + "e" * 32
        ),
    )
    values[field] = ""

    with pytest.raises(release_input_helper.ContractError):
        release_input_helper.validate_release_evidence_inputs(**values)


def test_legacy_rollback_contract_accepts_a_missing_production_latch_only():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    container["environment"] = [
        entry
        for entry in container["environment"]
        if entry["name"]
        != "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"
    ]

    helper.validate_runtime_off_task_definition(prepared, profile="rollback")


def test_legacy_rollback_latch_compatibility_still_requires_the_base_gate_off():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    container["environment"] = [
        entry
        for entry in container["environment"]
        if entry["name"]
        != "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"
    ]
    next(
        entry
        for entry in container["environment"]
        if entry["name"] == "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED"
    )["value"] = "1"

    with pytest.raises(helper.ContractError, match="RUNTIME_ENABLED=0"):
        helper.validate_runtime_off_task_definition(prepared, profile="rollback")


def test_legacy_rollback_latch_compatibility_still_rejects_provider_credentials():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    container["environment"] = [
        entry
        for entry in container["environment"]
        if entry["name"]
        != "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"
    ]
    container["secrets"].append(
        {
            "name": "OPENAI_API_KEY",
            "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/openai",
        }
    )

    with pytest.raises(helper.ContractError, match="Provider credential"):
        helper.validate_runtime_off_task_definition(prepared, profile="rollback")


def test_rollback_contract_rejects_an_explicitly_enabled_production_latch():
    prepared = _prepared_release_task_definition()
    container = _application_container(prepared)
    next(
        entry
        for entry in container["environment"]
        if entry["name"]
        == "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"
    )["value"] = "1"

    with pytest.raises(helper.ContractError, match="PRODUCTION_ACTIVATION_ENABLED"):
        helper.validate_runtime_off_task_definition(prepared, profile="rollback")


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
    inputs = release["on"]["workflow_dispatch"]["inputs"]
    assert inputs["release_mode"]["type"] == "choice"
    assert inputs["release_mode"]["options"] == ["code_only", "migration_bound"]
    assert inputs["schema_migration_evidence_ref"]["required"] == "true"
    assert inputs["migration_run_id"]["required"] == "false"
    assert inputs["gate_m_workflow_sha"]["required"] == "false"
    assert inputs["migration_task_arn"]["required"] == "false"
    assert release["jobs"]["promote"]["steps"][0]["with"]["ref"] == "${{ inputs.source_sha }}"
    assert "@${runtime_digest}" in release_script
    assert 'actual_sha="$(git rev-parse head)"' in release_script
    assert 'test "${actual_sha}" = "${source_sha}"' in release_script
    assert "deploymentcircuitbreaker={enable=true,rollback=true}" in release_script
    assert "dev_allow_membership_header" in release_script
    assert "CADENSY_B3_PROTECTED_PREVIEW_APPLY" in HELPER_PATH.read_text(encoding="utf-8")
    assert "aws ecs update-service" in release_script
    assert "required_reviewers" in release_script
    assert "aws ecs wait services-stable" in release_script
    assert "describe-target-health" in release_script
    assert "verify_pr06b_schema.py" in release_script
    assert "--validate-runtime-off-profile rollback" in release_script
    assert "--validate-runtime-off-profile release" in release_script
    assert "validate_release_evidence.py" in release_script
    assert "schema_migration_evidence_ref" in release_script
    assert 'if [[ "${release_mode}" == "migration_bound" ]]' in release_script
    assert 'elif [[ "${release_mode}" == "code_only" ]]' in release_script
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
    # Production accepts the PR-06B head only after the read-only catalog
    # verifier proves the deployed revision and objects. A migration-bound
    # release additionally verifies its exact Gate M run; code-only releases
    # record the historical migration reference without relabelling it.
    assert release["env"]["APPROVED_ALEMBIC_HEAD"] == "b7d3a1e9c642"
    assert "migration_run_id" in release["on"]["workflow_dispatch"]["inputs"]
    assert "schema_migration_evidence_ref" in release["on"]["workflow_dispatch"]["inputs"]
    assert "cd01-pr06b-schema-migration.yml" in _workflow_script(release)
    assert "/jobs?per_page=100" in _workflow_script(release)
    assert "execute one approved pr-06b schema migration" in _workflow_script(release)
    assert "verify_pr06b_schema.py" in _workflow_script(release)


def test_code_only_release_keeps_source_image_and_schema_evidence_independent():
    release = _workflow(RELEASE_WORKFLOW)
    release_script = _workflow_script(release)

    assert 'test "${actual_sha}" = "${source_sha}"' in release_script
    assert 'jq -e --arg tag "${source_sha}"' in release_script
    assert 'jq -e --arg tag "${source_sha}-migration"' in release_script
    assert "schema_migration_evidence_ref" in release_script
    assert 'schema_evidence_run_id="${schema_migration_evidence_ref##*/}"' in release_script
    assert 'actions/runs/${schema_evidence_run_id}' in release_script
    assert '.path == ".github/workflows/cd01-pr06b-schema-migration.yml"' in release_script
    assert 'actions/runs/${schema_evidence_run_id}/jobs?per_page=100' in release_script
    assert "historical-independent-of-source-sha" in release_script
    assert "verify_pr06b_schema.py --expected-head" in release_script
    assert "status=at-approved-head-no-schema-mutation" in release_script
    for forbidden in ("alembic upgrade", "alembic stamp", "alembic downgrade"):
        assert forbidden not in release_script


def test_pr06b_gate_m_is_manual_protected_and_cannot_promote_ecs():
    migration = _workflow(MIGRATION_WORKFLOW)
    migration_script = _workflow_script(migration)

    assert set(migration["on"]) == {"workflow_dispatch"}
    inputs = migration["on"]["workflow_dispatch"]["inputs"]
    assert inputs["confirmation"]["required"] == "true"
    assert inputs["backup_restore_evidence_ref"]["required"] == "true"
    assert inputs["maintenance_window_ref"]["required"] == "true"
    assert inputs["migration_owner"]["required"] == "true"
    assert "attestation only" in inputs["backup_restore_evidence_ref"]["description"]
    assert "7a6c8e1d5f04" in inputs["expected_start_revision"]["options"]
    assert migration["jobs"]["migrate"]["environment"] == "Main"
    assert migration["jobs"]["migrate"]["needs"] == "assert-migration-approval"
    assert migration["jobs"]["migrate"]["steps"][0]["with"]["ref"] == "${{ inputs.source_sha }}"
    assert inputs["confirmation"]["description"] == "Type EXECUTE_PR06B_SCHEMA_MIGRATION after reviewing the inputs"
    assert "run_pr06b_schema_migration.py" in migration_script
    assert "verify_pr06b_schema.py" in migration_script
    assert "pg_try_advisory_lock" in (REPOSITORY_ROOT / "backend" / "scripts" / "cd01" / "run_pr06b_schema_migration.py").read_text(encoding="utf-8")
    assert "alembic downgrade" not in migration_script
    assert "alembic stamp" not in migration_script
    assert "aws ecs update-service" not in migration_script
    assert ".[0].desiredcount >= 1" in migration_script
    assert ".[0].runningcount >= 1" in migration_script
    assert 'actual_sha="$(git rev-parse head)"' in migration_script
    assert 'test "${actual_sha}" = "${source_sha}"' in migration_script
    assert "neither inspects a backup nor performs or proves a restore rehearsal" in migration_script


def test_helper_rejects_missing_pr06b_hmac_secret_reference():
    payload = _task_definition()
    payload["taskDefinition"]["containerDefinitions"][0]["secrets"] = [
        {"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/database-url"}
    ]
    with pytest.raises(helper.ContractError, match="CONVERSATION_SUBMISSION_HMAC_KEY"):
        helper.prepare_task_definition(
            payload,
            "448678332746.dkr.ecr.us-east-1.amazonaws.com/tripsync-backend@sha256:" + "d" * 64,
        )


def test_round_one_release_contract_explicitly_disables_durable_and_fake_runtime():
    source = HELPER_PATH.read_text(encoding="utf-8")
    release_script = _workflow_script(_workflow(RELEASE_WORKFLOW))
    assert '"CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED"' in source
    assert '"CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED"' in source
    assert '"CADENSY_E2E_FAKE_RUNTIME"' in source
    assert "--validate-runtime-off-profile rollback" in release_script
    assert "--validate-runtime-off-profile release" in release_script
    assert "durable runtime base gate=0" in release_script
    assert "production activation latch=0" in release_script
    assert "e2e fake runtime=0" in release_script


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
