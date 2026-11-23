# DESIGN-04 Phase 1 Task 0 coverage closure

Status: point-in-time implementation evidence
Date: 2026-11-06
Code pin: `0a7f68b4aec8c91b0e19a8a09f133688b22beb86`

This note records a read-only re-run of the Phase 1 Task 0 searches on the pin above. It is not runtime proof, not an exhaustive caller inventory, and not independent QA.

## Result

No newly found in-repo Cadensy trip-context HTTP caller met all four defect conditions: a trip-context route, an intended-success path, no `X-Trip-Id`, and not under `dependency_overrides[current_membership]`.

Missing-header fix files: `[]`.

`backend/tests/**` and `backend/app/**` were not edited for this result. Negative probes and override tests were left unchanged, including bare 401 calls and override headers that omit `X-Trip-Id`.

## Searches

The package patterns were run from the repository root. `docs/`, `evals/`, and `agents/` are not directories here; `backend/evals/` is.

- `with TestClient(...) as NAME` in `backend/tests`: aliases observed were `client`, `c`, and `test_client`. No additional receiver alias.
- `httpx`, `httpx2`, `AsyncClient`, and `requests.(get|post|put|patch|delete|request)`: hits are outbound provider, cover, geoapify, or eval HTTP denial. No `import requests` Cadensy API client.
- Single-line `.(get|post|put|patch|delete)(...'/api/')` in `backend/tests` and `backend`: the test hits are inside the existing TestClient receiver set. Route decorators in `backend/app/api/main.py` are definitions, not callers. Headerless trip-context examples in that slice are asserted 401 probes (for example `backend/tests/test_trips.py` around the bare `GET /api/me` and `GET /api/trips/{id}` assertions) or independent routes such as invite preview.
- `getattr((client|c|test_client|http_client)`: one dynamic call, `backend/tests/test_pilot_fix_03_insufficient_role_http.py:95`. That test replaces `current_membership` (`:38`) and expects HTTP 403. It is an override authorization probe, not a success-path header gap.
- `curl` of `/api/`: health checks in workflow and AWS notes. `GET /api/health` is trip-independent.
- Helpers whose signatures mention `client`, `test_client`, or `http_client` still perform the HTTP call through a scanned receiver.

## Residual limits

Dynamic method names other than the one `getattr` above, future files, a helper that wraps HTTP behind a new receiver, and out-of-repo curls were not proven absent. The historical 370-call scanner was not re-executed as a full recount. This re-run does not prove production callers.
