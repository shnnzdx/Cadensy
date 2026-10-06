"""Formal PR-04D3 Legacy versus Pydantic A/B evaluation runner.

This module deliberately contains no evaluation semantics.  It runs the
already-frozen symmetric harness for every frozen case and passes the
sanitized ``HarnessObservation.as_grade_input()`` straight to the frozen A/B
grader.  It is isolated-Pydantic-only, like the symmetric harness it imports.
"""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base

from .graders import AB_GRADER_VERSION, grade_ab_case
from .runner import (
    _canonical_dataset_sha256,
    _git_commit,
    _require_local_disposable_database,
    _synthetic_fixture,
    load_chat_change_preview_dataset,
)
from .symmetric_harness import HarnessObservation, run_single_symmetric_case


EXPERIMENT_VERSION = "pr04d3-frozen-formal-ab-v1"
FROZEN_DATASET_VERSION = "chat-change-preview-v1"
FROZEN_DATASET_SHA256 = "e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f"
FROZEN_CASE_IDS = (
    "explicit-time-notice",
    "ambiguous-time",
    "ambiguous-item",
    "booked-item-confirm",
    "provider-failure-fallback",
    "tool-failure-fallback",
    "privacy-injection",
    "cross-trip-denied",
)
RUNTIMES = ("legacy", "pydantic")


def verify_frozen_dataset() -> dict[str, Any]:
    """Load Dataset V1 only when its identity and exact case set are intact."""

    dataset = load_chat_change_preview_dataset()
    if dataset.get("dataset_version") != FROZEN_DATASET_VERSION:
        raise RuntimeError("PR-04D3 requires the frozen chat-change-preview-v1 dataset")
    if _canonical_dataset_sha256() != FROZEN_DATASET_SHA256:
        raise RuntimeError("PR-04D3 frozen dataset SHA-256 mismatch")
    case_ids = tuple(case.get("id") for case in dataset.get("cases", ()))
    if case_ids != FROZEN_CASE_IDS:
        raise RuntimeError("PR-04D3 requires the frozen eight-case dataset in canonical order")
    return dataset


def _serialize_observation(observation: HarnessObservation) -> dict[str, object]:
    """Serialize only the harness's sanitized observation evidence."""

    serialized = observation.as_grade_input()
    serialized["provider_events"] = list(observation.provider_events)
    return serialized


def run_formal_ab_evaluation(db: Session) -> dict[str, object]:
    """Generate exactly 16 unmodified A/B observations and grader outputs."""

    dataset = verify_frozen_dataset()
    runtime_results: dict[str, list[dict[str, object]]] = {runtime: [] for runtime in RUNTIMES}
    with _synthetic_fixture(db) as fixture:
        for runtime in RUNTIMES:
            for case in dataset["cases"]:
                observation = run_single_symmetric_case(
                    db,
                    fixture=fixture,
                    case=case,
                    runtime_name=runtime,
                )
                grade_input = observation.as_grade_input()
                runtime_results[runtime].append(
                    {
                        "case_id": case["id"],
                        "observation": _serialize_observation(observation),
                        "grader": grade_ab_case(case, grade_input),
                    }
                )
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "git_commit": _git_commit(),
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": FROZEN_DATASET_SHA256,
        "grader_version": AB_GRADER_VERSION,
        "runtime_results": runtime_results,
    }


def write_formal_ab_results(report: dict[str, object], *, output_path: Path) -> Path:
    """Write one deterministic, machine-readable D3 artifact."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output_path


def main() -> None:
    """Run the formal evaluation against an explicitly local test database."""

    parser = ArgumentParser(description="Run the frozen PR-04D3 formal A/B evaluation")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit("TEST_DATABASE_URL is required for the formal A/B runner")
    _require_local_disposable_database(database_url)
    engine = create_engine(database_url, future=True, connect_args={"client_encoding": "utf8"})
    try:
        Base.metadata.create_all(engine)
        with Session(engine, future=True) as db:
            report = run_formal_ab_evaluation(db)
        write_formal_ab_results(report, output_path=args.output)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
