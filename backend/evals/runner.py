"""Framework-neutral, deterministic Chat Change Preview evaluation runner.

The runner normalizes one Legacy Custom Runtime observation per Golden Dataset
case. A future Pydantic AI adapter may provide the same observation shape, but
this module neither imports nor installs Pydantic AI.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from argparse import ArgumentParser
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.agents import base
from app.agents.legacy_runtime import LegacyChatAgentRuntime
from app.agents.runtime_contract import RuntimeFailure
from app.db.models import (
    Base,
    ChangeProposal,
    DecisionRound,
    MemberConstraint,
    MemberConstraintPrivate,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
    User,
    Vote,
)
from app.domain.chat import service as chat_service

from .graders import GRADER_VERSION, grade_case


EVAL_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = EVAL_ROOT.parent
DATASET_PATH = EVAL_ROOT / "datasets" / "chat_change_preview_v1.json"
RUNTIME_VERSION = "legacy_custom_runtime"
PRIVATE_FIXTURE_PHRASE = "sensitive fixture phrase"


@dataclass
class _ExecutionEvidence:
    """Evaluation-only trace facts captured from an actual case execution."""

    provider_prompts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    clarification_source: str | None = None
    safe_degradation: bool = False
    access_denied: bool = False
    provider_exception_observed: bool = False
    runtime_failure: RuntimeFailure | None = None


def load_chat_change_preview_dataset() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def run_legacy_chat_baseline(
    db: Session, *, dataset: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run every Golden case using a Fake Provider and deterministic fixtures."""
    dataset = dataset or load_chat_change_preview_dataset()
    started = time.perf_counter()
    with _synthetic_fixture(db) as fixture:
        fixture_visibility = _verify_fixture_visibility(db, fixture)
        case_results = [
            _run_legacy_case(db, fixture, case)
            for case in dataset["cases"]
        ]
    passed_cases = sum(result["passed"] for result in case_results)
    safety_violations = sum(
        len(result["grader"]["safety_violations"])
        for result in case_results
    )
    return {
        "manifest": {
            **_manifest(dataset),
            "fixture_visibility": fixture_visibility,
        },
        "summary": {
            "total_cases": len(case_results),
            "passed_cases": passed_cases,
            "failed_cases": len(case_results) - passed_cases,
            "safety_violations": safety_violations,
        },
        "cases": case_results,
        "fixture_visibility": fixture_visibility,
        "run_latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def write_evaluation_report(report: dict[str, Any], *, output_dir: Path) -> dict[str, Path]:
    """Write stable machine-readable evaluation artifacts for CI or reviewers."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    summary_path = output_dir / "summary.json"
    cases_path = output_dir / "cases.jsonl"
    _write_json(manifest_path, report["manifest"])
    _write_json(summary_path, report["summary"])
    with cases_path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in report["cases"]:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    return {"manifest": manifest_path, "summary": summary_path, "cases": cases_path}


def _run_legacy_case(
    db: Session, fixture: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    evidence = _ExecutionEvidence()
    started = time.perf_counter()
    before = _durable_counts(db)
    provider = _fake_provider(case, fixture, evidence)
    item_key = case["input"].get("item_key")
    membership = fixture["memberships"][case["fixture_preconditions"]["actor"]]
    result = None
    original_runtime_run = LegacyChatAgentRuntime.run
    original_degraded_reply = chat_service._degraded_reply
    original_plain_clarification = chat_service._plain_text_clarification_reply
    original_ambiguous_reply = chat_service._ambiguous_item_reference_reply
    original_missing_reply = chat_service._missing_item_reference_reply
    # Bind only this evaluation's worker factory to the caller-owned disposable
    # engine. This remains an independent Session from ``db`` and avoids
    # inheriting the process's unrelated runtime DATABASE_URL in CLI mode.
    worker_session_factory = _independent_session_factory(db)

    def record_runtime_result(runtime, *args, **kwargs):
        runtime_result = original_runtime_run(runtime, *args, **kwargs)
        evidence.runtime_failure = runtime_result.observation.failure
        return runtime_result

    def record_degraded_reply(*args, **kwargs):
        evidence.safe_degradation = True
        return original_degraded_reply(*args, **kwargs)

    def record_plain_clarification(*args, **kwargs):
        reply = original_plain_clarification(*args, **kwargs)
        if reply is not None:
            evidence.clarification_source = "deterministic_missing_slot"
        return reply

    def record_ambiguous_reply(*args, **kwargs):
        evidence.clarification_source = "deterministic_ambiguous_item"
        return original_ambiguous_reply(*args, **kwargs)

    def record_missing_reply(*args, **kwargs):
        evidence.clarification_source = "deterministic_missing_item"
        return original_missing_reply(*args, **kwargs)
    try:
        with (
            patch.object(base, "call_agent", provider),
            patch.object(LegacyChatAgentRuntime, "run", record_runtime_result),
            patch.object(chat_service, "SessionLocal", worker_session_factory),
            patch.object(chat_service, "_degraded_reply", record_degraded_reply),
            patch.object(
                chat_service, "_plain_text_clarification_reply", record_plain_clarification
            ),
            patch.object(chat_service, "_ambiguous_item_reference_reply", record_ambiguous_reply),
            patch.object(chat_service, "_missing_item_reference_reply", record_missing_reply),
        ):
            result = chat_service.respond_to_trip_chat(
                db,
                trip_id=fixture["trip"].id,
                membership=membership,
                message=case["input"]["message"],
                item_id=(fixture["items"][item_key].id if item_key else None),
            )
    except chat_service.ChatAccessDenied:
        evidence.access_denied = True

    observed = _extract_observed_result(
        result=result,
        evidence=evidence,
        item_keys=fixture["item_keys"],
        durable_side_effects=_durable_counts(db) != before,
        forbidden_values=(
            PRIVATE_FIXTURE_PHRASE,
            *(membership.id for membership in fixture["memberships"].values()),
        ),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    grader = grade_case(case, observed)
    return {
        "case_id": case["id"],
        "passed": grader["passed"],
        "observed": _public_observation(observed),
        "metrics": {
            "latency_ms": observed["latency_ms"],
            "token_usage": observed["token_usage"],
            "cost": None,
            "deadline": {
                "request_deadline_seconds": chat_service.CHAT_AGENT_TIMEOUT_SECONDS,
                "provider_configured_budget_seconds": chat_service.CHAT_AGENT_PROVIDER_TIMEOUT_SECONDS,
                # The Fake Provider replaces the client call, so no invocation
                # budget was consumed or invented for this baseline report.
                "provider_invocation_budget_seconds": None,
            },
        },
        "grader": grader,
    }


def _fake_provider(
    case: dict[str, Any],
    fixture: dict[str, Any],
    evidence: _ExecutionEvidence,
):
    mode = case["fake_provider"]

    def call_agent(**kwargs: Any) -> base.AgentRunResult:
        evidence.provider_prompts.append(str(kwargs.get("user") or ""))
        if mode == "must_not_run":
            raise AssertionError(f"{case['id']} should finish before Agent execution")
        if mode == "raise_provider_failure":
            evidence.provider_exception_observed = True
            raise RuntimeError("synthetic provider failure")
        if mode == "tool_failure":
            return base.AgentRunResult(
                content="",
                trace_id="eval-tool-failure",
                rounds=(),
                tool_results=(),
                total_tokens=0,
                total_elapsed_ms=0.0,
                stopped_reason="synthetic_tool_failure",
            )
        if mode == "safe_privacy_reply":
            return base.AgentRunResult(
                content="I can help with the current plan, but I cannot disclose private member details.",
                trace_id="eval-privacy",
                rounds=(),
                tool_results=(),
                total_tokens=0,
                total_elapsed_ms=0.0,
            )

        item_key = case["input"]["item_key"]
        start_hour = 15.5 if mode == "notice_preview" else 18.0
        tool_call = {
            "tool": "classify_change",
            "arguments": {"item_id": item_key, "new_start_hour": start_hour},
            "output": {
                "item": {"id": fixture["items"][item_key].id},
                "proposed_patch": {"start_hour": start_hour},
            },
            "guard_rejected": False,
        }
        evidence.tool_calls.append(
            {
                "name": "classify_change",
                "arguments": {"item_id": item_key, "new_start_hour": start_hour},
            }
        )
        return base.AgentRunResult(
            content="I can prepare this change; the Current Plan has not changed.",
            trace_id=f"eval-{case['id']}",
            rounds=(),
            tool_results=(tool_call,),
            total_tokens=0,
            total_elapsed_ms=0.0,
        )

    return call_agent


def _extract_observed_result(
    *,
    result: Any,
    evidence: _ExecutionEvidence,
    item_keys: dict[str, str],
    durable_side_effects: bool,
    forbidden_values: tuple[str, ...],
    latency_ms: float,
) -> dict[str, Any]:
    """Normalize only actual application/runtime behavior into observation.

    The function deliberately receives no case or Golden label. This makes the
    observer independent of business expectations; the grader alone compares
    this observation to those expectations.
    """

    reply = result.reply if result is not None else ""
    proposed = result.proposed_change if result is not None else None
    visible_text = "\n".join(
        [reply, *evidence.provider_prompts, json.dumps(evidence.tool_calls, sort_keys=True)]
    )
    private_data_leaked = any(value in visible_text for value in forbidden_values if value)
    if evidence.access_denied:
        output_kind = "access_denied"
    elif proposed is not None:
        output_kind = "change_preview"
    elif evidence.safe_degradation:
        output_kind = "safe_degraded"
    elif evidence.clarification_source is not None:
        output_kind = "clarification"
    else:
        output_kind = "reply_only"

    return {
        "output_kind": output_kind,
        "reply": reply,
        "proposed_item_key": item_keys.get(proposed.item_id) if proposed is not None else None,
        "decision_path": proposed.verdict.path.value if proposed is not None else None,
        "tool_calls": evidence.tool_calls,
        "provider_prompts": evidence.provider_prompts,
        "failure_taxonomy": _failure_taxonomy_from_execution(evidence),
        "latency_ms": latency_ms,
        # Fake provider results intentionally contain no metered usage. Null
        # is more truthful than treating a fixture response as zero cost.
        "token_usage": None,
        "durable_side_effects": durable_side_effects,
        "forbidden_values": list(forbidden_values),
        "safety": {
            "durable_side_effects": durable_side_effects,
            "private_data_leaked": private_data_leaked,
            "agent_executed": bool(evidence.provider_prompts),
        },
    }


def _failure_taxonomy_from_execution(evidence: _ExecutionEvidence) -> str | None:
    """Classify captured runtime/provider events, never a case expectation."""

    if evidence.access_denied:
        return "cross_trip_denied"
    if evidence.provider_exception_observed:
        return "provider_failure"
    failure = evidence.runtime_failure
    if failure is None:
        return None
    technical = failure.technical_kind.casefold()
    if "tool" in technical or failure.normalized_kind == "tool_timeout":
        return "tool_failure"
    if "provider" in technical or failure.normalized_kind == "provider_timeout":
        return "provider_failure"
    return failure.normalized_kind


@contextmanager
def _synthetic_fixture(db: Session) -> Iterator[dict[str, Any]]:
    """Create fixture rows visible to a separate worker-owned Session.

    A nested transaction on ``db`` cannot be observed by the independent
    Session ownership required by PR-01C. This helper therefore commits only
    uniquely identified synthetic rows through a separate local-test session,
    then deletes those exact rows in dependency order. It never drops schema,
    recreates a database, or removes rows outside this fixture.
    """
    session_factory = _independent_session_factory(db)
    fixture_db = session_factory()
    fixture_ids: dict[str, tuple[str, ...]] = {}
    try:
        organizer_user = User(name="Eval Organizer", email="eval-organizer@example.test")
        participant_user = User(name="Eval Participant", email="eval-participant@example.test")
        foreign_user = User(name="Eval Foreign", email="eval-foreign@example.test")
        fixture_db.add_all([organizer_user, participant_user, foreign_user])
        fixture_db.flush()
        trip = Trip(
            name="Evaluation Trip",
            destination="Synthetic City",
            created_by_user_id=organizer_user.id,
        )
        foreign_trip = Trip(
            name="Foreign Evaluation Trip",
            destination="Elsewhere",
            created_by_user_id=foreign_user.id,
        )
        fixture_db.add_all([trip, foreign_trip])
        fixture_db.flush()
        organizer = TripMembership(trip_id=trip.id, user_id=organizer_user.id, role="organizer")
        participant = TripMembership(trip_id=trip.id, user_id=participant_user.id, role="participant")
        foreign_member = TripMembership(trip_id=foreign_trip.id, user_id=foreign_user.id, role="participant")
        fixture_db.add_all([organizer, participant, foreign_member])
        fixture_db.flush()
        constraint = MemberConstraint(
            trip_membership_id=participant.id,
            kind="time_window",
            importance="required",
            params={"earliest_hour": 9.0},
        )
        fixture_db.add(constraint)
        fixture_db.flush()
        private_constraint = MemberConstraintPrivate(
            constraint_id=constraint.id,
            original_text=PRIVATE_FIXTURE_PHRASE,
            visibility="planning_only",
        )
        plan = Plan(trip_id=trip.id)
        foreign_plan = Plan(trip_id=foreign_trip.id)
        fixture_db.add_all([private_constraint, plan, foreign_plan])
        fixture_db.flush()
        art = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=date(2026, 8, 19),
            start_hour=14.0,
            duration_min=150,
            title="Art Institute of Chicago",
            place="Michigan Avenue",
            settledness="loose",
            tags=["museum"],
        )
        dinner = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=date(2026, 8, 19),
            start_hour=19.0,
            duration_min=120,
            title="Birthday dinner",
            place="River North",
            settledness="booked",
            is_meal=True,
        )
        foreign_item = PlanItem(
            plan_id=foreign_plan.id,
            day_index=1,
            day_date=date(2026, 8, 19),
            start_hour=10.0,
            duration_min=60,
            title="Foreign Fixture Only",
            place="Elsewhere",
            settledness="loose",
        )
        fixture_db.add_all([art, dinner, foreign_item])
        fixture_db.flush()
        fixture_ids = {
            "users": (organizer_user.id, participant_user.id, foreign_user.id),
            "trips": (trip.id, foreign_trip.id),
            "memberships": (organizer.id, participant.id, foreign_member.id),
            "constraints": (constraint.id,),
            "private_constraints": (private_constraint.constraint_id,),
            "plans": (plan.id, foreign_plan.id),
            "items": (art.id, dinner.id, foreign_item.id),
        }
        fixture_db.commit()
        yield {
            "trip": trip,
            "items": {"art": art, "dinner": dinner},
            "item_keys": {art.id: "art", dinner.id: "dinner"},
            "foreign": {"trip": foreign_trip, "item": foreign_item},
            "memberships": {
                "organizer": organizer,
                "participant": participant,
                "foreign_member": foreign_member,
            },
        }
    finally:
        fixture_db.close()
        if fixture_ids:
            _cleanup_synthetic_fixture(session_factory, fixture_ids)


def _independent_session_factory(db: Session) -> sessionmaker[Session]:
    """Return a new-connection Session factory bound to the caller's test engine."""
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    if not isinstance(engine, Engine):
        raise RuntimeError("Evaluation fixture requires an SQLAlchemy Engine binding")
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _cleanup_synthetic_fixture(
    session_factory: sessionmaker[Session], fixture_ids: dict[str, tuple[str, ...]]
) -> None:
    """Delete only fixture-owned rows; leave every unrelated test row intact."""
    with session_factory() as cleanup_db:
        for constraint_id in fixture_ids["private_constraints"]:
            row = cleanup_db.get(MemberConstraintPrivate, constraint_id)
            if row is not None:
                cleanup_db.delete(row)
        for model, ids in (
            (MemberConstraint, fixture_ids["constraints"]),
            (PlanItem, fixture_ids["items"]),
            (Plan, fixture_ids["plans"]),
            (TripMembership, fixture_ids["memberships"]),
            (Trip, fixture_ids["trips"]),
            (User, fixture_ids["users"]),
        ):
            for identifier in ids:
                row = cleanup_db.get(model, identifier)
                if row is not None:
                    cleanup_db.delete(row)
        cleanup_db.commit()


def _verify_fixture_visibility(db: Session, fixture: dict[str, Any]) -> dict[str, Any]:
    """Exercise real trip-scoped read tools through a distinct worker Session."""
    from app.agents.tools import build_read_only_trip_tools

    session_factory = _independent_session_factory(db)
    with session_factory() as worker_db:
        tools = {tool.name: tool for tool in build_read_only_trip_tools(
            worker_db,
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
        )}
        plan = tools["get_current_plan"].handler(day="all")
        facts = tools["get_trip_facts"].handler()
        classification = tools["classify_change"].handler(
            item_title=fixture["items"]["art"].title,
            item_id=fixture["items"]["art"].id,
            new_start_hour=15.5,
        )

    return {
        "worker_session_is_independent": True,
        "tool_invocations": ["get_current_plan", "get_trip_facts", "classify_change"],
        "destination": facts["destination"],
        "member_count": facts["member_count"],
        "plan_item_titles": [
            item["title"] for day in plan["days"] for item in day["items"]
        ],
        "classification_path": classification["classification"]["path"],
    }


def _durable_counts(db: Session) -> tuple[int, int, int, int]:
    return (
        len(db.scalars(select(PlanItem)).all()),
        len(db.scalars(select(ChangeProposal)).all()),
        len(db.scalars(select(DecisionRound)).all()),
        len(db.scalars(select(Vote)).all()),
    )


def _manifest(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime": {"name": RUNTIME_VERSION, "version": "source", "provider_mode": "fake"},
        "git_commit": _git_commit(),
        "dataset_version": dataset["dataset_version"],
        "dataset_hash": _canonical_dataset_sha256(),
        "dependency_configuration": _dependency_configuration(),
        "model_configuration": {"provider": "fake", "model": None, "thinking_mode": None},
        "prompt_version": dataset["prompt_version"],
        "tool_contract_version": dataset["tool_contract_version"],
        "grader_version": GRADER_VERSION,
        "deadline_contract": {
            "request_timeout_seconds": chat_service.CHAT_AGENT_TIMEOUT_SECONDS,
            "provider_timeout_seconds": chat_service.CHAT_AGENT_PROVIDER_TIMEOUT_SECONDS,
            "tool_timeout_seconds": chat_service.CHAT_AGENT_TOOL_TIMEOUT_SECONDS,
        },
    }


def _canonical_dataset_sha256() -> str:
    """Hash frozen dataset content independent of Git checkout line endings.

    The approved V1 hash is the canonical LF Git-object content hash.  A
    Windows CRLF checkout must not look like a new Golden Dataset version.
    """
    canonical_bytes = DATASET_PATH.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical_bytes).hexdigest()


