"""No-DB HTTP regression coverage for PILOT-FIX-03 authorization mapping."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.domain.transactions import InsufficientRole, MembershipNotAuthorized


class WriteTrackingSession:
    """A route dependency fake that makes accidental request-session writes visible."""

    def __init__(self) -> None:
        self.add_calls = 0
        self.commit_calls = 0
        self.flush_calls = 0

    def add(self, *_args: object, **_kwargs: object) -> None:
        self.add_calls += 1

    def commit(self) -> None:
        self.commit_calls += 1

    def flush(self) -> None:
        self.flush_calls += 1


@pytest.fixture
def request_context(monkeypatch: pytest.MonkeyPatch):
    session = WriteTrackingSession()
    membership = SimpleNamespace(id="member-1", trip_id="trip-1", user_id="user-1")
    api.app.dependency_overrides[api.get_session] = lambda: session
    api.app.dependency_overrides[api.current_membership] = lambda: membership
    monkeypatch.setattr(api, "_require_scoped_trip", lambda *_args: None)
    monkeypatch.setattr(
        api,
        "_protected_trip_revision",
        lambda **_kwargs: (None, None, "pilot-fix-03", 0, None),
    )
    @asynccontextmanager
    async def no_database_lifespan(_app):
        yield
    monkeypatch.setattr(api.app.router, "lifespan_context", no_database_lifespan)
    try:
        yield session
    finally:
        api.app.dependency_overrides.clear()


def _trip_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer dummy",
        "Idempotency-Key": "pilot-fix-03",
        "X-Expected-Trip-Facts-Revision": "0",
    }


def _organizer_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer dummy",
        "Idempotency-Key": "pilot-fix-03",
        "X-Expected-Plan-Revision": "0",
    }


@pytest.mark.parametrize(
    ("method", "path", "json_body", "executor_name"),
    [
        ("delete", "/api/trips/trip-1/members/member-2", None, "remove"),
        ("post", "/api/trips/trip-1/members/member-2/quarantine", {"reason": "abuse"}, "quarantine"),
    ],
)
def test_protected_membership_insufficient_role_returns_403_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    request_context: WriteTrackingSession,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
    executor_name: str,
) -> None:
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "test-key")
    protected_module = getattr(api, f"protected_membership_{executor_name}")
    monkeypatch.setattr(
        protected_module,
        f"execute_protected_membership_{executor_name}",
        lambda **_kwargs: (_ for _ in ()).throw(InsufficientRole("participant")),
    )

    with TestClient(api.app, raise_server_exceptions=False) as client:
        request = getattr(client, method)
        if json_body is None:
            response = request(path, headers=_trip_headers())
        else:
            response = request(path, headers=_trip_headers(), json=json_body)

    assert response.status_code == 403
    assert response.json() == {"detail": "Protected command authorization failed"}
    assert (request_context.add_calls, request_context.flush_calls, request_context.commit_calls) == (0, 0, 0)


@pytest.mark.parametrize(
    ("path", "json_body"),
    [
        ("/api/rounds/round-1/extend", None),
        ("/api/proposals/proposal-1/deadlock", {"action": "keep"}),
    ],
)
def test_protected_organizer_insufficient_role_returns_403_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    request_context: WriteTrackingSession,
    path: str,
    json_body: dict[str, str] | None,
) -> None:
    monkeypatch.setenv("CADENSY_B2E_PROTECTED_ORGANIZER_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "test-key")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        api.protected_organizer_actions,
        "execute_protected_organizer_action",
        lambda **_kwargs: (_ for _ in ()).throw(InsufficientRole("participant")),
    )

    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(path, headers=_organizer_headers(), json=json_body)

    assert response.status_code == 403
    assert response.json() == {"detail": "Protected organizer authorization failed"}
    assert (request_context.add_calls, request_context.flush_calls, request_context.commit_calls) == (0, 0, 0)


@pytest.mark.parametrize(
    ("path", "headers", "json_body", "protected_module", "executor_name", "detail"),
    [
        (
            "/api/trips/trip-1/members/member-2",
            _trip_headers(),
            None,
            "protected_membership_remove",
            "execute_protected_membership_remove",
            "Protected command authorization failed",
        ),
        (
            "/api/rounds/round-1/extend",
            _organizer_headers(),
            None,
            "protected_organizer_actions",
            "execute_protected_organizer_action",
            "Protected organizer authorization failed",
        ),
    ],
)
def test_membership_not_authorized_remains_403(
    monkeypatch: pytest.MonkeyPatch,
    request_context: WriteTrackingSession,
    path: str,
    headers: dict[str, str],
    json_body: dict[str, str] | None,
    protected_module: str,
    executor_name: str,
    detail: str,
) -> None:
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "test-key")
    if protected_module == "protected_organizer_actions":
        monkeypatch.setenv("CADENSY_B2E_PROTECTED_ORGANIZER_WRITES", "1")
        monkeypatch.setenv("APP_ENV", "test")
    module = getattr(api, protected_module)
    monkeypatch.setattr(
        module,
        executor_name,
        lambda **_kwargs: (_ for _ in ()).throw(MembershipNotAuthorized("not authorized")),
    )

    with TestClient(api.app, raise_server_exceptions=False) as client:
        if protected_module == "protected_membership_remove":
            response = client.delete(path, headers=headers)
        else:
            response = client.post(path, headers=headers, json=json_body)

    assert response.status_code == 403
    assert response.json() == {"detail": detail}
    assert (request_context.add_calls, request_context.flush_calls, request_context.commit_calls) == (0, 0, 0)
