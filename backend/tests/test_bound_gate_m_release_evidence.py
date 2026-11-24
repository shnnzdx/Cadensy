"""Offline binding tests for Gate M release evidence.

These tests do not call GitHub or AWS. They replay the metadata shape of
historical run 45046230145, whose successful Gate M conclusion used to satisfy
every later backend release.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "cd01" / "validate_bound_gate_m_evidence.py"
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release-pydantic-backend.yml"
GATE_M_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "cd01-pr06b-schema-migration.yml"
ARCHIVED_GATE_M = (
    REPOSITORY_ROOT
    / "AWS"
    / "archive"
    / "workflows"
    / "retired-release"
    / "release-pr06b-schema-migration.yml"
)
BUILD_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "artifact-backend-images.yml"

# Run 45046230145 completed on main at this dispatch ref. Its migrated source
# commit was bb4791fcf17a5678b72fea19c42169bbeb75d732, not this head.
STALE_RUN_HEAD = "05d92ca703fccf6b8a40af24957c60469079479b"
MIGRATED_SOURCE = "bb4791fcf17a5678b72fea19c42169bbeb75d732"
OTHER_SOURCE = "c" * 40
MIGRATION_DIGEST = "sha256:" + "ab" * 32
OTHER_DIGEST = "sha256:" + "cd" * 32
TASK_ARN = "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/" + "a" * 32
OTHER_TASK_ARN = "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/" + "b" * 32
APPROVED_HEAD = "c8e1d4a90b27"
OLD_HEAD = "b7d3a1e9c642"

spec = importlib.util.spec_from_file_location("validate_bound_gate_m_evidence", CHECKER_PATH)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def _logs(
    source_sha: str = MIGRATED_SOURCE,
    digest: str = MIGRATION_DIGEST,
    revision: str = APPROVED_HEAD,
    task_arn: str = TASK_ARN,
    extra_task_arn: str = "",
) -> str:
    extra = f"\nMigration task: {extra_task_arn}\n" if extra_task_arn else ""
    return (
        f"SOURCE_SHA: {source_sha}\n"
        f"MIGRATION_DIGEST: {digest}\n"
        'echo "- Git commit: ${SOURCE_SHA}"\n'
        f"- Git commit: {source_sha}\n"
        f"- Verified final revision: {revision}\n"
        f"- Migration task: {task_arn}\n"
        "- Status: verified-pr06b-head\n"
        'test "${exit_code}" = "0"\n'
        f"{extra}"
    )


def _run(head_sha: str = STALE_RUN_HEAD, **overrides) -> dict:
    payload = {
        "id": 45046230145,
        "event": "workflow_dispatch",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": head_sha,
        "path": ".github/workflows/cd01-pr06b-schema-migration.yml",
    }
    payload.update(overrides)
    return payload


def _jobs(conclusion: str = "success", step_conclusion: str | None = None) -> dict:
    return {
        "jobs": [
            {
                "name": "Require a protected Gate M approval environment",
                "conclusion": "success",
                "steps": [],
            },
            {
                "name": "Execute one approved PR-06B schema migration",
                "conclusion": conclusion,
                "steps": [
                    {
                        "name": "Run bounded migration and read-only catalog verification",
                        "conclusion": conclusion if step_conclusion is None else step_conclusion,
                    }
                ],
            },
        ]
    }


def _messages(*lines: str) -> list[str]:
    return list(lines)


def _upgrade(target: str = APPROVED_HEAD, start: str = OLD_HEAD) -> str:
    return f"Running upgrade {start} -> {target}, activation events"


def _validate(**overrides):
    values = {
        "source_sha": MIGRATED_SOURCE,
        "expected_alembic_head": APPROVED_HEAD,
        "migration_digest": MIGRATION_DIGEST,
        "run": _run(),
        "jobs": _jobs(),
        "github_logs": _logs(),
        "cloudwatch_messages": _messages(
            "Running upgrade c2a4b6d8e0f1 -> 9db6f1a1041b",
            _upgrade(),
        ),
    }
    values.update(overrides)
    return checker.validate_bound_gate_m_evidence(**values)


def test_matching_gate_m_evidence_is_accepted_for_its_own_commit():
    """The historical run shape is valid only for the commit its logs recorded."""

    assert _validate() == TASK_ARN


def test_stale_run_45046230145_is_rejected_for_a_different_commit():
    with pytest.raises(checker.ContractError, match="commit being released"):
        _validate(source_sha=OTHER_SOURCE)


def test_dispatch_head_sha_does_not_authorize_a_different_logged_source():
    """Run 45046230145's head_sha must not count as the migrated commit."""

    with pytest.raises(checker.ContractError, match="commit being released"):
        _validate(source_sha=STALE_RUN_HEAD)


