"""Password login and bearer sessions.

Accounts identify a person. Permissions and trip roles still come from
TripMembership, so the same user can be organizer in one trip and participant
in another.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import AuthSession, GuestSession, TripMembership, User

SESSION_TTL = timedelta(days=14)
GUEST_SESSION_TTL = timedelta(days=7)
GUEST_TOKEN_PREFIX = "gst_"
HASH_ALGO = "pbkdf2_sha256"
HASH_ROUNDS = 210_000


class InvalidCredentials(Exception):
    pass


class InvalidRegistration(Exception):
    pass


class EmailAlreadyRegistered(Exception):
    pass


class AuthRequired(Exception):
    pass


class TripMembershipRequired(Exception):
    pass


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str
    memberships: list[dict]


@dataclass(frozen=True)
class GuestSessionResult:
    session: GuestSession
    token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ROUNDS,
    ).hex()
    return f"{HASH_ALGO}${HASH_ROUNDS}${salt}${digest}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algo, rounds, salt, expected = encoded.split("$", 3)
        if algo != HASH_ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(rounds),
        ).hex()
    except Exception:
        return False
    return hmac.compare_digest(digest, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _memberships(db: Session, user_id: str) -> list[dict]:
    rows = db.scalars(
        select(TripMembership)
        .where(
            TripMembership.user_id == user_id,
            TripMembership.status != "removed",
        )
        .order_by(TripMembership.created_at)
    ).all()
    return [
        {
            "membership_id": row.id,
            "trip_id": row.trip_id,
            "role": row.role,
        }
        for row in rows
    ]


def _start_session(db: Session, user: User) -> LoginResult:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_hash=token_hash(token),
        expires_at=_now() + SESSION_TTL,
    )
    db.add(session)
    db.flush()
    return LoginResult(user=user, token=token, memberships=_memberships(db, user.id))


def is_guest_token(token: str | None) -> bool:
    return bool(token and token.startswith(GUEST_TOKEN_PREFIX))


def start_guest_session(db: Session, membership: TripMembership) -> GuestSessionResult:
    """Issue one bounded Guest bearer for a newly authenticated join.

    The token has 256 bits of entropy and is shown once. SHA-256 protects a
    database dump from directly replaying that high-entropy bearer; unlike a
    human password it is not a low-entropy value requiring a slow KDF.
    """
    if membership.user_id is not None or membership.status != "joined":
        raise AuthRequired("Guest credentials require a joined Guest membership")

    # This is also the race-safe rotation primitive for a future authenticated
    # reissue flow. Anonymous callers have no endpoint that can name a prior
    # membership, so joins never exchange a membership id for a new token.
    db.execute(
        update(GuestSession)
        .where(
            GuestSession.membership_id == membership.id,
            GuestSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    token = f"{GUEST_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    session = GuestSession(
        membership_id=membership.id,
        trip_id=membership.trip_id,
        token_hash=token_hash(token),
        expires_at=_now() + GUEST_SESSION_TTL,
    )
    db.add(session)
    db.flush()
    return GuestSessionResult(session=session, token=token)


def register(db: Session, *, name: str, email: str, password: str) -> LoginResult:
    normalized_name = name.strip()
    normalized_email = normalize_email(email)
    if not normalized_name:
        raise InvalidRegistration("Name is required")
    if (
        "@" not in normalized_email
        or normalized_email.startswith("@")
        or normalized_email.endswith("@")
        or any(character.isspace() for character in normalized_email)
    ):
        raise InvalidRegistration("Enter a valid email address")
    if len(password) < 8:
        raise InvalidRegistration("Password must be at least 8 characters")
    if db.scalar(select(User.id).where(func.lower(User.email) == normalized_email)):
        raise EmailAlreadyRegistered("An account with this email already exists")

    user = User(
        name=normalized_name,
        email=normalized_email,
        password_hash=hash_password(password),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise EmailAlreadyRegistered("An account with this email already exists") from exc
    return _start_session(db, user)


def login(db: Session, *, email: str, password: str) -> LoginResult:
    user = db.scalar(
        select(User).where(func.lower(User.email) == normalize_email(email))
    )
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials("Invalid email or password")

    return _start_session(db, user)


def user_for_token(db: Session, token: str | None) -> User:
    if not token:
        raise AuthRequired("Missing bearer token")
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(token))
    )
    expires_at = session.expires_at if session is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if session is None or session.revoked_at is not None or expires_at <= _now():
        raise AuthRequired("Invalid or expired session")
    user = db.get(User, session.user_id)
    if user is None:
        raise AuthRequired("Invalid session")
    return user


def revoke_token(db: Session, token: str) -> None:
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(token))
    )
    if session is not None:
        session.revoked_at = _now()
        db.flush()


def membership_for_guest_token(db: Session, token: str | None) -> TripMembership:
    """Resolve a Guest bearer without accepting a membership identifier."""
    if not is_guest_token(token):
        raise AuthRequired("Invalid Guest credential")
    session = db.scalar(
        select(GuestSession).where(GuestSession.token_hash == token_hash(token))
    )
    expires_at = session.expires_at if session is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if session is None or session.revoked_at is not None or expires_at <= _now():
        raise AuthRequired("Invalid or expired Guest credential")

    membership = db.get(TripMembership, session.membership_id)
    if (
        membership is None
        or membership.trip_id != session.trip_id
        or membership.user_id is not None
        or membership.status != "joined"
    ):
        raise AuthRequired("Guest membership is no longer active")
    return membership


def revoke_guest_token(db: Session, token: str) -> None:
    session = db.scalar(
        select(GuestSession).where(GuestSession.token_hash == token_hash(token))
    )
    if session is not None:
        session.revoked_at = _now()
        db.flush()


def revoke_guest_sessions_for_membership(db: Session, membership_id: str) -> None:
    db.execute(
        update(GuestSession)
        .where(
            GuestSession.membership_id == membership_id,
            GuestSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    db.flush()


def membership_for_trip(db: Session, user: User, trip_id: str | None) -> TripMembership:
    query = select(TripMembership).where(
        TripMembership.user_id == user.id,
        TripMembership.status != "removed",
    )
    if trip_id:
        query = query.where(TripMembership.trip_id == trip_id)
    membership = db.scalar(query.order_by(TripMembership.created_at))
    if membership is None:
        raise TripMembershipRequired("This account does not belong to this trip")
    return membership
