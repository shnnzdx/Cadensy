"""Fail-closed, offline validation for the PR-06B infrastructure recovery plan.

This tool deliberately consumes a redacted manifest instead of AWS credentials.
It is not a provisioning tool and never opens a database connection, resolves a
secret, or invokes an AWS API.  A future operator must separately obtain Gate
I1/I2/I3 authorization before carrying out any live operation described here.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


APPROVED_ACCOUNT = "448678332746"
APPROVED_REGION = "us-east-1"
RUNTIME_OFF_GATES = (
    "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED",
    "CADENSY_E2E_FAKE_RUNTIME",
)
SECRET_REFERENCE_PREFIXES = ("arn:aws:ssm:", "arn:aws:secretsmanager:")


class RecoveryContractError(ValueError):
    """The proposed recovery state is incomplete or unsafe to authorize."""


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _required(mapping: dict[str, Any], name: str) -> Any:
    value = mapping.get(name)
    if value in (None, "", [], {}):
        raise RecoveryContractError(f"missing required value: {name}")
    return value


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecoveryContractError(f"{name} must be an object")
    return value


def _parse_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise RecoveryContractError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryContractError(f"{name} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RecoveryContractError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _secret_reference(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.startswith(SECRET_REFERENCE_PREFIXES):
        raise RecoveryContractError(f"{name} must be an ECS SSM or Secrets Manager ARN reference")
    return value


def _runtime_off(environment: dict[str, Any]) -> None:
    for gate in RUNTIME_OFF_GATES:
        if environment.get(gate) != "0":
            raise RecoveryContractError(f"{gate} must be explicitly set to '0'")


def validate_manifest(manifest: dict[str, Any], *, now: datetime | None = None) -> list[Check]:
    """Validate a future recovery change package without mutating cloud state.

    A returned list is intentionally not a production authorization.  Every
    required live check has a named approval/evidence field, and missing facts
    raise rather than being inferred from snapshot existence or an old endpoint.
    """

    now = now or datetime.now(UTC)
    identity = _as_mapping(_required(manifest, "identity"), "identity")
    if identity.get("account") != APPROVED_ACCOUNT:
        raise RecoveryContractError("unexpected AWS account")
    if identity.get("region") != APPROVED_REGION:
        raise RecoveryContractError("unexpected AWS region")

    source = _as_mapping(_required(manifest, "source_database"), "source_database")
    replacement = _as_mapping(_required(manifest, "replacement_database"), "replacement_database")
    source_id = _required(source, "id")
    replacement_id = _required(replacement, "id")
    if source_id == replacement_id:
        raise RecoveryContractError("source and replacement database identities must differ")
    if source.get("production_ready") is True or source.get("storage_encrypted") is True:
        raise RecoveryContractError("the legacy source must be classified as an unencrypted remediation source")
    if replacement.get("storage_encrypted") is not True:
        raise RecoveryContractError("replacement database must have storage encryption enabled")
    if replacement.get("engine") != source.get("engine") or replacement.get("engine_version") != source.get("engine_version"):
        raise RecoveryContractError("replacement engine and version must match source until a separately approved upgrade")
    if replacement.get("publicly_accessible") is not False:
        raise RecoveryContractError("replacement database must remain private")
    if not _required(replacement, "db_subnet_group") or not _required(replacement, "security_group_ids"):
        raise RecoveryContractError("replacement network placement must be explicitly recorded")

    approved_min_retention = _required(manifest, "approved_min_backup_retention_days")
    if not isinstance(approved_min_retention, int) or approved_min_retention < 1:
        raise RecoveryContractError("approved_min_backup_retention_days must be a positive integer")
    if replacement.get("backup_retention_days", 0) < approved_min_retention:
        raise RecoveryContractError("replacement backup retention is below the approved minimum")

    key = _as_mapping(_required(manifest, "kms_key"), "kms_key")
    if key.get("customer_managed") is not True or key.get("state") != "Enabled" or not _required(key, "arn"):
        raise RecoveryContractError("replacement requires an enabled customer-managed KMS key reference")

    recovery = _as_mapping(_required(manifest, "recovery_evidence"), "recovery_evidence")
    snapshot = _as_mapping(_required(recovery, "encrypted_snapshot"), "recovery_evidence.encrypted_snapshot")
    if snapshot.get("encrypted") is not True or not _required(snapshot, "id"):
        raise RecoveryContractError("an encrypted copied snapshot is required; source snapshot existence is insufficient")
    if snapshot.get("kms_key_arn") != key["arn"]:
        raise RecoveryContractError("encrypted snapshot must identify the approved replacement KMS key")
    rehearsal = _as_mapping(_required(recovery, "restore_rehearsal"), "recovery_evidence.restore_rehearsal")
    if rehearsal.get("status") != "passed" or not _required(rehearsal, "evidence_ref"):
        raise RecoveryContractError("snapshot existence alone does not satisfy restore validation")
    rehearsed_at = _parse_timestamp(_required(rehearsal, "completed_at"), "restore_rehearsal.completed_at")
    max_age_hours = _required(recovery, "max_restore_rehearsal_age_hours")
    if not isinstance(max_age_hours, int) or max_age_hours < 1:
        raise RecoveryContractError("max_restore_rehearsal_age_hours must be a positive integer")
    if rehearsed_at < now - timedelta(hours=max_age_hours):
        raise RecoveryContractError("restore rehearsal evidence is stale")

    task = _as_mapping(_required(manifest, "ecs_bootstrap"), "ecs_bootstrap")
    if task.get("mode") != "planned_maintenance_cutover":
        raise RecoveryContractError("only the approved maintenance bootstrap mode is allowed")
    if task.get("approved_i3") is not True:
        raise RecoveryContractError("zero-capacity ECS recovery requires a distinct approved Gate I3")
    if task.get("public_traffic_blocked") is not True:
        raise RecoveryContractError("bootstrap must block ordinary public traffic before capacity is restored")
    environment = _as_mapping(_required(task, "environment"), "ecs_bootstrap.environment")
    _runtime_off(environment)
    if environment.get("APP_ENV") != "production" or environment.get("DISABLE_SCHEDULER") != "1":
        raise RecoveryContractError("bootstrap must retain production app mode and disabled scheduler")
    secret_references = _as_mapping(_required(task, "secret_references"), "ecs_bootstrap.secret_references")
    _secret_reference(secret_references.get("DATABASE_URL"), "DATABASE_URL")
    _secret_reference(secret_references.get("CONVERSATION_SUBMISSION_HMAC_KEY"), "CONVERSATION_SUBMISSION_HMAC_KEY")
    if "DATABASE_URL" in environment or "CONVERSATION_SUBMISSION_HMAC_KEY" in environment:
        raise RecoveryContractError("plaintext database or HMAC injection is forbidden")

    cutover = _as_mapping(_required(manifest, "cutover"), "cutover")
    if cutover.get("auto_execute") is True:
        raise RecoveryContractError("automated database endpoint cutover is forbidden")
    if cutover.get("source_database_id") != source_id or cutover.get("replacement_database_id") != replacement_id:
        raise RecoveryContractError("cutover must bind the reviewed source and replacement identities")
    for name in ("write_freeze_approval_ref", "compatibility_decision_ref", "endpoint_cutover_approval_ref", "old_database_retention_ref"):
        _required(cutover, name)
    _secret_reference(cutover.get("database_url_secret_reference"), "cutover DATABASE_URL reference")

    return [
        Check("identity", "PASS", "approved account and region"),
        Check("source", "REMEDIATION_REQUIRED", "legacy source is explicitly not production-ready"),
        Check("replacement", "PASS", "private encrypted replacement matches source engine/version"),
        Check("recovery", "PASS", "fresh encrypted snapshot and restore rehearsal evidence are present"),
        Check("ecs_bootstrap", "PASS", "I3-approved maintenance bootstrap blocks public traffic and runtime"),
        Check("cutover", "PASS", "manual I2 approval, write freeze, retention, and identity binding are present"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--now", help="UTC ISO-8601 time for deterministic rehearsal tests")
    args = parser.parse_args()
    try:
        now = _parse_timestamp(args.now, "--now") if args.now else None
        checks = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")), now=now)
    except (OSError, json.JSONDecodeError, RecoveryContractError) as exc:
        raise SystemExit(f"PR-06B infrastructure recovery contract failed: {exc}") from exc
    print(json.dumps([check.__dict__ for check in checks], indent=2))


if __name__ == "__main__":
    main()
