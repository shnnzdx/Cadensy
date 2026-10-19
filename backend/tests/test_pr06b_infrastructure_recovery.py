"""Offline contracts for PR-06B Round 1.5 recovery tooling."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "cd01" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = _load("pr06b_recovery_validator", "validate_pr06b_infrastructure_recovery.py")
collector = _load("pr06b_inventory_collector", "collect_pr06b_production_inventory.py")


NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
KMS_ARN = "arn:aws:kms:us-east-1:448678332746:key/11111111-2222-3333-4444-555555555555"
SSM_DATABASE_ARN = "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/database-url-next"
SSM_HMAC_ARN = "arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/conversation-hmac"


def _manifest() -> dict:
    return {
        "identity": {"account": "448678332746", "region": "us-east-1"},
        "source_database": {
            "id": "tripsync-postgres",
            "storage_encrypted": False,
            "production_ready": False,
            "engine": "postgres",
            "engine_version": "18.3",
        },
        "replacement_database": {
            "id": "tripsync-postgres-encrypted",
            "storage_encrypted": True,
            "engine": "postgres",
            "engine_version": "18.3",
            "publicly_accessible": False,
            "db_subnet_group": "tripsync-private-db-subnet-group",
            "security_group_ids": ["sg-replacement"],
            "backup_retention_days": 7,
        },
        "approved_min_backup_retention_days": 7,
        "kms_key": {"arn": KMS_ARN, "state": "Enabled", "customer_managed": True},
        "recovery_evidence": {
            "encrypted_snapshot": {"id": "tripsync-encrypted-copy", "encrypted": True, "kms_key_arn": KMS_ARN},
            "restore_rehearsal": {
                "status": "passed",
                "evidence_ref": "DB-1-restore-rehearsal-2026-10-19",
                "completed_at": "2026-10-19T11:00:00Z",
            },
            "max_restore_rehearsal_age_hours": 24,
        },
        "ecs_bootstrap": {
            "mode": "planned_maintenance_cutover",
            "approved_i3": True,
            "public_traffic_blocked": True,
            "environment": {
                "APP_ENV": "production",
                "DISABLE_SCHEDULER": "1",
                "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED": "0",
                "CADENSY_E2E_FAKE_RUNTIME": "0",
            },
            "secret_references": {
                "DATABASE_URL": SSM_DATABASE_ARN,
                "CONVERSATION_SUBMISSION_HMAC_KEY": SSM_HMAC_ARN,
            },
        },
        "cutover": {
            "auto_execute": False,
            "source_database_id": "tripsync-postgres",
            "replacement_database_id": "tripsync-postgres-encrypted",
            "write_freeze_approval_ref": "I2-write-freeze",
            "compatibility_decision_ref": "DB-3-compatibility",
            "endpoint_cutover_approval_ref": "I2-approved",
            "old_database_retention_ref": "DB-6-retention",
            "database_url_secret_reference": SSM_DATABASE_ARN,
        },
    }


def test_recovery_manifest_accepts_only_explicitly_safe_future_package():
    checks = validator.validate_manifest(_manifest(), now=NOW)
    assert [(check.name, check.status) for check in checks] == [
        ("identity", "PASS"),
        ("source", "REMEDIATION_REQUIRED"),
        ("replacement", "PASS"),
        ("recovery", "PASS"),
        ("ecs_bootstrap", "PASS"),
        ("cutover", "PASS"),
    ]


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        ("wrong account", lambda value: value["identity"].update(account="000000000000"), "account"),
        ("wrong region", lambda value: value["identity"].update(region="us-west-2"), "region"),
        ("ambiguous db identity", lambda value: value["replacement_database"].update(id="tripsync-postgres"), "identities must differ"),
        ("source declared ready", lambda value: value["source_database"].update(production_ready=True), "source"),
        ("unencrypted replacement", lambda value: value["replacement_database"].update(storage_encrypted=False), "encryption"),
        ("weak backup retention", lambda value: value["replacement_database"].update(backup_retention_days=1), "retention"),
        ("engine mismatch", lambda value: value["replacement_database"].update(engine_version="17.7"), "engine"),
        ("missing kms", lambda value: value["kms_key"].update(state="Disabled"), "KMS"),
        ("unencrypted evidence", lambda value: value["recovery_evidence"]["encrypted_snapshot"].update(encrypted=False), "encrypted"),
        ("snapshot without restore", lambda value: value["recovery_evidence"]["restore_rehearsal"].update(status="not_run"), "snapshot existence"),
        ("stale restore", lambda value: value["recovery_evidence"]["restore_rehearsal"].update(completed_at="2026-10-15T11:00:00Z"), "stale"),
        ("missing hmac", lambda value: value["ecs_bootstrap"]["secret_references"].pop("CONVERSATION_SUBMISSION_HMAC_KEY"), "HMAC"),
        ("plaintext database", lambda value: value["ecs_bootstrap"]["environment"].update(DATABASE_URL="postgresql://plaintext"), "plaintext"),
        ("runtime enabled", lambda value: value["ecs_bootstrap"]["environment"].update(CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED="1"), "must be explicitly"),
        ("unapproved zero capacity", lambda value: value["ecs_bootstrap"].update(approved_i3=False), "Gate I3"),
        ("public bootstrap", lambda value: value["ecs_bootstrap"].update(public_traffic_blocked=False), "public traffic"),
        ("unsafe auto cutover", lambda value: value["cutover"].update(auto_execute=True), "automated"),
        ("missing compatibility", lambda value: value["cutover"].pop("compatibility_decision_ref"), "compatibility"),
    ],
)
def test_recovery_manifest_rejects_each_unsafe_or_ambiguous_state(name, mutate, message):
    candidate = copy.deepcopy(_manifest())
    mutate(candidate)
    with pytest.raises(validator.RecoveryContractError, match=message):
        validator.validate_manifest(candidate, now=NOW)


def test_inventory_client_has_a_static_read_only_allow_list_and_rejects_mutation():
    invoked: list[list[str]] = []

    def runner(command, **kwargs):
        invoked.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    client = collector.AwsCli(region="us-east-1", runner=runner)
    client.call("rds", "describe-db-instances")
    with pytest.raises(collector.InventoryError, match="not approved"):
        client.call("rds", "start-db-instance")
    assert invoked[0][:3] == ["aws", "rds", "describe-db-instances"]
    assert all((command[1], command[2]) in collector.READ_ONLY_OPERATIONS for command in invoked)
    forbidden = {"create", "copy", "restore", "start", "stop", "update", "delete", "put", "register", "run", "scale"}
    assert not any(command[2].split("-", 1)[0] in forbidden for command in invoked)


def test_inventory_task_definition_redacts_environment_values_and_secret_references():
    result = collector._sanitize_task_definition(
        {
            "taskDefinition": {
                "family": "tripsync-backend",
                "revision": 19,
                "taskRoleArn": "arn:aws:iam::448678332746:role/task",
                "executionRoleArn": "arn:aws:iam::448678332746:role/execution",
                "containerDefinitions": [
                    {
                        "name": "tripsync-backend",
                        "image": "repo@sha256:" + "a" * 64,
                        "environment": [{"name": "APP_ENV", "value": "production"}],
                        "secrets": [{"name": "DATABASE_URL", "valueFrom": "arn:should-not-appear"}],
                    }
                ],
            }
        }
    )
    serialized = json.dumps(result)
    assert result["containers"][0]["environment_names"] == ["APP_ENV"]
    assert result["containers"][0]["secret_names"] == ["DATABASE_URL"]
    assert "production" not in serialized
    assert "arn:should-not-appear" not in serialized


def test_inventory_preserves_authorization_errors_instead_of_reporting_a_pass():
    error = subprocess.CalledProcessError(255, ["aws", "kms", "list-aliases"], stderr="AccessDeniedException")
    assert collector._short_error(error) == {"type": "aws_cli_error", "detail": "AccessDeniedException"}
