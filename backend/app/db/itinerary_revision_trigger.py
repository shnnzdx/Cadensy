"""Shared PostgreSQL trigger DDL for itinerary_revision.

Alembic Rev 1 and ``Base.metadata.create_all`` both execute these strings.
``DDL()`` percent-formats the body, so the SQL must not contain a literal ``%``.

Lock order inside the triggers is trip ``FOR KEY SHARE`` before any plan-row
lock the statement's foreign key would take. The AFTER bump trigger is not
enough on its own for writers that ``INSERT plan_change`` first (N-4) or for
``INSERT plan_item`` whose RI trigger can lock the plan before a later AFTER
trigger runs (N-3).

S-1a: a future writer that updates more than one ``plan_item`` must lock those
rows in deterministic ``id`` order, or already hold a self-conflicting trip
lock (``FOR NO KEY UPDATE`` or ``FOR UPDATE``). A trip ``FOR KEY SHARE`` does
not serialize two such writers.
"""

from __future__ import annotations

from sqlalchemy import DDL, event
from sqlalchemy.sql.schema import Table

MARKER_TABLE = "cadensy_invite_cleanup_marker"

FUNCTION_CADENSY_LOCK_TRIP_FOR_PLAN = """
CREATE OR REPLACE FUNCTION cadensy_lock_trip_for_plan(p text)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
  v_trip text;
BEGIN
  SELECT trip_id INTO v_trip FROM plan WHERE id = p;
  IF v_trip IS NOT NULL THEN
    PERFORM 1 FROM trip WHERE id = v_trip FOR KEY SHARE;
  END IF;
END;
$fn$
"""

FUNCTION_CADENSY_PLAN_ITEM_LOCK_TRIP = """
CREATE OR REPLACE FUNCTION cadensy_plan_item_lock_trip()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
  v_old_trip text;
  v_new_trip text;
  v_first text;
  v_second text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    PERFORM cadensy_lock_trip_for_plan(NEW.plan_id);
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.plan_id IS DISTINCT FROM NEW.plan_id THEN
      SELECT trip_id INTO v_old_trip FROM plan WHERE id = OLD.plan_id;
      SELECT trip_id INTO v_new_trip FROM plan WHERE id = NEW.plan_id;
      IF v_old_trip IS NOT NULL AND v_new_trip IS NOT NULL
         AND v_old_trip IS DISTINCT FROM v_new_trip THEN
        IF v_old_trip < v_new_trip THEN
          v_first := v_old_trip;
          v_second := v_new_trip;
        ELSE
          v_first := v_new_trip;
          v_second := v_old_trip;
        END IF;
        PERFORM 1 FROM trip WHERE id = v_first FOR KEY SHARE;
        PERFORM 1 FROM trip WHERE id = v_second FOR KEY SHARE;
      ELSIF v_new_trip IS NOT NULL THEN
        PERFORM 1 FROM trip WHERE id = v_new_trip FOR KEY SHARE;
      ELSIF v_old_trip IS NOT NULL THEN
        PERFORM 1 FROM trip WHERE id = v_old_trip FOR KEY SHARE;
      END IF;
    ELSE
      PERFORM cadensy_lock_trip_for_plan(NEW.plan_id);
    END IF;
    RETURN NEW;
  END IF;
  RETURN NEW;
END;
$fn$
"""

FUNCTION_CADENSY_PLAN_CHANGE_LOCK_TRIP = """
CREATE OR REPLACE FUNCTION cadensy_plan_change_lock_trip()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  PERFORM cadensy_lock_trip_for_plan(NEW.plan_id);
  RETURN NEW;
END;
$fn$
"""

FUNCTION_CADENSY_BUMP_ONE_PLAN = """
CREATE OR REPLACE FUNCTION cadensy_bump_one_plan(p text)
RETURNS void
LANGUAGE plpgsql
AS $fn$
BEGIN
  PERFORM 1 FROM trip WHERE id = (SELECT trip_id FROM plan WHERE id = p) FOR KEY SHARE;
  UPDATE plan SET itinerary_revision = itinerary_revision + 1 WHERE id = p;
END;
$fn$
"""

FUNCTION_CADENSY_BUMP_PLANS = """
CREATE OR REPLACE FUNCTION cadensy_bump_plans(plan_ids text[])
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
  rec record;
BEGIN
  FOR rec IN
    SELECT DISTINCT pl.trip_id AS trip_id
    FROM plan AS pl
    WHERE pl.id = ANY(plan_ids)
      AND pl.trip_id IS NOT NULL
    ORDER BY pl.trip_id
  LOOP
    PERFORM 1 FROM trip WHERE id = rec.trip_id FOR KEY SHARE;
  END LOOP;
  FOR rec IN
    SELECT DISTINCT pl.id AS plan_id
    FROM plan AS pl
    WHERE pl.id = ANY(plan_ids)
    ORDER BY pl.id
  LOOP
    UPDATE plan SET itinerary_revision = itinerary_revision + 1 WHERE id = rec.plan_id;
  END LOOP;
END;
$fn$
"""

FUNCTION_CADENSY_PLAN_ITEM_BUMP = """
CREATE OR REPLACE FUNCTION cadensy_plan_item_bump_itinerary()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
  content_changed boolean;
  v_old text;
  v_new text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_old := OLD.plan_id;
    PERFORM cadensy_bump_one_plan(v_old);
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
           AND (
                OLD.settledness IN ('settled', 'booked', 'removed')
                OR NEW.settledness IN ('settled', 'booked', 'removed')
           )
         );
    IF OLD.plan_id IS DISTINCT FROM NEW.plan_id THEN
      PERFORM cadensy_bump_plans(ARRAY[OLD.plan_id, NEW.plan_id]);
    ELSIF content_changed THEN
      PERFORM cadensy_bump_one_plan(NEW.plan_id);
    END IF;
    RETURN NEW;
  END IF;
  RETURN NULL;
END;
$fn$
"""

