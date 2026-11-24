# DESIGN-INVITE-RETRIEVE-C-rev5.5

Status: DESIGN ONLY. This is the **single normative** design for Option C invite retrieval and early close. It is not implementation, PR, merge, secret, migration, workflow-change, or deploy permission.

Date: 2026-11-23 (America/Chicago)

This revision folds in Cadensy_QA's review of rev5.4 (**ACCEPT WITH FINDINGS**; one blocking MEDIUM R1b-1 plus LOWs N-5, D-2, S-1): `/workspace/p0-activation/QA-DESIGN-INVITE-C-rev5.4-3179616e.md`, sha256 `07dfed1dbd49457646c189eddb9d6f6faa18a8e43773c9405544eeec7dd61ac8` (re-verified this revision, EXECUTED-PASS). Fixes are **DD-DECIDED** as specified by Cadensy_DD. rev5.4 and earlier revisions were **not** edited. Their hashes remain:

| Document | sha256 (EXECUTED-PASS) |
| --- | --- |
| QA rev5.4 report | `07dfed1dbd49457646c189eddb9d6f6faa18a8e43773c9405544eeec7dd61ac8` |
| rev5.4 | `3179616ed8b9637df355090415b0136df026dc11c89ca6582abc948b1ebca307` |
| QA rev5.3 report | `11b7b31219e6a34b60d632c5aae5e025fba34079d920e0015adbf5c4c1a1b82f` |
| rev5.3 | `20bb7915f0ae1e635fbc8f613b40fc612de4e4261cdd23a4f79d0556a8d3408e` |
| QA rev5.2 report | `1cae0039396838b8f45b619eb9cb3df77753aa0084b146863256a893037786bb` |
| rev5.2 | `3f49b9a51460d5d7b0914032d1f1f9637471ccab9c6765e09e382412bae85ab5` |
| QA rev5.1 report | `e01daf6ff972fdc2d90fd80054de2ec3ae985936f37538d2b44a59d9e895cd20` |
| rev5.1 | `2cfc75fd34331f576df5fa0f7aba61463975c8b26d757c92b1aca0fa60d00731` |
| QA rev5 report | `d2d0d09aa75e431230d5b125e062338f7e5842659d8865d71d2049a9cb60e30f` |
| rev5 | `507190119c4554ad2b04beb8aa45586df865a439b8ef4b24011462bc5643d350` |
| QA rev4 report | `6888ab230e3dc82e33c8f0d45356c4d6abacad624bd4aacf3a9cf1e22d22050a` |
| rev4 | `728da30d6a11217ced3ca2bdc4688cda5fcf02625fa5f99497bf565a119d586f` |
| rev3 | `b5eaec765b0d55beb96c9fbe466f6aeaebd896d40e3480669b4cf2d34cfce0f1` |
| rev2 | `b1a12abaf51aa29be99311ef6e85a4a483822c8144c08bbd59dbbff29707e74f` |
| rev1 | `d871589f4933dd7459ffb90aa862f1b214c942c8294311cae17e10522d1bfb64` |

Cite base SHA (full 40): `27c3489936ec34bebbbc9562a1d99e8717df6751`  
All `file:line` cites are at that SHA unless a live-`main` documentation path is named as such.

