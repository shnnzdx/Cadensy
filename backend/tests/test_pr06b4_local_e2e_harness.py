"""Offline safety contracts for the disposable Chromium acceptance harness."""

from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "backend" / "scripts" / "run_pr06b4_local_e2e.ps1"
FIXTURE = REPOSITORY_ROOT / "backend" / "scripts" / "pr06b4_e2e_fixture.py"
BROWSER = REPOSITORY_ROOT / "frontend" / "tests" / "pr06b4-local-browser-e2e.mjs"


def test_browser_runner_never_overwrites_an_existing_database_and_cleans_only_its_own():
    source = RUNNER.read_text(encoding="utf-8")

    assert "DROP DATABASE IF EXISTS" not in source
    assert "SELECT 1 FROM pg_database WHERE datname = %s" in source
    assert "Disposable database already exists" in source
    assert "$databaseCreated = $true" in source
    assert "if ($databaseCreated)" in source


def test_browser_runner_uses_per_run_temporary_fixture_and_control_files():
    source = RUNNER.read_text(encoding="utf-8")

    assert "[System.IO.Path]::GetTempPath()" in source
    assert "[guid]::NewGuid()" in source
    assert "Remove-Item -LiteralPath $runRoot -Recurse -Force" in source
    assert "$previousEnvironment = @{}" in source
    assert "$previousEnvironment.ContainsKey($name)" in source
    assert "$cleanupFailures" in source
    assert "Disposable database cleanup failed." in source


def test_fixture_seed_stdout_is_credential_free():
    source = FIXTURE.read_text(encoding="utf-8")

    assert "print(json.dumps(payload))" not in source
    assert 'print(json.dumps({"seeded": True' in source
    assert ".order_by(PlanItem.day_index, PlanItem.start_hour, PlanItem.id)" in source


def test_browser_acceptance_uses_direct_candidate_and_checks_current_then_stale_resolution():
    source = BROWSER.read_text(encoding="utf-8")

    assert "redactEvidenceRoute" in source
    assert "[redacted-invite-token]" in source
    assert "[E2E-DIRECT-CANDIDATE]" in source
    assert "currentResolutionBody.ready, true" in source
    assert "submissionsAfterRelogin, submissionsBeforeRecovery" in source
    assert "ownerFresh = await login" in source
    assert "J08-CURRENT" in source
    assert "J08-STALE" in source
