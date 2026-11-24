"""Offline binding tests for Gate M release evidence.

Fixtures follow the job log of run 45046230145, job 137281368161. Summary
echoes stay unexpanded there. Env-dump lines and one expression-substituted
task ARN are the GitHub signals. These tests do not call GitHub or AWS.
"""

from __future__ import annotations

import hashlib
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
RESTORED_GATE_M_SHA256 = "bef7a4be9aef3bca102c4ffe904119efa28ca435c62e1cd6d9e75741e4d6db97"

STALE_RUN_HEAD = "05d92ca703fccf6b8a40af24957c60469079479b"
MIGRATED_SOURCE = "bb4791fcf17a5678b72fea19c42169bbeb75d732"
OTHER_SOURCE = "c" * 40
MIGRATION_DIGEST = (
    "sha256:2d55a69208593f15354146c09bbb9fc93828c7d0c5b1b25988474e2aa454802d"
)
OTHER_DIGEST = "sha256:" + "cd" * 32
TASK_ID = "3995fcb60ca3417e87dd87fa891cf65a"
TASK_ARN = (
    "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/" + TASK_ID
)
OTHER_TASK_ID = "b" * 32
OTHER_TASK_ARN = (
    "arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/" + OTHER_TASK_ID
)
APPROVED_HEAD = "c8e1d4a90b27"
PREDECESSOR = "b7d3a1e9c642"

spec = importlib.util.spec_from_file_location("validate_bound_gate_m_evidence", CHECKER_PATH)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def _alembic(message: str) -> str:
    """Format one line the way backend/alembic.ini's generic formatter does."""

    return f"{'INFO':<5} [alembic.runtime.migration] {message}"


def _upgrade(target: str = APPROVED_HEAD, start: str = PREDECESSOR, detail: str = "activation events") -> str:
    return _alembic(f"Running upgrade {start} -> {target}, {detail}")


def _env(key: str, value: str, stamp: str = "2026-11-22T16:53:16.2491749Z") -> str:
    return f"{stamp}   {key}: {value}"


def _script(body: str, stamp: str = "2026-11-22T16:54:28.1055355Z") -> str:
    return f"{stamp} \x1b[36;1m{body}\x1b[0m"


def _real_log(
    *,
    source_lines: list[str] | None = None,
    digest_lines: list[str] | None = None,
    task_lines: list[str] | None = None,
    extra: list[str] | None = None,
) -> str:
    """Minimal lines copied from the shape of job 137281368161."""

    if source_lines is None:
        source_lines = [
            _env("SOURCE_SHA", MIGRATED_SOURCE, "2026-11-22T16:53:16.2491749Z"),
            _env("SOURCE_SHA", MIGRATED_SOURCE, "2026-11-22T16:54:28.1101329Z"),
        ]
    if digest_lines is None:
        digest_lines = [
            _env("MIGRATION_DIGEST", MIGRATION_DIGEST, "2026-11-22T16:53:16.2493652Z"),
            _env("MIGRATION_DIGEST", MIGRATION_DIGEST, "2026-11-22T16:54:28.1101812Z"),
        ]
    if task_lines is None:
        task_lines = [
            _script(
                f'  echo "- Migration task: {TASK_ARN}"',
                "2026-11-22T16:54:28.1055355Z",
            )
        ]
    lines = [
        _script(
            '[[ "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]',
            "2026-11-22T16:53:16.2391773Z",
        ),
        _script(
            '[[ "${MIGRATION_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]',
            "2026-11-22T16:53:16.2393492Z",
        ),
        *source_lines,
        *digest_lines,
        _script('test "${exit_code}" = "0"', "2026-11-22T16:53:22.7706153Z"),
        _script(
            'echo "status=verified-pr06b-head" >> "${GITHUB_OUTPUT}"',
            "2026-11-22T16:53:22.7706845Z",
        ),
        _script('  echo "- Git commit: ${SOURCE_SHA}"', "2026-11-22T16:54:28.1053058Z"),
        _script(
            '  echo "- Verified final revision: ${PR06B_ALEMBIC_HEAD}"',
            "2026-11-22T16:54:28.1054747Z",
        ),
        *task_lines,
        *(extra or []),
    ]
    return "\n".join(lines) + "\n"


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


