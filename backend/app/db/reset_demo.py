"""把演示数据恢复原样,**但不换 ID**。

和 `seed.py` 的区别:
  seed.py       删表重建 → 所有 ID 都变 → 前端 .env 失效
  reset_demo.py 只清测试留下的痕迹 → **ID 全部保留** → .env 照常能用

演示之前跑一次。测着测着行程被改乱了、提案挂着没结束、投票开着没关,
都用这个清干净。

    .venv/bin/python -m app.db.reset_demo
"""

from __future__ import annotations

from sqlalchemy import delete, select, text, update

from .models import (
    ChangeProposal,
    DecisionRound,
    FinalPlanApproval,
    InviteLink,
    PlanChange,
    PlanItem,
    ProposalDecision,
    Trip,
    TripMembership,
    UpdateNotice,
    Vote,
)
from .seed import ITEMS, PHOTOS
from .session import SessionLocal


def reset() -> dict:
    with SessionLocal() as db:
        # 决策过程留下的东西,全部清掉。顺序按外键依赖来。
        cleared = {}
        for model in (PlanChange, UpdateNotice, Vote, ProposalDecision,
                      DecisionRound, ChangeProposal):
            cleared[model.__tablename__] = db.execute(
                delete(model)
            ).rowcount
            db.flush()

        # 测试邀请流程时加进来的人也要清掉,否则演示时成员数会越来越多。
        # 只删通过邀请进来的,原来那 6 个(creator / invite_login 种子)留着。
        cleared["invited_members"] = db.execute(
            delete(TripMembership).where(
                TripMembership.join_method == "invite_guest"
            )
        ).rowcount
        cleared["invite_link"] = db.execute(delete(InviteLink)).rowcount
        cleared["final_plan_approval"] = db.execute(delete(FinalPlanApproval)).rowcount
        db.execute(
            update(Trip).values(
                first_invite_created_at=None,
                invite_window_ends_at=None,
                final_plan_approved_at=None,
            )
        )
        db.flush()

        # S-1a: lock item rows in id order, then rewrite them in created_at order.
        db.scalars(select(PlanItem.id).order_by(PlanItem.id).with_for_update()).all()
        items = db.scalars(
            select(PlanItem).order_by(PlanItem.created_at, PlanItem.id)
        ).all()
        for index, (item, row) in enumerate(zip(items, ITEMS)):
            day, when, hour, mins, title, place, price, is_meal, settledness, lat, lng = row
            item.day_index = day
            item.day_date = when
            item.start_hour = hour
            item.duration_min = mins
            item.title = title
            item.place = place
            item.price_per_person = price
            item.is_meal = is_meal
            item.settledness = settledness
            item.settled_at = None
            item.settled_by_round_id = None
            item.lat = lat
            item.lng = lng
            item.photo_url = PHOTOS[index % len(PHOTOS)]
            item.source = "mock"
        db.flush()
        # The content trigger bumped itinerary_revision. The demo epoch starts at 0.
        db.execute(text("UPDATE plan SET itinerary_revision = 0"))

        db.commit()
        return {"restored_items": len(items), "cleared": cleared}


if __name__ == "__main__":
    result = reset()
    print(f"恢复了 {result['restored_items']} 条安排,ID 没变")
    for table, count in result["cleared"].items():
        if count:
            print(f"  清掉 {table}: {count} 条")