def test_matching_commit_with_a_different_revision_is_rejected():
    with pytest.raises(checker.ContractError, match="Alembic revision"):
        _validate(
            github_logs=_logs(revision=OLD_HEAD),
            cloudwatch_messages=_messages(_upgrade(target=OLD_HEAD, start="7a6c8e1d5f04")),
        )


def test_old_upgrade_chain_without_the_approved_head_is_rejected():
    with pytest.raises(checker.ContractError, match="CloudWatch"):
        _validate(
            cloudwatch_messages=_messages(
                "Running upgrade c2a4b6d8e0f1 -> 9db6f1a1041b",
                "Running upgrade 9db6f1a1041b -> 7a6c8e1d5f04",
                "Running upgrade 7a6c8e1d5f04 -> b7d3a1e9c642",
            )
        )


def test_wrong_upgrade_target_is_rejected():
    with pytest.raises(checker.ContractError, match="CloudWatch"):
        _validate(cloudwatch_messages=_messages("Running upgrade b7d3a1e9c642 -> c8e1d4a90b28"))


def test_digest_mismatch_is_rejected():
    with pytest.raises(checker.ContractError, match="schema-verifier digest"):
        _validate(migration_digest=OTHER_DIGEST)


def test_missing_logs_empty_cloudwatch_and_failed_job_are_rejected():
    with pytest.raises(checker.ContractError, match="logs are missing"):
        _validate(github_logs="   ")
    with pytest.raises(checker.ContractError, match="CloudWatch evidence is missing"):
        _validate(cloudwatch_messages=[])
    with pytest.raises(checker.ContractError, match="migration job"):
        _validate(jobs=_jobs(conclusion="failure"))
    with pytest.raises(checker.ContractError, match="migration job"):
        _validate(jobs=_jobs(step_conclusion="failure"))


def test_ambiguous_migration_task_arn_is_rejected():
    with pytest.raises(checker.ContractError, match="exactly one"):
        _validate(github_logs=_logs(extra_task_arn=OTHER_TASK_ARN))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("run", {"event": "push", "conclusion": "success", "head_branch": "main", "head_sha": STALE_RUN_HEAD, "path": checker.GATE_M_WORKFLOW_PATH}, "manual dispatch"),
        ("run", {"event": "workflow_dispatch", "conclusion": "failure", "head_branch": "main", "head_sha": STALE_RUN_HEAD, "path": checker.GATE_M_WORKFLOW_PATH}, "did not succeed"),
        ("run", {"event": "workflow_dispatch", "conclusion": "success", "head_branch": "feature", "head_sha": STALE_RUN_HEAD, "path": checker.GATE_M_WORKFLOW_PATH}, "not on main"),
        ("run", {"event": "workflow_dispatch", "conclusion": "success", "head_branch": "main", "head_sha": STALE_RUN_HEAD, "path": "AWS/archive/workflows/retired-release/release-pr06b-schema-migration.yml"}, "active schema-migration"),
        ("run", {"event": "workflow_dispatch", "conclusion": "success", "head_branch": "main", "path": checker.GATE_M_WORKFLOW_PATH}, "head SHA is missing"),
    ],
)
def test_unsuccessful_or_unbound_run_metadata_is_rejected(field, value, match):
    with pytest.raises(checker.ContractError, match=match):
        _validate(**{field: value})


