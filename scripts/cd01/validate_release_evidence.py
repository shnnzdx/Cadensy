"""Validate CD-01 release-mode and evidence inputs without contacting AWS.

This keeps the code-only versus migration-bound contract executable and
reviewable.  It validates identifiers only; it does not claim that referenced
GitHub or AWS evidence exists.  The protected workflow performs those live
checks after this fail-closed shape validation succeeds.
"""

from __future__ import annotations

import argparse
import re


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GITHUB_MIGRATION_RUN_RE = re.compile(
    r"^https://github\.com/shnnzdx/Cadensy/actions/runs/(?P<run_id>[1-9][0-9]*)$"
)
MIGRATION_TASK_ARN_RE = re.compile(
    r"^arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/[0-9a-f]{32}$"
)


class ContractError(ValueError):
    """Release evidence inputs do not satisfy the CD-01 contract."""


def _require(pattern: re.Pattern[str], value: str, label: str) -> re.Match[str]:
    match = pattern.fullmatch(value)
    if match is None:
        raise ContractError(f"{label} has an invalid format.")
    return match


def validate_release_evidence_inputs(
    *,
    release_mode: str,
    source_sha: str,
    runtime_digest: str,
    migration_digest: str,
    schema_migration_evidence_ref: str,
    migration_run_id: str,
    gate_m_workflow_sha: str,
    migration_task_arn: str,
) -> None:
    """Validate immutable identities and mode-specific Gate M semantics."""

    _require(SHA_RE, source_sha, "Source SHA")
    _require(DIGEST_RE, runtime_digest, "Runtime image digest")
    _require(DIGEST_RE, migration_digest, "Schema-verifier image digest")
    evidence = _require(
        GITHUB_MIGRATION_RUN_RE,
        schema_migration_evidence_ref,
        "Historical schema migration evidence reference",
    )

    gate_m_values = {
        "migration_run_id": migration_run_id,
        "gate_m_workflow_sha": gate_m_workflow_sha,
        "migration_task_arn": migration_task_arn,
    }
    if release_mode == "code_only":
        populated = [name for name, value in gate_m_values.items() if value]
        if populated:
            raise ContractError(
                "Code-only Gate M fields must be empty: " + ", ".join(populated) + "."
            )
        return

    if release_mode != "migration_bound":
        raise ContractError("Release mode must be 'code_only' or 'migration_bound'.")

    if not migration_run_id.isdigit() or migration_run_id.startswith("0"):
        raise ContractError("Migration run ID must be a positive integer.")
    _require(SHA_RE, gate_m_workflow_sha, "Gate M workflow SHA")
    _require(MIGRATION_TASK_ARN_RE, migration_task_arn, "Gate M migration task ARN")
    if evidence.group("run_id") != migration_run_id:
        raise ContractError(
            "Migration-bound evidence reference must identify migration_run_id."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-mode", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-digest", required=True)
    parser.add_argument("--migration-digest", required=True)
    parser.add_argument("--schema-migration-evidence-ref", required=True)
    parser.add_argument("--migration-run-id", default="")
    parser.add_argument("--gate-m-workflow-sha", default="")
    parser.add_argument("--migration-task-arn", default="")
    args = parser.parse_args()
    try:
        validate_release_evidence_inputs(
            release_mode=args.release_mode,
            source_sha=args.source_sha,
            runtime_digest=args.runtime_digest,
            migration_digest=args.migration_digest,
            schema_migration_evidence_ref=args.schema_migration_evidence_ref,
            migration_run_id=args.migration_run_id,
            gate_m_workflow_sha=args.gate_m_workflow_sha,
            migration_task_arn=args.migration_task_arn,
        )
    except ContractError as exc:
        raise SystemExit(f"CD-01 release evidence contract failed: {exc}") from exc


if __name__ == "__main__":
    main()