DROP_TRIGGER_PLAN_ITEM_LOCK = (
    "DROP TRIGGER IF EXISTS cadensy_plan_item_lock_trip_trg ON plan_item"
)
TRIGGER_PLAN_ITEM_LOCK = """
CREATE TRIGGER cadensy_plan_item_lock_trip_trg
BEFORE INSERT OR UPDATE OF plan_id ON plan_item
FOR EACH ROW
EXECUTE PROCEDURE cadensy_plan_item_lock_trip()
"""

DROP_TRIGGER_PLAN_ITEM_BUMP = (
    "DROP TRIGGER IF EXISTS cadensy_plan_item_bump_trg ON plan_item"
)
TRIGGER_PLAN_ITEM_BUMP = """
CREATE TRIGGER cadensy_plan_item_bump_trg
AFTER INSERT OR DELETE OR UPDATE OF plan_id, day_index, day_date, start_hour,
  duration_min, title, local_title, place, price_per_person, tags, dietary_tags,
  is_meal, lat, lng, photo_url, settledness, settled_at
ON plan_item
FOR EACH ROW
EXECUTE PROCEDURE cadensy_plan_item_bump_itinerary()
"""

DROP_TRIGGER_PLAN_CHANGE_LOCK = (
    "DROP TRIGGER IF EXISTS cadensy_plan_change_lock_trip_trg ON plan_change"
)
TRIGGER_PLAN_CHANGE_LOCK = """
CREATE TRIGGER cadensy_plan_change_lock_trip_trg
BEFORE INSERT ON plan_change
FOR EACH ROW
EXECUTE PROCEDURE cadensy_plan_change_lock_trip()
"""

DROP_PLAN_CHANGE_TRIGGER = DROP_TRIGGER_PLAN_CHANGE_LOCK
DROP_PLAN_ITEM_TRIGGERS = (
    DROP_TRIGGER_PLAN_ITEM_BUMP,
    DROP_TRIGGER_PLAN_ITEM_LOCK,
)
DROP_FUNCTIONS = (
    "DROP FUNCTION IF EXISTS cadensy_plan_item_bump_itinerary()",
    "DROP FUNCTION IF EXISTS cadensy_bump_plans(text[])",
    "DROP FUNCTION IF EXISTS cadensy_bump_one_plan(text)",
    "DROP FUNCTION IF EXISTS cadensy_plan_change_lock_trip()",
    "DROP FUNCTION IF EXISTS cadensy_plan_item_lock_trip()",
    "DROP FUNCTION IF EXISTS cadensy_lock_trip_for_plan(text)",
)

# Registration order is normative: functions first, then plan_item BEFORE,
# plan_item AFTER, then the plan_change BEFORE trigger.
FUNCTION_STATEMENTS: tuple[str, ...] = (
    FUNCTION_CADENSY_LOCK_TRIP_FOR_PLAN,
    FUNCTION_CADENSY_PLAN_ITEM_LOCK_TRIP,
    FUNCTION_CADENSY_PLAN_CHANGE_LOCK_TRIP,
    FUNCTION_CADENSY_BUMP_ONE_PLAN,
    FUNCTION_CADENSY_BUMP_PLANS,
    FUNCTION_CADENSY_PLAN_ITEM_BUMP,
)
PLAN_ITEM_TRIGGER_STATEMENTS: tuple[str, ...] = (
    DROP_TRIGGER_PLAN_ITEM_LOCK,
    TRIGGER_PLAN_ITEM_LOCK,
    DROP_TRIGGER_PLAN_ITEM_BUMP,
    TRIGGER_PLAN_ITEM_BUMP,
)
PLAN_CHANGE_TRIGGER_STATEMENTS: tuple[str, ...] = (
    DROP_TRIGGER_PLAN_CHANGE_LOCK,
    TRIGGER_PLAN_CHANGE_LOCK,
)

INSTALL_STATEMENTS: tuple[str, ...] = (
    FUNCTION_STATEMENTS + PLAN_ITEM_TRIGGER_STATEMENTS + PLAN_CHANGE_TRIGGER_STATEMENTS
)

FUNCTION_NAMES: tuple[str, ...] = (
    "cadensy_lock_trip_for_plan",
    "cadensy_plan_item_lock_trip",
    "cadensy_plan_change_lock_trip",
    "cadensy_bump_one_plan",
    "cadensy_bump_plans",
    "cadensy_plan_item_bump_itinerary",
)
TRIGGER_NAMES: tuple[tuple[str, str], ...] = (
    ("cadensy_plan_item_lock_trip_trg", "plan_item"),
    ("cadensy_plan_item_bump_trg", "plan_item"),
    ("cadensy_plan_change_lock_trip_trg", "plan_change"),
)


def register_itinerary_revision_ddl(plan_item: Table, plan_change: Table) -> None:
    """Install the same DDL ``create_all`` uses. Listeners are idempotent."""

    for statement in FUNCTION_STATEMENTS:
        event.listen(plan_item, "after_create", DDL(statement))
    for statement in PLAN_ITEM_TRIGGER_STATEMENTS:
        event.listen(plan_item, "after_create", DDL(statement))
    for statement in PLAN_CHANGE_TRIGGER_STATEMENTS:
        event.listen(plan_change, "after_create", DDL(statement))
    event.listen(plan_change, "before_drop", DDL(DROP_PLAN_CHANGE_TRIGGER))
    for statement in DROP_PLAN_ITEM_TRIGGERS + DROP_FUNCTIONS:
        event.listen(plan_item, "before_drop", DDL(statement))
