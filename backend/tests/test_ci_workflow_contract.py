"""Static public contracts for the repository-owned CI regression gate."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "build-validation.yml"
APPROVED_CHAT_CHANGE_PREVIEW_V1_HASH = (
    "e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f"
)


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _legacy_steps() -> list[dict]:
    return _workflow()["jobs"]["backend-legacy-full-regression"]["steps"]


def test_legacy_artifact_upload_requires_successful_privacy_provenance_validation():
    steps = _legacy_steps()
    validation = next(
        step
        for step in steps
        if step["name"] == "Validate evaluation report is traceable and privacy-safe"
    )
    upload = next(step for step in steps if step["name"] == "Upload Legacy evaluation artifacts")

    assert validation["id"] == "validate_evaluation_artifact"
    assert upload["if"] == "${{ steps.validate_evaluation_artifact.outcome == 'success' }}"


def test_legacy_evaluation_validation_freezes_the_approved_v1_dataset_hash():
    steps = _legacy_steps()
    validation = next(
        step for step in steps if step.get("id") == "validate_evaluation_artifact"
    )

    assert APPROVED_CHAT_CHANGE_PREVIEW_V1_HASH in validation["run"]
    assert 'computed_dataset_hash == manifest["dataset_hash"]' in validation["run"]
    assert "manifest[\"dataset_hash\"]" in validation["run"]


def test_checked_in_v1_dataset_still_has_the_approved_frozen_hash():
    dataset_path = (
        REPOSITORY_ROOT / "backend" / "evals" / "datasets" / "chat_change_preview_v1.json"
    )

    # The approved V1 value identifies canonical Git content.  Windows may
    # check this text file out with CRLF, which is not a Dataset version bump.
    canonical_bytes = dataset_path.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_bytes).hexdigest() == (
        APPROVED_CHAT_CHANGE_PREVIEW_V1_HASH
    )


def test_legacy_artifact_validation_requires_standalone_runner_isolation_evidence():
    validation = next(
        step for step in _legacy_steps() if step.get("id") == "validate_evaluation_artifact"
    )

    assert 'manifest["database_isolation"]' in validation["run"]
    assert '"source": "TEST_DATABASE_URL"' in validation["run"]
    assert '"runtime_database_url_used": False' in validation["run"]
    assert 'manifest["fixture_visibility"]' in validation["run"]
    assert '"worker_session_is_independent": True' in validation["run"]


def test_legacy_job_excludes_all_pydantic_modules_and_proves_no_collected_skips():
    steps = _legacy_steps()
    regression = next(step for step in steps if step["name"] == "Run Legacy full regression")
    skip_contract = next(
        step for step in steps if step["name"] == "Validate Legacy JUnit skip contract"
    )

    assert "--ignore=tests/test_pydantic_ai_poc.py" in regression["run"]
    assert "--ignore=tests/test_pydantic_runtime.py" in regression["run"]
    assert "--ignore=tests/test_provider_smoke_harness.py" in regression["run"]
    assert "--ignore=tests/test_live_provider_smoke_adapter.py" in regression["run"]
    assert "--junitxml" in regression["run"]
    assert 'findall(".//skipped")' in skip_contract["run"]
    assert "assert not skipped" in skip_contract["run"]


def test_ci_remote_job_contract_has_pinned_runtimes_isolated_postgres_and_no_soft_failures():
    workflow = _workflow()
    assert {"pull_request", "push", "workflow_dispatch"} <= set(workflow["on"])
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"] == {"PYTHON_VERSION": "3.13.5", "NODE_VERSION": "22.13.0"}

    jobs = workflow["jobs"]
    assert set(jobs) == {
        "backend-legacy-full-regression",
        "pydantic-ai-isolated-fake-compatibility",
        "frontend-regression",
    }
    for job in jobs.values():
        assert "needs" not in job
        assert "continue-on-error" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step
            assert "|| true" not in step.get("run", "")

    for job_name, database_name in (
        ("backend-legacy-full-regression", "cadensy_ci_legacy_test"),
        ("pydantic-ai-isolated-fake-compatibility", "cadensy_ci_pydantic_test"),
    ):
        postgres = jobs[job_name]["services"]["postgres"]
        assert postgres["image"] == "postgres:16"
        assert postgres["env"]["POSTGRES_DB"] == database_name
        assert "pg_isready" in postgres["options"]
        assert jobs[job_name]["env"]["TEST_DATABASE_URL"].endswith(database_name)
        assert jobs[job_name]["env"]["DATABASE_URL"] != jobs[job_name]["env"]["TEST_DATABASE_URL"]

    legacy_python = next(
        step for step in jobs["backend-legacy-full-regression"]["steps"] if step["name"] == "Set up Python"
    )
    poc_python = next(
        step
        for step in jobs["pydantic-ai-isolated-fake-compatibility"]["steps"]
        if step["name"] == "Set up Python"
    )
    frontend_node = next(
        step for step in jobs["frontend-regression"]["steps"] if step["name"] == "Set up Node.js"
    )
    assert legacy_python["with"]["python-version"] == "${{ env.PYTHON_VERSION }}"
    assert poc_python["with"]["python-version"] == "${{ env.PYTHON_VERSION }}"
    assert frontend_node["with"]["node-version"] == "${{ env.NODE_VERSION }}"


def test_pydantic_job_requires_the_installed_poc_and_executes_its_tests():
    steps = _workflow()["jobs"]["pydantic-ai-isolated-fake-compatibility"]["steps"]
    install = next(
        step
        for step in steps
        if step["name"] == "Install isolated Pydantic AI PoC dependencies"
    )
    test_step = next(
        step for step in steps if step["name"] == "Run isolated Fake compatibility contracts"
    )

    assert "requirements-pydantic-ai-poc.lock.txt" in install["run"]
    assert "import pydantic_ai" in install["run"]
    assert "tests/test_pydantic_ai_poc.py" in test_step["run"]
    assert "tests/test_pydantic_runtime.py" in test_step["run"]
    assert "tests/test_provider_smoke_harness.py" in test_step["run"]
    assert "tests/test_live_provider_smoke_adapter.py" in test_step["run"]
    assert "tests/test_evaluation_foundation.py" in test_step["run"]
