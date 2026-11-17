-- Cadensy activation funnel.
--
-- Observation cutoff is clock_timestamp(). Run this against a read-only role. It does not
-- write, and it does not read preference text, tokens, names, or emails.
--
-- Invitation acceptance rate
--   Denominator: invites with at least one invite_opened row whose dedup key is
--   the first-open key (invite_opened:{invite_id}). invite_opened is written
--   only when a public lookup succeeds, so the invite was not revoked and not
--   expired at open time. Later revocation does not remove the open.
--   Per-account open rows are excluded from this denominator.
--   Numerator: those invites that also have at least one member_joined row
--   attributed to the same invite, with join_method invite_guest or
--   invite_account, at or before the cutoff.
--
-- 72-hour preference completion rate
--   Cohort: member_joined rows for invite_guest and invite_account whose join
--   is at least 72 hours before the cutoff. Trip creators / organizers are
--   not in this cohort.
--   Numerator: cohort members whose first preferences_submitted event is at
--   or within 72 hours after they joined. Later preferences_updated rows do
--   not count. Only the first submission counts.
--   Guests (invite_guest) and account invite joins (invite_account) are
--   reported separately and combined.
--   Guest memberships can duplicate one person when they join again without
--   the same guest session. Rates count memberships, not proven unique people.
--
-- sample_flag is 样本不足 when the denominator is below 20.

WITH params AS (
    SELECT clock_timestamp() AS cutoff
),
opened AS (
    SELECT e.trip_id, e.invite_id
    FROM activation_event AS e
    CROSS JOIN params AS p
    WHERE e.event_name = 'invite_opened'
      AND e.invite_id IS NOT NULL
      AND e.dedup_key = 'invite_opened:' || e.invite_id
      AND e.occurred_at <= p.cutoff
    GROUP BY e.trip_id, e.invite_id
),
joined_invites AS (
    SELECT DISTINCT e.trip_id, e.invite_id
    FROM activation_event AS e
    CROSS JOIN params AS p
    WHERE e.event_name = 'member_joined'
      AND e.invite_id IS NOT NULL
      AND e.metadata->>'join_method' IN ('invite_guest', 'invite_account')
      AND e.occurred_at <= p.cutoff
),
acceptance AS (
    SELECT
        'invitation_acceptance_rate'::text AS metric,
        o.trip_id AS scope_trip_id,
        NULL::text AS audience,
        COUNT(j.invite_id)::bigint AS numerator,
        COUNT(*)::bigint AS denominator,
        ROUND(
            COUNT(j.invite_id)::numeric / NULLIF(COUNT(*), 0),
            4
        ) AS rate,
        CASE WHEN COUNT(*) < 20 THEN '样本不足' ELSE 'ok' END AS sample_flag,
        'Valid opens are successful public lookups. Joins are new invite memberships attributed to that invite.'::text AS notes
    FROM opened AS o
    LEFT JOIN joined_invites AS j
        ON j.trip_id = o.trip_id
       AND j.invite_id = o.invite_id
    GROUP BY GROUPING SETS ((o.trip_id), ())
),
cohort AS (
    SELECT
        e.trip_id,
        e.membership_id,
        e.occurred_at AS joined_at,
        e.metadata->>'join_method' AS join_method
    FROM activation_event AS e
    CROSS JOIN params AS p
    WHERE e.event_name = 'member_joined'
      AND e.membership_id IS NOT NULL
      AND e.metadata->>'join_method' IN ('invite_guest', 'invite_account')
      AND e.occurred_at <= p.cutoff - INTERVAL '72 hours'
),
first_pref AS (
    SELECT e.membership_id, MIN(e.occurred_at) AS first_submit
    FROM activation_event AS e
    WHERE e.event_name = 'preferences_submitted'
    GROUP BY e.membership_id
),
scored AS (
    SELECT
        c.trip_id,
        c.join_method,
        (
            f.first_submit IS NOT NULL
            AND f.first_submit >= c.joined_at
            AND f.first_submit <= c.joined_at + INTERVAL '72 hours'
        ) AS within_72h
    FROM cohort AS c
    LEFT JOIN first_pref AS f
        ON f.membership_id = c.membership_id
),
preference_completion AS (
    SELECT
        'preference_completion_72h'::text AS metric,
        s.trip_id AS scope_trip_id,
        COALESCE(s.join_method, 'combined') AS audience,
        COUNT(*) FILTER (WHERE s.within_72h)::bigint AS numerator,
        COUNT(*)::bigint AS denominator,
        ROUND(
            (COUNT(*) FILTER (WHERE s.within_72h))::numeric / NULLIF(COUNT(*), 0),
            4
        ) AS rate,
        CASE WHEN COUNT(*) < 20 THEN '样本不足' ELSE 'ok' END AS sample_flag,
        'Organizers are excluded. Only the first submission counts. Duplicate guest memberships cannot be merged, so guest rates can be inflated.'::text AS notes
    FROM scored AS s
    GROUP BY GROUPING SETS (
        (s.trip_id, s.join_method),
        (s.trip_id),
        (s.join_method),
        ()
    )
)
SELECT *
FROM acceptance
UNION ALL
SELECT *
FROM preference_completion
ORDER BY metric, scope_trip_id NULLS FIRST, audience NULLS FIRST
