"""Bind a backend release to one successful Gate M run.

The release workflow downloads the Gate M run, jobs, and logs, then pages
through that migration task's CloudWatch stream until ``nextForwardToken``
is unchanged. This checker only evaluates those local artifacts. It does not
call GitHub or AWS.

Accepted evidence is:

- a successful manual dispatch of the active Gate M workflow on main;
- a successful migration job and bounded-migration step in the jobs payload;
- every env-dump ``SOURCE_SHA`` equal to the commit being released;
- every env-dump ``MIGRATION_DIGEST`` equal to the schema-verifier digest;
- exactly one task ARN;
- CloudWatch whose last Alembic upgrade line is
  ``Running upgrade b7d3a1e9c642 -> <expected head>``.

GitHub's step log keeps ``echo "- Git commit: ${SOURCE_SHA}"`` and
``echo "- Verified final revision: ${PR06B_ALEMBIC_HEAD}"`` unexpanded. Those
lines, ``test "${exit_code}" = "0"``, and ``status=verified-pr06b-head`` are
static script text and are not evidence. The values that do appear are the
    per-step env dump (timestamp, three spaces, ``KEY: value``) and the task ARN
substituted into the summary script.

``BUILD_ALEMBIC_HEAD``, ``APPROVED_ALEMBIC_HEAD``, and Gate M
``PR06B_ALEMBIC_HEAD`` stay ``c8e1d4a90b27``. The upgrade predecessor stays
``b7d3a1e9c642``. Production is already at that head, so a backend release can
bind only to the existing Gate M run until a later head has its own run.
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
ENV_DUMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    # Job 137281368161 places three spaces between "Z" and the key:
    # the log-field separator plus the env block's two-space indent.
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+Z {3}"
    r"(?P<key>[A-Z][A-Z0-9_]*): (?P<value>.*)$"
)
TASK_ARN_RE = re.compile(
    r"(?<![0-9A-Za-z])"
    r"arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/"
    r"([0-9a-f]{32})"
    r"(?![0-9a-f])"
)
ALEMBIC_LOG_PREFIX = r"(?:INFO  \[alembic\.runtime\.migration\] )?"
UPGRADE_LINE_RE = re.compile(
    rf"^{ALEMBIC_LOG_PREFIX}"
    r"Running upgrade (?P<start>[0-9a-f]{12}) -> (?P<target>[0-9a-f]{12})"
    r"(?:,.*)?$"
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
        parts.append(path.read_text(encoding="utf-8-sig", errors="replace"))
    return "\n".join(parts)


def env_dump_values(logs: str, key: str) -> list[str]:
    """Return values from GitHub Actions env-dump lines for one key.

    Only ``<timestamp>Z`` followed by three spaces and ``KEY: value`` counts.
    Unexpanded script text and other free-text echoes are ignored.
    """

    values: list[str] = []
    for line in logs.splitlines():
        match = ENV_DUMP_RE.fullmatch(line.rstrip("\r"))
        if match and match.group("key") == key:
            values.append(match.group("value"))
    return values


def migration_task_arns_from_logs(logs: str) -> list[str]:
    """Return the distinct bounded migration task ARNs in the log text."""

    return sorted(set(TASK_ARN_RE.findall(logs)))


def migration_task_arn_from_logs(logs: str) -> str:
    """Return the single migration task ARN recorded by Gate M."""

    found = migration_task_arns_from_logs(logs)
    if len(found) != 1:
        _fail("Gate M logs must contain exactly one migration task ARN.")
    return (
        "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/"
        + found[0]
    )


def merge_cloudwatch_pages(pages: object) -> list[str]:
    """Concatenate CloudWatch pages after ``nextForwardToken`` stabilizes.

    A read is complete only when a later page returns the same
    ``nextForwardToken`` that was used to request it, and that page is the
    last page. A single page, a token that never repeats, or pages after the
    token has already stabilized fail closed.
    """

    if not isinstance(pages, list) or len(pages) < 2:
        _fail(
            "Gate M CloudWatch pagination did not reach an unchanged nextForwardToken."
        )
    messages: list[str] = []
    previous_token: str | None = None
    stabilized = False
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            _fail("Gate M CloudWatch evidence is malformed.")
        token = page.get("nextForwardToken")
        events = page.get("events")
        if not isinstance(token, str) or not token:
            _fail("Gate M CloudWatch page is missing nextForwardToken.")
        if not isinstance(events, list):
            _fail("Gate M CloudWatch evidence is malformed.")
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("message"), str):
                _fail("Gate M CloudWatch evidence is malformed.")
            messages.append(event["message"])
        if previous_token is not None and token == previous_token:
            if index != len(pages) - 1:
                _fail(
                    "Gate M CloudWatch pagination continued after the token stabilized."
                )
            stabilized = True
            break
        previous_token = token
    if not stabilized:
        _fail(
            "Gate M CloudWatch pagination did not reach an unchanged nextForwardToken."
        )
    if not messages:
        _fail("Gate M CloudWatch evidence is missing.")
    return messages


def _require_exact_env_values(
    logs: str,
    key: str,
    expected: str,
    value_pattern: re.Pattern[str],
    missing: str,
    mismatch: str,
) -> None:
    values = env_dump_values(logs, key)
    if not values:
        _fail(missing)
    for value in values:
        if value_pattern.fullmatch(value) is None or value != expected:
            _fail(mismatch)


def _cloudwatch_revision_is_bound(
    messages: list[str],
    *,
    start_revision: str,
    expected_alembic_head: str,
) -> None:
    upgrades: list[tuple[str, str]] = []
    for raw in messages:
        line = raw.strip()
        if "Running downgrade" in line:
            _fail("Gate M CloudWatch evidence contains a downgrade.")
        if "Running upgrade" not in line:
            continue
        match = UPGRADE_LINE_RE.fullmatch(line)
        if match is None:
            _fail("Gate M CloudWatch upgrade line is not an anchored Alembic message.")
        upgrades.append((match.group("start"), match.group("target")))
    if not upgrades:
        _fail("Gate M CloudWatch evidence is not bound to the expected Alembic revision.")
    start, target = upgrades[-1]
    if start != start_revision or target != expected_alembic_head:
        _fail("Gate M CloudWatch evidence is not bound to the expected Alembic revision.")


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

    _require_exact_env_values(
        github_logs,
        "SOURCE_SHA",
        source_sha,
        SHA_RE,
        "Gate M logs are missing an env-dump SOURCE_SHA.",
        "Gate M logs are not bound to the commit being released.",
    )
    _require_exact_env_values(
        github_logs,
        "MIGRATION_DIGEST",
        migration_digest,
        DIGEST_RE,
        "Gate M logs are missing an env-dump MIGRATION_DIGEST.",
        "Gate M logs are not bound to the schema-verifier digest being released.",
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
    _cloudwatch_revision_is_bound(
        cloudwatch_messages,
        start_revision=UPGRADE_START_REVISION,
        expected_alembic_head=expected_alembic_head,
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
        payload = _load_json(args.cloudwatch_json, "Gate M CloudWatch evidence")
        if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
            _fail("Gate M CloudWatch evidence must be paginated pages.")
        validate_bound_gate_m_evidence(
            source_sha=args.source_sha,
            expected_alembic_head=args.expected_alembic_head,
            migration_digest=args.migration_digest,
            run=_load_json(args.run_json, "Gate M run evidence"),
            jobs=_load_json(args.jobs_json, "Gate M job evidence"),
            github_logs=logs,
            cloudwatch_messages=merge_cloudwatch_pages(payload["pages"]),
        )
    except ContractError as exc:
        raise SystemExit(f"Bound Gate M evidence check failed: {exc}") from exc


if __name__ == "__main__":
    main(sys.argv[1:])