def _pages(*message_groups: list[str], tokens: list[str] | None = None) -> list[dict]:
    if tokens is None:
        tokens = ["page-1", "page-1"]
        message_groups = (message_groups[0] if message_groups else [_upgrade()], [])
    pages = []
    for messages, token in zip(message_groups, tokens, strict=True):
        pages.append(
            {
                "events": [{"message": message} for message in messages],
                "nextForwardToken": token,
            }
        )
    return pages


def _messages() -> list[str]:
    return [
        _upgrade("9db6f1a1041b", "c2a4b6d8e0f1", "membership"),
        _upgrade(),
    ]


def _validate(**overrides):
    values = {
        "source_sha": MIGRATED_SOURCE,
        "expected_alembic_head": APPROVED_HEAD,
        "migration_digest": MIGRATION_DIGEST,
        "run": _run(),
        "jobs": _jobs(),
        "github_logs": _real_log(),
        "cloudwatch_messages": _messages(),
    }
    values.update(overrides)
    return checker.validate_bound_gate_m_evidence(**values)


def test_real_log_shape_for_run_45046230145_is_accepted():
    """Env dumps from job 137281368161 bind that run to its migrated source."""

    assert _validate() == TASK_ARN
    assert _validate(cloudwatch_messages=[f"Running upgrade {PREDECESSOR} -> {APPROVED_HEAD}, activation events"]) == TASK_ARN


def test_unexpanded_summary_text_is_not_evidence():
    script_only = "\n".join(
        [
            _script('  echo "- Git commit: ${SOURCE_SHA}"'),
            _script('  echo "- Verified final revision: ${PR06B_ALEMBIC_HEAD}"'),
            _script('test "${exit_code}" = "0"'),
            _script('echo "status=verified-pr06b-head" >> "${GITHUB_OUTPUT}"'),
            _script(f'  echo "- Migration task: {TASK_ARN}"'),
        ]
    )
    with pytest.raises(checker.ContractError, match="env-dump SOURCE_SHA"):
        _validate(github_logs=script_only)


def test_stale_run_is_rejected_for_a_different_commit_and_for_its_dispatch_head():
    with pytest.raises(checker.ContractError, match="commit being released"):
        _validate(source_sha=OTHER_SOURCE)
    with pytest.raises(checker.ContractError, match="commit being released"):
        _validate(source_sha=STALE_RUN_HEAD)


def test_every_source_sha_env_dump_must_match_and_end_on_40_hex():
    with pytest.raises(checker.ContractError, match="commit being released"):
        _validate(
            github_logs=_real_log(
                source_lines=[
                    _env("SOURCE_SHA", MIGRATED_SOURCE),
                    _env("SOURCE_SHA", OTHER_SOURCE, "2026-11-22T16:54:28.1101329Z"),
                ]
            )
        )
    with pytest.raises(checker.ContractError, match="commit being released"):
        _validate(
            github_logs=_real_log(
                source_lines=[_env("SOURCE_SHA", MIGRATED_SOURCE + "ff")]
            )
        )


def test_digest_uses_only_exact_env_dump_values():
    free_text = _script(
        f"echo MIGRATION_DIGEST: {OTHER_DIGEST}",
        "2026-11-22T16:53:16.2400000Z",
    )
    assert _validate(github_logs=_real_log(extra=[free_text])) == TASK_ARN
    with pytest.raises(checker.ContractError, match="env-dump MIGRATION_DIGEST"):
        _validate(github_logs=_real_log(digest_lines=[free_text]))
    with pytest.raises(checker.ContractError, match="schema-verifier digest"):
        _validate(migration_digest=OTHER_DIGEST)
    with pytest.raises(checker.ContractError, match="schema-verifier digest"):
        _validate(
            github_logs=_real_log(
                digest_lines=[_env("MIGRATION_DIGEST", MIGRATION_DIGEST + "a")]
            )
        )
    with pytest.raises(checker.ContractError, match="schema-verifier digest"):
        _validate(
            github_logs=_real_log(
                digest_lines=[
                    _env("MIGRATION_DIGEST", MIGRATION_DIGEST),
                    _env("MIGRATION_DIGEST", OTHER_DIGEST, "2026-11-22T16:54:28.1101812Z"),
                ]
            )
        )


