# QA delta re-check: DESIGN-INVITE-RETRIEVE-C rev5.5

- Reviewer: Cadensy_QA (independent, read-only). Date: 2026-11-23, 17:21 CST (America/Chicago).
- Target: `DESIGN-INVITE-RETRIEVE-C-rev5.5.md`, sha256 `c4f2cdeaae852493164dd870277cc8c3e76b4e14e68d71d98d52a1b7a0d871f3`, 1061 lines. **EXECUTED-PASS**: both match DD's report.
- Base, **EXECUTED-PASS**:
  - rev5.4 `3179616ed8b9637df355090415b0136df026dc11c89ca6582abc948b1ebca307`
  - QA rev5.4 report `07dfed1dbd49457646c189eddb9d6f6faa18a8e43773c9405544eeec7dd61ac8`
- Code: `27c3489936ec34bebbbc9562a1d99e8717df6751`, from an anonymous public clone, left unmodified.
  - Live `main` is `df4bd930e8217feffb6d70e1b83413eee8ba5245` (`git ls-remote`, EXECUTED-PASS), unchanged.
- Nothing was executed except the hash checks, `git ls-remote` and the clone. No repo, code, branch, DB, AWS or workflow change. No connectors or tokens. Design docs not edited.
- The 2026-11-23 Owner decision is still **REPORTED** (relayed by DD) and is labelled that way in rev5.5.
- Diff rev5.4 → rev5.5: 289 lines, 21 hunks. All of them map to R1b-1, N-5, D-2, S-1, §17 or §19/§20. Owner rules §1.1 are unchanged.

## Verdict

**PASS (delta). No HIGH or MEDIUM findings. R1b-1 is CLOSED.** N-5, D-2 and S-1 are addressed. Five LOW implementation notes remain:
- N-5a: the lock placement in N-5 needs one correction.
- M-a … M-c: marker edge cases.
- S-1a: the S-1 guard wording.

## 1. R1b-1: CLOSED (STATIC-VERIFIED, design text)

| Required | rev5.5 §12.0 / §12 |
| --- | --- |
| Step 2 scoped | `… WHERE revoked_at IS NULL AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)`, used for the first apply and every re-run; the unscoped predicate is explicitly withdrawn |
| Step 3 | Asserts zero unrevoked **non-HMAC** rows (not zero overall); `CREATE UNIQUE INDEX IF NOT EXISTS` |
| Step 7 first apply only | `cadensy_invite_cleanup_marker(name TEXT PK, completed_at TIMESTAMPTZ NOT NULL)` is created empty in Rev 1. Rev 2 checks for `rev2_first_apply` after the LOCK. If absent, it runs steps 1–3 and 7 and then INSERTs the marker in the **same transaction**. If present, it skips step 7 |
| Rev 2 down | Drops the index only; must not delete the marker row or drop the table |
| Rev 1 down | Refused if any `hmac_v1` row exists; otherwise drops the marker table along with everything else |
| Owner gate | §12.0, §13.1, §14 and §17 item 1: any post-go-live scoped re-run needs explicit Owner authorization at execution time; it is "not an unattended mandatory step" |
| Verifier | §13.2 catalog includes `cadensy_invite_cleanup_marker` |
| Tests | §15 R-1/R1b-1: re-run with live HMAC rows and non-empty approvals succeeds and touches neither; first apply writes the marker; a second apply skips step 7; IF NOT EXISTS; the unscoped control is forbidden |

### Marker logic gap analysis

**Transaction boundaries.**
- `alembic/env.py:56` has `transaction_per_migration=True` (**STATIC-VERIFIED** at `27c3489936ec34bebbbc9562a1d99e8717df6751`). Each revision commits on its own; a one-run `upgrade` to the final head still commits Rev 1 before Rev 2 starts.
- Inside Rev 2, the LOCK, cleanup, step-7 assert, marker INSERT and index creation are atomic.
- If step 7 aborts (or anything else in Rev 2 fails), Rev 2 rolls back with no marker written, and the DB stays at the Rev 1 intermediate head. That head is an allowed recovery start, and a retry is again a first apply. Correct.

**Rev 2 down → up.**
- The marker survives, so the re-upgrade takes the re-run path: scoped steps 1–3, step 7 skipped, index recreated. Live HMAC rows and approvals are untouched. Correct.
- While Rev 2 is down, the unique index is absent. The new backend still serializes ensure/replace with trip `FOR UPDATE`. A rolled-back old app's `create_invite` revokes and then inserts, and step 2 later revokes only the non-HMAC row. So the re-upgrade's index build cannot fail on two unrevoked rows from new-backend paths.
  - **SUSPECTED (NOT RUN):** a residual risk remains if the old app's pre-existing concurrent first-create race (§3.1) runs during that window and leaves two unrevoked rows. Step 2 revokes both if both are non-HMAC, so this is covered as well.

