"""偏好和六种约束的读写,以及"改严了会撞到什么"的扫描。

三条纪律:

  1. **只能碰自己的。** 接口路径里根本没有"别人"这个位置,
     不是靠权限判断挡住的 —— 想读别人得先改 URL 设计。
  2. **原话只对自己可见。** `MemberConstraintPrivate` 只在 read_mine() 里被读到,
     其他任何函数都不碰那张表。
  3. **改严了只报告,不自动改行程。**
     自动修复要等 Planner agent;现在诚实地告诉用户"撞到了这几条",
     让他自己决定是改行程还是放宽要求 —— 这个决定本来也不该系统替他做。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import (
    MemberConstraint,
    MemberConstraintPrivate,
    Plan,
    PlanItem,
    Preference,
    Trip,
    TripMembership,
    User,
)
from ..constraints.engine import SAFE_TEXT, violates
from ..membership_policy import current_participant_membership_clause
from ..constraints.types import (
    Constraint,
    ConstraintKind,
    Importance,
    ItemView,
    ProposedChange,
    Settledness,
)

# 这两种约束是"整趟旅行"级别的,不是"某一条安排"级别的,扫描时单独处理。
TRIP_LEVEL = {ConstraintKind.BUDGET_CEILING, ConstraintKind.WALK_LIMIT}


class NotYours(Exception):
    """这条约束不是你的。别人的东西你连读都不该读到。"""


class UnknownConstraintKind(Exception):
    """只有六种。填不进去的,系统会老实说保护不了,而不是硬塞一个。"""


class InvalidConstraintParams(Exception):
    """支持的类型也必须带可执行的结构化参数。"""


class PreferenceDateOutOfTripRange(Exception):
    """Preference dates must stay within the trip date window."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item_view(item: PlanItem) -> ItemView:
    return ItemView(
        id=item.id,
        day_date=item.day_date,
        start_hour=item.start_hour,
        duration_min=item.duration_min,
        price_per_person=item.price_per_person,
        tags=frozenset(item.tags or ()),
        dietary_tags=frozenset(item.dietary_tags or ()),
        is_meal=item.is_meal,
        settledness=Settledness(item.settledness),
    )


def _as_domain(row: MemberConstraint) -> Constraint:
    return Constraint(
        id=row.id,
        membership_id=row.trip_membership_id,
        kind=ConstraintKind(row.kind),
        importance=Importance(row.importance),
        params=row.params or {},
    )


# ————————————————————— 读自己的 —————————————————————


def read_mine(db: Session, membership: TripMembership) -> dict:
    """我填了什么。**只有这个函数会读到原话**,因为读的是自己写的。"""
    pref = db.scalar(
        select(Preference).where(Preference.trip_membership_id == membership.id)
    )
    rows = db.scalars(
        select(MemberConstraint)
        .where(MemberConstraint.trip_membership_id == membership.id)
        .order_by(MemberConstraint.created_at)
    ).all()

    constraints = []
    for row in rows:
        private = db.get(MemberConstraintPrivate, row.id)
        constraints.append(
            {
                "id": row.id,
                "kind": row.kind,
                "importance": row.importance,
                "params": row.params or {},
                # 自己写的那句话,只有自己看得到
                "original_text": private.original_text if private else "",
                "visibility": private.visibility if private else "planning_only",
            }
        )

    return {
        "submitted": membership.status == "preferences_submitted",
        "preference": None
        if pref is None
        else {
            "preferred_start_date": pref.preferred_start_date,
            "preferred_end_date": pref.preferred_end_date,
            "available_start_date": pref.available_start_date,
            "available_end_date": pref.available_end_date,
            "ideal_budget": pref.ideal_budget,
            "maximum_budget": pref.maximum_budget,
            "currency": pref.currency,
            "budget_visibility": pref.budget_visibility,
            "travel_style": pref.travel_style,
            "top_interests": pref.top_interests or [],
            "submitted_at": pref.submitted_at,
        },
        "constraints": constraints,
    }


# ————————————————————— 存三层偏好 —————————————————————