def _dependency_configuration() -> dict[str, str]:
    """Record the selected, repository-owned lock without exposing secrets."""
    lockfile_name = os.getenv(
        "CADENSY_EVAL_DEPENDENCY_LOCK", "requirements-legacy-regression.lock.txt"
    )
    lockfile_path = (BACKEND_ROOT / lockfile_name).resolve()
    try:
        lockfile_path.relative_to(BACKEND_ROOT)
    except ValueError as exc:
        raise RuntimeError("Evaluation dependency lock must be inside backend/") from exc
    if not lockfile_path.is_file():
        raise RuntimeError(f"Evaluation dependency lock does not exist: {lockfile_name}")
    return {
        "lockfile": lockfile_path.name,
        "lockfile_sha256": hashlib.sha256(lockfile_path.read_bytes()).hexdigest(),
        "python_version": ".".join(map(str, sys.version_info[:3])),
    }


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=BACKEND_ROOT.parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _public_observation(observed: dict[str, Any]) -> dict[str, Any]:
    return {
        key: observed[key]
        for key in (
            "output_kind",
            "proposed_item_key",
            "decision_path",
            "tool_calls",
            "failure_taxonomy",
            "durable_side_effects",
            "safety",
        )
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Run a Fake-Provider baseline only against an explicitly local test DB."""
    parser = ArgumentParser(description="Run the deterministic Legacy Chat baseline")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Explicit non-archived directory receiving manifest.json, summary.json, "
            "and cases.jsonl"
        ),
    )
    args = parser.parse_args()
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit("TEST_DATABASE_URL is required for the evaluation runner")
    _require_local_disposable_database(database_url)
    engine = create_engine(database_url, future=True, connect_args={"client_encoding": "utf8"})
    try:
        # The runner owns only synthetic fixtures and rolls their rows back.
        # Creating tables is allowed solely because the URL has passed the
        # local/disposable guard above; it is never a production migration path.
        Base.metadata.create_all(engine)
        with Session(engine, future=True) as db:
            report = run_legacy_chat_baseline(db)
        # This is deliberately recorded at the CLI boundary.  The runner
        # consumes the explicit TEST_DATABASE_URL above; it never binds its
        # work to DATABASE_URL or a Session inherited from pytest.
        report["manifest"]["database_isolation"] = _database_isolation_metadata(
            database_url
        )
        files = write_evaluation_report(report, output_dir=args.output)
    finally:
        engine.dispose()
    print(json.dumps({"summary": report["summary"], "files": {key: str(value) for key, value in files.items()}}, indent=2))


def _require_local_disposable_database(database_url: str) -> None:
    url = make_url(database_url)
    database = (url.database or "").casefold()
    host = (url.host or "").casefold()
    test_named = database.startswith("test_") or "_test" in database
    if url.get_backend_name() != "postgresql" or host not in {"localhost", "127.0.0.1", "::1"} or not test_named:
        raise SystemExit("Evaluation runner requires an explicitly local, disposable PostgreSQL TEST_DATABASE_URL")


def _database_isolation_metadata(database_url: str) -> dict[str, str | bool]:
    """Return non-secret evidence of the standalone runner's database binding."""
    url = make_url(database_url)
    return {
        "source": "TEST_DATABASE_URL",
        "backend": url.get_backend_name(),
        "host": url.host or "",
        "database": url.database or "",
        "runtime_database_url_used": False,
    }


if __name__ == "__main__":
    main()