**Rev 1 down.**
- Refused if `hmac_v1` rows exist. Otherwise it drops the marker, so the next upgrade is a true first apply. Correct for the pre-go-live rollback it is meant for.
- **M-a (LOW, confirmed static):** after go-live it is possible to have **no** `hmac_v1` rows but **existing approvals**. §9.5 allows approvals before any invite (M-1 guard (a)).
  - In that state Rev 1 down drops `final_plan_approval` and silently loses post-go-live user approvals. (`final_plan_approved_at` cannot be set without an invite, by guard (a), so only approvals are lost.)
  - Fix: also refuse Rev 1 down when `final_plan_approval` is non-empty, or when the marker exists and the Owner has not explicitly authorized the data loss.

**Runbook re-run outside Alembic.**
- **M-b (LOW, confirmed static):** §12.0 says the runbook re-runs "steps 1–3" but does not restate the execution envelope. It should say: one transaction; the timeouts first; the same `LOCK TABLE trip, invite_link, plan` order; and **precondition: the marker row exists** (refuse otherwise, so a runbook cannot stand in for a first apply).
- Without the LOCK, a concurrent ensure/replace (trip FU → invite FU) would only wait on the runbook's invite row locks. There is no cycle, but the step 3 assertion could race the old app. Spell it out.

**`create_all` / test environments.**
- The marker exists only via Alembic; it is not described as an ORM model. `create_all` schemas (conftest, seed, `init_schema`) will lack it. That is harmless for the app, which never reads it, but:
  - **M-c (LOW):** cleanup, marker and downgrade tests must run against an Alembic-built schema.
  - Either add the table to the models, or exclude it explicitly from any future autogenerate. **STATIC-VERIFIED:** no `compare_metadata` / `alembic check` / `include_object` drift test exists in `tests/`, `alembic/`, `app/` or `scripts/` today, so nothing fails now. But `env.py` sets `target_metadata` with `compare_type=True`, so a future autogenerate would propose dropping the table.
  - The §9.6 parity test compares functions and triggers only; it does not need the marker. The verifier catalog covers the marker.

**Tests (LOW, under M-c).** Add explicit cases for:
- Rev 2 down then up with live HMAC rows and approvals (marker survives; step 7 skipped);
- a step-7 abort leaves no marker;
- Rev 1 down drops the marker;
- the M-a refusal.

**Owner gate.** A Rev 2 down/up after go-live is a production migration through the Owner-gated Gate M (items 1 and 5). The runbook re-run is explicitly item-1 Owner-authorized. Satisfied.

## 2. N-5: addressed; one placement correction (N-5a)

STATIC-VERIFIED at `27c3489936ec34bebbbc9562a1d99e8717df6751`, `preferences/service.py` `save_mine` (`:177-221`). In order:
1. `_validate_preference_dates` (defined after `save_mine`) does a plain `db.get(Trip)`.
2. `select(Preference)` (`:182-184`).
3. If none exists, `db.add(Preference(...))` (`:185-187`), still pending.
4. Field assignments.
5. `membership.status = …` (`:213`).
6. `db.flush()` (`:214`), which emits **both** the Preference INSERT and the membership UPDATE.
7. `record_preferences_submitted` → `pg_insert(ActivationEvent).on_conflict_do_nothing` (`analytics/activation.py:155-167`, `:270-276`): RI on trip KS and membership KS.
8. `_mark_existing_plan_for_refresh`: plan NKU.

Auth (`main.py:206-241`, `auth.py`) writes only on an expired-session revoke, and that path then raises 401. There are no other earlier writes.

**Lock modes (documented PostgreSQL semantics, NOT RUN):**
- `FOR KEY SHARE` on trip conflicts with `FOR UPDATE`, which join, approve/close, remove and quarantine all take first.
- If save_mine gets trip KS first, J waits and save_mine never waits on J:
  - it holds no lock J has;
  - its later locks (membership NKU, Preference RI membership KS, activation RI trip/membership KS, plan NKU) are on rows J reaches only after the trip.
- If J holds the trip first, save_mine blocks at the trip KS while holding nothing.
- **No upgrade on trip:** save_mine never UPDATEs `trip`. The activation insert's trip RI is KS, already held. There is no bump (no `plan_item` write). `_mark_existing_plan_for_refresh` UPDATEs `plan`, not `trip`.
- **KEY SHARE is sufficient. FOR UPDATE is not needed:** every conflicting J path uses trip `FOR UPDATE`. FOR UPDATE here would needlessly serialize concurrent preference saves against each other.

**N-5a (LOW, confirmed static; lock semantics NOT RUN):** rev5.5 places the trip lock "before the membership assignment at `:213`".
- Expressed as an ORM query there (`with_for_update(key_share=True)` or `db.execute`), it **autoflushes** the pending Preference INSERT first. SQLAlchemy autoflush is on: no override in `app/db/`.
- `Preference.trip_membership_id` is `ForeignKey("trip_membership.id")` (`models.py:316`), so that INSERT's RI takes membership `FOR KEY SHARE` **before** the trip lock. That still conflicts with J's memberships `FOR UPDATE`, so the cycle remains for the common first-submit case (no existing Preference row).
- Fix: take the trip KS **at the start of `save_mine`, before `db.add(pref)` (`:185-187`)**, or before any pending write, or under `no_autoflush`.
- Extend the §15 N-5 test to the "no existing Preference row" case.

