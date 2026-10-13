from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.domain.idempotency import (
    CanonicalPayloadError,
    canonical_request_hmac,
    canonical_request_json,
)


FIXTURE_HMAC_KEY = b"pr05b1-deterministic-fixture-key-only"


def test_canonical_hmac_is_deterministic_for_equivalent_mapping_order() -> None:
    first = canonical_request_hmac(
        "proposal.apply",
        {"item_id": "item-1", "option": {"label": "Move", "hour": 15}},
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )
    second = canonical_request_hmac(
        "proposal.apply",
        {"option": {"hour": 15, "label": "Move"}, "item_id": "item-1"},
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )

    assert first == second
    assert len(first.digest) == 64


def test_fingerprint_version_is_bound_to_the_canonical_hmac_envelope() -> None:
    payload = {"item_id": "item-1", "start_hour": 15}
    first = canonical_request_hmac(
        "plan.change.submit.v1",
        payload,
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )
    same_version = canonical_request_hmac(
        "plan.change.submit.v1",
        payload,
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )
    next_version = canonical_request_hmac(
        "plan.change.submit.v1",
        payload,
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=2,
        hmac_key_version="fixture-v1",
    )

    assert first.digest == same_version.digest
    assert first.digest != next_version.digest
    assert canonical_request_json(
        "plan.change.submit.v1", payload, fingerprint_version=1
    ) != canonical_request_json(
        "plan.change.submit.v1", payload, fingerprint_version=2
    )


def test_canonical_hmac_changes_for_different_semantic_payload_or_operation() -> None:
    baseline = canonical_request_hmac(
        "proposal.apply",
        {"item_id": "item-1", "option_id": "move-later"},
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )
    changed_payload = canonical_request_hmac(
        "proposal.apply",
        {"item_id": "item-1", "option_id": "keep"},
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )
    changed_operation = canonical_request_hmac(
        "proposal.dismiss",
        {"item_id": "item-1", "option_id": "move-later"},
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=1,
        hmac_key_version="fixture-v1",
    )

    assert baseline.digest != changed_payload.digest
    assert baseline.digest != changed_operation.digest


def test_hmac_result_retains_explicit_key_and_fingerprint_versions() -> None:
    result = canonical_request_hmac(
        "notice.create",
        {"plan_id": "plan-1", "label": "Updated"},
        key=FIXTURE_HMAC_KEY,
        fingerprint_version=2,
        hmac_key_version="rotation-2026-10",
    )

    assert result.fingerprint_version == 2
    assert result.hmac_key_version == "rotation-2026-10"


def test_canonical_json_rejects_unsupported_values_and_empty_operation() -> None:
    with pytest.raises(CanonicalPayloadError):
        canonical_request_json(
            "proposal.apply", {"when": object()}, fingerprint_version=1
        )
    with pytest.raises(CanonicalPayloadError):
        canonical_request_json("", {"item_id": "item-1"}, fingerprint_version=1)
    with pytest.raises(CanonicalPayloadError):
        canonical_request_json(
            "proposal.apply", {"when": datetime.now(timezone.utc)}, fingerprint_version=1
        )
    with pytest.raises(ValueError):
        canonical_request_json("proposal.apply", {}, fingerprint_version=True)


def test_canonical_json_normalizes_unicode_numbers_and_api_dates() -> None:
    composed = canonical_request_json(
        "plan.change.submit.v1",
        {"label": "Café", "start_hour": 15, "day_date": date(2026, 8, 15)},
        fingerprint_version=1,
    )
    decomposed = canonical_request_json(
        "plan.change.submit.v1",
        {"label": "Cafe\u0301", "start_hour": 15.0, "day_date": date(2026, 8, 15)},
        fingerprint_version=1,
    )
    negative_zero = canonical_request_json(
        "plan.change.submit.v1", {"start_hour": -0.0}, fingerprint_version=1
    )
    positive_zero = canonical_request_json(
        "plan.change.submit.v1", {"start_hour": 0.0}, fingerprint_version=1
    )

    assert composed == decomposed
    assert b"2026-08-15" in composed
    assert negative_zero == positive_zero


def test_canonical_json_preserves_semantic_type_and_shape_boundaries() -> None:
    operation = "plan.change.submit.v1"
    assert canonical_request_json(operation, {"remove": False}, fingerprint_version=1) != (
        canonical_request_json(operation, {"remove": 0}, fingerprint_version=1)
    )
    assert canonical_request_json(
        operation, {"options": ["first", "second"]}, fingerprint_version=1
    ) != canonical_request_json(
        operation, {"options": ["second", "first"]}, fingerprint_version=1
    )
    assert canonical_request_json(operation, {"local_title": None}, fingerprint_version=1) != (
        canonical_request_json(operation, {}, fingerprint_version=1)
    )
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalPayloadError):
            canonical_request_json(operation, {"start_hour": value}, fingerprint_version=1)
