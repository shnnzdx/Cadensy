"""Bind a backend release to one successful Gate M run.

The release workflow downloads the Gate M run, jobs, and logs, then reads the
migration task's CloudWatch stream. This checker only evaluates those local
artifacts. It does not call GitHub or AWS.

A run is accepted only when all of the following hold:

- the run is a successful manual dispatch of the active Gate M workflow on main;
- the migration job and its bounded migration step succeeded;
- the logs record ``SOURCE_SHA`` as the commit being released;
- the logs record the schema-verifier digest supplied for that commit;
- the logs and CloudWatch stream both record Alembic head
  ``expected_alembic_head``, including
  ``Running upgrade b7d3a1e9c642 -> <expected_alembic_head>``.

The workflow run's ``head_sha`` is the ref selected at dispatch. Gate M checks
out ``inputs.source_sha``, which is the commit being released. Those values
differed for historical run 45046230145 (``head_sha`` ``05d92ca7…``, migrated
source ``bb4791fc…``). Binding only ``head_sha`` would treat the dispatch ref
as the migrated commit, so the commit binding is the logged ``SOURCE_SHA``.

Missing files, a different commit, a different digest, or a different revision
fail closed. ``BUILD_ALEMBIC_HEAD`` and ``APPROVED_ALEMBIC_HEAD`` stay
``c8e1d4a90b27`` in this change. A later schema head has to update those pins
and this ``b7d3a1e9c642`` predecessor together before a release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_HEAD_RE = re.compile(r"^[0-9a-f]{12}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_ARN_RE = re.compile(
    r"arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/[0-9a-f]{32}"
)
GATE_M_WORKFLOW_PATH = ".github/workflows/cd01-pr06b-schema-migration.yml"
GATE_M_JOB_NAME = "Execute one approved PR-06B schema migration"
GATE_M_STEP_NAME = "Run bounded migration and read-only catalog verification"
UPGRADE_START_REVISION = "b7d3a1e9c642"


class ContractError(ValueError):
    """Gate M evidence is missing or is not bound to this release."""


def _fail(message: str) -> None:
    raise ContractError(message)


def load_github_logs(logs_dir: Path) -> str:
    """Read every file in a downloaded Gate M log directory."""

    if not logs_dir.is_dir():
        _fail("Gate M log directory is missing.")
    files = [path for path in logs_dir.rglob("*") if path.is_file()]
    if not files:
        _fail("Gate M log directory is empty.")
    parts: list[str] = []
    for path in sorted(files):
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def migration_task_arn_from_logs(logs: str) -> str:
    """Return the single migration task ARN recorded by Gate M."""

    found = sorted(set(TASK_ARN_RE.findall(logs)))
    if len(found) != 1:
        _fail("Gate M logs must contain exactly one migration task ARN.")
    task_arn = found[0]
    if f"Migration task: {task_arn}" not in logs:
        _fail("Gate M logs must record the migration task ARN in the evidence summary.")
    return task_arn


def cloudwatch_messages_from_payload(payload: object) -> list[str]:
    """Accept the ``aws logs get-log-events`` JSON shape."""

    if not isinstance(payload, dict):
        _fail("Gate M CloudWatch evidence is missing.")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        _fail("Gate M CloudWatch evidence is missing.")
    messages: list[str] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("message"), str):
            _fail("Gate M CloudWatch evidence is malformed.")
        messages.append(event["message"])
    return messages


def _require_same_values(values: list[str], expected: str, message: str) -> None:
    if not values or any(value != expected for value in values):
        _fail(message)


def validate_bound_gate_m_evidence(
    *,
    source_sha: str,
    expected_alembic_head: str,
    migration_digest: str,
    run: object,
    jobs: object,
    github_logs: str,
    cloudwatch_messages: list[str],
) -> str:
    """Validate the bound Gate M chain and return its migration task ARN."""

    if SHA_RE.fullmatch(source_sha) is None:
        _fail("Source SHA has an invalid format.")
    if ALEMBIC_HEAD_RE.fullmatch(expected_alembic_head) is None:
        _fail("Expected Alembic head has an invalid format.")
    if DIGEST_RE.fullmatch(migration_digest) is None:
        _fail("Schema-verifier digest has an invalid format.")
    if not isinstance(run, dict):
        _fail("Gate M run evidence is missing.")
    if run.get("event") != "workflow_dispatch":
        _fail("Gate M run was not a manual dispatch.")
    if run.get("conclusion") != "success":
        _fail("Gate M run did not succeed.")
    if run.get("head_branch") != "main":
        _fail("Gate M run was not on main.")
    if run.get("path") != GATE_M_WORKFLOW_PATH:
        _fail("Gate M run did not execute the active schema-migration workflow.")
    if SHA_RE.fullmatch(str(run.get("head_sha") or "")) is None:
        _fail("Gate M run head SHA is missing.")

    if not isinstance(github_logs, str) or not github_logs.strip():
        _fail("Gate M logs are missing.")
    _require_same_values(
        re.findall(r"SOURCE_SHA: ([0-9a-f]{40})", github_logs),
        source_sha,
        "Gate M logs are not bound to the commit being released.",
    )
    _require_same_values(
        re.findall(r"Git commit: ([0-9a-f]{40})", github_logs),
        source_sha,
        "Gate M evidence summary is not bound to the commit being released.",
    )
    if f"MIGRATION_DIGEST: {migration_digest}" not in github_logs:
        _fail("Gate M logs are not bound to the schema-verifier digest being released.")
    if "Status: verified-pr06b-head" not in github_logs:
        _fail("Gate M logs are missing the verified-head status.")
    if 'test "${exit_code}" = "0"' not in github_logs:
        _fail("Gate M logs are missing the migration exit-code check.")
    _require_same_values(
        re.findall(r"Verified final revision: ([0-9a-f]{12})", github_logs),
        expected_alembic_head,
        "Gate M logs are not bound to the expected Alembic revision.",
    )

    jobs_list = jobs.get("jobs") if isinstance(jobs, dict) else None
    if not isinstance(jobs_list, list) or not jobs_list:
        _fail("Gate M job evidence is missing.")
    migration_succeeded = False
    for job in jobs_list:
        if not isinstance(job, dict):
            continue
        if job.get("name") != GATE_M_JOB_NAME or job.get("conclusion") != "success":
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        if any(
            isinstance(step, dict)
            and step.get("name") == GATE_M_STEP_NAME
            and step.get("conclusion") == "success"
            for step in steps
        ):
            migration_succeeded = True
            break
    if not migration_succeeded:
        _fail("Gate M migration job did not succeed.")

    task_arn = migration_task_arn_from_logs(github_logs)
    if not cloudwatch_messages:
        _fail("Gate M CloudWatch evidence is missing.")
    upgrade_line = (
        f"Running upgrade {UPGRADE_START_REVISION} -> {expected_alembic_head}"
    )
    if upgrade_line not in "\n".join(cloudwatch_messages):
        _fail(
            "Gate M CloudWatch evidence is not bound to the expected Alembic revision."
        )
    return task_arn


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is missing or malformed.") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--emit-task-arn", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--expected-alembic-head")
    parser.add_argument("--migration-digest")
    parser.add_argument("--run-json", type=Path)
    parser.add_argument("--jobs-json", type=Path)
    parser.add_argument("--cloudwatch-json", type=Path)
    args = parser.parse_args(argv)
    try:
        logs = load_github_logs(args.logs_dir)
        if args.emit_task_arn:
            print(migration_task_arn_from_logs(logs))
            return
        required = {
            "source_sha": args.source_sha,
            "expected_alembic_head": args.expected_alembic_head,
            "migration_digest": args.migration_digest,
            "run_json": args.run_json,
            "jobs_json": args.jobs_json,
            "cloudwatch_json": args.cloudwatch_json,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            _fail("Bound Gate M evidence is incomplete: " + ", ".join(missing) + ".")
        cloudwatch_payload = _load_json(args.cloudwatch_json, "Gate M CloudWatch evidence")
        validate_bound_gate_m_evidence(
            source_sha=args.source_sha,
            expected_alembic_head=args.expected_alembic_head,
            migration_digest=args.migration_digest,
            run=_load_json(args.run_json, "Gate M run evidence"),
            jobs=_load_json(args.jobs_json, "Gate M job evidence"),
            github_logs=logs,
            cloudwatch_messages=cloudwatch_messages_from_payload(cloudwatch_payload),
        )
    except ContractError as exc:
        raise SystemExit(f"Bound Gate M evidence check failed: {exc}") from exc


if __name__ == "__main__":
    main(sys.argv[1:])