## 3. D-2: CLOSED (STATIC-VERIFIED)

§10 now states a first-match precedence:
1. early_expired
2. expired / window_expired
3. active_hmac
4. non_hmac
5. revoked_replaceable
6. none

`non_hmac` beats `none`. The unique index means at most one unrevoked row, so 3 and 4 cannot both match.

Consistency checks:
- **Retrieve:** the token appears only for `active_hmac`.
- **Status table:** `non_hmac` has `can_create` iff the anchor is NULL, and `can_replace` iff the window is open and the trip not closed, matching §6/§7.
  - With a NULL anchor, ensure may mint, and it revokes the leftover.
  - With an anchor set, ensure is non-minting and replace is allowed.
- **Join/preview:**
  - R-1 rejects non-HMAC rows regardless of status.
  - HMAC rows follow expires/early-close.
  - The `expired` / `early_expired` rows note that a leftover non-HMAC token still fails under R-1.

No contradictions.

## 4. S-1 residual: acceptable, with a wording fix

Accepting no bump change is reasonable. No production legacy writer updates more than one existing item per transaction (QA rev5.4 static search; not exhaustive). The design adds an implementation guard and a test-plan note (§8, §15, §19).

**S-1a (LOW, documented PG semantics, NOT RUN):** the guard's alternative, "or already hold a trip `FOR KEY SHARE`", does **not** prevent the cycle. Two writers both holding trip KS are compatible and can still interleave items and the plan in opposite order. Fix:
- keep "lock `plan_item` rows in deterministic `id` order"; and
- restrict the alternative to a trip lock that conflicts with itself (`FOR NO KEY UPDATE` or `FOR UPDATE`).

## 5. Owner checklist and new issues

- §17 OPEN = 1, 2, 5, 6 and G-1 (**STATIC-VERIFIED**). Items 3 and 4 remain RESOLVED (as relayed).
- Item 1 covers the destructive first-apply cleanup **and** any post-go-live scoped re-run, each needing explicit Owner authorization at execution time.
- The Owner decision is labelled as relayed (REPORTED) in §0.2, §17 and §19.
- New HIGH / MEDIUM: **none**.

## Findings

**Confirmed (static) — all LOW, implementation notes:**

| ID | Summary |
| --- | --- |
| N-5a | Take the trip KS at the start of `save_mine`, before `db.add(pref)`. Otherwise autoflush emits the Preference INSERT (membership KS) first and the cycle remains |
| M-a | Rev 1 down can silently drop post-go-live approvals when no `hmac_v1` rows exist. Also refuse when `final_plan_approval` is non-empty (or require explicit Owner authorization) |
| M-b | Specify the runbook re-run envelope: one transaction, timeouts, the `trip → invite_link → plan` LOCK, and the marker-exists precondition |
| M-c | Marker is absent from `create_all`/ORM: run cleanup/marker/downgrade tests on an Alembic-built schema, add the extra marker tests listed above, and add the table to the models or exclude it from autogenerate |
| S-1a | "Trip FOR KEY SHARE" does not serialize multi-item writers. Use deterministic item-id order or a self-conflicting trip lock |

**Suspected:** none beyond the PostgreSQL-execution aspects marked NOT RUN.

## Implementation-ready design (pending Owner items): **yes**

There are no HIGH or MEDIUM findings. R1b-1 (the only rev5.4 blocker) is closed. N-5a, M-a, M-b, M-c and S-1a are LOW implementation notes to fold into the implementation tickets or a minor doc revision. Pending Owner items: **1** (including the destructive cleanup and any re-run, each with explicit Owner authorization at execution time), **2**, **5**, **6** and **G-1**.

## NOT RUN / UNKNOWN

- **NOT RUN:**
  - All PostgreSQL behaviour: lock conflicts, trigger order, deadlock detection, the cleanup SQL, the marker insert/skip/survival, downgrade, parity.
  - All project tests, the migration and the dry-run.
  - No scratch run this round.
- **UNKNOWN:** production PostgreSQL version and `alembic_version`, row counts, ALB logs.
- **REPORTED:** the 2026-11-23 Owner decision.

## Handoff

- **Cadensy_DD:** fold N-5a, M-a, M-b, M-c and S-1a into the implementation tickets (or a rev5.6 doc touch-up; no QA re-review is required for LOWs). Present Owner items 1, 2, 5, 6 and G-1.
- **QA:** implementation QA once authorized, with the deadlock (N-3/N-4/N-5), parity, cleanup/marker/downgrade, R-1 and approve tests executed on real PostgreSQL.

## No authorization

This report authorizes nothing: no implementation, merge, migration, destructive cleanup or re-run, key creation, workflow change or deploy. All Owner items above remain OPEN. The Owner decision remains REPORTED.