@dataclass(frozen=True)
class PreferenceData:
    preferred_start_date: date | None = None
    preferred_end_date: date | None = None
    available_start_date: date | None = None
    available_end_date: date | None = None
    ideal_budget: float | None = None
    maximum_budget: float | None = None
    currency: str = "USD"
    budget_visibility: str = "planning_only"
    travel_style: str | None = None
    top_interests: tuple[str, ...] = ()


def save_mine(
    db: Session, membership: TripMembership, data: PreferenceData
) -> dict:
    _validate_preference_dates(db, membership, data)
    pref = db.scalar(
        select(Preference).where(Preference.trip_membership_id == membership.id)
    )
    if pref is None:
        pref = Preference(trip_membership_id=membership.id)
        db.add(pref)

    pref.preferred_start_date = data.preferred_start_date
    pref.preferred_end_date = data.preferred_end_date
    pref.available_start_date = data.available_start_date
    pref.available_end_date = data.available_end_date
    pref.ideal_budget = data.ideal_budget
    pref.maximum_budget = data.maximum_budget
    pref.currency = data.currency
    pref.budget_visibility = data.budget_visibility
    pref.travel_style = data.travel_style
    # 最多三个 —— 什么都想要等于没有偏好
    pref.top_interests = list(data.top_interests)[:3]
    pref.submitted_at = _now()

    # 组织者要看得到"6 个人里几个交了",没有这一行系统根本不知道谁交了。
    membership.status = "preferences_submitted"
    db.flush()
    _mark_existing_plan_for_refresh(db, membership.trip_id)
    return read_mine(db, membership)


def _validate_preference_dates(
    db: Session, membership: TripMembership, data: PreferenceData
) -> None:
    trip = db.get(Trip, membership.trip_id)
    if trip is None:
        return

    ranges = (
        ("preferred dates", data.preferred_start_date, data.preferred_end_date),
        ("available dates", data.available_start_date, data.available_end_date),
    )
    for label, start, end in ranges:
        if start is not None and end is not None and end < start:
            raise PreferenceDateOutOfTripRange(f"{label} must end on or after they start")

    if trip.preferred_start_date is None or trip.preferred_end_date is None:
        return

    for label, start, end in ranges:
        for value in (start, end):
            if value is None:
                continue
            if value < trip.preferred_start_date or value > trip.preferred_end_date:
                raise PreferenceDateOutOfTripRange(
                    f"{label} must stay within the trip date window"
                )


# ————————————————————— 六种约束的增删改 —————————————————————


def _owned(db: Session, membership: TripMembership, constraint_id: str) -> MemberConstraint:
    row = db.get(MemberConstraint, constraint_id)
    if row is None or row.trip_membership_id != membership.id:
        # 不存在和不是你的,回同一句话 —— 别让人靠错误信息试探出别人有哪些约束
        raise NotYours("Constraint not found")
    return row


