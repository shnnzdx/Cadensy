"""Dormant canonical request-HMAC primitives for PR-05B.1.

There is intentionally no HTTP header parsing, database lookup, replay, or
locking here. Later mutation commands may use these pure functions only after
their operation-specific payload contract and authorization protocol are live.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import hmac
import json
import math
import unicodedata
from types import MappingProxyType
from typing import Any


class CanonicalPayloadError(ValueError):
    """Raised when a value cannot be represented by the JSON fingerprint contract."""


@dataclass(frozen=True)
class RequestHmac:
    """A versioned HMAC result suitable for later storage in the ledger."""

    digest: str
    fingerprint_version: int
    hmac_key_version: str


def canonical_request_json(
    operation_code: str,
    payload: Mapping[str, Any],
    *,
    fingerprint_version: int,
) -> bytes:
    """Return a deterministic, versioned, operation-bound JSON representation.

    The accepted payload surface is intentionally JSON-native: mappings with
    string keys, ordered sequences, strings, null, booleans, finite numbers,
    integers, and validated API ``date`` values. Integer-valued finite floats
    share their JSON-number form with integers, while booleans remain distinct
    from integers. Unsupported values fail closed instead of gaining an
    accidental string representation.
    """

    normalized_operation = _normalized_string(operation_code, field="operation_code")
    if not normalized_operation:
        raise CanonicalPayloadError("operation_code must not be empty")
    normalized_fingerprint_version = _fingerprint_version(fingerprint_version)
    normalized_payload = _normalize_json_value(payload)
    return json.dumps(
        {
            "fingerprint_version": normalized_fingerprint_version,
            "operation_code": normalized_operation,
            "command": normalized_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_request_hmac(
    operation_code: str,
    payload: Mapping[str, Any],
    *,
    key: bytes,
    fingerprint_version: int,
    hmac_key_version: str,
) -> RequestHmac:
    """Create a SHA-256 HMAC without owning secret storage or rotation policy."""

    if not key:
        raise ValueError("HMAC key must not be empty")
    normalized_fingerprint_version = _fingerprint_version(fingerprint_version)
    normalized_key_version = _normalized_string(hmac_key_version, field="hmac_key_version")
    if not normalized_key_version:
        raise ValueError("hmac_key_version must not be empty")
    digest = hmac.new(
        key,
        canonical_request_json(
            operation_code,
            payload,
            fingerprint_version=normalized_fingerprint_version,
        ),
        hashlib.sha256,
    ).hexdigest()
    return RequestHmac(
        digest=digest,
        fingerprint_version=normalized_fingerprint_version,
        hmac_key_version=normalized_key_version,
    )


def canonical_payload_snapshot(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a deeply immutable view of B.1-normalized command fields.

    Durable command code uses this exact canonicalizer-derived projection for both
    fingerprinting and its local callback, so caller-owned nested containers cannot
    diverge from the signed semantic input. This is not a second format.
    """

    if not isinstance(payload, Mapping):
        raise CanonicalPayloadError("command payload must be a mapping")
    normalized = _normalize_json_value(payload)
    assert isinstance(normalized, dict)
    frozen = _freeze_json_value(normalized)
    assert isinstance(frozen, Mapping)
    return frozen


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalPayloadError("floating-point values must be finite")
        # API numeric fields are validated as JSON numbers. For those fields,
        # 15 and 15.0 describe the same value. Keep booleans separate above.
        if value == 0 or value.is_integer():
            return int(value)
        return value
    if isinstance(value, datetime):
        raise CanonicalPayloadError("datetime values are not accepted by this date-only contract")
    if isinstance(value, date):
        # Pydantic validates the API's date fields to ``date`` before a future
        # command handler invokes this helper. ISO-8601 is their public form.
        return value.isoformat()
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalPayloadError("mapping keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise CanonicalPayloadError("mapping has duplicate normalized keys")
            normalized[normalized_key] = _normalize_json_value(child)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_normalize_json_value(child) for child in value]
    raise CanonicalPayloadError(f"unsupported canonical payload value: {type(value).__name__}")


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json_value(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(child) for child in value)
    return value


def _normalized_string(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise CanonicalPayloadError(f"{field} must be a string")
    return unicodedata.normalize("NFC", value)


def _fingerprint_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("fingerprint_version must be a positive integer")
    return value
