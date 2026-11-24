"""Option C invite HMAC keyring and recoverable token.

The keyring is read from ``CADENSY_INVITE_HMAC_KEY`` once per process. A
missing or malformed document is a missing keyring: HMAC mint and re-derive
fail closed later. Import and process startup do not raise.

Never log key material or tokens.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass


_PREFIX = b"cadensy.invite.hmac.v1"
_MIN_KEY_BYTES = 32
_MAX_KEY_ID = 2**32 - 1
_ENV_NAME = "CADENSY_INVITE_HMAC_KEY"

_cached: "Keyring | None" = None
_loaded = False


@dataclass(frozen=True)
class Keyring:
    active_key_id: int
    keys: dict[int, bytes]


class InviteHmacUnavailable(Exception):
    """HMAC mint or re-derive cannot proceed. Callers map this to HTTP 503."""


def reset_invite_hmac_keyring_cache() -> None:
    """Test hook. Production reloads only on process restart."""

    global _cached, _loaded
    _cached = None
    _loaded = False


def get_invite_keyring() -> Keyring | None:
    global _cached, _loaded
    if not _loaded:
        _cached = _parse_keyring(os.environ.get(_ENV_NAME))
        _loaded = True
    return _cached


def require_invite_keyring() -> Keyring:
    keyring = get_invite_keyring()
    if keyring is None:
        raise InviteHmacUnavailable("invite_hmac_unavailable")
    return keyring


def mint_invite_token(keyring: Keyring, invite_id: str, trip_id: str) -> str:
    return derive_invite_token(keyring, keyring.active_key_id, invite_id, trip_id)


def derive_invite_token(keyring: Keyring, key_id: int, invite_id: str, trip_id: str) -> str:
    raw_key = keyring.keys.get(key_id)
    if raw_key is None or len(raw_key) < _MIN_KEY_BYTES:
        raise InviteHmacUnavailable("invite_hmac_unavailable")
    digest = hmac.new(raw_key, _message(key_id, invite_id, trip_id), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches_hash(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(token_hash(token), stored_hash)


def _message(key_id: int, invite_id: str, trip_id: str) -> bytes:
    invite_bytes = invite_id.encode("utf-8")
    trip_bytes = trip_id.encode("utf-8")
    return (
        _PREFIX
        + _be32(key_id)
        + _be32(len(invite_bytes))
        + invite_bytes
        + _be32(len(trip_bytes))
        + trip_bytes
    )


def _be32(value: int) -> bytes:
    return int(value).to_bytes(4, "big", signed=False)


def _parse_keyring(raw: str | None) -> Keyring | None:
    if raw is None or not raw.strip():
        return None
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict) or document.get("v") != 1 or isinstance(document.get("v"), bool):
        return None
    active_raw = document.get("active_key_id")
    if isinstance(active_raw, bool) or not isinstance(active_raw, int):
        return None
    if not 1 <= active_raw <= _MAX_KEY_ID:
        return None
    keys_raw = document.get("keys")
    if not isinstance(keys_raw, dict):
        return None
    keys: dict[int, bytes] = {}
    for name, encoded in keys_raw.items():
        key_id = _parse_key_id(name)
        decoded = _decode_key(encoded)
        if key_id is None or decoded is None:
            if key_id == active_raw:
                return None
            continue
        if len(decoded) < _MIN_KEY_BYTES:
            if key_id == active_raw:
                return None
            continue
        keys[key_id] = decoded
    active = keys.get(active_raw)
    if active is None or len(active) < _MIN_KEY_BYTES:
        return None
    return Keyring(active_key_id=active_raw, keys=keys)


def _parse_key_id(name: object) -> int | None:
    if not isinstance(name, str) or not name.isdecimal():
        return None
    if str(int(name)) != name:
        return None
    value = int(name)
    if not 1 <= value <= _MAX_KEY_ID:
        return None
    return value


def _decode_key(encoded: object) -> bytes | None:
    if not isinstance(encoded, str) or not encoded or "=" in encoded:
        return None
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, binascii.Error):
        return None