def _parse_constraint_date(value: object, field: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidConstraintParams(f"{field} must be an ISO date") from exc
    raise InvalidConstraintParams(f"{field} must be an ISO date")


def _parse_constraint_number(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (int, float)):
        raise InvalidConstraintParams(f"{field} must be a number")
    numeric = float(value)
    if minimum is not None and numeric < minimum:
        raise InvalidConstraintParams(f"{field} must be at least {minimum}")
    if maximum is not None and numeric > maximum:
        raise InvalidConstraintParams(f"{field} must be at most {maximum}")
    return numeric


def _normalize_constraint_tags(value: object, field: str) -> list[str]:
    if value in (None, ""):
        raw = []
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = list(value)
    else:
        raise InvalidConstraintParams(f"{field} must be a list of tags")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise InvalidConstraintParams(f"{field} must contain only text tags")
        tag = item.strip().casefold().replace(" ", "_")
        if not tag or tag in seen:
            continue
        cleaned.append(tag)
        seen.add(tag)
    if not cleaned:
        raise InvalidConstraintParams(f"{field} must include at least one tag")
    return cleaned


def _validated_constraint_params(kind: ConstraintKind, params: dict) -> dict:
    raw = dict(params or {})

    if kind is ConstraintKind.TIME_WINDOW:
        earliest = _parse_constraint_number(
            raw.get("earliest_hour"), "earliest_hour", minimum=0.0, maximum=24.0
        )
        latest = _parse_constraint_number(
            raw.get("latest_hour"), "latest_hour", minimum=0.0, maximum=24.0
        )
        if earliest is None and latest is None:
            raise InvalidConstraintParams(
                "time_window requires earliest_hour or latest_hour"
            )
        if earliest is not None and latest is not None and latest < earliest:
            raise InvalidConstraintParams("latest_hour must be at or after earliest_hour")
        normalized = {}
        if earliest is not None:
            normalized["earliest_hour"] = earliest
        if latest is not None:
            normalized["latest_hour"] = latest
        return normalized

    if kind is ConstraintKind.BUDGET_CEILING:
        ceiling = _parse_constraint_number(
            raw.get("max_total_per_person"),
            "max_total_per_person",
            minimum=0.0,
        )
        if ceiling is None:
            raise InvalidConstraintParams(
                "budget_ceiling requires max_total_per_person"
            )
        return {"max_total_per_person": ceiling}

    if kind is ConstraintKind.DATE_RANGE:
        start = _parse_constraint_date(raw.get("start"), "start")
        end = _parse_constraint_date(raw.get("end"), "end")
        if start is None and end is None:
            raise InvalidConstraintParams("date_range requires start or end")
        if start is not None and end is not None and end < start:
            raise InvalidConstraintParams("end must be on or after start")
        normalized = {}
        if start is not None:
            normalized["start"] = start.isoformat()
        if end is not None:
            normalized["end"] = end.isoformat()
        return normalized

    if kind is ConstraintKind.WALK_LIMIT:
        limit = _parse_constraint_number(
            raw.get("max_km_per_day"), "max_km_per_day", minimum=0.0
        )
        if limit is None:
            raise InvalidConstraintParams("walk_limit requires max_km_per_day")
        return {"max_km_per_day": limit}

    if kind is ConstraintKind.DIETARY:
        return {
            "required_tags": _normalize_constraint_tags(
                raw.get("required_tags"), "required_tags"
            )
        }

    if kind is ConstraintKind.AVOID_TAG:
        return {"tags": _normalize_constraint_tags(raw.get("tags"), "tags")}

    return raw


def add_constraint(
    db: Session,
    membership: TripMembership,
    *,
    kind: str,
    params: dict,
    importance: str = "required",
    original_text: str = "",
    visibility: str = "planning_only",
) -> tuple[MemberConstraint, list[dict]]:
    try:
        kind_enum = ConstraintKind(kind)
    except ValueError as exc:
        raise UnknownConstraintKind(kind) from exc
    validated_params = _validated_constraint_params(kind_enum, params)

    row = MemberConstraint(
        trip_membership_id=membership.id,
        kind=kind,
        importance=importance,
        params=validated_params,
    )
    db.add(row)
    db.flush()
    db.add(
        MemberConstraintPrivate(
            constraint_id=row.id,
            original_text=original_text,
            visibility=visibility,
        )
    )
    db.flush()
    _mark_existing_plan_for_refresh(db, membership.trip_id)
    return row, scan_conflicts(db, membership, only=row)


def update_constraint(
    db: Session,
    membership: TripMembership,
    constraint_id: str,
    *,
    params: dict | None = None,
    importance: str | None = None,
) -> tuple[MemberConstraint, list[dict]]:
    row = _owned(db, membership, constraint_id)
    if params is not None:
        row.params = _validated_constraint_params(ConstraintKind(row.kind), params)
    if importance is not None:
        row.importance = importance
    db.flush()
    _mark_existing_plan_for_refresh(db, membership.trip_id)
    return row, scan_conflicts(db, membership, only=row)


def delete_constraint(db: Session, membership: TripMembership, constraint_id: str) -> None:
    """删掉一条。

    如果它正挡着一个待确认的提案,**提案不会自动通过** ——
    其他人是基于旧情况表的态,得让发起人重新提一次。
    """
    row = _owned(db, membership, constraint_id)
    private = db.get(MemberConstraintPrivate, row.id)
    if private is not None:
        db.delete(private)
        # 必须先落地这一条再删主表。放同一个 flush 里，SQLAlchemy 会把顺序排反，
        # 撞外键报错 —— 原话那张表反过来指着这张。
        db.flush()
    db.delete(row)
    db.flush()
    _mark_existing_plan_for_refresh(db, membership.trip_id)


def _mark_existing_plan_for_refresh(db: Session, trip_id: str) -> None:
    """Flag stale planning inputs without silently rewriting shared decisions."""
    plan = db.scalar(select(Plan).where(Plan.trip_id == trip_id))
    if plan is None:
        return
    has_items = db.scalar(
        select(PlanItem.id).where(PlanItem.plan_id == plan.id).limit(1)
    )
    if has_items is not None:
        plan.needs_refresh = True
        db.flush()


# ————————————————————— 扫描:我这条要求撞到了什么 —————————————————————


def scan_conflicts(
    db: Session,
    membership: TripMembership,
    only: MemberConstraint | None = None,
) -> list[dict]:
    """拿我的约束扫一遍现有行程,列出撞到的安排。

    **只报告,不改任何东西。** 用户可能宁愿放宽自己的要求,
    也不愿意去动一个已经定好的安排 —— 这个选择该他自己做。
    """
    plan = db.scalar(select(Plan).where(Plan.trip_id == membership.trip_id))
    if plan is None:
        return []

    rows = [only] if only is not None else db.scalars(
        select(MemberConstraint).where(
            MemberConstraint.trip_membership_id == membership.id
        )
    ).all()
    checked = [_as_domain(r) for r in rows if r.importance == Importance.REQUIRED.value]
    if not checked:
        return []

    items = db.scalars(
        select(PlanItem)
        .where(PlanItem.plan_id == plan.id)
        .order_by(PlanItem.day_index, PlanItem.start_hour)
    ).all()

    conflicts: list[dict] = []
    for constraint in checked:
        if constraint.kind in TRIP_LEVEL:
            # 整趟旅行级别的:和总价比一次就够,不逐条比
            change = ProposedChange(
                before=_item_view(items[0]) if items else None,
                after=_item_view(items[0]) if items else None,
                day_walk_km_after=0.0,
                trip_total_after=plan.estimated_total_per_person or 0.0,
                requested_by_membership_id=membership.id,
            )
            if items and violates(constraint, change):
                conflicts.append(
                    {
                        "constraint_id": constraint.id,
                        "code": constraint.kind.value.upper(),
                        "text": SAFE_TEXT[constraint.kind],
                        "scope": "trip",
                        "item_id": None,
                        "item_title": None,
                        "day_date": None,
                        "settledness": None,
                    }
                )
            continue

        for item in items:
            view = _item_view(item)
            change = ProposedChange(
                before=view,
                after=view,
                day_walk_km_after=0.0,
                trip_total_after=plan.estimated_total_per_person or 0.0,
                requested_by_membership_id=membership.id,
            )
            if violates(constraint, change):
                conflicts.append(
                    {
                        "constraint_id": constraint.id,
                        "code": constraint.kind.value.upper(),
                        "text": SAFE_TEXT[constraint.kind],
                        "scope": "item",
                        "item_id": item.id,
                        "item_title": item.title,
                        "day_date": item.day_date,
                        "settledness": item.settledness,
                    }
                )
    return conflicts


# ————————————————————— 成员名单 —————————————————————


def list_members(db: Session, membership: TripMembership) -> dict:
    """谁在这趟旅行里,交没交偏好。

    ⚠️ **只回答"交没交",绝不回答"交了什么"。**
    名字是公开的(你当然知道跟谁一起去),偏好内容永远不出现在这里。
    """
    rows = db.scalars(
        select(TripMembership)
        .where(
            TripMembership.trip_id == membership.trip_id,
            current_participant_membership_clause(),
        )
        .order_by(TripMembership.created_at)
    ).all()

    members = []
    for row in rows:
        user = db.get(User, row.user_id) if row.user_id else None
        members.append(
            {
                "membership_id": row.id,
                "name": user.name if user else (row.guest_display_name or "Guest"),
                "role": "guest" if user is None else row.role,
                "joined": row.status in ("joined", "preferences_submitted"),
                "preferences_submitted": row.status == "preferences_submitted",
                "is_me": row.id == membership.id,
            }
        )

    return {
        "members": members,
        "total": len(members),
        "submitted": sum(1 for m in members if m["preferences_submitted"]),
    }