def test_task_arn_must_be_one_bounded_32_hex_id():
    with pytest.raises(checker.ContractError, match="exactly one"):
        _validate(
            github_logs=_real_log(
                task_lines=[_script(f'  echo "- Migration task: {TASK_ARN}ab"')]
            )
        )
    with pytest.raises(checker.ContractError, match="exactly one"):
        _validate(
            github_logs=_real_log(
                task_lines=[
                    _script(f'  echo "- Migration task: {TASK_ARN}"'),
                    _script(
                        f'  echo "- Migration task: {OTHER_TASK_ARN}"',
                        "2026-11-22T16:54:28.1056000Z",
                    ),
                ]
            )
        )


@pytest.mark.parametrize(
    "messages",
    [
        [_upgrade(APPROVED_HEAD + "ff")],
        [f"echo Running upgrade {PREDECESSOR} -> {APPROVED_HEAD}, activation events"],
        [_upgrade(), _upgrade("abcdefabcdef", APPROVED_HEAD, "later head")],
        [_upgrade(), _alembic(f"Running downgrade {APPROVED_HEAD} -> {PREDECESSOR}, rollback")],
        [_upgrade("c8e1d4a90b27", "9db6f1a1041b", "wrong predecessor")],
    ],
)
def test_cloudwatch_upgrade_line_is_anchored_and_final(messages):
    with pytest.raises(checker.ContractError, match="CloudWatch"):
        _validate(cloudwatch_messages=messages)


def test_cloudwatch_pagination_requires_an_unchanged_token():
    merged = checker.merge_cloudwatch_pages(
        _pages([_upgrade(), "earlier context"], [], tokens=["tok-a", "tok-a"])
    )
    assert merged == [_upgrade(), "earlier context"]
    with pytest.raises(checker.ContractError, match="unchanged nextForwardToken"):
        checker.merge_cloudwatch_pages(_pages([_upgrade()], tokens=["only"]))
    with pytest.raises(checker.ContractError, match="unchanged nextForwardToken"):
        checker.merge_cloudwatch_pages(
            _pages([_upgrade()], ["still going"], [], tokens=["tok-a", "tok-b", "tok-c"])
        )
    with pytest.raises(checker.ContractError, match="continued after the token stabilized"):
        checker.merge_cloudwatch_pages(
            _pages([_upgrade()], [], ["extra"], tokens=["tok-a", "tok-a", "tok-b"])
        )


def test_missing_logs_empty_cloudwatch_and_failed_job_are_rejected():
    with pytest.raises(checker.ContractError, match="logs are missing"):
        _validate(github_logs="   ")
    with pytest.raises(checker.ContractError, match="CloudWatch evidence is missing"):
        checker.merge_cloudwatch_pages(
            _pages([], [], tokens=["tok-a", "tok-a"])
        )
    with pytest.raises(checker.ContractError, match="migration job"):
        _validate(jobs=_jobs(conclusion="failure"))
    with pytest.raises(checker.ContractError, match="migration job"):
        _validate(jobs=_jobs(step_conclusion="failure"))


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