def test_cli_accepts_matching_evidence_and_rejects_stale_evidence(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "summary.txt").write_text(_logs(), encoding="utf-8")
    run_path = tmp_path / "run.json"
    jobs_path = tmp_path / "jobs.json"
    cloudwatch_path = tmp_path / "cloudwatch.json"
    run_path.write_text(json.dumps(_run()), encoding="utf-8")
    jobs_path.write_text(json.dumps(_jobs()), encoding="utf-8")
    cloudwatch_path.write_text(
        json.dumps({"events": [{"message": _upgrade()}]}),
        encoding="utf-8",
    )

    emitted = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--emit-task-arn",
            "--logs-dir",
            str(logs_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert emitted.returncode == 0
    assert emitted.stdout.strip() == TASK_ARN

    accepted = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--source-sha",
            MIGRATED_SOURCE,
            "--expected-alembic-head",
            APPROVED_HEAD,
            "--migration-digest",
            MIGRATION_DIGEST,
            "--run-json",
            str(run_path),
            "--jobs-json",
            str(jobs_path),
            "--logs-dir",
            str(logs_dir),
            "--cloudwatch-json",
            str(cloudwatch_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr

    rejected = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--source-sha",
            OTHER_SOURCE,
            "--expected-alembic-head",
            APPROVED_HEAD,
            "--migration-digest",
            MIGRATION_DIGEST,
            "--run-json",
            str(run_path),
            "--jobs-json",
            str(jobs_path),
            "--logs-dir",
            str(logs_dir),
            "--cloudwatch-json",
            str(cloudwatch_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "commit being released" in rejected.stderr

    missing_cloudwatch = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--source-sha",
            MIGRATED_SOURCE,
            "--expected-alembic-head",
            APPROVED_HEAD,
            "--migration-digest",
            MIGRATION_DIGEST,
            "--run-json",
            str(run_path),
            "--jobs-json",
            str(jobs_path),
            "--logs-dir",
            str(logs_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing_cloudwatch.returncode != 0
    assert "incomplete" in missing_cloudwatch.stderr


def test_release_workflow_uses_the_binder_and_keeps_the_current_head_pin():
    workflow = yaml.load(RELEASE_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    script = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["jobs"]["promote"]["environment"] == "Main"
    assert workflow["env"]["APPROVED_ALEMBIC_HEAD"] == APPROVED_HEAD
    assert workflow["env"]["BACKEND_LOG_GROUP"] == "/ecs/tripsync-backend"
    assert "validate_bound_gate_m_evidence.py --emit-task-arn --logs-dir /tmp/gate-m-logs" in script
    assert (
        "arn:aws:ecs:${APPROVED_AWS_REGION}:${APPROVED_AWS_ACCOUNT_ID}:task/"
        "${ECS_CLUSTER}/[0-9a-f]{32}"
    ) in script
    assert '--source-sha "${SOURCE_SHA}"' in script
    assert '--expected-alembic-head "${APPROVED_ALEMBIC_HEAD}"' in script
    assert '--migration-digest "${SCHEMA_VERIFIER_DIGEST}"' in script
    assert '--log-group-name "${BACKEND_LOG_GROUP}"' in script
    assert '--log-stream-name "ecs/${CONTAINER_NAME}/${migration_task_id}"' in script
    assert "actions/runs/${schema_evidence_run_id}/logs" in script
    unbound = (
        '.event == "workflow_dispatch" and .conclusion == "success" '
        'and .head_branch == "main" and .path == '
        '".github/workflows/cd01-pr06b-schema-migration.yml"'
    )
    assert unbound not in script
    for forbidden in ("alembic upgrade", "alembic stamp", "alembic downgrade"):
        assert forbidden not in script

    build = yaml.load(BUILD_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert build["jobs"]["build-and-push"]["env"]["BUILD_ALEMBIC_HEAD"] == APPROVED_HEAD


def test_restored_gate_m_workflow_stays_manual_and_protected():
    assert GATE_M_WORKFLOW.is_file()
    assert not ARCHIVED_GATE_M.exists()
    workflow = yaml.load(GATE_M_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    for automatic in ("push", "pull_request", "schedule", "workflow_run"):
        assert automatic not in workflow["on"]
    assert workflow["jobs"]["migrate"]["environment"] == "Main"
    assert workflow["jobs"]["migrate"]["needs"] == "assert-migration-approval"
    assert workflow["jobs"]["assert-migration-approval"]["if"] == (
        "github.ref == 'refs/heads/main' && inputs.confirmation == 'EXECUTE_PR06B_SCHEMA_MIGRATION'"
    )
    assert workflow["env"]["PR06B_ALEMBIC_HEAD"] == APPROVED_HEAD
    assert "aws ecs update-service" not in GATE_M_WORKFLOW.read_text(encoding="utf-8")