Live `main` (from QA rev5.4, EXECUTED-PASS anonymous `git ls-remote` in that report; this revision did not re-run `ls-remote`): `df4bd930e8217feffb6d70e1b83413eee8ba5245`  
Drift vs cite base (unchanged since rev5.3 / QA rev5.4): ahead_by 2, behind_by 0, documentation-only under `application_docs/` (`21d0992e99d7ede0a2f1ebc674de4956ca51440f` #61 at 2026-11-23 11:54 AM America/Chicago; `df4bd930e8217feffb6d70e1b83413eee8ba5245` #62 at 12:43 PM). No source, workflow, or CD-01 pin drift was reported. Cites at the base SHA remain valid. No re-cite at a new SHA is required.

Verification vocabulary: `STATIC-VERIFIED` / `EXECUTED-PASS` / `NOT RUN` / `UNKNOWN`.  
Decision vocabulary: `DECIDED` / `DD-DECIDED` / `PROPOSED` / `OPEN`.

Hard limits observed: no repo code edits, branch, PR, DB, AWS, secret, test execution, workflow, or deploy actions; no other agent worktree was used; `/tmp/qa-design04` was not touched. Inspection was read-only. Tests were `NOT RUN`.

---

## Delta vs rev5.4 (`3179616ed8b9637df355090415b0136df026dc11c89ca6582abc948b1ebca307`)

- **R1b-1 (MEDIUM, blocking; `DD-DECIDED`).** A cleanup re-run must **never** touch production HMAC links. Step 2 revokes only unrevoked **non-HMAC** rows: `WHERE revoked_at IS NULL AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)`. Step 3 asserts zero unrevoked **non-HMAC** rows, not zero unrevoked rows. Step 7 (`final_plan_approval` empty assertion) runs **only on the first Rev 2 apply**, guarded by the `cadensy_invite_cleanup_marker` row described in §12.0; it never runs on a re-run. **Withdrawn:** rev5.4 text that a re-run of unscoped `WHERE revoked_at IS NULL` is idempotent / mandatory and would only revoke leftovers, and that step 7 also runs on the roll-forward re-run. Any **post-go-live** re-run requires explicit Owner authorization at execution time (item 1 runbook; no new Owner item). Tests: a re-run preserves live HMAC links and existing approvals, and revokes only non-HMAC rows. See §12.0, §13.1, §14, §15, §17.
- **N-5 (LOW, QA rev5.4 preferences lock; `DD-DECIDED`; distinct from the earlier §14 rollout-gap finding also labeled N-5).** Preferences first-submit (`save_mine` in `preferences/service.py:213-216`) currently UPDATEs `membership.status` and flushes, then `record_preferences_submitted` inserts `activation_event` (RI trip KEY SHARE; `activation.py:155-167` / `:270-276`). Take trip `FOR KEY SHARE` first (same helper style, keyed by `membership.trip_id`) **before** the membership UPDATE. Do **not** reorder the event insert. Add a deadlock test against join / approve / remove / quarantine. §8, §15.
- **D-2 (LOW; `DD-DECIDED`).** §10 states a full status precedence order. `non_hmac` takes precedence over `none` when an unrevoked non-HMAC row exists and no production HMAC invite has been created.
- **S-1 (LOW; residual risk, `DD-DECIDED`).** Two multi-item content writers on the same plan can cycle through the bump's `plan` UPDATE if they lock items in opposite order. No design change (not cheap to change the bump). Implementation guard / test: any future multi-item writer must lock items in deterministic `plan_item.id` order, or already hold a trip lock. §8, §15, §19.
- **§17.** OPEN set **unchanged**: 1, 2, 5, 6, and G-1. Item 1's runbook now also covers a scoped post-go-live cleanup re-run (Owner-authorized at execution time). Items 3 and 4 stay RESOLVED (as relayed by DD).
- **§19.** Gaps updated for R1b-1 (SQL scoped, marker behavior NOT RUN), N-5 (lock modes documented PG, NOT RUN), and S-1.

---

## Historical: Delta vs rev5.3 (`20bb7915f0ae1e635fbc8f613b40fc612de4e4261cdd23a4f79d0556a8d3408e`)

- **N-4 (MEDIUM, blocking; `DD-DECIDED`).** Legacy booking / notice / settle / confirm (and deadlock `keep`) flush `INSERT plan_change` first. That row's FK to `plan.id` takes `plan FOR KEY SHARE` with no trip lock, then `UPDATE plan_item` / `INSERT update_notice` wait on trip — a cycle with join, approve, close, remove, and quarantine. Adopt the QA-recommended fix: a `BEFORE INSERT` row trigger on `plan_change` that takes the parent trip `FOR KEY SHARE` first (resolve `trip_id` via `plan`). Added to the shared SQL module, `after_create` / parity, the verifier catalog, and Rev 1 upgrade/downgrade. Full FK audit of every table whose FK points at `plan` or a plan-dependent row is in §8.1. **Withdrawn:** the rev5.3 §8 claim that the bump-trigger trip KEY SHARE alone preserves trip-then-plan order, and the sentence that this cycle "does **not** exist today, because legacy writers never hold the plan lock." See §8, §9.6, §12, §13.2, §15.
- **R-1 (LOW; `DD-DECIDED`, both (a) and (b)).** Post-cutover joinability requires an HMAC-era row (`token_scheme = 'hmac_v1'` and a non-null `key_id`). Any non-HMAC row is non-joinable and is reported 410 `invite_revoked` (or 404 `invite_invalid`) regardless of `expires_at`. New §10 status `non_hmac`. Rev 2 cleanup is **idempotent** and is a mandatory re-run on any roll-forward after a backend rollback (§12.0, §14). (**SUPERSEDED** in rev5.5 / R1b-1: re-run is scoped to non-HMAC rows, skips step 7, and needs Owner authorization; it must never revoke live HMAC links.)
- **C-1 (LOW; `DD-DECIDED`).** Rev 2 **asserts** `final_plan_approval` is empty and **aborts** otherwise. No conditional `DELETE FROM final_plan_approval`. §12.0 step 7.
- **P-1 (LOW; `DD-DECIDED`).** `content_changed` is evaluated only inside the `UPDATE` branch of the AFTER bump trigger (PG-version independent; does not read `OLD` on INSERT or `NEW` on DELETE). §9.3.
- **§19.** Gaps updated: N-4 lock modes remain documented PG / NOT RUN; QA's N-2b flush-order scratch is credited as EXECUTED-PASS at ORM level (in-memory SQLite); this revision did not re-run that scratch.
- **§17.** Unchanged. N-4 / R-1 / C-1 / P-1 are item-1 DDL and runbook details, not new Owner items. OPEN remain 1, 2, 5, 6, and G-1; items 3 and 4 stay RESOLVED (as relayed by DD).

---

## Historical: Delta vs rev5.2 (`3f49b9a51460d5d7b0914032d1f1f9637471ccab9c6765e09e382412bae85ab5`)

Owner decision as relayed by DD 2026-11-23 (pre-launch cleanup, not a lifecycle-rule change):

- **A1 / A3 / items 3–4.** 180-day validity applies uniformly, including existing production trips. No grandfathering and no special extension for historical test trips. Owner item 4 option B is **removed** and marked **RESOLVED**. Owner item 3 (null-expiry cap-on-read) is **moot** and marked **RESOLVED**. See §6, §17.
- **A2 / A7.** Pre-launch legacy 30-day invite rows and any test approvals are disposable. No preservation of 30-day expiry or cross-device recovery of old tokens. Cleanup is an in-migration **revoke-in-place** of all existing `invite_link` rows (no `DELETE`), inside Rev 2 of simplified split B. See §12.0.
- **A4.** `first_invite_created_at` / `invite_window_ends_at` are new nullable columns and are **never backfilled**. Pre-launch trips enter production with both NULL. The first successful HMAC invite after go-live sets the immutable window. See §6, §12.0.
- **A5.** Replace = new invite ID + token, same `expires_at` (unchanged). §7.
- **A6.** Revocation history and `activation_event` rows for production-system invites are kept. Legacy invite **rows** are also kept (revoked, not deleted) so analytics FKs are not cascaded. See §12.0.
- **Removed sections / statuses.** `legacy_active` and `expired_replaceable` (F-7) are dropped from §10. Legacy retrieve, legacy `can_replace` branches, null-expiry cap-on-read, and anchor backfill / live-link dedupe (as a data-preservation step) are removed. F-7 is retired.

QA rev5.2 LOWs:

- **D-1.** §9.3 again states the full `content_changed` predicate, including `::jsonb` casts.
- **N-2b.** `upsert_demo_seed` must `db.flush()` after `_upsert_items` and before any plan-field assignment. §8 states that **flush order**, not Python statement order, is what matters.
- **N-3.** A `BEFORE INSERT OR UPDATE OF plan_id` trigger on `plan_item` takes trip `FOR KEY SHARE` so the trip lock precedes the RI plan lock. The AFTER bump trigger is unchanged. INSERT-writer deadlock test added. Parity covers **all** trigger functions and triggers.
- **I-1.** A blocked generate not refreshing `plan.currency` is acceptable; currency refreshes on the next successful generate.
- **T-1.** Two explicit remove/quarantine close tests are in §15.
- **G-1.** Remains **OPEN**: Owner confirmation of the M-1 reading of rule 5, specifically guard (b). Listed in §17; not resolved by the 2026-11-23 cleanup decision.

**Still OPEN in §17:** items 1 (production Alembic upgrade, now including the destructive §12.0 cleanup and simplified split B / window), 2 (HMAC keyring secret), 5 (restore Gate M), 6 (rebind release evidence), and G-1.

---

## Historical: Delta vs rev5.1 (`2cfc75fd34331f576df5fa0f7aba61463975c8b26d757c92b1aca0fa60d00731`)

- **M-1 (MEDIUM, blocking).** Early close fires only when (a) `trip.first_invite_created_at IS NOT NULL`, (b) there are at least two current participants including at least one non-organizer, and (c) all current participants have a non-stale approval. Approvals may be recorded before the guard holds. Close is **written** at approve and at remove/quarantine; **evaluated at join only to not break a terminal close** (a join can never make (c) true). This is a DD interpretation of Owner rule 5, not a new Owner item.
- **N-2 (LOW).** Legacy `generator.py` and `upsert_demo_seed.py` must not `UPDATE plan` before writing `plan_item`. Pick: **reorder** (defer plan field writes until after item writes). Minimal change in the AI-runtime generator; flag Cadensy_AI_Agent. Protected `generator_commit.py:156-159` already writes items first under a trip lock.
- **L-1.** Trigger uses `OLD.plan_id` on DELETE, `NEW.plan_id` on INSERT, and on UPDATE of `plan_id` bumps **both** plans (trip KEY SHARE in `trip.id` order, then the two plan increments). `plan_id` is added to `UPDATE OF`. No bare `plan_id` in the SQL.
- **L-2.** POST approve after close returns **409 `invite_closed`** (no write, does not reopen). A re-admitted member's old approval is ignored unless it belongs to the **current membership incarnation** and is bound to the current revision.
- **L-3.** DDL: escape `%` as `%%` in SQLAlchemy `DDL()`; register function DDL before trigger DDL; `create_all` on an existing DB skips `after_create`; parity test compares `pg_get_functiondef` / `pg_get_triggerdef` text.
- **L-4.** Keyring: a short **old** key is ignored (treated as absent), not a whole-keyring failure; the **active** key must still be valid. `v: 1` has no disabled list — absence only. Retention: keep an old id until every `hmac_v1` row with that id is expired or revoked (at most 180 days after it last stopped being active). Parse once per process and cache; reload on deploy/restart.
- **L-5.** No design change; `init_schema` drift remains acknowledged and non-production.
- **INFO §16.** Scope text now says this rev5.2, not "rev5".

---

## 0. Provenance and supersession

This file supersedes rev1–rev5.4 as the implementer-facing design. Earlier files, including rev5.4, remain historical evidence and must not be edited.

| Topic | Normative here | Superseded / historical |
| --- | --- | --- |
| Owner rules 1–8 | §1.1 | — |
| Approval epoch and close rules | §1.2, §9, §9.5 | rev3 §1.2; rev5.1 close-without-guard (M-1); rev5.1 post-close 200 |
| Current code model (invites, routes, join locks, analytics, `first_decision_completed`, pins, PR #55) | §3 (carried from rev3 §3, pins completed by rev4) | — |
| HMAC / key_id / fail-closed / threat / access-log redaction | §5 | — |
| Anchor and 180-day window | §6 (never backfilled; first HMAC create after go-live) | rev5.2 item-4 grandfather / item-3 cap-on-read / `MIN(created_at)` backfill |
| Ensure / replace API (F-1; F-7 retired) | §7 | rev5.2 F-7 / `expired_replaceable` |
| Transactional correctness and lock order | §8, §8.1 (N-4 `plan_change` BEFORE trigger; FK audit) | rev5.3 §8 claim that the cycle does not exist today / that the bump trigger alone preserves trip-then-plan order |
| Status enum / `can_replace` | §10 | `legacy_active`, `expired_replaceable` (F-7) |
| Analytics, revoke, isolation, share (F-11) | §11 (rev3 §4.8) | — |
| Alembic shape, unique index, downgrade, pre-launch cleanup | §12, §12.0 | **rev3 §4.7 one-revision** and **rev5.2 Rev 2 backfill / live-link dedupe / F-4** are SUPERSEDED by simplified split B + revoke-in-place |
| Rollout, CD-01 pins, Gate M evidence | §13 | rev4 Owner item 6 `head_sha == source_sha` mechanism (R4-3) |
| Deploy / FE–BE gap / rollback | §14 | — |
| Test plan, scope, Owner items, evidence gaps | §15–§19 | — |
| Trigger JSON comparison, trip KEY SHARE, create_all parity, approve contract, keyring, CHECK, FE fallback discriminator, rules table | §5.1, §8–§10, §12, §14, §15, §21 | rev5 trigger SQL; rev5 L253 deadlock claim; rev5.3 bump-trigger-only lock-order claim; same-txn NOT VALID/VALIDATE; unconditional 404 fallback |

If a sentence in rev1–rev5.4 conflicts with this file, this file wins.

---

## 0.1 Historical: Delta vs rev5 (already in rev5.1) (`507190119c4554ad2b04beb8aa45586df865a439b8ef4b24011462bc5643d350`)

- **R5-1 (HIGH).** Trigger compares `tags` and `dietary_tags` as `OLD.col::jsonb IS DISTINCT FROM NEW.col::jsonb`. Those two columns are PostgreSQL `json` (not `jsonb`) at this SHA; every other compared column has a native equality operator. A PostgreSQL test must UPDATE each content column type.
- **R5-2.** The trigger takes `SELECT 1 FROM trip WHERE id = (SELECT trip_id FROM plan WHERE id = <plan_id>) FOR KEY SHARE` **before** `UPDATE plan`. rev5's "trigger cannot deadlock" claim is withdrawn. Full lock graph is in §8. Waiting on `FOR UPDATE` vs `FOR KEY SHARE` is fine; cycles are not. (**Further corrected in this revision by N-4:** the bump trigger alone is not sufficient for `plan_change` INSERT-first writers.)
- **R5-3.** One SQL source module is shared by the Alembic migration and `event.listen(PlanItem.__table__, "after_create", DDL(...))`, so `create_all` environments (`conftest.py:118`, `seed.py:90`, `init_schema.py:46`) also get the function and trigger. A parity test checks `pg_trigger` / `pg_proc` on both an Alembic-built and a `create_all`-built schema. No SQLite pytest database exists at this SHA.
- **R5-4.** Approve contract is fully specified in §9.5. Staleness is derived (`approval.bound_itinerary_revision != plan.itinerary_revision`); no `stale_at` column. Uniqueness is `(trip_id, membership_id)` with upsert, not `(trip_id, user_id, itinerary_revision)`. No withdraw/unapprove in v1. Close is **written** at approve and at remove/quarantine; retrieve/preview/join/ensure/replace only **classify**.
- **R5-5.** Frontend fallback only on 405, or on 404 whose string `detail` is not a known invite error. Once per page load, sticky, never on a known invalid/expired/revoked response.
- **R5-6.** Rev 1 adds a plain `CHECK (itinerary_revision >= 0)` in the same transaction as `ADD COLUMN`. `NOT VALID` / `VALIDATE` is dropped: `VALIDATE` in the same txn cannot use a lighter lock, because `ADD COLUMN` already holds ACCESS EXCLUSIVE until COMMIT.
- **R5-7.** Keyring is a versioned JSON document in one secret `CADENSY_INVITE_HMAC_KEY` (not `CADENSY_INVITE_HMAC_KEY_<id>` env vars). Parsing, 32-byte minimum, `active_key_id`, fail-closed rules, and rotation are in §5.1.
- **R5-8.** Owner-rules 1–8 traceability table is §21.
- **R5-9 (INFO, addressed).** No `TRUNCATE` of `plan_item` in product or demo scripts (none exists at this SHA). A helper must not also increment when the trigger will fire. Approve reads `itinerary_revision` under `FOR UPDATE` with `populate_existing`. No trigger on `plan` (no recursion). `plan_item.plan_id` has no `ON DELETE CASCADE`.

---

## 0.2 Owner decision as relayed by DD 2026-11-23

The following is recorded as an **Owner decision as relayed by Cadensy_DD on 2026-11-23**. This document's author has not seen it directly from the Owner. It is **pre-launch cleanup**, not a change to the post-go-live lifecycle rules (rules 1–8 still govern production invites). Executing the cleanup is a **destructive production data operation** and still requires explicit Owner authorization at execution time (folded into Owner item 1).

1. The 180-day validity window applies uniformly to all production invites, including existing trips.
2. There are no real users yet. Existing legacy invite records (30-day random tokens) and related test approvals are disposable pre-launch data and may be reset or retired. Do not preserve legacy 30-day expiry, and do not offer cross-device recovery of old tokens.
3. No grandfathering and no special extension for historical test trips. This **removes** former Owner item 4 option B and makes former Owner item 3 (null-expiry cap-on-read) **moot**.
4. The first successful HMAC invite creation after go-live establishes that trip's immutable `first_invite_created_at` / `invite_window_ends_at` (180 days). Pre-launch trips must not carry a pre-existing window into production.
5. Explicit replace remains: new invite ID + token, same expiry.
6. Preserve revocation history and activation events for invites created under the production system.
7. Remove legacy-compat complexity that existed only to preserve pre-launch data. Cleanup mechanism, FK consequences, and the simplified split are in §12.0.

---

## 1. Binding decisions

### 1.1 Owner rules (`DECIDED`)

1. One active invite link per trip by default.
2. Organizer refresh / re-login / new device / copy / WeChat share always restores the SAME link; no new token.
3. Validity starts at the trip's FIRST successful invite creation; maximum 180 days.
4. Organizer explicit replace: revoke the old invite, create a NEW invite ID and token; trip ID, first-invite-created time, and final expiry stay unchanged.
5. The link expires early when ALL currently active members formally approve the final trip plan. Not `first_decision_completed`.
6. Keep revoked invite history and their activation events.
7. Option C: HMAC-SHA256 recoverable token, dedicated key, organizer-only retrieve, hash-at-rest.
8. Replacing a link must never extend the 180-day validity.

These are design direction only, not authorization to implement, migrate, change workflows, create a secret, or deploy.

### 1.2 DD-DECIDED (Owner may override)

- **Epoch:** do **not** bind approvals to `Plan.revision`. Use dedicated `plan.itinerary_revision`, bumped only by itinerary-content changes (§9).
- **N-8:** join does **not** reset or stale-mark approvals. A new unapproved member simply means the live set is not-all-approved.
- **F-6 remainder:** an increment of `itinerary_revision` before early close stale-marks existing approvals; members must re-approve. Once `trip.final_plan_approved_at` is set, early close is **terminal**; later itinerary edits do not reopen the invite. Close is written only when the M-1 guard holds (§9.5).
- **F-11:** refuse share events on time-expired or early-closed invites (same as today's revoke refusal).
- **R4-1:** a PostgreSQL trigger on `plan_item` is the **authoritative** bump mechanism. A Python helper may still be called for clarity; it is not the safety net. The trigger covers ORM, bulk, Core, and raw SQL (including `clear_plan.py:40` and `purge_demo_data.py:148`).
- **R4-2:** the bump is the SQL `UPDATE plan SET itinerary_revision = itinerary_revision + 1` performed by the trigger. Approve and close evaluation lock **trip → (invite if the transaction will read/write invites) → memberships → plan**, record the `itinerary_revision` seen under the plan lock, and reject/stale-mark the approval if it differs.
- **R4-3:** Owner item 6 uses the archived evidence chain in `release-runtime-off-backend.yml:256-302` (not `head_sha == source_sha`). Target must equal `APPROVED_ALEMBIC_HEAD`. The evidence run must have been created after the migration image digest was published. Drop the `head_sha == source_sha` rule.
- **R4-5:** deadlock `keep` and settle `KEEP_CURRENT` empty-patch `PlanChange` rows do **not** bump. The rule is a content-column change on `plan_item`, enforced by the trigger (`UPDATE OF` + `IS DISTINCT FROM`). There is no trigger on `plan_change`.
- **R4-6:** `reset_demo.py:55-71` is in the inventory. Purge / reset / clear scripts must handle `final_plan_approval` and trip anchor columns, in FK-safe order.
- **R4-7:** a Rev1-committed / Rev2-failed intermediate head is an allowed **recovery** start. Both split revisions have downgrade steps. Table `LOCK` order follows the app order (trip, then `invite_link`, then `plan`). The `itinerary_revision` CHECK is a **plain CHECK in Rev 1** (R5-6; same-transaction `NOT VALID`/`VALIDATE` is not used). The PostgreSQL ≥ 11 assumption for metadata-only `ADD COLUMN` defaults is `UNKNOWN` and must be confirmed by Cloud (read-only) before migration.
- **R4-8:** there is no once-per-transaction Python flag. Multiple monotonic trigger bumps in one transaction are harmless. The helper, if used, also just issues the SQL increment and does not stash a session flag.
- **R4-9:** the new frontend falls back to path routes on 404/405 from the new body/header endpoints. Rollback order is in §14.
- **R4-10:** **SUPERSEDED** by the Owner decision as relayed by DD 2026-11-23. Former Owner item 4 option B (explicit exception to rule 3) is removed. There is no grandfathering and no 180-day exception (§6, §17).
- **R5-1:** compare `json` columns via `::jsonb` casts in the trigger predicate. The only `json` columns in the compared list are `tags` and `dietary_tags`.
- **R5-2:** the AFTER bump trigger takes `FOR KEY SHARE` on the parent trip **before** `UPDATE plan`. That is necessary but **not sufficient**: it does not cover writers that `INSERT plan_change` before any `plan_item` write (**N-4**; claim of complete trip-then-plan preservation in rev5.3 is **withdrawn** — §8).
- **N-4:** a `BEFORE INSERT` trigger on `plan_change` takes trip `FOR KEY SHARE` (via `plan.trip_id`) before the `plan` FK RI check. Shared module, parity, verifier catalog, Rev 1 up/down. FK audit in §8.1.
- **R-1 / R1b-1:** post-cutover joinability requires an HMAC-era row. Rev 2 cleanup (and any later re-run) revokes only unrevoked **non-HMAC** rows; it must never revoke a live HMAC link. Step 7 runs only on the first Rev 2 apply (marker in §12.0). A post-go-live re-run needs Owner authorization at execution time (item 1).
- **N-5:** `save_mine` takes trip `FOR KEY SHARE` before updating `membership.status` (`preferences/service.py:213-216`). Do not reorder the `record_preferences_submitted` insert.
- **D-2:** §10 status precedence; `non_hmac` wins over `none`.
- **S-1:** residual multi-item writer deadlock risk; guard is deterministic item-id lock order or a held trip lock, not a bump-trigger change.
- **C-1:** Rev 2 asserts `final_plan_approval` is empty and aborts; no conditional DELETE.
- **P-1:** evaluate `content_changed` only inside the UPDATE branch of the AFTER bump trigger.
- **R5-3:** shared SQL module + `after_create` DDL; parity test of Alembic vs `create_all`; no SQLite trigger substitute.
- **R5-4:** derived staleness; unique `(trip_id, membership_id)` with upsert; approve contract and close-evaluation points in §9.5; no unapprove in v1.
- **R5-5:** fallback only on 405 or 404 without a known invite `detail` string; once per load; never on a known invalid-invite response.
- **R5-6:** plain CHECK in Rev 1; no `NOT VALID`/`VALIDATE` in the same transaction.
- **R5-7:** one versioned JSON keyring secret as specified in §5.1.
- **R5-8:** §21 traceability table is normative for reviewers.
- **M-1:** early-close guard in §9 / §9.5. This is a **DD interpretation of Owner rule 5** (not a new Owner item). The Owner may override.
- **N-2 / N-2b:** defer plan-row updates until after `plan_item` writes in legacy generate and `upsert_demo_seed` (§8). After `_upsert_items`, call `db.flush()` before assigning plan fields — flush order, not Python statement order, is what matters. Flag Cadensy_AI_Agent; minimal generator change only.
- **N-3:** a `BEFORE INSERT OR UPDATE OF plan_id` trigger on `plan_item` takes trip `FOR KEY SHARE` so the trip lock precedes the RI plan lock. Keep the AFTER bump trigger.
- **I-1:** a blocked generate not refreshing `plan.currency` is acceptable; currency refreshes on the next successful generate.
- **D-1:** the full `content_changed` predicate, including `::jsonb` casts, is stated in §9.3 (do not refer to a superseded revision).
- **T-1:** removal/quarantine close tests in §15.
- **L-1:** trigger `plan_id` handling as in §9.3 (INSERT NEW, DELETE OLD, UPDATE bumps both plans).
- **L-2:** post-close approve is 409 `invite_closed`; re-admission approvals follow current-incarnation rules in §9.5.
- **L-3:** shared DDL details and `pg_get_*def` parity in §9.6.
- **L-4:** keyring fail-closed and retention wording in §5.1 (absent-only disable; short old keys ignored).

---

## 2. Delta vs rev4 (`728da30d6a11217ced3ca2bdc4688cda5fcf02625fa5f99497bf565a119d586f`)

- **R4-1.** Authoritative bump is a `plan_item` AFTER INSERT/DELETE/UPDATE trigger that executes `UPDATE plan SET itinerary_revision = itinerary_revision + 1`. Python helper kept for clarity only. `before_flush` is not authoritative (misses `clear_plan.py:40` and `purge_demo_data.py:148` bulk deletes). No trigger on `plan_change` or `plan_item_comment`.
- **R4-2.** Approve/close take `SELECT plan … FOR UPDATE` after trip and memberships (and invite only if the txn touches invites). The approval stores the revision seen under that lock and is rejected if it differs. Compared to join (`membership_join.py:184-195` / `:254-296`: trip → invite → memberships → plan) and durable kernels (trip first). Quarantine locks invites *after* plan (`membership_quarantine.py:118-127`); deadlock is avoided because both paths take the trip row first.
- **R4-3.** Owner item 6 now reuses the archived chain (`release-runtime-off-backend.yml:263-302`: `SOURCE_SHA`, `MIGRATION_DIGEST`, `Migration task: <ARN>`, CloudTrail `RunTask`, per-task log stream `ecs/<container>/<task-id>`, final `Running upgrade … -> <new_head>`), plus target == `APPROVED_ALEMBIC_HEAD` and run created after the digest. `head_sha == source_sha` is dropped (would have rejected legitimate run `45046230145`).
- **R4-4.** This file is the consolidated self-contained design. rev3 §4.7 single-transaction revision is marked SUPERSEDED. Downgrade covers both split revisions and `itinerary_revision`.
- **R4-5.** Full `plan_item` column classification is in §9.3. Empty-patch `PlanChange` (deadlock keep `organizer.py:199-206`; settle `KEEP_CURRENT` `_log` at `orchestrator.py:828`) does not bump. `settled_at` alone (already-settled keep-current rewrite) does not bump.
- **R4-6.** `reset_demo.py:55-71` added. Purge/reset/clear must delete `final_plan_approval` in FK order and reset/clear trip anchor and `final_plan_approved_at` as specified in §12.5. Eval harness `run_real_trip_tools_trace.py:178-236` is non-production (excluded from product inventory; still covered by the trigger if pointed at a real DB).
- **R4-7.** Intermediate Rev1 head is an allowed recovery start and is on the pin list. Downgrade for both revisions. `LOCK TABLE` order is trip, `invite_link`, `plan`. CHECK was `NOT VALID` then `VALIDATE` in rev5; **this file supersedes that with a plain CHECK in Rev 1 (R5-6)**. PG≥11 is `UNKNOWN` pending Cloud.
- **R4-8.** Once-per-txn Python flag dropped.
- **R4-9.** New frontend falls back to path routes on 404/405. Rollback: frontend-first is still forbidden for rollout; for rollback, path fallback lets a new frontend survive an old backend; if fallback is not yet shipped, revert frontend before backend.
- **R4-10.** Owner item 4 option B is an explicit exception to the 180-day maximum. (**SUPERSEDED** in this revision by the Owner decision as relayed by DD 2026-11-23; see §0.2 / §17.)

---

## 3. Current model at `27c3489936ec34bebbbc9562a1d99e8717df6751`

Carried from rev3 §3, with pin completeness from rev4. All claims `STATIC-VERIFIED` unless labeled otherwise.

### 3.1 Invite persistence and writers

`InviteLink` (`models.py:208-221`): `id`, `trip_id`, unique `token_hash`, `is_primary`, nullable `expires_at`, nullable `revoked_at`. Only `uq_invite_link_id_trip` (`:213`). No unique partial index on one unrevoked invite per trip.

`Trip` (`models.py:112-147`): no `first_invite_created_at`, invite window, or final-plan-approval column. Status comment "按日期自动流转,没有 locked" (`:129-130`). `planning_facts_revision` is present (`:140-144`).

`INVITE_TTL = timedelta(days=30)` (`service.py:64`). Tokens are `secrets.token_urlsafe(32)` (`:289`); at rest `_token_hash` is SHA-256 hex (`:255-256`).

`create_invite` (`service.py:278-301`): no `FOR UPDATE`; revokes all unrevoked rows; inserts `expires_at=issued_at+INVITE_TTL`; `record_invite_created`. Concurrent first-creates can leave two unrevoked rows. `active_invite_metadata` documents that case and orders `is_primary DESC, created_at DESC` (`:323-343`, `:341`).

`GET /api/trips/{trip_id}/invite` (`main.py:2064-2078`) returns metadata only ("The raw token is never returned again."), no `Cache-Control`, no commit, 404 when no active row. `POST /api/trips/{trip_id}/invite` (`:2038-2061`) always mints and returns `token` and `url`.

`_require_organizer` (`service.py:263-269`): same trip, `role == "organizer"`, `is_membership_access_authorized`.

### 3.2 Public token-in-path routes and logs

- `GET /api/invites/{token}` (`main.py:1606-1607`)
- `POST /api/invites/{token}/join` (`main.py:2111-2112`)
- `POST /api/invites/{token}/account-join` (`main.py:2152-2153`)

Frontend: `TripAppState.jsx:895`, `:903`, `:911`. Shared/WeChat links use `#/join/:token` (`TripAppState.jsx:1409`; `FinalApp.jsx:1734`). A fragment is not sent to the server; the leak is the **API** path.

Backend image: `uvicorn app.api.main:app --host 0.0.0.0 --port 8000` (`backend/Dockerfile:24`), default access log on, no redaction filter at this SHA. ALB/CloudWatch access-log enablement is `UNKNOWN`. Public classify: 410 `invite_revoked` / `invite_expired`, 404 `invite_invalid` (neighbor of `main.py:1624-1625`). CORS: `allow_methods=["*"]`, `allow_headers=["*"]` (`main.py:187-192`).

### 3.3 Join locking vs create

Join (`membership_join.py`): untrusted hash lookup (`:59-68`); then `session.begin()`, `SELECT trip … FOR UPDATE` (`:71-80`); invite `FOR UPDATE` (`:83-99`); memberships (`:102-108`); plan (`:112-118`). Guest/account order is **trip → invite → memberships → plan** (`:184-195`, `:254-296`). `create_invite` does not participate. `session.py:37-38` sets no `isolation_level` (default READ COMMITTED assumed; a deployment override is `UNKNOWN`).

### 3.4 Activation events

`ActivationEvent` (`models.py:224-303`). Privacy check forbids `token`, `token_hash`, `invite_token`, `url` in metadata (`:255-262`). FK `fk_activation_event_invite_same_trip` is `ON DELETE CASCADE` (`models.py:270-274`; creating migration `c8e1d4a90b27_activation_event_and_first_submitted.py:95-99`). Invite rows must be revoked and kept, never deleted — including in the §12.0 pre-launch cleanup — or those events would cascade-delete. `record_invite_created` dedup is `invite_created:{invite_id}` (`activation.py:182-192`). Share recording refuses only revoked invites today (`service.py:411-412`).

### 3.5 `first_decision_completed` is not final-plan approval

Allowlisted outcomes: `activation.py:74-84` (strings `:77-81`). Required metadata `outcome`: `:93`. Writer: `:309-317`. Call sites: `settle_round` (`orchestrator.py:798-857`, zero-vote emits nothing `:843-845`); `decide_proposal` / `_record_confirm_terminal` (`:969-1052`); `resolve_deadlock` (`organizer.py:166-234`). Product definition: `application_docs/activation-funnel.md:47`, `:53-104`. Confirm "everyone accepted" is per-proposal affected members (`orchestrator.py:933-950`, `models.py:905`), not the whole plan.

Trip status is date-driven (`models.py:129-130`). `Plan.status` is `active | blocked` (`:382`). `DecisionRound` is `open | closed` (`:879`). Current participants: `joined` or `preferences_submitted` and not quarantined (`membership_policy.py:18-22, 40-64, 87-90`). There is no all-member final-plan approval state.

`Plan.revision` (`models.py:377-381`) is incremented by durable kernels on any `state_changed` (`durable.py:370-373`, `trip_durable.py:128-135`, `generator_durable.py:447-450`) and by join (`membership_join.py:125-132`). Production `APP_ENV` refuses protected B2C writers (503; gates require non-production, `main.py:299-360`). Production itinerary writes are the legacy paths, which do **not** increment `Plan.revision`, except join (always) and a protected scheduler that is off in production (`jobs/scheduler.py:35-39`, `:71-86`). This is why `Plan.revision` is the wrong approval epoch.

### 3.6 CD-01 pins and PR #55 (summary)

Alembic head at this SHA is `c8e1d4a90b27`. Live pins still at that head are listed in §13. No dispatchable Gate M workflow exists under `.github/workflows/` (retired to `AWS/archive/workflows/retired-release/release-pr06b-schema-migration.yml`). `release-pydantic-backend.yml:185/:191` still name the missing path and do not bind source SHA or target revision. Tests `test_cd01_delivery_safety.py:22-23` point at archived files. PR #55 existed because PR #54 moved the Alembic head without pins, then start-allowlist and evidence strings lagged; both failure classes must be avoided.

---

## 4. Intent of the change (problem statement)

GET never returns the raw token (`main.py:2070`); the frontend caches it in `localStorage` (`cadensy.inviteLink.v1`) and logout clears it. POST always revokes and mints a new 30-day random token. After refresh, re-login, or a new device, the organizer cannot copy the same link. Option C is a recoverable HMAC token, organizer-only retrieve, one active link, 180-day window from the first successful HMAC create after go-live, explicit replace as a new row, and early close when all current participants formally approve the final plan.

---

## 5. Option C token, key lifecycle, threat, and logging

### 5.1 Derivation and `key_id`

Dedicated secret `CADENSY_INVITE_HMAC_KEY`. Do **not** reuse `CADENSY_B2C_HMAC_KEY` or `CONVERSATION_SUBMISSION_HMAC_KEY`.

**Supply format (`DD-DECIDED`, R5-7):** one versioned JSON document in that single secret (not `CADENSY_INVITE_HMAC_KEY_<id>` environment variables; rev3 offered both, this revision picks the JSON map because ECS injects one secret more cleanly). Example:

```json
{
  "v": 1,
  "active_key_id": 1,
  "keys": {
    "1": "<unpadded base64url of >=32 raw bytes>"
  }
}
```

Parsing (fail-closed; never log key material):

- Missing or blank env: treat as no keyring.
- `json.loads` must yield an object. `v` must be the integer `1` (unknown version is malformed).
- `active_key_id` must be an integer in `1..2^32-1`.
- `keys` must be an object whose names are decimal `key_id` strings in that same range and whose values are unpadded base64url encodings of the raw secret bytes.
- After decode, the **active** key must be at least **32 raw bytes** (not 32 characters of base64). A short, undecodable, or out-of-range **non-active** id is **ignored** (treated as absent) and does not invalidate the rest of the keyring (L-4).
- `active_key_id` must be present in `keys` and must decode to ≥32 raw bytes. Extra (old) ids are allowed.
- Malformed JSON, wrong types, missing/invalid `active_key_id`, `v` ≠ 1, or `key_id` 0 on the **active** entry: treat as no keyring.
- There is no `"disabled"` list in `v: 1`. A key is disabled only by **absence** from `keys`. Retrieve for an absent `key_id` is 503, never a substitute key.

Fail-closed (extends N-6): a missing or malformed keyring, a short or missing **active** key, or a row whose `key_id` is absent from `keys` causes **503** on HMAC mint and on organizer re-derive only. No random-token fallback. Absence or malformation must **not** raise at import or lifespan (no startup crash). Independent paths in §5.2 still work.

Parse the document **once per process** and cache it (first use or process start). Reload only on deploy / process restart; do not re-read the env on every request.

Rotation steps (Owner item 2):

1. Generate a new ≥32-byte key and a **never-reused** `key_id` (including ids retired after compromise).
2. Add it to `keys` without changing `active_key_id`; deploy.
3. Switch `active_key_id` to the new id; deploy. New mints use only the active id.
4. Keep an old id in `keys` until every `hmac_v1` row with that id is expired or revoked, and for at most 180 days after that id last stopped being active. Then remove it from `keys`.
5. Compromise: stop minting with that id; **remove it from `keys`** (absence only; `v: 1` has no disabled list) so retrieve is 503 rather than a substitute key; then Owner-authorized mass revoke (prefer revoke-and-let-organizer-replace over silent remint). A compromised id is **never reused**.

Message (unchanged):

`cadensy.invite.hmac.v1` ‖ `be32(key_id)` ‖ `be32(len(invite_id))` ‖ `invite_id` ‖ `be32(len(trip_id))` ‖ `trip_id`

`invite_link.key_id` is NULL on `legacy_random` rows and required on `hmac_v1` rows. `token = base64url(HMAC-SHA256(key[key_id], message))` without padding. At rest: existing SHA-256 hex of the utf-8 token. Join, preview, and classify stay hash lookups and do not need the HMAC key. After a hash hit, post-cutover joinability additionally requires an **HMAC-era** row (`token_scheme = 'hmac_v1'` and `key_id IS NOT NULL`). Any other row is non-joinable and is reported 410 `invite_revoked` or 404 `invite_invalid`, regardless of `expires_at` (R-1; §10). Retrieve recomputes with the row's `key_id` from the keyring, then `hmac.compare_digest(sha256(token), row.token_hash)` before returning. Mismatch: no token, redacted log, no implicit remint.

### 5.2 Fail-closed scope (N-6)

The key is required only to **mint** a new `hmac_v1` token (POST ensure when `first_invite_created_at IS NULL`, or explicit replace) and to **re-derive** a token for organizer GET when status would be `active_hmac`. Those operations return **503** if the current or row `key_id` is missing or too short. No random-token fallback.

Independent of the key (must work without it): public join and preview (including the R-1 HMAC-era reject); organizer share; organizer GET for `none` / `non_hmac` / `revoked_replaceable` / `expired` / `window_expired` / `early_expired`; revoke. Absence of the key must **not** raise at import or lifespan (no startup crash).

### 5.3 Cache-Control (N-7)

`Cache-Control: no-store` and `Pragma: no-cache` on organizer retrieve / ensure / replace and on public preview (path GET `main.py:1606` and the new body/header POST). FastAPI 422 may echo a malformed body token to the same caller only; do not log it.

### 5.4 Access-log redaction and token placement (F-2)

Mandatory in this change set: a uvicorn/app access-log filter that replaces the token path segment in `/api/invites/{token}`, `/join`, and `/account-join` with `<redacted>` (also redact a `token` query parameter if one is ever added). Test: a known token must not appear in captured access log lines.

Do not put the token in a query string (worse Referer leakage). Add body or header variants (`POST /api/invites/preview` and join/account-join with `{ "token": "..." }`) and switch the frontend (`TripAppState.jsx:895, :903, :911`) to them. Keep path routes through a deprecation window, still redacted, so already-shipped clients and `#/join/:token` WeChat links do not break. **New frontend fallback (R4-9 / R5-5):** see §14. Fall back to path routes only on 405, or on 404 whose string `detail` is not a known invite error; once per page load; never on a known invalid-invite response. ALB/CloudWatch access-log settings are `UNKNOWN` (Cloud to check); uvicorn redaction does not rewrite ALB lines, which is why moving the token out of the path matters.

### 5.5 Threat-model notes

A database dump of hashes is not a join token. Retrieve is organizer-only. Public preview does not leak approval details. HMAC recoverability means key + `key_id` + ids reconstruct the token. Unique index + trip lock close the two-live-links race in today's `create_invite`. F-1 (no silent remint after revoke) keeps revoke meaningful. Access logs are a current leak (`Dockerfile:24`); redaction + path-to-body are required controls.

---

## 6. Persistence of `first_invite_created_at` and the 180-day window

**Normative after the Owner decision as relayed by DD 2026-11-23:** trip-level `trip.first_invite_created_at` (timestamptz, nullable, set once) and immutable `trip.invite_window_ends_at` set at the same time to `first_invite_created_at + 180 days`. There is **no** migrate-time exception and **no** grandfathering. Each HMAC invite row's `expires_at` is a **copy** of `invite_window_ends_at`, never `now()+180d`. 180 days is `timedelta(days=180)` in UTC on the stored timestamptz. This 180-day window applies uniformly to all production invites, including on trips that existed before go-live.

**Pre-launch trips do not carry a window into production.** These two columns are **new** in this migration. They are created nullable and are **never backfilled** (not from `MIN(invite_link.created_at)`, not from migrate-now, not from any legacy `expires_at`). After Rev 1 and after the Rev 2 cleanup, every existing trip has `first_invite_created_at IS NULL` and `invite_window_ends_at IS NULL`. The first successful **HMAC** invite creation after go-live is what sets both, immutably.

- **No production invite yet** (`first_invite_created_at` is NULL): GET returns `none`, `can_create=true`, no token — even if revoked pre-launch `invite_link` rows exist for the trip. Only this case may mint from POST ensure, and only as an explicit create, not first paint.
- **All production invites revoked, window open, not early-closed:** GET/ensure return `revoked_replaceable`. No mint. Explicit replace reuses the original anchor and `expires_at`.
- **Window elapsed:** ensure and replace fail closed. No new window.
- **Early-closed:** ensure and replace fail closed.

Writer, inside `SELECT trip FOR UPDATE`:

```
if trip.first_invite_created_at is NULL:          # only first successful HMAC create after go-live
    trip.first_invite_created_at = now
    trip.invite_window_ends_at = first + 180 days
if now >= trip.invite_window_ends_at: reject
if trip.final_plan_approved_at is not NULL: reject
new_row.expires_at = trip.invite_window_ends_at
```

Pre-launch 30-day tokens are **not** joinable and are **not** retrievable after the cleanup (§12.0). There is no cap-on-read for null `expires_at`, no "stay joinable until the old 30-day expiry," and no cross-device recovery of old tokens.

---

## 7. One active link, restore, ensure vs explicit replace (F-1)

At most one unrevoked `invite_link` per trip (unique partial index, §12). Time-expired or early-expired rows may remain unrevoked and are not joinable.

**Restore (rule 2):** GET only. First paint is GET, never a minting path. For `active_hmac`, GET returns the same token. `localStorage` may cache for instant paint; GET is the source of truth.

**API (`PROPOSED`):**

- `GET /api/trips/{trip_id}/invite` — retrieve only. Never mints.
- `POST /api/trips/{trip_id}/invite` (ensure) — mints **iff** `first_invite_created_at IS NULL`. Otherwise a non-minting status read (including post-revoke of a production HMAC invite).
- `POST /api/trips/{trip_id}/invite/replace` — explicit gesture. Revokes the current unrevoked row (kept), inserts a new `hmac_v1` row with a new id and token, **same** `expires_at` (rule 8 / Owner relay point 5; never extends the window). Allowed when `can_replace` is true. The frontend "生成新链接" button maps here, with the existing warning.

**F-7 is retired.** It existed only to let an unrevoked legacy row past its own 30-day `expires_at` be replaced inside a backfilled window. After the pre-launch cleanup there are no unrevoked legacy rows and no backfilled window, so `expired_replaceable` is removed (§10).

Today's POST always revokes and mints (`service.py:278-301`). That must not survive this change. Revoke remains an in-place `revoked_at` write and does not clear the trip anchor.

---

## 8. Transactional correctness and lock order

**Isolation (`PROPOSED`):** process default (READ COMMITTED unless a deployment changed it). No SERIALIZABLE for this feature. Correctness = lock order + unique partial index + SQL-atomic revision bump.

**Invite ensure / first-create / replace / revoke:** `SELECT trip … FOR UPDATE` first (same as `_locked_trip`, `membership_join.py:71-76`), then unrevoked invites `FOR UPDATE`, then revoke/insert, `record_invite_created` in the same transaction. Retrieve (GET) is read-only, no `FOR UPDATE`.

**Approve and close evaluation (R4-2, `DD-DECIDED`):** `SELECT trip … FOR UPDATE`, then all current memberships `FOR UPDATE` (ordered by `TripMembership.id`, as join does at `:106`), then `SELECT plan … FOR UPDATE` (ordered by `Plan.id`). Lock unrevoked invites **only if** this transaction will also read or write invites, and if so, lock them **after trip and before memberships and plan**, matching join (`:184-195`). Under the plan lock, read `plan.itinerary_revision`. An approval write stores that value as `bound_itinerary_revision`. If, still under the lock, the stored value would differ from the current plan revision, the approval is rejected as stale (client retries against the new revision). Close evaluation uses the same snapshot: the live current-participant set must all have non-stale approvals bound to that locked revision.

**Comparison with existing orders (`STATIC-VERIFIED`):**

| Path | Order | Cite |
| --- | --- | --- |
| Join (guest/account) | trip → invite → memberships → plan | `membership_join.py:184-195`, `:254-296` |
| Trip/plan durable kernels (remove, most protected writers) | trip first (`trip_kernel.py:176-182`, `kernel.py:259-265`), then actor credential + actor membership, then all memberships if requested, then plan (`trip_kernel.py:193-207`) | No invite lock in the kernel |
| Quarantine mutation | After the kernel's trip → memberships → plan, locks guest sessions then **unrevoked invites** | `membership_quarantine.py:118-127` — invite **after** plan |
| Legacy `create_invite` / most legacy itinerary writers | no trip/plan `FOR UPDATE` | `service.py:278-301`; QA: the only `with_for_update` in `decisions/`, `plans/`, `main.py`, `jobs/` is `manual_items.py:61` (protected path) |


**Lock graph (R5-2, `STATIC-VERIFIED` cites; conflict behaviour from documented PostgreSQL lock modes, not executed):**

| Path | Order and locks | Cite | Later trip-touching writes in the same txn |
| --- | --- | --- | --- |
| Join, guest | `trip FOR UPDATE` → invite `FOR UPDATE` → memberships `FOR UPDATE` → plan `FOR UPDATE` | `membership_join.py:184-195` (`_locked_trip` `:71-76`, invite `:83-88`, memberships `:102-108`, plan `:112-118`) | `record_member_joined` insert (`:207-214`) takes RI `FOR KEY SHARE` on trip / invite / membership; already holds stronger trip lock |
| Join, account | trip `FOR UPDATE` → AuthSession `FOR UPDATE` → invite → memberships → plan | `:254-296` (invite at `:275`, memberships `:276`, plan `:296`) | Same activation RI; AuthSession is outside the trip/invite/plan set |
| Approve / close write (this design) | trip `FOR UPDATE` → (invite `FOR UPDATE` only if the txn reads/writes invites, before memberships) → memberships `FOR UPDATE` (ordered by id) → plan `FOR UPDATE` (`populate_existing`) | §8, §9.5 | Approval upsert FKs take `FOR KEY SHARE` on trip/membership (already held) |
| Ensure / first-create / replace / revoke (this design) | trip `FOR UPDATE` first, then unrevoked invites `FOR UPDATE` | §8 | Invite insert RI `FOR KEY SHARE` on trip (already held `FOR UPDATE`) |
| Remove | Kernel: trip → actor credential + membership → all memberships → plan; mutation re-locks actor/target memberships and guest sessions; **does not lock invite** | `trip_kernel.py:176-207`; `membership_remove.py:53-67` | Close evaluation uses the already-locked plan; no itinerary bump |
| Quarantine | Kernel trip → memberships → plan, then mutation locks guest sessions and **then** unrevoked invites | `membership_quarantine.py:118-127` after `:139-145` / `trip_kernel.py:176-207` | Invite-after-plan inverts join; safe only because both paths take trip first |
| Legacy itinerary writers (booking, notice, settle, decide / confirm, and deadlock resolve) | Generally **no** trip or plan `FOR UPDATE`. A single flush typically emits `INSERT plan_change`, then `UPDATE plan_item`, then `INSERT update_notice` | booking `orchestrator.py:621-643` (`_log` `:629`, notice `:631`, flush `:643`); notice `_do_notice` `:658-678`; settle `:817-842`; confirm apply `:1020-1035`; deadlock `organizer.py:201-231` (including empty-patch `keep`). `PlanChange.plan_id` FK `models.py:836`; `UpdateNotice.trip_id` FK `models.py:959` | **N-4:** `INSERT plan_change` RI takes `plan FOR KEY SHARE` **before** any trip lock. `INSERT update_notice` RI takes trip / `plan_item` KEY SHARE. Activation-event inserts also FK to trip (`models.py:264-268`). Flush order is QA EXECUTED-PASS at ORM level (SQLAlchemy 2.0.52, in-memory SQLite); PostgreSQL statement order NOT RUN |
| Legacy `create_invite` (today, pre-change) | No trip `FOR UPDATE`; revoke/insert invite | `service.py:278-301` | Invite FK takes `FOR KEY SHARE` on trip. **Pre-existing** deadlock with join (invite vs trip). New trip-first ensure/replace removes it |
| Protected manual add | Kernel locks trip then plan, then children `plan_item FOR UPDATE` | `manual_items.py:61` | Item-after-plan inverts the trigger's item → (trip KEY SHARE) → plan order. Protected writers are off in production |
| AFTER bump trigger (R5-2) | On bump: `SELECT 1 FROM trip WHERE id = (SELECT trip_id FROM plan WHERE id = <plan_id>) FOR KEY SHARE`, **then** `UPDATE plan SET itinerary_revision = itinerary_revision + 1` | §9.2 | Necessary once a `plan_item` write has begun, but **not sufficient** for writers that `INSERT plan_change` first (N-4). `UPDATE plan` does not change `plan.trip_id`, so the plan→trip FK is not rechecked. Subsequent `UpdateNotice` / `ActivationEvent` RI on trip is compatible **only if** the trip KEY SHARE is already held |
| N-3 `plan_item` BEFORE trigger | `BEFORE INSERT OR UPDATE OF plan_id`: trip `FOR KEY SHARE` (both trips in `trip.id` order on a move), then return NEW | §8, §9.3 | Runs before the `plan_item.plan_id` RI check can take `plan FOR KEY SHARE` |
| N-4 `plan_change` BEFORE trigger | `BEFORE INSERT`: trip `FOR KEY SHARE` via `SELECT trip_id FROM plan WHERE id = NEW.plan_id` (plain SELECT on `plan`, then trip KEY SHARE), then return NEW | §8, §8.1 | Runs before the `plan_change.plan_id` RI check can take `plan FOR KEY SHARE` |

**Conflict modes (documented PostgreSQL semantics, not executed):** `FOR UPDATE` conflicts with `FOR KEY SHARE` (one waits). Two `FOR KEY SHARE` locks are compatible. `FOR UPDATE` conflicts with `FOR UPDATE`. RI checks on INSERT/UPDATE of a referencing row take `FOR KEY SHARE` on the referenced row; they do not themselves take `FOR UPDATE`. **Waiting is fine. Cycles are not.**

**Withdrawn (rev5.3 §8, explicit):** the claim that the bump-trigger-then-`UpdateNotice` cycle "does **not** exist today, because legacy writers never hold the plan lock," and the claim that taking trip `FOR KEY SHARE` **before** `UPDATE plan` in the AFTER bump trigger is by itself sufficient to preserve trip-then-plan order against join, approve, and later `UpdateNotice` RI. Both are **false** for the main live legacy writers (N-4).

**N-4 cycle (`DD-DECIDED` fix; flush order QA EXECUTED-PASS at ORM level; lock modes documented PostgreSQL semantics, NOT RUN):** a legacy booking / notice / settle / confirm flush, and deadlock resolve including empty-patch `keep`, emits `INSERT plan_change` first (`_log` at `orchestrator.py:274-283`, booking flush `:643`, notice `:678`, settle `:842`, confirm `:1035`; deadlock `:201-231`). `PlanChange.plan_id` is `ForeignKey("plan.id")` (`models.py:836`; baseline `f9ff23b7b84d_baseline_existing_schema.py:229`, no `ondelete`). That INSERT's RI check takes `plan FOR KEY SHARE` **with no trip lock**. Then `UPDATE plan_item` fires the AFTER bump (waits for trip KEY SHARE) and/or `INSERT update_notice` waits for trip KEY SHARE (`models.py:959`). Join / approve / close / remove / quarantine already hold `trip FOR UPDATE` and wait for `plan FOR UPDATE`, which conflicts with the writer's plan KEY SHARE. PostgreSQL aborts one transaction after `deadlock_timeout`. This cycle is pre-existing against join today (via `update_notice` after `plan_change`) and becomes newly frequent once approve/close joins the trip-first set. The N-3 `plan_item` BEFORE trigger does **not** fire on these paths (no `plan_item` INSERT or `plan_id` change). The AFTER bump trigger's trip KEY SHARE is too late: plan is already held.

**Fix (`DD-DECIDED`, QA option (a)):** a `BEFORE INSERT` row trigger on `plan_change`, `cadensy_plan_change_lock_trip`, that takes `FOR KEY SHARE` on the parent trip first (`PERFORM cadensy_lock_trip_for_plan(NEW.plan_id)`: a plain `SELECT trip_id FROM plan WHERE id = p` — no plan lock — then `SELECT 1 FROM trip WHERE id = … FOR KEY SHARE`) and returns NEW, **before** the `plan_change.plan_id` RI check can lock plan. `plan_change` is append-only (`models.py:828-830`: no update, no delete), so `BEFORE INSERT` is sufficient; do not add `UPDATE OF plan_id`. Shared helper `cadensy_lock_trip_for_plan(p)` is also used by the N-3 `plan_item` BEFORE trigger. Keep the AFTER bump trigger unchanged. This is part of the Rev 1 trigger DDL authorized by Owner item 1. Extend deadlock tests to the booking / notice / settle / confirm / deadlock-`keep` flush shape against both join and approve.

After this trigger: the writer holds trip KEY SHARE before taking plan KEY SHARE; a trip-first join/approve either waits on trip (writer never waits on them) or already holds trip, so the writer's BEFORE trigger waits without holding plan. No cycle. QA option (b) (plan `FOR NO KEY UPDATE` on approve/close) is not adopted.

Quarantine's invite-after-plan inversion remains safe on one trip because both sides take `Trip` first. Approve/close must never lock invite, memberships, or plan before trip. Migration `LOCK TABLE` remains **trip, then `invite_link`, then `plan`**.

**Residual cycle N-2 (`STATIC-VERIFIED`, LOW):** a transaction that `UPDATE`s `plan` *before* writing `plan_item` still deadlocks with join/approve. The trigger then waits for trip `FOR KEY SHARE` while the same txn already holds the plan row from the earlier `UPDATE plan`. Join/approve hold trip `FOR UPDATE` and wait for plan.

- Legacy `generate_plan` calls `_plan_for_trip` at `generator.py:191`, which assigns `plan.currency = trip.currency` on an existing plan (`:328`) before `_write_items` (`:286`, items flushed at `:1744-1764`). If currency actually changed, autoflush emits `UPDATE plan` first. Blocked early returns (`:250-253`, `:271-274`) update plan status and flush **without** writing items, so they do not start this cycle. After the currency assignment is moved to the post-item success block, a **blocked** generate no longer refreshes `plan.currency`. **I-1 (`DD-DECIDED`): that is acceptable.** Currency is refreshed on the next successful generate (the `:287-290` block). Do not add a currency write to the blocked branches just for freshness.
- `upsert_demo_seed.py` `_ensure_plan` assigns status/totals/currency on an existing plan (`:216-219`) before `_upsert_items` (`:330`, inserts at `:245`). Admin script, same shape.
- Protected `generator_commit.py:156-159` already writes items first, then plan fields, under the kernel's trip lock. No change.

**Pick (`DD-DECIDED`): reorder, not a new trip lock in the generator.** This is the minimal change in AI-runtime code. Flag **Cadensy_AI_Agent** coordination; do not otherwise change generate / planner orchestration.

- `_plan_for_trip` must **not** assign `plan.currency` on an existing plan. For a new plan, leaving `currency=trip.currency` on the `Plan(...)` constructor (`:330`) is an INSERT and is fine. Move the existing-plan currency assignment into the post-item block already at `:287-290` (`plan.status`, totals, `needs_refresh`).
- `upsert_demo_seed._ensure_plan` must not mutate an existing plan before `_upsert_items`. After `_upsert_items` returns, call **`db.flush()` immediately**, and only then assign plan status/totals/currency (N-2b). A new-plan INSERT at `:206-214` may stay before items. Python statement order alone is **not** enough: `_upsert_items` (`:236-262`) has no flush, and the next flush today is `db.commit()` (`:332`). If plan fields are assigned after `_upsert_items` but in the same unit of work, SQLAlchemy still emits `UPDATE plan` before child `plan_item` INSERT/UPDATE (parent-before-child save order; `Plan.items` back_populates at `models.py:392` / `:778`). The explicit flush makes the item writes (and the bump trigger's trip KEY SHARE) take effect before the plan UPDATE.
- General rule for **new** writers: any transaction that will `UPDATE plan` should take trip `FOR KEY SHARE` or `FOR UPDATE` first. When relying on reorder instead, **plan UPDATE must be flushed after item writes; Python statement order is insufficient.**
- The generator path is already safe after the currency move **because** `_write_items` does `db.add(row); db.flush()` per item (`generator.py:1763-1764`). Flag **Cadensy_AI_Agent**: this remains a minimal, AI-runtime-adjacent change (reorder + no new lock protocol in `generate_plan`).

**N-3 (`DD-DECIDED`, documented PG semantics, NOT RUN / SUSPECTED until tested):** on `INSERT plan_item` (and on `UPDATE OF plan_id`), PostgreSQL's FK RI check is an internal AFTER ROW trigger (`RI_ConstraintTrigger_c_<oid>`) that takes `FOR KEY SHARE` on the **plan** row. Same-event triggers fire in name order, so `RI_…` (uppercase `R`) can run **before** a lowercase-named AFTER bump trigger. The bump trigger then waits for trip KEY SHARE while the RI check already holds plan — a cycle with join/approve (trip `FOR UPDATE`, then plan `FOR UPDATE`). This would apply to every legacy INSERT, including the reordered generator.

Fix: a **`BEFORE INSERT OR UPDATE OF plan_id`** row trigger on `plan_item` that takes `FOR KEY SHARE` on the parent trip (or both trips, in `trip.id` order, if `plan_id` moved) via `cadensy_lock_trip_for_plan` and returns NEW, **before** the INSERT/UPDATE RI check can lock plan. Keep the existing AFTER bump trigger unchanged (it still KEY SHAREs trip and increments). Shared SQL source must include this trigger, the N-4 `plan_change` BEFORE trigger, the AFTER bump trigger, and all of their functions. This is part of the Rev 1 trigger DDL authorized by Owner item 1. Add an INSERT-writer versus join/approve deadlock test.
- **N-2 / N-2b tests:** concurrent join versus legacy generate after a currency change, and versus upsert-demo with the explicit post-item flush, must not deadlock. Protected generate is already items-then-plan.

**N-5 (QA rev5.4 preferences lock, not the §14 rollout-gap N-5; `DD-DECIDED`; lock modes documented PostgreSQL semantics, NOT RUN):** preferences first submission in `save_mine` (`preferences/service.py:213-216`, `STATIC-VERIFIED` at the cite SHA) currently does `membership.status = "preferences_submitted"` then `db.flush()` (`:214`), then `record_preferences_submitted` (`:216`) which `pg_insert`s `activation_event` (`activation.py:270-276` → `:155-167`). That INSERT's trip FK takes `trip FOR KEY SHARE` **after** the membership row already holds `FOR NO KEY UPDATE`. Join, approve/close, remove, and quarantine take `trip FOR UPDATE` then memberships `FOR UPDATE` — a wait-for cycle. The same membership-then-trip shape applies if a first-submit flush actually emits the membership UPDATE (the `preferences_updated` branch only fires when status was already submitted, so it may not emit that UPDATE).

**Fix:** before the membership assignment at `:213`, take `SELECT 1 FROM trip WHERE id = :trip_id FOR KEY SHARE` (same helper style as `cadensy_lock_trip_for_plan`, but keyed directly by `membership.trip_id`; Python `with_for_update(of=Trip, keyshare=True)` or equivalent raw SQL is fine). Then the status UPDATE, flush, and event insert (trip RI KEY SHARE, already held). **Do not reorder** the `record_preferences_submitted` / `record_preferences_updated` insert relative to the membership write. `_mark_existing_plan_for_refresh` (`:221`, function `:461-471`) stays after the event insert; with the trip already held it remains cycle-free. This is a writer change, not a new trigger (no plan FK). Add a deadlock test against join / approve / remove / quarantine.

**S-1 (LOW, residual risk; documented PG semantics, NOT RUN; `DD-DECIDED` no design change):** the AFTER bump trigger UPDATEs `plan`, so `plan` is a shared serialization point. Two transactions that each UPDATE more than one existing `plan_item` on the same plan, in opposite item-id order (item A → bump/`UPDATE plan` → item B vs item B → plan → item A), can deadlock. QA's static search found no production legacy writer that updates more than one existing item per transaction; the known multi-item writers are admin scripts (`reset_demo.py`, `upsert_demo_seed.py`). **No change to the bump trigger** (not a cheap or necessary fix for current writers). **Implementation guard:** any future multi-item content writer must lock the affected `plan_item` rows in deterministic `id` order (or already hold a trip `FOR KEY SHARE` / `FOR UPDATE`) before writing them. Add a test or an explicit comment in those writers and in the test plan (§15).

Optional `Idempotency-Key` on replace so a retried same request does not mint twice; last-writer-wins for two different replace clicks.

### 8.1 FK audit: tables referencing `plan` or a plan-dependent row (`STATIC-VERIFIED` at `27c3489936ec34bebbbc9562a1d99e8717df6751`)

Scope: every `ForeignKey` / `ForeignKeyConstraint` in `app/db/models.py` whose target is `plan.id`, `plan_item.id`, or a row that itself depends on `plan_item` (`decision_round`, `change_proposal`), plus the creating migration. No later migration at this SHA alters these FKs (search of `alembic/versions/` besides the creating files: no `ALTER` / drop-and-recreate of these constraints). None of these FKs set `ondelete`; PostgreSQL default is `NO ACTION`. The new `final_plan_approval` table does not exist at this SHA (no FK to audit); it will reference trip/membership, and approve/close already take trip first.

| Table.column | References | models.py | Creating migration | Trip-unlocked writers at this SHA? | Treatment |
| --- | --- | --- | --- | --- | --- |
| `plan_item.plan_id` | `plan.id` | `:752` | `f9ff23b7b84d_baseline_existing_schema.py:158` | Yes: legacy generate, upsert, notice/settle/booking item writes, bulk delete, seed | **Trigger (N-3):** `BEFORE INSERT OR UPDATE OF plan_id` takes trip KEY SHARE before RI can lock plan. Content-only UPDATE does not recheck the `plan` FK. |
| `plan_change.plan_id` | `plan.id` | `:836` | `f9ff23b7b84d:229` | Yes: `_log` (`orchestrator.py:274-283`) used by booking `:629`, notice `:661`, settle `:828`, confirm `:1025`; deadlock resolve `organizer.py:202-210` (including `keep`); `generator._write_items` `:1766-1778` after a per-item flush | **Trigger (N-4):** `BEFORE INSERT` takes trip KEY SHARE via `cadensy_lock_trip_for_plan(NEW.plan_id)`. These are the only FKs to `plan.id` besides `plan_item.plan_id`. |
| `plan_change.plan_item_id` | `plan_item.id` | `:837` | `f9ff23b7b84d:230` | Same writers | **No extra trigger.** RI takes `plan_item FOR KEY SHARE`, not `plan`. The N-4 trigger already takes trip before this statement's `plan` RI. `plan_item` KEY SHARE does not cycle with join/approve (those paths do not lock `plan_item` before trip). |
| `update_notice.trip_id` | `trip.id` | `:959` | `f9ff23b7b84d:254` | Yes, often in the same flush as `plan_change` / item UPDATE (booking `:630-642`, notice `:662-677`, settle `:830-841`, confirm `:1026-1034`, expire `:877-889`, reminder `organizer.py:98-109`, extend `:133-144`, protected scheduler `:73`) | **No trigger.** There is **no** FK to `plan`. INSERT RI already takes trip `FOR KEY SHARE`. That is the desired order. |
| `update_notice.plan_item_id` | `plan_item.id` (nullable) | `:960` | `f9ff23b7b84d:253` | Same writers | **No trigger.** RI is `plan_item KEY SHARE` only; the `trip_id` FK already takes trip. Name-order of the two RI triggers cannot take a `plan` lock. |
| `plan_item_comment.plan_item_id` | `plan_item.id` | `:818` | `f9ff23b7b84d:239` | Yes: `main.py:2689-2706` inserts a comment and commits; no trip `FOR UPDATE`; no `plan_item` / `plan` write | **No trigger.** No FK to `plan`. The path does not bump, does not insert `plan_change`, and does not lock `plan`. `plan_item` KEY SHARE alone does not cycle with join/approve. |
| `plan_item_comment.trip_membership_id` | `trip_membership.id` | `:819` | `f9ff23b7b84d:240` | Same | **No trigger.** Membership RI, not plan. |
| `decision_round.plan_item_id` | `plan_item.id` | `:871` | `f9ff23b7b84d:207` | Legacy open/settle paths have no trip `FOR UPDATE`; settle then writes `plan_change` (`:828`) | **No trigger** on `decision_round`. No FK to `plan`. The N-4 trigger covers the subsequent `plan_change` INSERT. |
| `change_proposal.plan_item_id` | `plan_item.id` | `:918` | `f9ff23b7b84d:190` | Legacy `propose_change` (`orchestrator.py:481`) / `main.py:2570` may be trip-unlocked; apply/confirm goes through `_log` | **No trigger** on `change_proposal`. No FK to `plan`. Confirm's `plan_change` INSERT is covered by N-4. |
| `vote.round_id` | `decision_round.id` | `:896` | `f9ff23b7b84d:274` | Legacy `cast_vote` (`orchestrator.py:758`, `main.py:2949`); no plan write until a following `settle_round` | **No trigger.** Plan-dependent only via `decision_round` → `plan_item`. Vote INSERT does not take a `plan` lock. Settle is covered by N-4. |
| `proposal_decision.proposal_id` | `change_proposal.id` | `:941` | `f9ff23b7b84d:263` | `decide_proposal` `:975-984`; apply path then `_log` | **No trigger.** No FK to `plan` / `plan_item`. Apply is covered by N-4. |
| `conversation_message.selected_plan_item_id` | `plan_item.id` (nullable) | `:579` | `b7d3a1e9c642_pr06b1b_conversation_schema.py:120` | Writers go through `conversations/service.py`, which takes `trip FOR UPDATE` first (`:200`, `:517`) | **No trigger.** Nullable FK to `plan_item` only; writers already trip-first; no FK to `plan`. |
| `conversation_message_option.target_plan_item_id` | `plan_item.id` (nullable) | `:618` | `b7d3a1e9c642:144` | Same service; options added with the assistant message (`:691`) under the same trip lock | **No trigger.** Same justification. |

`rg` over `models.py`: the **only** FKs whose referenced table is `plan` are `plan_item.plan_id` and `plan_change.plan_id`. Those are the only statements whose RI can take `plan FOR KEY SHARE`. Both have a BEFORE trip-lock trigger under this design.

Unique partial index: `CREATE UNIQUE INDEX ux_invite_link_one_unrevoked_per_trip ON invite_link (trip_id) WHERE revoked_at IS NULL`. Do not put `expires_at > now()` in the predicate. After the **first** Rev 2 apply, every pre-launch unrevoked row has been revoked (all were non-HMAC), so the index is built under ACCESS EXCLUSIVE against zero unrevoked rows, with no live-link dedupe (§12). A later scoped re-run leaves live HMAC unrevoked rows in place and uses `IF NOT EXISTS`.

---

## 9. Early close and `plan.itinerary_revision`

### 9.1 Data model

- `plan.itinerary_revision` `bigint NOT NULL DEFAULT 0`, plain `CHECK (itinerary_revision >= 0)` in Rev 1 (§12 / R5-6).
- `trip.final_plan_approved_at` timestamptz nullable, set once, never cleared.
- `final_plan_approval`: `id`, `trip_id`, `membership_id`, `approved_at`, `bound_itinerary_revision` (bigint, not null). **Unique constraint `uq_final_plan_approval_trip_membership` on `(trip_id, membership_id)`** (one row per member; no `stale_at` column). Staleness is **derived**: a row is live iff `bound_itinerary_revision = plan.itinerary_revision` (read under the plan lock). Same-trip FKs; recommend `ON DELETE CASCADE` from trip and a same-trip membership FK so purge is not blocked; still list the table in demo scripts (§12.5). No silent auto-approval. No withdraw / unapprove in v1 (§9.5).

Who counts: `is_current_participant` (`membership_policy.py:40-64, 87-90`). Not `invited`, `removed`, quarantined, or frozen-electorate snapshots.

Public join/preview of an early-closed link: 410 `invite_expired` (non-leaking). Organizer GET: `early_expired`, `can_replace=false`. Do not revoke the invite row for this close; classify only.

Hooks: write close only when the M-1 guard holds (§9.5), after a successful approve or after roster shrinkage in `execute_protected_membership_remove` (`membership_remove.py:36-67`) and `execute_protected_membership_quarantine` (`membership_quarantine.py:52-130`), still under the kernel's trip + memberships + plan locks, without bumping `itinerary_revision`. Organizer removal of the last non-approver can close the invite if the remaining set still satisfies (a)–(c); document that in the UI. Join **evaluates** close only to refuse an already-closed invite (it cannot write close; §9.5).

### 9.2 Authoritative bump (R4-1, R4-2, R4-8)

**Authoritative mechanism (`DD-DECIDED`):** a PostgreSQL `AFTER INSERT OR DELETE OR UPDATE OF <content columns>` trigger on `plan_item`, `FOR EACH ROW`. When the predicate says bump, the function, in this order:

1. `PERFORM 1 FROM trip WHERE id = (SELECT trip_id FROM plan WHERE id = <plan_id>) FOR KEY SHARE;` (R5-2; trip-then-plan).
2. `UPDATE plan SET itinerary_revision = itinerary_revision + 1 WHERE id = <plan_id>;`

The increment is atomic and takes the plan row lock, so two concurrent content edits cannot both write N+1. The KEY SHARE is taken first so a later `UpdateNotice` / `ActivationEvent` RI check cannot wait on trip while a join/approve holds trip and waits on plan.

**Coverage:** every path that inserts, deletes, or content-updates `plan_item`, including ORM, `session.execute(delete(PlanItem))` (`clear_plan.py:40`), `_delete_by_ids` (`purge_demo_data.py:148`), Core, and raw SQL. Out-of-app SQL is also covered.

**Not covered / must not bump via a second trigger:** `plan_change` (audit; deadlock keep writes an empty `patch={}` at `organizer.py:199-206`; settle `KEEP_CURRENT` logs an empty-patch `PlanChange` at `orchestrator.py:828`). `plan_item_comment` (comments, `main.py:2689` area; not itinerary content). `plan` status / totals (a blocked generate that writes no items must not bump). Preferences, constraints, votes, objections, membership, invites.

**Python helper** `bump_itinerary_revision(db, plan)` may still exist as a named chokepoint for readability. It is **not** authoritative, must **not** keep a once-per-transaction flag (R4-8; durable callbacks use `begin_nested()` at `durable.py:341-360`), and must **not** also issue the SQL increment on a path where the trigger will fire (that would double-bump; monotonic and harmless, but avoid it). Prefer a no-op / documentation helper. ORM code must **never assign** `plan.itinerary_revision`. Approve and close read it under `FOR UPDATE` with `populate_existing` (as the kernels already do). `TRUNCATE plan_item` does not fire row triggers; there is no `TRUNCATE` in app or scripts at this SHA (`STATIC-VERIFIED` search of `backend/` and `scripts/`). Product and demo scripts must not `TRUNCATE plan_item`. No `AFTER TRUNCATE` trigger is required if that forbid is kept. `plan_item.plan_id` has no `ON DELETE CASCADE` (`f9ff23b7b84d:158`), so there is no cascade-delete path that would bypass the row trigger today.

**Cadensy_AI_Agent coordination:** the trigger needs no kernel redesign in `durable.py` or `generator_durable.py`. The only generator change is the N-2 reorder in `_plan_for_trip` / the post-item block of `generate_plan` (do not add a new trip-lock protocol or otherwise change planner orchestration). Flag coordination.

### 9.3 Full `plan_item` column set and trigger predicate (R4-5)

`STATIC-VERIFIED` against `models.py:746-779` and baseline `f9ff23b7b84d:136-157`. `TimestampMixin` adds only `created_at` (`models.py:51-54`); there is no `updated_at` on `plan_item`.

| Column | Class | Trigger role |
| --- | --- | --- |
| `id` | non-content (PK) | INSERT/DELETE only (always bump). Not in `UPDATE OF`. |
| `plan_id` | parent FK (not a user-facing edit; no app writer moves it today) | INSERT uses `NEW.plan_id`. DELETE uses `OLD.plan_id`. **In `UPDATE OF`**: if `OLD.plan_id IS DISTINCT FROM NEW.plan_id`, bump **both** plans (L-1). |
| `day_index` | content | `UPDATE OF` + `IS DISTINCT FROM` |
| `day_date` | content | same (`_VIEW_FIELDS`, `orchestrator.py:647-650`) |
| `start_hour` | content | same |
| `duration_min` | content | same |
| `title` | content | same; `_apply` / `_patch_with_poi_metadata` (`:322-334`) |
| `local_title` | content | same (`:326-327`) |
| `place` | content | same |
| `price_per_person` | content | same |
| `tags` | content | same (`_VIEW_FIELDS`). **PostgreSQL `json`** (see types below) |
| `dietary_tags` | content | same. **PostgreSQL `json`** |
| `is_meal` | content | same |
| `lat` | content | same (`_patch_with_poi_metadata` `:331`) |
| `lng` | content | same (`:332`) |
| `photo_url` | content | same (`:333`) |
| `source` | non-content (provenance / trust label, not itinerary text) | not in `UPDATE OF` |
| `settledness` | content **only** when old or new value is `settled`, `booked`, or `removed` | in `UPDATE OF`; body requires `IS DISTINCT FROM` **and** that membership test. `loose` → `touched` (e.g. `_do_round` `:725-726`, `_do_notice` `:660`) does **not** bump. |
| `settled_at` | content-adjacent | in `UPDATE OF`, but bump only when `settledness` also `IS DISTINCT FROM` under the rule above. A keep-current rewrite of `settled_at` on an already-settled item (`orchestrator.py:821-823`) does **not** bump. |
| `settled_by_round_id` | non-content (audit pointer) | not in `UPDATE OF` |
| `created_at` | non-content (row metadata) | not in `UPDATE OF` |

`UPDATE OF` list: `plan_id, day_index, day_date, start_hour, duration_min, title, local_title, place, price_per_person, tags, dietary_tags, is_meal, lat, lng, photo_url, settledness, settled_at`. `plan_id` is included so a parent move fires the trigger (L-1). There is no app path that moves `plan_id` today.

**PostgreSQL types of every compared column** (`STATIC-VERIFIED` against `models.py:746-777` and baseline `f9ff23b7b84d:136-157`; no later migration alters these types — later revisions only FK `plan_item` or add unrelated JSONB on other tables):

| Column | SQLAlchemy | PostgreSQL type | Equality in the trigger |
| --- | --- | --- | --- |
| `day_index` | `Integer` | `integer` | `IS DISTINCT FROM` |
| `day_date` | `Date` | `date` | `IS DISTINCT FROM` |
| `start_hour` | `Float` | `double precision` | `IS DISTINCT FROM` |
| `duration_min` | `Integer` nullable | `integer` | `IS DISTINCT FROM` |
| `title` | `String(300)` | `varchar(300)` | `IS DISTINCT FROM` |
| `local_title` | `String(300)` nullable | `varchar(300)` | `IS DISTINCT FROM` |
| `place` | `String(500)` | `varchar(500)` | `IS DISTINCT FROM` |
| `price_per_person` | `Float` nullable | `double precision` | `IS DISTINCT FROM` |
| `tags` | `JSON` (`models.py:761`; baseline `:147`) | **`json` (not `jsonb`)** | `OLD.tags::jsonb IS DISTINCT FROM NEW.tags::jsonb` (R5-1). `json` has no equality operator; an uncast `IS DISTINCT FROM` errors when the UPDATE branch is planned and would break **every** `UPDATE OF` firing after Rev 1, including under the old app. |
| `dietary_tags` | `JSON` (`models.py:762`; baseline `:148`) | **`json` (not `jsonb`)** | `OLD.dietary_tags::jsonb IS DISTINCT FROM NEW.dietary_tags::jsonb` |
| `is_meal` | `Boolean` | `boolean` | `IS DISTINCT FROM` |
| `lat` | `Float` nullable | `double precision` | `IS DISTINCT FROM` |
| `lng` | `Float` nullable | `double precision` | `IS DISTINCT FROM` |
| `photo_url` | `String(1000)` nullable | `varchar(1000)` | `IS DISTINCT FROM` |
| `settledness` | `String(20)` | `varchar(20)` | `IS DISTINCT FROM` plus the membership test below |
| `settled_at` | `DateTime(timezone=True)` nullable | `timestamptz` | in `UPDATE OF` only; does not appear alone in the bump predicate |

`JSONB` in this codebase (ActivationEvent `metadata`, conversation `safe_patch`, etc.) is **not** on `plan_item` and is not in the compared list. Do not alter `tags` / `dietary_tags` to `jsonb` in this change set (out of scope; the cast is sufficient).

Trigger body (normative logic, not production-formatted SQL). Use declared variables, never a bare `plan_id` (in PL/pgSQL that would not resolve to `OLD`/`NEW`; L-1). The `content_changed` predicate is **normative here** (D-1; do not refer to a superseded revision) and is evaluated **only inside the `UPDATE` branch** (P-1; PG-version independent — do not read `OLD` on INSERT or `NEW` on DELETE):

```
IF TG_OP = 'DELETE' THEN
  v_old := OLD.plan_id;
  PERFORM cadensy_bump_one_plan(v_old);  -- KEY SHARE that plan's trip, then increment that plan
  RETURN OLD;
ELSIF TG_OP = 'INSERT' THEN
  v_new := NEW.plan_id;
  PERFORM cadensy_bump_one_plan(v_new);
  RETURN NEW;
ELSIF TG_OP = 'UPDATE' THEN
  content_changed :=
         OLD.day_index IS DISTINCT FROM NEW.day_index
      OR OLD.day_date IS DISTINCT FROM NEW.day_date
      OR OLD.start_hour IS DISTINCT FROM NEW.start_hour
      OR OLD.duration_min IS DISTINCT FROM NEW.duration_min
      OR OLD.title IS DISTINCT FROM NEW.title
      OR OLD.local_title IS DISTINCT FROM NEW.local_title
      OR OLD.place IS DISTINCT FROM NEW.place
      OR OLD.price_per_person IS DISTINCT FROM NEW.price_per_person
      OR OLD.tags::jsonb IS DISTINCT FROM NEW.tags::jsonb
      OR OLD.dietary_tags::jsonb IS DISTINCT FROM NEW.dietary_tags::jsonb
      OR OLD.is_meal IS DISTINCT FROM NEW.is_meal
      OR OLD.lat IS DISTINCT FROM NEW.lat
      OR OLD.lng IS DISTINCT FROM NEW.lng
      OR OLD.photo_url IS DISTINCT FROM NEW.photo_url
      OR (
           OLD.settledness IS DISTINCT FROM NEW.settledness
           AND (OLD.settledness IN ('settled','booked','removed')
                OR NEW.settledness IN ('settled','booked','removed'))
         );
  -- settled_at alone does not set content_changed.
  -- plan_id is not in this predicate; a parent move is handled separately below.
  IF OLD.plan_id IS DISTINCT FROM NEW.plan_id THEN
    -- Move: bump both plans, even if no other content column changed.
    -- Lock parent trips in trip.id order, then increment plans in plan.id order.
    PERFORM cadensy_bump_plans(ARRAY[OLD.plan_id, NEW.plan_id]);
  ELSIF content_changed THEN
    PERFORM cadensy_bump_one_plan(NEW.plan_id);
  END IF;
  RETURN NEW;
END IF;
```

`cadensy_bump_one_plan(p)` does `PERFORM 1 FROM trip WHERE id = (SELECT trip_id FROM plan WHERE id = p) FOR KEY SHARE;` then `UPDATE plan SET itinerary_revision = itinerary_revision + 1 WHERE id = p`. `cadensy_bump_plans` deduplicates the id list, looks up each plan's `trip_id`, takes `FOR KEY SHARE` on those trips in `trip.id` ascending order, then increments the plans in `plan.id` ascending order. There is still no trigger on `plan` (no recursion). Shared helper `cadensy_lock_trip_for_plan(p)` does a plain `SELECT trip_id FROM plan WHERE id = p` and then `trip FOR KEY SHARE`. The N-3 **BEFORE** trigger (`cadensy_plan_item_lock_trip`) only takes that trip KEY SHARE (both trips in `trip.id` order if `plan_id` moved) and does not bump. The N-4 **BEFORE INSERT** trigger (`cadensy_plan_change_lock_trip`) calls the same helper for `NEW.plan_id` and does not bump.

Use `EXECUTE PROCEDURE` in the `CREATE TRIGGER` statement (works on PostgreSQL 11+; `EXECUTE FUNCTION` is later). Downgrade drops the trigger then the function.

The function + trigger DDL lives in **one SQL source module** (for example `app/db/itinerary_revision_trigger.py`) that exports **all** functions and triggers: `cadensy_lock_trip_for_plan` (shared helper), `cadensy_plan_item_lock_trip` (N-3 BEFORE), `cadensy_plan_change_lock_trip` (N-4 BEFORE INSERT), the AFTER bump function, `cadensy_bump_one_plan`, and `cadensy_bump_plans`, plus **all** `CREATE TRIGGER` statements (plan_item BEFORE, plan_item AFTER, plan_change BEFORE). The Alembic revision `op.execute`s those strings. The same strings are attached with `event.listen` on **both** `PlanItem.__table__` and `PlanChange.__table__` `"after_create"` so `Base.metadata.create_all` installs them (R5-3). Register in order: functions first (lock helper, both BEFORE lock functions, bump helpers, AFTER trigger function), then the plan_item BEFORE trigger, then the plan_item AFTER trigger, then the plan_change BEFORE trigger (or attach the last of these on `PlanChange.after_create`; `plan_change` is created after `plan_item` by FK order). `CREATE OR REPLACE FUNCTION` is fine; trigger creates should be idempotent (`DROP TRIGGER IF EXISTS` then `CREATE`). See §9.6.

### 9.4 Writer inventory (classification unchanged except R4-5/R4-6)

Bump when the trigger sees an INSERT/DELETE or a §9.3 content change. Does-not-bump otherwise.

| Writer | Cite | Bump? |
| --- | --- | --- |
| Legacy generate `_write_items` | `generator.py:286`, `:1736-1778` ← `main.py:2315` | Yes, when items are written. Blocked generate `:250-284` (no items): no. |
| Protected generate | `generator_commit.py:156` (QA-verified) → same `_write_items` | Yes, on item write. Kernel `Plan.revision++` at `generator_durable.py:447-450` is not the epoch. |
| Legacy / protected manual add | `manual_items.py:91` ← `main.py:1380`; protected `manual_plan_item.py:124` (QA-verified) ← `main.py:1437` | Yes |
| Notice apply | `orchestrator._do_notice` `:656-658` → `_apply` `:290-299` ← `propose_change` `:481-515` ← `main.py:2518` / `:2570` | Yes, when `_apply` changes content. Opening a waiting proposal/round only: no. |
| Settle, including scheduler | `settle_round` `:798-823` ← `main.py:2953`, `:3054`; `jobs/scheduler.py:84` → `settle_due_rounds` `:894-906` | Yes when `_apply` runs or `settledness` first becomes `settled`/`removed`. **No** for `KEEP_CURRENT` on an already-settled item (empty patch, `settledness` unchanged). |
| Protected scheduler settle / expire | `transactions/scheduler.py:48-51`, `:70-76` | Settle: as above. Expire: **no** (plan unchanged). |
| Proposal apply / decline / withdraw | `decide_proposal` `:969-1020` ← `main.py:3123`; `withdraw_proposal` `:1055` | Apply yes; decline / withdraw no. |
| Booking | `set_booking_status` `:583` / `_for_actor` `:606-627` ← `main.py:2734` | Yes when `before != after` (settledness booked/settled). No-op: no. |
| Deadlock | `organizer.py:191-197` ← `main.py:3560` | `split`/`clear` yes; **`keep` no** (empty patch, no item column write, `:199-206`). |
| Conversation resolve | `main.py:1105` → `conversations/service.py:459`, `:539-541` | No (read-only classify). |
| `clear_plan.py:40` | bulk `delete(PlanItem)` | **Yes, via the trigger** (not via `before_flush`). |
| `purge_demo_data.py:148` | `_delete_by_ids` bulk delete | **Yes, via the trigger**, and the script must also delete `final_plan_approval` first (§12.5). |
| `seed.py:187-203` | inserts items | Yes, via the trigger. |
| `upsert_demo_seed.py:236-262` | insert or overwrite (`_upsert_items`; no flush today) | Yes, when content actually changes. After the N-2b flush, item writes (and the bump) happen before any subsequent plan-field UPDATE. |
| `reset_demo.py:55-71` | rewrites content fields + `settledness` / `settled_at` / `lat` / `lng` / `photo_url` | **Yes, via the trigger**, when a column in §9.3 actually changes. Also must clear approvals/anchors (§12.5). |
| Join / remove / quarantine | `membership_join.py:125-132`; `membership_remove.py:36-67`; `membership_quarantine.py:52-130` | **No.** Join does not stale-mark (N-8). Remove/quarantine evaluate close after the roster change. |
| Preferences / constraints | `main.py:3310`, `:3350`, `:3386`, `:3422` | No |
| Vote cast / notice objection | `cast_vote` `:758`; `object_to_notice` `:683-697` | No |
| Invite lifecycle | `service.py` invite paths | No |
| Eval harness `run_real_trip_tools_trace.py:178-236` | non-production | Excluded from the product inventory; trigger would still fire if used against a real DB. |

No delete/reorder/edit item HTTP endpoints exist at this SHA (QA: item routes are add `:1351`, generate `:2479`, classify `:2505`, changes `:2528`, comments `:2689`, booking `:2711`). AI tools write through `propose_change`.


### 9.5 Approve contract (R5-4, `DD-DECIDED`)

**Route and method:** `POST /api/trips/{trip_id}/final-plan/approve`

**Authz:** same trip-scoped membership dependency as other trip APIs (`current_membership` + `_require_scoped_trip`). Account or guest credentials are both allowed. The approved membership is **only** the caller's membership (self only). The body must not contain a target `membership_id` or `user_id`; an organizer cannot approve for someone else. The caller must be a current participant (`is_current_participant`, `membership_policy.py:56-64`, SQL `:87-90`). Otherwise 403. Unauthenticated: 401.

**Request schema:**

```json
{ "itinerary_revision": <integer >= 0> }
```

`itinerary_revision` is required: the revision the client last loaded. This is optimistic concurrency, not a stale-column write.

**Transaction:** `SELECT trip … FOR UPDATE`, then all current memberships `FOR UPDATE` (ordered by `TripMembership.id`), then `SELECT plan … FOR UPDATE` with `populate_existing`. Do not lock invites (this path does not write invites). Under the plan lock, read `plan.itinerary_revision`. If it differs from the request body, abort with **409**. Stay consistent with the existing string-`detail` convention (`tripContextClientError.js:4-7`; `messageFromResponseBody` only promotes a string `detail`). Return:

```json
{"detail": "itinerary_revision_stale"}
```

HTTP 409. The client reloads the plan (GET below includes `itinerary_revision`) and may re-approve. Do not write a stale marker.

**Upsert, uniqueness, and derived staleness:** `UNIQUE (trip_id, membership_id)`. On a successful (non-stale) approve, `INSERT … ON CONFLICT (trip_id, membership_id) DO UPDATE SET bound_itinerary_revision = EXCLUDED.bound_itinerary_revision, approved_at = now()`. Justification for this pick versus `UNIQUE (trip_id, user_id, itinerary_revision)`:

- Guests have `user_id IS NULL` (`membership_join.py:199`), so a unique key on `user_id` cannot identify a guest participant.
- Including `itinerary_revision` in the unique key would keep a history of rows, but close evaluation only needs the live binding, and the trigger never writes a stale marker. One row and a derived comparison `bound_itinerary_revision != plan.itinerary_revision` is the consistent rule (rev3's `stale_at` option is not used).
- Upsert on re-approve after a bump rebinds the same row; idempotent repeats of the same revision are the same upsert with the same values.

**Idempotent repeat:** if the caller already has a row bound to the locked current revision, return 200 and skip a no-op write (or perform the same upsert). Response shape is identical.

**Response (200):**

```json
{
  "approved": true,
  "membership_id": "<caller>",
  "bound_itinerary_revision": <int>,
  "itinerary_revision": <int>,
  "final_plan_approved": <bool>,
  "final_plan_approved_at": "<iso>" | null
}
```

**Early-close guard (M-1, `DD-DECIDED`):** `trip.final_plan_approved_at` is written only when all of the following hold under the same locks:

- **(a)** `trip.first_invite_created_at IS NOT NULL` (an invite has actually been created; the window may already have a revoked or unrevoked row).
- **(b)** the current-participant set (`is_current_participant`, `membership_policy.py:56-64`) has **at least two** members and includes **at least one non-organizer**.
- **(c)** every current participant has a live approval: `bound_itinerary_revision` equals the locked `plan.itinerary_revision`, and the approval belongs to that **current membership incarnation** (L-2).

Approvals **may be recorded** before the guard holds (a solo organizer may approve; ensure/replace and later invites still work). The approve upsert itself is unchanged; only the close write is gated. Once written, close remains terminal (never cleared; later itinerary edits do not reopen).

This guard is a **DD interpretation of Owner rule 5** ("the link expires early when ALL currently active members formally approve the final plan"). A literal reading without (a)–(b) would let a solo organizer permanently disable invites before anyone is invited. It is **not** a new Owner item: it does not change the "all current members must approve" rule; it states when that set is a real invited group. The Owner may override (for example, to allow solo close, or to require an explicit confirmation instead). See §17 and §21.

**Close evaluation (write vs classify vs join):**

- **Write** `trip.final_plan_approved_at` only after a successful or idempotent approve, and after roster shrinkage in remove/quarantine (`membership_remove.py:36-67`, `membership_quarantine.py:52-130`), still under trip + memberships + plan locks, without bumping `itinerary_revision`, and only when (a)–(c) all hold. Organizer removal of the last non-approver can close the invite if the remaining set still satisfies the guard; document that in the UI.
- **Join** is an evaluation point but **cannot write close.** A join can never make (c) true: a new member (including an account rejoin after removal, which is a new incarnation — `models.py:153-154`, `membership_join.py:247-248`) has no live approval; N-8 does not copy or stale-mark approvals; `already_member` is a no-op on the roster; a guest with an inactive membership is refused (`GuestRejoinDenied`, `:167-168`) rather than duplicated. Join **must not break** an already-written close: classify the invite as expired (`410 invite_expired`) in `_locked_invite` (or an equivalent early-close check) **before** mutating the roster, so a terminal close cannot be widened by a new member. Join does not compute-and-write `final_plan_approved_at`.
- **Classify only** (read `final_plan_approved_at`, do not write close): organizer retrieve / ensure / replace, and public preview. This is the surviving meaning of the rest of the rev3 evaluation-point list.

**POST approve after close (L-2, `DD-DECIDED`):** if `final_plan_approved_at` is already set, return **409** with `{"detail": "invite_closed"}`. No write. Does not reopen. Applies whether or not the caller has a binding. Justification: a 200 with `approved: true` for a caller who has no binding would look like a new approval of a closed plan; a single terminal code is unambiguous, matches fail-closed classify, and is distinct from `itinerary_revision_stale`. The request that *first* satisfies the guard and writes close still returns **200** as in the response schema above. Clients should treat a later 409 `invite_closed` as "already closed," not as a retryable conflict. A non-participant is still 403.

**Re-admitted members and old approvals (L-2, `STATIC-VERIFIED`):**

- There is no `joined_at` / `reactivated_at` column. `TripMembership` has `TimestampMixin.created_at` only (`models.py:51-54`, `:149-205`) plus `quarantined_at`.
- **Removal then account rejoin creates a new membership incarnation** (new `id`, new `created_at`): `models.py:153-154`; `membership_join.py:247-248` ("a later permitted join makes a new row"); unique active-account index `ux_trip_membership_active_account` (`models.py:183-189`) is partial on `status <> 'removed'`. The old `final_plan_approval` row stays on the **old** `membership_id` and is ignored, because that membership is not a current participant and the unique key is `(trip_id, membership_id)`. The new incarnation must approve again.
- **Quarantine does not change the incarnation** (`models.py:199-201`; `membership_quarantine.py:4`, `:129`). A quarantined member is not a current participant (`membership_policy.py:53-64`), so their approval does not count toward (c). There is **no unquarantine path** at this SHA; a quarantined incarnation is "intentionally not replaceable" (`membership_join.py:248`, `:286-287`).
- **Rule:** an approval counts toward (c) only if it is bound to the locked current `itinerary_revision` **and** belongs to the **current membership incarnation** that is a current participant. Treat any other row as stale/ignored. If a future unquarantine reuses the same row, do **not** automatically recount a pre-quarantine approval; require a new approve after reactivation (or introduce a `reactivated_at` and require `approved_at >= reactivated_at`). Do not invent an activation column in this change set.

**Withdraw / unapprove:** not in v1. An approval stops counting when it becomes derived-stale after an itinerary bump, or when the member leaves the current-participant set (removed or quarantined), or when a new incarnation replaces them. Owner/DD may add an unapprove later.

**Read endpoint (for the UI, same authz: any current participant):** `GET /api/trips/{trip_id}/final-plan`

```json
{
  "itinerary_revision": <int>,
  "self_approved": <bool>,
  "self_bound_itinerary_revision": <int> | null,
  "approved_current_count": <int>,
  "current_participant_count": <int>,
  "final_plan_approved": <bool>,
  "final_plan_approved_at": "<iso>" | null
}
```

Do not return a list of other members' names or ids (privacy). `self_approved` is true only when the caller's binding equals the current plan revision.

### 9.6 Trigger installation in `create_all` environments (R5-3)

At this SHA the default test engine runs `Base.metadata.create_all(engine)` (`tests/conftest.py:118`, session-scoped `test_engine`; default `TEST_DATABASE_URL` is `postgresql+psycopg://localhost/tripsync_test`, `:33-35`). `app/db/seed.py:89-90` does `drop_all` + `create_all`. `app/db/init_schema.py:46-49` is a **legacy** compatibility helper (`create_all` of a baseline table list only; docstring `:1-6` says new databases must use `alembic upgrade head`; documented for empty-database ECS proofs in `AWS/TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md`). None of these paths run Alembic, so a migration-only trigger would be missing in tests and in any `create_all` schema: approvals would never go stale, and the R5-1 predicate bug would not be caught.

**Required:** the shared SQL module from §9.3 is also registered with `event.listen(PlanItem.__table__, "after_create", DDL(...))` **and** `event.listen(PlanChange.__table__, "after_create", DDL(...))` for the N-4 trigger (and matching `before_drop` / `DROP TRIGGER IF EXISTS` + `DROP FUNCTION IF EXISTS` if needed). DDL details (L-3):

- SQLAlchemy `DDL()` applies `%` formatting. Any literal `%` in the function body must be written as `%%` in the Python string (the current body has none; keep it that way when editing).
- Register the **function** `DDL` listeners before the **trigger** `DDL` listeners. Listeners run in registration order, and each trigger depends on its function. Register `PlanChange.after_create` only after the shared lock helper is installed (it may be installed from `PlanItem.after_create`, which runs first because `plan_item` is created before `plan_change`).
- `create_all` on an **existing** database uses checkfirst and **skips** `after_create` for tables that already exist. Alembic remains the only install path for existing databases (production, and a leftover test DB if the session fixture were ever changed). The default conftest drops and recreates the test database each session (`conftest.py:72-100`) before `create_all` (`:118`), so `after_create` does run there.

Then:

- Alembic Rev 1 `op.execute`s the same strings.
- `create_all` in conftest, seed, and a greenfield `init_schema` also installs **all** functions and **all** triggers (once `itinerary_revision` is on the `Plan` model, `create_all` creates that column too). Note the drift from `init_schema`'s "PR-05A baseline" comment and update that docstring in the implementation change set; `init_schema` remains non-production.

**Parity test (real PostgreSQL, `NOT RUN` here):** build one schema with `alembic upgrade <new_head>` and another with `Base.metadata.create_all` on a fresh database. The test must compare **`pg_get_functiondef`** for **every** function in the shared module (`cadensy_lock_trip_for_plan`, `cadensy_plan_item_lock_trip`, `cadensy_plan_change_lock_trip`, the AFTER bump function, `cadensy_bump_one_plan`, `cadensy_bump_plans`) and **`pg_get_triggerdef`** for **every** trigger in that module (the `plan_item` BEFORE lock trigger, the `plan_item` AFTER bump trigger, and the `plan_change` BEFORE INSERT trigger), not existence alone and not a single function or a single table. A further test UPDATEs every content column type (including `tags` / `dietary_tags` JSON, a `settledness`-only booking change, and a synthetic `plan_id` move) against real PostgreSQL, plus INSERT-writer versus join/approve (N-3) and booking/notice/settle/confirm/deadlock-`keep` versus join **and** approve (N-4) deadlock tests.

**SQLite (`STATIC-VERIFIED` search of the tree at this SHA):** there is **no SQLite pytest database**. `conftest.py` defaults to PostgreSQL and only no-ops the UTF-8 database recreate when the URL backend is not `postgresql` (`:59-60`). The only production-tree `sqlite` string in backend tests is `test_db_session.py:11`, a unit test that `_connect_args("sqlite:///trip.db") == {}`. Other "sqlite" hits are frontend example/D1 drizzle fixtures and design-doc prose, not the API test suite. If someone pointed `TEST_DATABASE_URL` at SQLite, `create_all` would not install this PL/pgSQL trigger; tests that need it must skip or fail on a non-postgresql dialect. Do **not** ship a SQLite trigger substitute (behavior would diverge).

---

## 10. Retrieval status enum (rev3 §4.6)

GET remains organizer-only (`service.py:263-269` plus `_require_scoped_trip`). Foreign-trip and non-organizer stay 403. HTTP 200 for the following when the trip exists (404 only if the trip is missing). Token only for `active_hmac`.

**Precedence (D-2; first match wins, evaluated in this order):**

1. `early_expired` — `trip.final_plan_approved_at IS NOT NULL`.
2. `expired` / `window_expired` — `first_invite_created_at IS NOT NULL` and `now >= invite_window_ends_at`.
3. `active_hmac` — an unrevoked HMAC-era row exists and `now < expires_at`. (`expires_at` is a copy of `invite_window_ends_at`, so this should not disagree with step 2.)
4. `non_hmac` — an unrevoked **non-HMAC** row exists. This **takes precedence over `none`** even when `first_invite_created_at` is NULL (a leftover 30-day row and no production window yet).
5. `revoked_replaceable` — `first_invite_created_at IS NOT NULL`, the window is still open, and there is no unrevoked row.
6. `none` — `first_invite_created_at` is NULL **and** there is no unrevoked non-HMAC row (including trips that only have revoked legacy rows).

The unique partial index allows at most one unrevoked row per trip, so `active_hmac` and `non_hmac` cannot both apply.

| Status | Token | `can_replace` | `can_create` | Meaning |
| --- | --- | --- | --- | --- |
| `none` | No | false | true | No production HMAC invite has ever been created (`first_invite_created_at` is NULL) **and** there is no unrevoked non-HMAC row. Explicit create/ensure may mint. |
| `active_hmac` | Yes | true | false | Unrevoked HMAC-era row (`token_scheme = 'hmac_v1'` and `key_id IS NOT NULL`), now < `expires_at`, not early-closed. |
| `non_hmac` | No | true if a production window exists and is open and not early-closed; else false | true iff `first_invite_created_at` is NULL | An unrevoked row is **not** HMAC-era (null `key_id`, or `token_scheme` other than `hmac_v1`, including leftover 30-day rows minted by a rolled-back old backend), and the trip is not early-closed or window-elapsed. Public join/preview: 410 `invite_revoked` or 404 `invite_invalid`, **regardless of `expires_at`** (R-1). Organizer GET never returns the token. Ensure (when `can_create`) or replace (when `can_replace`) revokes the row in place and mints an HMAC invite. |
| `revoked_replaceable` | No | true | false | A production window exists, no unrevoked row, window open, not early-closed. Page load does not mint (F-1). |
| `expired` / `window_expired` | No | false | false | Production window elapsed. Public join of a leftover non-HMAC token still fails under R-1. |
| `early_expired` | No | false | false | `final_plan_approved_at` is set. Public join of a leftover non-HMAC token still fails under R-1. |

`legacy_active` and `expired_replaceable` are **removed**. They existed only to preserve pre-launch 30-day tokens.

**HMAC-era rule (R-1, post-cutover, always on):** a row is HMAC-era iff `token_scheme = 'hmac_v1'` **and** `key_id IS NOT NULL`. Join, preview, and classify, after the existing hash lookup, reject any non-HMAC-era row as 410 `invite_revoked` or 404 `invite_invalid` even if `revoked_at` is null and `expires_at` is still in the future. This is defense in depth for a rollback-minted leftover, a gap after Rev 2, or a delayed cleanup re-run.

---

## 11. Analytics, revoke, isolation, share

Revoke stays in-place; no `DELETE FROM invite_link` in product paths (activation FK `ON DELETE CASCADE`, `models.py:270-274`). Trip isolation and organizer authorization are unchanged. New `invite_created` on each new id.

**Share events (F-11, `DD-DECIDED`):** extend `record_organizer_share_event` (`service.py:411-412`) to refuse time-expired and early-closed invites as well as revoked (same 404, no row). Live-share funnel events must not be inflated by a dead link.

---

## 12. Migration (simplified split B; SUPERSEDES rev3 §4.7 and rev5.2's backfill/dedupe Rev 2)

`STATIC-VERIFIED`: `alembic/env.py:56` `transaction_per_migration=True`. Gate M AUTOCOMMIT (`run_pr06b_schema_migration.py:55`) is only on the advisory-lock connection; Alembic is a subprocess (`:71-76`). Timeouts 900 default / 1800 max (`:101`, `:105`). `CREATE INDEX CONCURRENTLY` cannot run in a default Alembic transaction and is not the default here.

**Lock reality:** `ALTER TABLE … ADD COLUMN` takes **ACCESS EXCLUSIVE** and holds it until COMMIT. `lock_timeout` bounds only **acquisition**; `statement_timeout` bounds hold time of a long statement.

**Required in every revision in this chain:** the first statements, before any `ALTER TABLE` or `LOCK TABLE`, are session-local `SET lock_timeout` and `SET statement_timeout`. Proposed starting point: `lock_timeout=2s`; `statement_timeout` from the dry-run, under the Gate M bound.

### 12.0 Pre-launch cleanup (Owner decision as relayed by DD 2026-11-23)

This is a **pre-launch cleanup**, not a post-go-live lifecycle rule. There are no real users; existing 30-day invite rows and related test approvals are disposable. Executing it is a **destructive production data operation** and remains authorized only as part of Owner item 1, at execution time.

**Chosen mechanism: revoke-in-place inside Rev 2**, under the same `LOCK TABLE trip, invite_link, plan` as the unique index. Not a standalone data-reset script as the primary path (demo scripts in §12.5 remain for later demo use).

Normative Rev 2 steps, after the LOCK, in this order:

1. Optionally stamp `token_scheme = 'legacy_random'` on any `invite_link` row whose `token_scheme` is NULL (label only). Idempotent.
2. **R1b-1:** `UPDATE invite_link SET revoked_at = COALESCE(revoked_at, now()), is_primary = false WHERE revoked_at IS NULL AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL);` This is the predicate for the **first apply and every re-run**. It never revokes a live HMAC-era row. A second run updates zero rows if no new non-HMAC unrevoked row was minted. **Withdrawn:** the rev5.4 unscoped `WHERE revoked_at IS NULL` and the claim that a second run "updates zero rows if nothing new was minted" under that unscoped predicate (false once HMAC rows exist).
3. Assert there are **zero unrevoked non-HMAC** rows (`revoked_at IS NULL AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)`); **abort** if not. Do **not** assert zero unrevoked rows overall — live HMAC links may remain. Then `CREATE UNIQUE INDEX IF NOT EXISTS ux_invite_link_one_unrevoked_per_trip ON invite_link (trip_id) WHERE revoked_at IS NULL`.
4. **Do not** `DELETE` from `invite_link`.
5. **Do not** `DELETE` or rewrite `activation_event`.
6. **Do not** backfill `trip.first_invite_created_at`, `trip.invite_window_ends_at`, or `trip.final_plan_approved_at`. Leave them NULL (they are new columns).
7. **C-1, first apply only:** Assert `final_plan_approval` is **empty** and **abort** if any row exists. **Do not** `DELETE FROM final_plan_approval`. **Do not run this assertion on a re-run** (approvals are expected after go-live). Whether this is a first apply is determined by the marker in the next paragraph.

**First-apply marker (`DD-DECIDED`):** table `cadensy_invite_cleanup_marker` (`name TEXT PRIMARY KEY`, `completed_at TIMESTAMPTZ NOT NULL`), created **empty in Rev 1**. Rev 2, in the same transaction as the cleanup, after the LOCK:

- If no row with `name = 'rev2_first_apply'` exists, this is the **first apply**: run steps 1–3 and step 7, then `INSERT INTO cadensy_invite_cleanup_marker(name, completed_at) VALUES ('rev2_first_apply', now());`. If step 7 aborts, the transaction rolls back and no marker is written, so a retry is still a first apply.
- If that row exists, this is a **re-run** (Owner-authorized runbook, or a Rev 2 downgrade followed by upgrade): run steps 1–3 only; **skip step 7**.

**Why a marker table, not `alembic_version`:** a runbook re-run happens with Alembic already at the Rev 2 head, so an alembic-head check would skip step 7 correctly in that case — but a Rev 2 **downgrade then re-upgrade** looks like a first apply to Alembic (the version is Rev 1 again) while live HMAC invites and `final_plan_approval` rows may already exist. Step 7 would then abort, or an unscoped revert would be unsafe. The marker row **survives Rev 2 downgrade** (Rev 2 down drops the unique index only; it must **not** `DELETE` the marker row and must **not** drop the table). Rev 1 down **does** drop the table, and Rev 1 down is already refused if any `hmac_v1` row exists, so a full pre-go-live rollback clears the marker and the next upgrade is a true first apply. The insert is idempotent (`name` is the primary key; a first-apply transaction writes it once). A missing marker after go-live (manual delete) would retry step 7 and abort if approvals exist — fail-safe, not a silent re-clean of HMAC rows.

**Re-run after rollback (R-1 (b) / R1b-1):** if the **application** is rolled back to the old backend after Rev 2 has already been applied, the old `create_invite` (`service.py:278-301`) can revoke a live HMAC row and mint a new unrevoked 30-day row (the unique index still holds). A **scoped** re-execution of steps 1–3 (not 7) revokes that leftover and leaves any remaining HMAC-era unrevoked rows and all `final_plan_approval` rows untouched. Alembic will not re-run Rev 2 on its own when the head is already recorded, so this is a **runbook** step. It is a **destructive production data operation** and requires **explicit Owner authorization at execution time**, folded into Owner item 1; it is **not** an unattended mandatory step and is **not** a new Owner item. Combined with the R-1 (a) HMAC-era join check, a leftover unrevoked non-HMAC row is never joinable even if the re-run is delayed or declined.

**`activation_event` FK finding (`STATIC-VERIFIED`):** `fk_activation_event_invite_same_trip` is `FOREIGN KEY (invite_id, trip_id) REFERENCES invite_link (id, trip_id) ON DELETE CASCADE` at `models.py:270-274` and in the creating migration `c8e1d4a90b27_activation_event_and_first_submitted.py:95-99` (`invite_id` itself is nullable, `:43` / `models.py:291`). `fk_activation_event_trip` and `fk_activation_event_membership_same_trip` are also `ON DELETE CASCADE` (`models.py:264-280`; migration `:89-105`). A `DELETE FROM invite_link` would therefore **delete** `activation_event` rows that reference those invites. The DD default is to keep historical analytics rows untouched unless the FK forces otherwise: revoke-in-place does not fire `ON DELETE CASCADE`, so pre-launch `invite_created` / share / join events remain. Production-system invites created after go-live also keep their revocation history and events (Owner relay points 6–7).

**Split B, re-evaluated:** keep a **simplified** split B.

- There is no longer an invite-window backfill, no F-4 grandfather, and no live-link dedupe of two unrevoked rows (the cleanup revokes them all). That removes the long hold that originally motivated packing those steps into Rev 2 with ADD COLUMN.
- Cleanup + unique index must still not race with the old `create_invite` (`service.py:278-301`). They belong in a declared window, under ACCESS EXCLUSIVE, immediately followed by the new backend (the old app must not mint a fresh 30-day unrevoked row in a gap after Rev 2).
- Rev 1 can still add nullable columns and triggers while the old app runs (new columns unused; no data destroyed). Intermediate-head recovery remains useful.

A single revision that does columns + triggers + cleanup + index entirely inside the window is also acceptable, because the work is now short and pre-launch row counts should be small. Recommendation remains simplified B so the destructive cleanup is a distinct, item-1-authorized Rev 2. If Owner prefers one revision, the same cleanup SQL and "no backfill" rule apply.

**Split steps (`DD-DECIDED` recommendation):**

1. **Revision 1 (brief, old app may keep running after commit):** timeouts first; `LOCK TABLE` in app order if a lock is needed: `trip`, then `invite_link`, then `plan`. Add nullable trip anchor / window / `final_plan_approved_at` columns (**no backfill**). Add `invite_link.token_scheme` and `key_id`. Add `plan.itinerary_revision bigint NOT NULL DEFAULT 0` (metadata-only default depends on PostgreSQL ≥ 11 — **UNKNOWN, Cloud must confirm read-only before migrate**; if the cluster is older, add the column nullable in Rev 1 and `SET NOT NULL` inside Rev 2's window — this is a schema completion for a new counter, not an invite-window backfill). Add a **plain** `CHECK (itinerary_revision >= 0)` in the same transaction as `ADD COLUMN` (R5-6). Create `final_plan_approval` with `UNIQUE (trip_id, membership_id)`. Create empty `cadensy_invite_cleanup_marker` (`name TEXT PRIMARY KEY`, `completed_at TIMESTAMPTZ NOT NULL`). Install **all** trigger functions and **all** triggers (N-3 `plan_item` BEFORE lock, N-4 `plan_change` BEFORE INSERT, AFTER bump; jsonb casts; trip `FOR KEY SHARE`; P-1 `content_changed` only in the UPDATE branch). No unique invite index yet. No invite revoke yet.

2. **Revision 2 (declared short maintenance window; Owner item 1 authorizes the destructive cleanup):** timeouts first; `LOCK TABLE trip, invite_link, plan IN ACCESS EXCLUSIVE MODE` **in that order**; run the §12.0 cleanup (scoped revoke-in-place of non-HMAC rows, first-apply marker + step 7 only when the marker is absent, no DELETE, no anchor backfill); create the unique index. Bring the new backend up in the same window so the old `create_invite` cannot mint a new unrevoked 30-day row after the lock is released.

Gate M may still `alembic upgrade <new_head>` in one run; the window is Rev 2's LOCK plus the new-app cutover. **If Rev 2 fails after Rev 1 commits, the DB sits at the intermediate Rev 1 head** (columns and triggers present; cleanup not done; old invites still as they were). That revision id is an allowed **recovery** start: add it to `APPROVED_STARTS` and Gate M start options. The verifier's promotion head remains the final new head, not Rev 1. Evidence must require the **final** `Running upgrade … -> <new_head>` line in the same task stream (a failed Rev 2 may still have logged Rev 1's upgrade line).

**Downgrade:**

- Rev 2 down: drop `ux_invite_link_one_unrevoked_per_trip`. **Do not** un-revoke cleaned-up rows. **Do not** `DELETE` from `cadensy_invite_cleanup_marker` and **do not** drop that table (the first-apply record must survive so a later re-upgrade skips step 7). Leave trip anchor columns NULL (Rev 1 still applied).
- Rev 1 down: if any `invite_link.token_scheme = 'hmac_v1'` row exists, **refuse**. Otherwise drop `cadensy_invite_cleanup_marker`, drop **all** triggers (`plan_item` BEFORE, `plan_item` AFTER, `plan_change` BEFORE) and **all** functions in the shared module (`cadensy_lock_trip_for_plan`, `cadensy_plan_item_lock_trip`, `cadensy_plan_change_lock_trip`, the AFTER bump function, `cadensy_bump_one_plan`, `cadensy_bump_plans`), drop `final_plan_approval`, drop `itinerary_revision` and its CHECK, drop `token_scheme` / `key_id`, drop the three trip columns.

**Dry-run (Owner item 1, read-only):** current `alembic_version`; `invite_link` row counts (total, unrevoked, null `expires_at`); `activation_event` counts by `event_name` that reference those invites; `plan` / `trip` row counts for timeout sizing. No F-4 already-elapsed classification is required (item 4 is resolved; no backfill).


### 12.5 Demo / admin scripts (R4-6)

These use explicit table lists and bulk deletes. They must be updated in the same implementation change set:

- `final_plan_approval` must be deleted **before** `trip_membership` / `trip` (or rely on `ON DELETE CASCADE` **and** still list the table so a missing cascade cannot surprise a purge). `purge_demo_data.py` currently deletes `plan_item` at `:148` then `plan` `:149` then memberships `:150` then `trip` `:153` — insert `final_plan_approval` before memberships/trips (and before or after items; if the membership FK is RESTRICT, delete approvals first).
- `reset_demo.py:50` already deletes `invite_link` and `:45-48` deletes invited members, but leaves trip rows. It must also `DELETE` `final_plan_approval` for the demo trip, **NULL** `first_invite_created_at`, `invite_window_ends_at`, and `final_plan_approved_at` (otherwise F-1/ensure and early close would leave the demo uninvitable or closed), and may reset `plan.itinerary_revision` to 0 after restoring items (or leave the bumped value; either is acceptable if documented). Item rewrites at `:55-71` will bump via the trigger during the restore; a final set-to-zero should be a direct `UPDATE plan` (not a dummy item write).
- `clear_plan.py` empties items (trigger bumps). For the demo use case ("Generate again"), also clear `final_plan_approval` and `final_plan_approved_at` on the demo trip; do **not** automatically NULL invite anchors unless the script's contract is a full invite reset (it is not today).

---

## 13. Rollout, CD-01 pins, and Gate M evidence

### 13.1 Deploy ordering vs old code

1. Owner-authorized **Rev 1** (§12) may run while the **old application still runs** (new columns and triggers only; no cleanup, no unique index). HMAC key is not required for this step.
2. Install `CADENSY_INVITE_HMAC_KEY` in the task definition (Owner item 2) **before** the new application boots (else 503 on mint/re-derive only, N-6).
3. **Rev 2** (cleanup + unique index) and the **new backend** start in the **same declared maintenance window**. Do not leave a gap in which the old `create_invite` (`service.py:278-301`) can mint a new unrevoked 30-day row after the lock is released. Then frontend in the same operational window (§14). If the new backend is rolled back to the old app after Rev 2, a **scoped** §12.0 re-run (steps 1–3 only; never step 7; never revoke HMAC-era rows) may be performed only with **explicit Owner authorization** at execution time (item 1). The R-1 (a) join check already makes a leftover non-HMAC token non-joinable if that re-run is delayed or declined.

### 13.2 Complete pin list (same change set as the new revisions)

- `backend/scripts/cd01/run_pr06b_schema_migration.py:22` `DEFAULT_TARGET` = final new head; `:23-30` `APPROVED_STARTS` must include confirmed production start (almost certainly `c8e1d4a90b27`) **and** the intermediate Rev 1 id (R4-7 recovery); `:103-106`.
- `backend/scripts/cd01/verify_pr06b_schema.py:12` `PR06B_HEAD` = final new head; `:135-138`; catalog checks for the unique invite index, new columns, **all** triggers (including the N-4 `plan_change` BEFORE INSERT) and **all** functions, `final_plan_approval`, `cadensy_invite_cleanup_marker`, `plan.itinerary_revision`.
- `.github/workflows/artifact-backend-images.yml:113`, `:115` `BUILD_ALEMBIC_HEAD`, `:208`; add **both** new revision files (and current `c8e1…` if retained) to the `test -f` list at `:134-138`.
- `.github/workflows/release-pydantic-backend.yml:54` `APPROVED_ALEMBIC_HEAD`; `:209` verifier; `:185` / `:191` after Owner items 5–6.
- Tests: `test_pr06b1b_conversation_migration_postgres.py:164`; `test_activation_migration_postgres.py:20` (keep as a b7d3→c8e1 step test unless its purpose changes); `test_cd01_delivery_safety.py:22-23` (repoint to **live** workflow files), `:534`, `:542`, `:593`, `:658`, `:680`, `:713-723`, `:732`; `test_cd01_activation_gate_postgres.py:19-20` similarly.
- `HANDOFF.md:17`, `:19`, `:90`. Optional: `application_docs/activation-funnel.md:12`, `:177`, `:187`. Historical `P0-ACTIVATION-FUNNEL-FINAL-RECORD_2026-11-22.md`: leave as-is.
- Archived copies are not live gates; a restore of Gate M will copy and then update `release-pr06b-schema-migration.yml:25`, `:62`, `:135`.

Avoid a PR #55 repeat: move head pins, start allowlist (including the intermediate head), live evidence, verifier catalog, `test -f` list, and live tests together. Confirm production `alembic_version` before setting `expected-start`.

### 13.3 Gate M path and Owner items 5–6 (R4-3)

Facts: no live `.github/workflows/cd01-pr06b-schema-migration.yml`. Last production Gate M run `45046230145` (2026-11-22 10:52 AM America/Chicago, success) ran about an hour before the archive (#58). Its `head_sha` (`05d92ca703fc…`, VERIFIED by QA via public API) is **not** the migration source SHA (`bb4791fc…`, REPORTED). Live `:185/:191` would still accept that stale run if the historical path and job names are restored, unless a **mandatory** stronger check is added.

**Item 5 (`OPEN`, workflow change, do not perform here):** restore a live Owner-gated `workflow_dispatch` + `environment: Main` Gate M workflow from `AWS/archive/workflows/retired-release/release-pr06b-schema-migration.yml`. Start options include the confirmed production revision and the intermediate Rev 1 id; target is the final new head.

**Item 6 (`OPEN`, workflow change, do not perform here; mechanism `DD-DECIDED`):** rebind live `release-pydantic-backend.yml:185/:191` to the restored path (job/step names can be kept). **Replace** any `head_sha == source_sha` idea with the archived proof chain at `AWS/archive/workflows/retired-release/release-runtime-off-backend.yml:256-302` (`STATIC-VERIFIED`):

- Download the evidence run logs (`:257-262`).
- Require `SOURCE_SHA: <release source SHA>` (`:263`), `MIGRATION_DIGEST: <release migration image digest>` (`:264`), and `Migration task: <ARN>` (`:265`).
- CloudTrail `RunTask` for that task ARN and task definition within the run's time window (`:280-293`).
- Read the **per-task** CloudWatch stream `ecs/${CONTAINER_NAME}/${migration_task_id}` (`:295-299`).
- Require `Running upgrade <confirmed start> -> <APPROVED_ALEMBIC_HEAD>` in that stream (`:300-301` today hard-codes `b7d3… -> c8e1…`; generalize to the release's `APPROVED_ALEMBIC_HEAD`, and require the **final** `-> <new_head>` line so a failed Rev 2 cannot pass on Rev 1's log line).
- Require the evidence run's `created_at` to be **after** the migration image digest was published.
- DescribeTasks remains supplementary (`:303-311`).

With that chain, stale run `45046230145` fails (wrong upgrade line, digest, and source). The new check is mandatory, not optional. Repoint `test_cd01_delivery_safety.py:22-23` to the live workflows.

---

## 14. Frontend / backend gap and rollback (N-5, R4-9)

Backend is `release-pydantic-backend.yml`; frontend is a separate `release-frontend.yml` (`workflow_dispatch`, `environment: Main` at `:3-13`, `:56`). Two Owner dispatches.

**Rollout:** backend first, frontend immediately after, same session, proposed maximum gap **2 hours**, not left overnight. Never frontend first (`/replace` would 404; GET would expect a token the old backend does not return). During the gap, path routes stay and are redacted; generate-new is non-minting on already-invited trips; old UI cannot restore a token. That is degraded UX, not a join outage.

**New frontend (R4-9 / R5-5, `DD-DECIDED`):** try body/header preview and join first. Fall back to the existing path routes (`main.py:1606`, `:2111`, `:2152`) **only when**:

- the response is **405**, or
- the response is **404** and the body's string `detail` is **not** a known invite error.

Known public-invite error shape (`STATIC-VERIFIED`): FastAPI `HTTPException(status, "<code>")` yields `{"detail": "<code>"}` — a **string** `detail`, not a nested `code` field (`main.py:1622-1630`; frontend `tripContextClientError.js:4-7`, `messageFromResponseBody` copies a string `detail` onto `error.message`; `describeInviteJoinError` in `invite-join-flow.js:32-42` keys off that string). Known values:

- 404 + `detail === "invite_invalid"` (`InviteNotFound.code`, `service.py:46-49`, raised at `:1628-1629`)
- 410 + `detail === "invite_revoked"` (`:1624-1625`)
- 410 + `detail === "invite_expired"` (`:1626-1627`)
- 404 + `detail === "Invite not found"` (generic fallback at `:1630`)

Do **not** fall back on those, or on 410 generally, or on 401 / 403 / 409 / 422 join errors (`invite_join_forbidden`, `membership_change_pending_decision`, etc.). A 404 with `detail === "Not Found"` (missing route) or a 405 **does** fall back. `POST /api/invites/preview` on the old backend matches the path template `GET /api/invites/{token}` and naturally returns 405.

At most **one** fallback attempt, sticky for the rest of that page load (in-memory flag). No loop. Never put a token into a path after a known invalid-invite response (ALB logs are `UNKNOWN` / unredacted). Shared `#/join/:token` links stay valid.

**Rollback:** if the new frontend has the fallback, an old backend can stay joinable (path routes). If the fallback is not yet in the shipped frontend, **revert the frontend before the backend**, otherwise preview/join against an old backend that lacks POST preview is a join outage. Record both run URLs.

**Backend rollback after Rev 2 (R-1 / R1b-1):** the old `create_invite` may mint an unrevoked non-HMAC row (and may revoke a live HMAC row in the same transaction). Public join of that leftover token must already fail under R-1 (a). A **scoped** re-run of §12.0 steps 1–3 (not 7) will revoke only the leftover non-HMAC row and must **preserve** any remaining live HMAC links and all `final_plan_approval` rows. That re-run requires **explicit Owner authorization** at execution time (item 1). Do not rely on Alembic to do this automatically when Rev 2 is already applied. **Withdrawn:** the rev5.4 instruction to re-run steps 1–3 **and 7** as a mandatory unscoped cleanup.

---

## 15. Test plan

All design tests, `NOT RUN` here. Retain still-valid rev3/rev4 cases (F-1 revoke-then-page-load, 180-day boundary from first HMAC create, replace near expiry, cross-device restore of a **production HMAC** token, concurrent replace, replace vs join, authz/isolation, analytics continuity, N-6 per-operation 503 and startup-without-key, N-7 no-store, access-log redaction, F-11 shares, old-frontend POST ensure, early close and join/leave/quarantine, F-6 stale-mark on itinerary change only). Drop tests that existed only for F-7 / `legacy_active` / `expired_replaceable` / null-expiry cap-on-read / anchor backfill / item 4 grandfathering.

**Added / adjusted:**

- Trigger: ORM update of a content column bumps once; `loose`→`touched` does not; already-settled `KEEP_CURRENT` / deadlock `keep` do not; booking no-op does not; **bulk `delete(PlanItem)` (as in `clear_plan.py:40`) does bump**.
- **R5-1:** against real PostgreSQL, `UPDATE` each content column type in turn (integer, date, float, varchar, boolean, timestamptz, and **json** `tags` / `dietary_tags`) and assert the trigger neither errors nor fails to bump on a real content change. An uncast control (or a comment in the test) documents why the `::jsonb` cast is required.
- Concurrent itinerary edits: two content writes cannot lose an increment (SQL `+ 1`).
- **R5-2 / N-4:** concurrent join **and** concurrent approve versus a legacy booking, notice, settle, confirm, and deadlock-`keep` flush (`INSERT plan_change` then `UPDATE plan_item` and/or `INSERT update_notice`) must not deadlock once the `plan_change` BEFORE INSERT trigger is installed. A control without that trigger (or an explicit comment) documents why the bump trigger alone is not enough.
- **P-1:** on real PostgreSQL, an INSERT or DELETE of `plan_item` must not error from evaluating `content_changed`; an UPDATE still uses the full predicate (including `::jsonb` casts).
- **N-2 / N-2b:** concurrent join versus legacy generate after a currency change, and versus upsert-demo that flushes items **before** assigning plan fields, must not deadlock.
- **N-3:** an INSERT-writer (legacy generate or equivalent `INSERT plan_item`) versus join/approve must not deadlock once the `plan_item` BEFORE trip-lock trigger is installed.
- **I-1:** a blocked generate does not refresh `plan.currency`; a later successful generate does.
- **L-1:** DELETE bumps `OLD.plan_id`; INSERT bumps `NEW.plan_id`; an UPDATE that moves `plan_id` bumps both plans and does not error on a bare `plan_id`.
- Approve contract: self-only; non-participant 403; stale body revision 409 `itinerary_revision_stale` and no write; idempotent repeat of the same revision 200 **before close**; upsert rebinds after a bump; derived staleness has no `stale_at` column; close writes only at approve and remove/quarantine, and only when the M-1 guard holds.
- **M-1:** a solo organizer who approves before any invite is created does **not** close (`first_invite_created_at` stays NULL; ensure/replace still work). Approving after creating an invite but before anyone else joins (only the organizer is a current participant) does **not** close; ensure/replace still work. The path where (a)–(c) all hold **does** close, and then ensure/replace fail closed.
- **T-1:** removing or quarantining the last non-organizer leaves an organizer-only current set and must **not** close. Removing or quarantining the last non-approver **does** close when (a)–(c) still hold for the remaining set.
- Pre-launch cleanup: after the Rev 2 revoke, GET on a trip that only had legacy invites is `none` (`first_invite_created_at` is NULL); ensure may mint the first HMAC invite; public join on an old token is not a recoverable retrieve; `activation_event` rows that referenced those invites still exist.
- **R-1 / R1b-1:** public join/preview of an unrevoked non-HMAC token is 410 `invite_revoked` or 404 `invite_invalid` even when `expires_at` is still in the future; organizer GET is `non_hmac` (and beats `none` when `first_invite_created_at` is NULL; D-2) and does not return the token; ensure/replace revokes it. A re-run of the scoped Rev 2 cleanup with live HMAC rows and a non-empty `final_plan_approval` **succeeds**, leaves those HMAC rows and approvals untouched, and revokes only unrevoked non-HMAC rows. A first apply still runs step 7 and writes the marker; a second apply/re-run sees the marker and skips step 7. `CREATE UNIQUE INDEX IF NOT EXISTS` does not fail when the index already exists. An unscoped `WHERE revoked_at IS NULL` must not be used (control or comment).
- **N-5:** concurrent `save_mine` first-submit versus join, approve, remove, and quarantine must not deadlock once trip KEY SHARE is taken before the membership status UPDATE. The event insert stays after that UPDATE.
- **S-1:** document (test comment or a two-session fixture if cheap) that a future multi-item writer must lock `plan_item` rows in `id` order or already hold a trip lock. No failing test against current single-item production writers is required.
- **C-1:** Rev 2 aborts if `final_plan_approval` is non-empty; it does not DELETE those rows.
- **L-2:** POST approve after close returns 409 `invite_closed` and does not write. A removed-then-rejoined account (new incarnation) must approve again; the old membership's approval does not count.
- Join does not stale-mark; close stays false until the new member approves (N-8). Join never writes `final_plan_approved_at`. Join of an already-closed invite is 410 and does not add a member.
- Migration: timeouts are the first statements; `LOCK TABLE` order is trip, `invite_link`, `plan`; downgrade of Rev 2 then Rev 1; refuse Rev 1 down if `hmac_v1` rows exist; intermediate head is a valid runner start and not the verifier promotion head; Rev 1 CHECK is a plain CHECK (no `NOT VALID` in that txn).
- **R5-3 / L-3:** Alembic-built and `create_all`-built schemas have the same `pg_get_functiondef` and `pg_get_triggerdef` text for **all** shared-module functions and triggers (including the N-3 `plan_item` BEFORE trigger, the N-4 `plan_change` BEFORE trigger, the lock helper, and the AFTER bump).
- Frontend fallback: 405, or 404 without a known invite `detail`, falls back once per load; 404 `invite_invalid` and 410 `invite_expired` / `invite_revoked` do **not** fall back.
- Keyring: malformed JSON, short or missing **active** key, and unknown row `key_id` each 503 mint/re-derive only, and do not crash startup. A short **old** (non-active) key is ignored and does not 503 mint with a valid active key.
- Demo scripts: purge/reset/clear handle `final_plan_approval` and anchors without FK errors.

---

## 16. Scope estimate

`PROPOSED`, excluding Owner waits: about **16–23 days**. Sequencing: schema and pre-launch cleanup (Owner item 1) → key (Owner item 2) → retrieve / F-1 / F-2 / N-6 / N-7 / R4-9 fallback → approval slice (trigger + approve/close locks). Token/retrieve is design-ready with the pin, evidence work (Owner 5–6), and the pre-launch cleanup (Owner item 1). Approval slice is design-ready once this rev5.5 is accepted, pending Owner items 1, 2, 5, 6 and G-1 (R1b-1 no longer blocking).

---

## 17. Owner Decision checklist

This document authorizes none of the remaining OPEN items. Items 5 and 6 are production-`Main` workflow changes. The 2026-11-23 decision below is recorded as an **Owner decision as relayed by Cadensy_DD**; this file's author has not seen it directly from the Owner.

**RESOLVED by Owner 2026-11-23 (as relayed by DD) — pre-launch cleanup:**

3. **Null-expiry legacy links (cap-on-read).** **RESOLVED / moot.** All pre-launch invite rows are revoked in place; there is no cap-on-read and no preserved 30-day or null expiry.
4. **Already-elapsed / historical test trips (former options A/B/C, including option B grandfathering).** **RESOLVED / removed.** No grandfathering and no special extension. Anchors are never backfilled. The first HMAC create after go-live starts a fresh 180-day window.

**OPEN:**

1. **Authorize the production Alembic upgrade**, including the dry-run, **the §12.0 pre-launch cleanup** (scoped revoke-in-place of non-HMAC `invite_link` rows; destructive; authorized only at execution time as part of this item), unique index, simplified split B / maintenance window and new-backend cutover, intermediate-head recovery pins, trigger DDL as finally specified (R5-1 jsonb casts, R5-2 / N-3 trip KEY SHARE, BEFORE + AFTER triggers, N-4 `plan_change` BEFORE), and §13.2 pin updates. This same item also covers any **post-go-live scoped cleanup re-run** (steps 1–3 only, never step 7, never revoke HMAC-era rows), which likewise requires explicit Owner authorization at execution time. No new Owner item. Recommendation: simplified split B; Rev 2 is the first-apply cleanup+index window.
2. **Authorize creation, rotation, and compromise handling of `CADENSY_INVITE_HMAC_KEY`** (versioned JSON keyring in §5.1) and ECS injection. Do not reuse existing HMAC keys.
5. **Restore a live Owner-gated Gate M workflow** from the archive, with start options including the confirmed production revision and the intermediate Rev 1 head, and target equal to the final new head. Do not perform this in the design task. Recommendation: restore (same job/step names reduce `:191` churn).
6. **Rebind live release evidence** using the archived proof chain in §13.3 (source SHA, migration digest, task ARN, CloudTrail `RunTask`, per-task stream, final `Running upgrade … -> <APPROVED_ALEMBIC_HEAD>`, run created after the digest). Do **not** use `head_sha == source_sha`. Repoint tests to live workflow files. Recommendation: ship with item 5 and the new Alembic head. Do not perform this in the design task.

**G-1.** **Owner confirmation of the M-1 reading of rule 5**, specifically guard (b) (close requires at least two current participants including at least one non-organizer). QA recommended presenting this as an explicit yes/no confirmation. It is **not** resolved by the 2026-11-23 cleanup decision. DD still classifies the guard as an interpretation the Owner may override; until the Owner confirms, it remains OPEN. An Owner override of (b) must be applied before implementation.


---

## 18. DD-DECIDED and remaining PROPOSED items

**DD-DECIDED:** §1.2 including M-1 early-close guard (Owner-rule-5 interpretation; G-1 still OPEN for Owner confirmation of guard (b)), N-2 / N-2b flush-after-items, N-3 `plan_item` BEFORE trip-lock trigger, N-4 `plan_change` BEFORE INSERT trip-lock trigger (QA option (a); option (b) not adopted), **N-5 trip KEY SHARE before preferences first-submit membership UPDATE** (do not reorder the event insert), I-1 blocked-generate currency, D-1 explicit `content_changed` predicate, P-1 `content_changed` only in the UPDATE branch, T-1 removal tests, **R-1 HMAC-era joinability**, **R1b-1 scoped cleanup / first-apply marker / Owner-authorized re-run**, **C-1 assert-empty `final_plan_approval` on first apply only**, **D-2 status precedence**, **S-1 residual multi-item lock-order guard (no bump change)**, L-1 `plan_id` handling, L-2 `invite_closed` and current-incarnation approvals, L-3 `pg_get_*def` parity of **all** functions and triggers, L-4 keyring wording, the §12.0 scoped revoke-in-place cleanup (no invite-window backfill), plus the prior R4/R5 decisions (R5-2 qualified by N-4).

**PROPOSED** for DD (not escalated): trip-level invite anchor as specified in §6 (now aligned with the Owner decision as relayed by DD); F-1 ensure/replace split and `can_replace`; unique index after the revoke-in-place cleanup; Idempotency-Key on replace; HMAC message as §5.1; N-6/N-7; F-2 redaction + body APIs with path deprecation and fallback; helper as non-authoritative; simplified split B versus a single-window revision; 2-hour backend-then-frontend gap; `is_current_participant`; classify rather than revoke on expiry; `lock_timeout=2s` as a starting point pending dry-run.

---

## 19. Evidence gaps

- Tests, dry-run counts, lock hold times, ALB access logs, and production `alembic_version` / PostgreSQL major version: `NOT RUN` / `UNKNOWN`. Cloud must confirm PG version read-only before migrate (R4-7). Next Alembic start is almost certainly `c8e1d4a90b27` (run `45046230145` conclusion VERIFIED; parameters REPORTED) but must still be confirmed.
- R5-1 (`json` has no equality operator), R5-2 / N-4 (`FOR UPDATE` vs `FOR KEY SHARE` deadlock; `plan_change` and bump-trigger lock modes), N-3 (RI check name-order vs AFTER bump on INSERT), N-5 (membership NKU vs trip KEY SHARE), S-1 (multi-item plan serialization), and P-1 (OLD/NEW on the wrong TG_OP, now avoided in the SQL) rest on documented PostgreSQL semantics and were **not executed** on PostgreSQL.
- N-2b flush / unit-of-work parent-before-child save order: **QA EXECUTED-PASS** at ORM level (SQLAlchemy 2.0.52, the version in `requirements-legacy-regression.lock.txt:38`, in-memory SQLite, real models at the cite SHA; evidence from QA rev5.3, not re-run in QA rev5.4 or in this revision). Statement order on PostgreSQL is still **NOT RUN**. QA also EXECUTED-PASS the booking-shaped flush order (`INSERT plan_change`, then `UPDATE plan_item`, then `INSERT update_notice`) on the same scratch setup; PostgreSQL confirmation is NOT RUN.
- R1b-1 scoped cleanup SQL and the first-apply marker (insert / skip-step-7 / survive Rev 2 downgrade) were **not executed**.
- Writer inventory is static at this SHA; the trigger is the mitigation for missed dynamic paths, not a proof that none exist.
- Approval UX copy (who initiates "approve the final plan") remains for DD. G-1 remains OPEN: if the Owner overrides the M-1 interpretation of rule 5, specifically guard (b), the guard must be changed before implementation.
- The 2026-11-23 pre-launch-cleanup decision is recorded as relayed by Cadensy_DD; this file's author has not seen it directly from the Owner. Executing the cleanup still requires explicit Owner authorization at execution time (item 1).
- Run `45046230145` `head_sha` was VERIFIED by QA in an earlier round; its source SHA is REPORTED.

---

## 20. Verification inventory

| Claim | Label |
| --- | --- |
| QA rev5.4 sha256 `07dfed1dbd49457646c189eddb9d6f6faa18a8e43773c9405544eeec7dd61ac8`; rev5.4 sha256 `3179616ed8b9637df355090415b0136df026dc11c89ca6582abc948b1ebca307`; earlier design hashes unchanged | EXECUTED-PASS |
| Live `main` `df4bd930e8217feffb6d70e1b83413eee8ba5245`; docs-only drift vs `27c3489936ec34bebbbc9562a1d99e8717df6751` (ahead 2, behind 0) | From QA rev5.4 EXECUTED-PASS; not re-run in this revision |
| `fk_activation_event_invite_same_trip` `ON DELETE CASCADE` at `models.py:270-274` and `c8e1d4a90b27_activation_event_and_first_submitted.py:95-99`; `invite_id` nullable at `models.py:291` / migration `:43` | STATIC-VERIFIED |
| Only FKs to `plan.id` are `plan_item.plan_id` (`models.py:752`, `f9ff23b7b84d:158`) and `plan_change.plan_id` (`models.py:836`, `f9ff23b7b84d:229`); no `ondelete`; no later migration alters them | STATIC-VERIFIED |
| `UpdateNotice` FKs `models.py:959-960` / `f9ff23b7b84d:253-254`; comment / round / proposal / vote / proposal_decision / conversation plan_item FKs as in §8.1 | STATIC-VERIFIED |
| Booking-shaped `_log` + `UpdateNotice` + one flush at `orchestrator.py:621-643`, `:658-678`, `:817-842`, `:1020-1035`; deadlock `:201-231` | STATIC-VERIFIED |
| Preferences first-submit `save_mine`: `membership.status` + `db.flush()` at `preferences/service.py:213-214`, then `record_preferences_submitted` `:216`; event insert `activation.py:270-276` → `pg_insert` `:155-167`; `_mark_existing_plan_for_refresh` `:461-471` | STATIC-VERIFIED |


| `plan_item` columns `models.py:746-779` / baseline `:136-157`; mixin `created_at` only | STATIC-VERIFIED |
| `clear_plan.py:40`; `purge_demo_data.py:148`; `reset_demo.py:50`, `:55-71` | STATIC-VERIFIED |
| Deadlock keep empty patch `organizer.py:199-206`; settle `_log` `:828`; `_VIEW_FIELDS` `:647-650`; `_patch_with_poi_metadata` `:322-334` | STATIC-VERIFIED |
| Join lock order `:184-195`; quarantine invites after plan `:118-127`; trip first `trip_kernel.py:176-182` | STATIC-VERIFIED |
| Archived evidence chain `release-runtime-off-backend.yml:256-302` (`SOURCE_SHA` `:263`, digest `:264`, task ARN `:265`, CloudTrail `:280-293`, stream `:295-301`, upgrade line `:300-301`) | STATIC-VERIFIED |
| `alembic/env.py:56` `transaction_per_migration=True` | STATIC-VERIFIED |
| `tags` / `dietary_tags` are `sa.JSON()` → PG `json`; no later migration alters them | STATIC-VERIFIED |
| `UpdateNotice.trip_id` FK `models.py:959`; inserts `orchestrator.py:631`, `:663`, `:831`, `:1027` | STATIC-VERIFIED |
| `conftest.py:118` `create_all`; `seed.py:90`; `init_schema.py:46`; no SQLite pytest DB (`test_db_session.py:11` is connect_args only) | STATIC-VERIFIED |
| Public invite errors are string `detail` (`main.py:1622-1630`; `tripContextClientError.js:4-7`) | STATIC-VERIFIED |
| Removal-then-rejoin is a new membership incarnation (`models.py:153-154`, `membership_join.py:247-248`); no unquarantine path; `created_at` only | STATIC-VERIFIED |
| Legacy generate `_plan_for_trip` `:328` before `_write_items` `:286`; `_write_items` flushes per item `:1763-1764`; upsert `_ensure_plan` `:216-219`; `_upsert_items` `:236-262` has no flush; commit `:332`; protected commit items-then-plan `:156-159`; blocked generate `:250-253`, `:271-274` | STATIC-VERIFIED |
| No `TRUNCATE` of `plan_item` in `backend/` or `scripts/` | STATIC-VERIFIED |
| Production PG version, `alembic_version`, ALB logs, json-equality / lock-conflict execution, tests | UNKNOWN / NOT RUN / REPORTED |

## 21. Owner-rules 1–8 traceability (R5-8)

| Owner rule | Normative sections | Design tests (all `NOT RUN` here) |
| --- | --- | --- |
| 1. One active invite per trip | §7; unique partial index §8, §12 | Concurrent first-create / replace cannot leave two unrevoked rows; unique-index violation surfaces as a controlled error |
| 2. Restore the same link (refresh / new device / copy / WeChat) | §7 (GET-only restore, `active_hmac` token); §10 `active_hmac` | Cross-device GET returns the same token; first paint is GET, never a mint |
| 3. Validity starts at first successful **HMAC** create after go-live; maximum 180 days, uniformly, with **no** grandfathering | §6, §12.0; former item 4 / R4-10 **RESOLVED / removed** | 180-day boundary from first HMAC create; replace near expiry does not extend `expires_at`; pre-launch trips start with NULL anchors |
| 4. Explicit replace = new id + token; anchor and final expiry unchanged | §7 replace; §6 writer copies `invite_window_ends_at` | Replace mints a new id/token with the same `expires_at`; GET after replace returns the new token |
| 5. Early close when all current members formally approve the final plan | §1.2, §8, §9, §9.5, §17 (G-1 OPEN) | Approve contract, derived staleness, M-1 guard, close write at approve and remove/quarantine (T-1), join evaluates only so it cannot break a terminal close (and cannot make (c) true). **Note: the M-1 guard, specifically (b), is a DD interpretation of this rule and remains OPEN for Owner confirmation (G-1). Not a new numbered Owner item.** |
| 6. Keep revoked history and activation events | §3.4, §11, §12.0 | Revoke is in-place on product paths **and** in the pre-launch cleanup; no `DELETE FROM invite_link` (would cascade-delete `activation_event`); `invite_created` on each new id |
| 7. Option C HMAC-SHA256, dedicated key, organizer-only retrieve, hash-at-rest | §5.1–§5.5; §10 | N-6 503 and startup-without-key; keyring malformed/short/missing-id 503; organizer-only GET; hash lookup on join; R-1 HMAC-era reject of non-HMAC rows |
| 8. Replace never extends the 180-day window | §6, §7 | Replace near expiry keeps `expires_at`; window-elapsed replace fails closed |