def test_cli_accepts_the_real_log_shape_and_rejects_a_stale_commit(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "migration.txt").write_text(_real_log(), encoding="utf-8")
    run_path = tmp_path / "run.json"
    jobs_path = tmp_path / "jobs.json"
    cloudwatch_path = tmp_path / "cloudwatch.json"
    run_path.write_text(json.dumps(_run()), encoding="utf-8")
    jobs_path.write_text(json.dumps(_jobs()), encoding="utf-8")
    cloudwatch_path.write_text(
        json.dumps({"pages": _pages(_messages(), [], tokens=["tok-a", "tok-a"])}),
        encoding="utf-8",
    )
    common = [
        sys.executable,
        str(CHECKER_PATH),
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
    ]

    emitted = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--emit-task-arn", "--logs-dir", str(logs_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert emitted.returncode == 0
    assert emitted.stdout.strip() == TASK_ARN

    accepted = subprocess.run(
        [*common, "--source-sha", MIGRATED_SOURCE],
        text=True,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr

    rejected = subprocess.run(
        [*common, "--source-sha", OTHER_SOURCE],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "commit being released" in rejected.stderr


def test_release_workflow_pages_cloudwatch_and_bounds_the_task_arn():
    workflow = yaml.load(RELEASE_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    script = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["jobs"]["promote"]["environment"] == "Main"
    assert workflow["env"]["APPROVED_ALEMBIC_HEAD"] == APPROVED_HEAD
    assert "validate_bound_gate_m_evidence.py --emit-task-arn --logs-dir /tmp/gate-m-logs" in script
    assert '[[ "${migration_task_id}" =~ ^[0-9a-f]{32}$ ]]' in script
    assert (
        'expected_migration_task_arn="arn:aws:ecs:${APPROVED_AWS_REGION}:'
        '${APPROVED_AWS_ACCOUNT_ID}:task/${ECS_CLUSTER}/${migration_task_id}"'
    ) in script
    assert '[[ "${migration_task_arn}" == "${expected_migration_task_arn}" ]]' in script
    assert "--next-token" in script
    assert "nextForwardToken" in script
    assert 'if [[ "${page_count}" -gt 20 ]]; then' in script
    assert 'if [[ -n "${token}" && "${next}" == "${token}" ]]; then' in script
    assert '--expected-alembic-head "${APPROVED_ALEMBIC_HEAD}"' in script
    for forbidden in ("alembic upgrade", "alembic stamp", "alembic downgrade"):
        assert forbidden not in script


def test_head_pins_stay_coupled_to_the_upgrade_predecessor():
    release = yaml.load(RELEASE_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    build = yaml.load(BUILD_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    gate = yaml.load(GATE_M_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    approved = release["env"]["APPROVED_ALEMBIC_HEAD"]
    assert approved == build["jobs"]["build-and-push"]["env"]["BUILD_ALEMBIC_HEAD"]
    assert approved == gate["env"]["PR06B_ALEMBIC_HEAD"]
    assert approved == APPROVED_HEAD
    assert checker.UPGRADE_START_REVISION == gate["env"]["PR06B_CONVERSATION_HEAD"]
    assert checker.UPGRADE_START_REVISION == PREDECESSOR
    assert f"Running upgrade {PREDECESSOR} -> {approved}" in (
        f"Running upgrade {checker.UPGRADE_START_REVISION} -> {approved}"
    )


def test_restored_gate_m_workflow_stays_manual_and_byte_identical():
    assert GATE_M_WORKFLOW.is_file()
    assert not ARCHIVED_GATE_M.exists()
    assert hashlib.sha256(GATE_M_WORKFLOW.read_bytes()).hexdigest() == RESTORED_GATE_M_SHA256
    workflow = yaml.load(GATE_M_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    for automatic in ("push", "pull_request", "schedule", "workflow_run"):
        assert automatic not in workflow["on"]
    assert workflow["jobs"]["migrate"]["environment"] == "Main"
    assert workflow["jobs"]["migrate"]["needs"] == "assert-migration-approval"
    assert workflow["jobs"]["assert-migration-approval"]["if"] == (
        "github.ref == 'refs/heads/main' && inputs.confirmation == 'EXECUTE_PR06B_SCHEMA_MIGRATION'"
    )
    assert "aws ecs update-service" not in GATE_M_WORKFLOW.read_text(encoding="utf-8")
