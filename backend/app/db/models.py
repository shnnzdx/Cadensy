"""数据库的全部表。

设计上有三件事是靠数据库本身保证的,不是靠代码记得去检查:

  1. 一个行程条目**同时只能有一个**未结算的投票轮 / 未确认的提案
     —— 靠部分唯一索引。前端那个 activeRound 是单数的毛病在这里被彻底治好。
  2. 一个人**在一轮里只能投一票**,在一个提案上只能表一次态 —— 靠唯一约束。
  3. 私密偏好原文**存在单独一张表**,判定引擎和展示接口拿的是两套东西。

还有一条:plan_change 是流水账,只追加不修改不删除。
一个条目"现在长什么样"是原始状态叠加所有改动的结果。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ————————————————————— 人和旅行 —————————————————————


class User(Base, TimestampMixin):
    __tablename__ = "user_account"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    avatar: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))


class AuthSession(Base, TimestampMixin):
    """Login session for a real account.

    The browser receives the raw token once. The database only stores a hash,
    so a database dump is not enough to impersonate a user.
    """

    __tablename__ = "auth_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_account.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GuestSession(Base, TimestampMixin):
    """A bounded bearer credential for one anonymous trip membership.

    ``TripMembership.id`` remains an identifier. The one-time raw credential
    returned by Guest join is represented only by this hashed, revocable row.
    """

    __tablename__ = "guest_session"
    __table_args__ = (
        Index("ix_guest_session_trip_id", "trip_id"),
        Index(
            "one_active_guest_session_per_membership",
            "membership_id",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    membership_id: Mapped[str] = mapped_column(ForeignKey("trip_membership.id"))
    trip_id: Mapped[str] = mapped_column(ForeignKey("trip.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Trip(Base, TimestampMixin):
    __tablename__ = "trip"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str] = mapped_column(String(200))
    preferred_start_date: Mapped[date | None] = mapped_column(Date)
    preferred_end_date: Mapped[date | None] = mapped_column(Date)
    expected_group_size: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    preferences_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # planning | upcoming | traveling | completed —— 按日期自动流转,没有 locked
    status: Mapped[str] = mapped_column(String(20), default="planning")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("user_account.id"))
    # Trip-level city cover presentation. This must never be populated from a
    # Place or PlanItem image because those represent a specific activity.
    cover_image_url: Mapped[str | None] = mapped_column(String(1500))
    cover_image_source: Mapped[str | None] = mapped_column(String(30))
    cover_attribution_name: Mapped[str | None] = mapped_column(String(200))
    cover_attribution_url: Mapped[str | None] = mapped_column(String(1000))
    cover_source_url: Mapped[str | None] = mapped_column(String(1000))
    cover_image_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["TripMembership"]] = relationship(back_populates="trip")


class TripMembership(Base, TimestampMixin):
    """角色挂在这张表上,不挂在账户上。

    同一个人可以在 A 旅行是组织者、B 旅行是普通成员。
    guest 的 user_id 为空;以后绑定账户时**只更新这一行的 user_id**,不新建成员。
    """

    __tablename__ = "trip_membership"
    __table_args__ = (
        Index(
            "one_membership_per_user_per_trip",
            "trip_id",
            "user_id",
            unique=True,
            postgresql_where="user_id IS NOT NULL",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trip.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("user_account.id"))
    guest_display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="participant")  # organizer | participant
    join_method: Mapped[str] = mapped_column(String(20), default="invite_guest")
    status: Mapped[str] = mapped_column(String(30), default="invited")

    trip: Mapped[Trip] = relationship(back_populates="memberships")


class InviteLink(Base, TimestampMixin):
    """邀请链接。token 只存哈希 —— 数据库被看到也没法拿去冒充别人加入。"""

    __tablename__ = "invite_link"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trip.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ————————————————————— 偏好和约束 —————————————————————


class Preference(Base):
    """三层偏好。Preferred/Available、Ideal/Maximum 必须分开存,不能合并。"""

    __tablename__ = "preference"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_membership_id: Mapped[str] = mapped_column(
        ForeignKey("trip_membership.id"), unique=True
    )
    preferred_start_date: Mapped[date | None] = mapped_column(Date)
    preferred_end_date: Mapped[date | None] = mapped_column(Date)
    available_start_date: Mapped[date | None] = mapped_column(Date)
    available_end_date: Mapped[date | None] = mapped_column(Date)
    ideal_budget: Mapped[float | None] = mapped_column(Float)
    maximum_budget: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    budget_visibility: Mapped[str] = mapped_column(String(20), default="planning_only")
    travel_style: Mapped[str | None] = mapped_column(String(40))
    top_interests: Mapped[list] = mapped_column(JSON, default=list)  # 最多 3 个
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemberConstraint(Base, TimestampMixin):
    """一条能被机器判定的约束 —— 判定引擎读的就是这张表。

    kind 只能是那六种之一,params 是对应的数字/日期/标签。
    这里**没有原文**:用户写的那句话在 MemberConstraintPrivate 里。
    """

    __tablename__ = "member_constraint"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_membership_id: Mapped[str] = mapped_column(ForeignKey("trip_membership.id"))
    kind: Mapped[str] = mapped_column(String(30))
    importance: Mapped[str] = mapped_column(String(20), default="required")
    params: Mapped[dict] = mapped_column(JSON, default=dict)


class MemberConstraintPrivate(Base):
    """用户原话 + 可见性。单独一张表,是为了让"读得到原文"变成一件要专门做的事。

    判定引擎不查这张表;任何面向全组的接口也不查。
    只有两个地方读它:用户看自己填的东西,以及 AI 帮忙翻译的那一步。
    """

    __tablename__ = "member_constraint_private"

    constraint_id: Mapped[str] = mapped_column(
        ForeignKey("member_constraint.id"), primary_key=True
    )
    original_text: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(20), default="planning_only")


# ————————————————————— 行程 —————————————————————


class Plan(Base, TimestampMixin):
    __tablename__ = "plan"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trip.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | blocked
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    # Null means one or more itinerary prices are unknown. It must not be
    # presented as a free itinerary.
    estimated_total_per_person: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # Planning inputs can change after an itinerary exists. Keep the current
    # plan intact, but make that mismatch explicit to clients.
    needs_refresh: Mapped[bool] = mapped_column(Boolean, default=False)

    items: Mapped[list["PlanItem"]] = relationship(back_populates="plan")


class PlanItem(Base, TimestampMixin):
    """行程里的一个条目。settledness 就是那四档结实程度。"""

    __tablename__ = "plan_item"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plan.id"))
    day_index: Mapped[int] = mapped_column(Integer)
    day_date: Mapped[date] = mapped_column(Date)
    start_hour: Mapped[float] = mapped_column(Float)  # 14.5 == 2:30 PM
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    local_title: Mapped[str | None] = mapped_column(String(300))
    place: Mapped[str] = mapped_column(String(500))
    price_per_person: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    dietary_tags: Mapped[list] = mapped_column(JSON, default=list)
    is_meal: Mapped[bool] = mapped_column(Boolean, default=False)
    # 地图坐标。换地点时跟着换,前端地图自动重画。
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    # 条目配图。AI 从景点库生成时,直接抄景点自带的那张;
    # 手动加的条目可以是空的,前端有占位框兜着。
    photo_url: Mapped[str | None] = mapped_column(String(1000))
    # 数据可信度:verified | ai_estimate | mock | not_verified —— 由代码打,不由 AI 自称
    source: Mapped[str] = mapped_column(String(20), default="mock")

    # loose | touched | settled | booked
    settledness: Mapped[str] = mapped_column(String(20), default="loose")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_by_round_id: Mapped[str | None] = mapped_column(String(32))

    plan: Mapped[Plan] = relationship(back_populates="items")


class Place(Base, TimestampMixin):
    """Provider-backed place library shared by planner runs.

    Planner-specific facts such as duration, price, and walking effort do not
    belong here unless a reliable source provides them. Chicago keeps those
    curated facts in ``data/poi_chicago.py``.
    """

    __tablename__ = "place"
    __table_args__ = (
        UniqueConstraint("provider", "provider_place_id", name="uq_place_provider_id"),
        Index("ix_place_city_country", "city", "country"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(40))
    provider_place_id: Mapped[str] = mapped_column(String(255))
    # Keep the provider's raw name, plus optional language-specific display facts.
    name: Mapped[str] = mapped_column(String(300))
    english_name: Mapped[str | None] = mapped_column(String(300))
    local_name: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(500))
    image_url: Mapped[str | None] = mapped_column(String(1000))
    opening_hours: Mapped[str | None] = mapped_column(String(500))


class PlanItemComment(Base, TimestampMixin):
    """A public group note attached to one itinerary item."""

    __tablename__ = "plan_item_comment"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_item_id: Mapped[str] = mapped_column(ForeignKey("plan_item.id"))
    trip_membership_id: Mapped[str] = mapped_column(ForeignKey("trip_membership.id"))
    body: Mapped[str] = mapped_column(Text)

    item: Mapped[PlanItem] = relationship()
    membership: Mapped[TripMembership] = relationship()


class PlanChange(Base):
    """流水账。三条路径最后都只做一件事:往这里追加一行。

    只追加,不修改,不删除。一个条目"现在长什么样"是原始状态叠加所有改动。
    origin 记着这次改动是怎么来的 —— 答辩时把这张表摊开就是决策全过程。
    """

    __tablename__ = "plan_change"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plan.id"))
    plan_item_id: Mapped[str] = mapped_column(ForeignKey("plan_item.id"))
    # notice | round | reopen_round | confirm | ai_generate | rule_generate | preference_update
    origin: Mapped[str] = mapped_column(String(30))
    patch: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_membership_id: Mapped[str | None] = mapped_column(String(32))
    source_round_id: Mapped[str | None] = mapped_column(String(32))
    source_proposal_id: Mapped[str | None] = mapped_column(String(32))
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ————————————————————— 投票(Round) —————————————————————


class DecisionRound(Base):
    """一轮投票。同一个条目上同时只能有一轮开着 —— 由下面那个部分唯一索引保证。"""

    __tablename__ = "decision_round"
    __table_args__ = (
        Index(
            "one_open_round_per_item",
            "plan_item_id",
            unique=True,
            postgresql_where="status = 'open'",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_item_id: Mapped[str] = mapped_column(ForeignKey("plan_item.id"))
    kind: Mapped[str] = mapped_column(String(20), default="normal")  # normal | reopen
    options: Mapped[list] = mapped_column(JSON, default=list)  # 必须含「分头行动」
    reason: Mapped[str | None] = mapped_column(Text)  # 重开轮必填
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed
    winning_option_id: Mapped[str | None] = mapped_column(String(40))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A round can be extended once. More time is fine; never settling is not.
    extended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vote(Base, TimestampMixin):
    """一人一票。没有记录 = 没表态,**不算同意**。"""

    __tablename__ = "vote"
    __table_args__ = (UniqueConstraint("round_id", "trip_membership_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    round_id: Mapped[str] = mapped_column(ForeignKey("decision_round.id"))
    trip_membership_id: Mapped[str] = mapped_column(ForeignKey("trip_membership.id"))
    option_id: Mapped[str] = mapped_column(String(40))


# ————————————————————— 确认(Confirm) —————————————————————


class ChangeProposal(Base, TimestampMixin):
    """一个待确认的改动。所有受影响成员都 accepted 才写进行程。"""

    __tablename__ = "change_proposal"
    __table_args__ = (
        Index(
            "one_pending_proposal_per_item",
            "plan_item_id",
            unique=True,
            postgresql_where="status = 'waiting_affected_members'",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_item_id: Mapped[str] = mapped_column(ForeignKey("plan_item.id"))
    action_type: Mapped[str] = mapped_column(String(30))
    before_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # waiting_affected_members | applied | withdrawn | declined | expired
    status: Mapped[str] = mapped_column(String(30), default="waiting_affected_members")
    requested_by_membership_id: Mapped[str] = mapped_column(
        ForeignKey("trip_membership.id")
    )
    # 到点没凑齐就**作废**，不是通过。
    # 到期通过等于把沉默当同意，那是这个产品最不能破的一条。
    # 作废是安全的：行程一个字不变，想改的人重新提一次就行。
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProposalDecision(Base, TimestampMixin):
    """某人对某个提案的表态。发起人创建时直接是 accepted。"""

    __tablename__ = "proposal_decision"
    __table_args__ = (UniqueConstraint("proposal_id", "trip_membership_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("change_proposal.id"))
    trip_membership_id: Mapped[str] = mapped_column(ForeignKey("trip_membership.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")


# ————————————————————— 通知 —————————————————————


class UpdateNotice(Base, TimestampMixin):
    """Updates 页面里的一条动态。

    **故意不存"是谁干的"。** 存了就总有一天会被某个接口带出去。
    需要署名的只有行程条目下的公开评论,那是另一张表的事。
    """

    __tablename__ = "update_notice"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trip.id"))
    plan_item_id: Mapped[str | None] = mapped_column(ForeignKey("plan_item.id"))
    # plan | preference | round | proposal | reminder
    kind: Mapped[str] = mapped_column(String(30))
    # A notice addressed to one person (a reminder). NULL = the whole group.
    # Note this is "who it is for", not "who sent it" -- the latter is
    # deliberately not stored, because stored means eventually leaked.
    recipient_membership_id: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    can_object: Mapped[bool] = mapped_column(Boolean, default=False)
