"""Focused contract tests for the thin PR-04D3 formal evaluation runner."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

pytest.importorskip(
    "pydantic_ai",
    reason="PR-04D3 formal runner requires the isolated Pydantic lockfile",
)

from evals.formal_ab_runner import (
    FROZEN_CASE_IDS,
    FROZEN_DATASET_SHA256,
    RUNTIMES,
    run_formal_ab_evaluation,
    write_formal_ab_results,
)
from evals.graders import grade_ab_case
from evals.runner import PRIVATE_FIXTURE_PHRASE, load_chat_change_preview_dataset


def test_formal_runner_serializes_each_frozen_case_once_per_runtime(db: Session, tmp_path):
    report = run_formal_ab_evaluation(db)

    assert set(report["runtime_results"]) == set(RUNTIMES)
    records = [
        record
        for runtime in RUNTIMES
        for record in report["runtime_results"][runtime]
    ]
    assert len(records) == 16
    for runtime in RUNTIMES:
        assert tuple(record["case_id"] for record in report["runtime_results"][runtime]) == FROZEN_CASE_IDS
        assert any(
            record["observation"]["runtime_executed"]
            for record in report["runtime_results"][runtime]
        )
    assert all(record["observation"]["network_attempts"] == 0 for record in records)
    assert all(record["observation"]["runtime"] in RUNTIMES for record in records)
    assert report["dataset_sha256"] == FROZEN_DATASET_SHA256

    artifact = write_formal_ab_results(report, output_path=tmp_path / "formal.json")
    serialized = artifact.read_text(encoding="utf-8")
    for sensitive_marker in (
        PRIVATE_FIXTURE_PHRASE,
        "Authorization",
        "DEEPSEEK_API_KEY",
        "GEOAPIFY_API_KEY",
    ):
        assert sensitive_marker not in serialized
    assert json.loads(serialized) == report


def test_formal_runner_preserves_frozen_grader_outputs_without_scores(db: Session):
    report = run_formal_ab_evaluation(db)
    cases = {case["id"]: case for case in load_chat_change_preview_dataset()["cases"]}

    for runtime in RUNTIMES:
        for record in report["runtime_results"][runtime]:
            assert record["grader"] == grade_ab_case(
                cases[record["case_id"]], record["observation"]
            )

    def keys(value):
        if isinstance(value, dict):
            yield from value
            for child in value.values():
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    forbidden = {"passed", "score", "winner_score", "weighted_score"}
    assert forbidden.isdisjoint(keys(report))
