# Trip-independent routes and unused `X-Trip-Id`

Status: current behavior note
Last reviewed: 2026-11-06
Code pin: `0a7f68b4aec8c91b0e19a8a09f133688b22beb86`

`backend/app/api/main.py` at this pin is byte-identical to analysis SHA `4d3bdceb4ff96faddb1dfc8d96f641f4a89553e1`. Line numbers below are from that file.

This note records how the ten trip-independent routes treat `X-Trip-Id` today. It does not add runtime behavior, and it does not decide a product exemption.

Owner Q1 already says login, register, listing trips, and creating a trip must not be forced to require `X-Trip-Id`. That is a non-requirement for those independent interfaces. It is not a name for the rest of the API.

## CURRENT mechanic

None of the handlers below declare an `x_trip_id` parameter. FastAPI therefore does not bind `X-Trip-Id` into the function. A supplied `X-Trip-Id` is unused for trip selection because the handler never reads it.

There is no runtime statement of the form “if `X-Trip-Id` is present, ignore it.” Rev5.3’s phrase “ignore if supplied” is **PROPOSED documentation** of this CURRENT unused-header mechanic. It is not a new runtime policy, and it is not an exemption.

`GET /api/me` is not in this list. It is trip-context and header-only: `get_me` at `backend/app/api/main.py:1273-1282` depends on `current_membership`.

## Routes

| Method | Path | Handler | Why a supplied `X-Trip-Id` is unused |
| --- | --- | --- | --- |
| GET | `/api/health` | `health` | `backend/app/api/main.py:1188-1190`. No header parameter. |
| POST | `/api/auth/login` | `login` | `:1193-1194`. Parameters are `LoginRequest` and the database session. |
| POST | `/api/auth/register` | `register` | `:1218-1219`. Parameters are `RegisterRequest` and the database session. |
| POST | `/api/auth/logout` | `logout` | `:1245-1260`. The only header parameter is `authorization`. Token selection uses that bearer value. |
| GET | `/api/account` | `get_account` | `:1263-1264`. Depends on `current_account_user` (`:244-247`), which binds `authorization` only. |
| GET | `/api/trips` | `list_trips` | `:1285-1290`. Depends on `current_account_user`. Optional query `priority_trip_id` (`:1287`) is a different input from `X-Trip-Id`. |
| GET | `/api/invites/{token}` | `get_invite` | `:1565-1570`. Parameters are the path `token` and the database session. |
| POST | `/api/trips` | `create_trip` | `:1939-1943`. Depends on `current_account_user`. No `x_trip_id` parameter. |
| POST | `/api/invites/{token}/join` | `join_invite` | `:2001-2006`. Parameters are the path `token`, `InviteJoinRequest`, and the database session. |
| POST | `/api/invites/{token}/account-join` | `account_join_invite` | `:2033-2038`. Depends on `current_account_user` and binds `authorization` only. |

## Unchanged trip-context behavior

This note does not change trip-context routes.

- An account request that omits `X-Trip-Id` still selects the earliest membership by `created_at` (`backend/app/domain/auth.py:303-305`).
- A path trip that is not the membership trip still returns HTTP 403 with `"This identity belongs to a different trip"` (`backend/app/api/main.py:199-200`).
