"""定时任务:每分钟检查一次有没有投票到期了要结算。

只有这一个任务,而且只做一件事 —— 调 settle_due_rounds。
所有规则都在 orchestrator 里,这里不写任何业务判断。

两种跑法:
  - 跟着后端一起跑(默认)。后端一启动它就在后台转,不用管。
  - 单独跑一次:python -m app.jobs.scheduler   —— 给 cron 用的
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

from ..db.session import SessionLocal
from ..db.models import ChangeProposal, DecisionRound, Plan, PlanItem
from ..domain.decisions.orchestrator import expire_due_proposals, settle_due_rounds
from ..domain.transactions import scheduler as protected_scheduler
from . import conversation_purge

log = logging.getLogger("tripsync.scheduler")

TICK_SECONDS = int(os.getenv("SETTLE_TICK_SECONDS", "60"))
_PROTECTED_SCHEDULER_GATE = "CADENSY_B2E_PROTECTED_SCHEDULER_WRITES"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_NON_PRODUCTION = {"dev", "development", "local", "test", "testing", "ci", "staging"}
_CONVERSATION_PURGE_GATE = "CADENSY_CONVERSATION_PURGE_ENABLED"


def _protected_scheduler_enabled() -> bool:
    return (
        os.getenv(_PROTECTED_SCHEDULER_GATE, "0").strip().lower() in _TRUE_VALUES
        and os.getenv("APP_ENV", "").strip().lower() in _NON_PRODUCTION
    )


def _conversation_purge_enabled() -> bool:
    return os.getenv(_CONVERSATION_PURGE_GATE, "0").strip().lower() in _TRUE_VALUES


def _protected_run_once() -> dict[str, list[str]]:
    """Discover due IDs without locks, then claim each under its Trip root."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rounds = db.execute(
            select(DecisionRound.id, Plan.trip_id)
            .join(PlanItem, DecisionRound.plan_item_id == PlanItem.id)
            .join(Plan, PlanItem.plan_id == Plan.id)
            .where(DecisionRound.status == "open", DecisionRound.deadline <= now)
        ).all()
        proposals = db.execute(
            select(ChangeProposal.id, Plan.trip_id)
            .join(PlanItem, ChangeProposal.plan_item_id == PlanItem.id)
            .join(Plan, PlanItem.plan_id == Plan.id)
            .where(
                ChangeProposal.status.in_(("waiting_affected_members", "escalated")),
                ChangeProposal.deadline.is_not(None),
                ChangeProposal.deadline <= now,
            )
        ).all()
    settled = [round_id for round_id, trip_id in rounds if protected_scheduler.settle_due_round(session_factory=SessionLocal, trip_id=trip_id, round_id=round_id, now=now)]
    expired = [proposal_id for proposal_id, trip_id in proposals if protected_scheduler.expire_due_proposal(session_factory=SessionLocal, trip_id=trip_id, proposal_id=proposal_id, now=now)]
    return {"settled": settled, "expired": expired}


def run_once() -> dict[str, list[str]]:
    """结算到期的投票 + 作废到期的确认。跑多少次都安全。"""
    if _protected_scheduler_enabled():
        result = _protected_run_once()
        if result["settled"]:
            log.info("结算了 %d 轮投票: %s", len(result["settled"]), result["settled"])
        if result["expired"]:
            log.info("作废了 %d 个过期提案: %s", len(result["expired"]), result["expired"])
        if _conversation_purge_enabled():
            with SessionLocal.begin() as db:
                result["conversation_purge"] = conversation_purge.run_once(db)  # type: ignore[assignment]
        return result
    with SessionLocal() as db:
        settled = settle_due_rounds(db)
        expired = expire_due_proposals(db)
        db.commit()
    if settled:
        log.info("结算了 %d 轮投票: %s", len(settled), settled)
    if expired:
        log.info("作废了 %d 个过期提案: %s", len(expired), expired)
    result: dict = {"settled": settled, "expired": expired}
    if _conversation_purge_enabled():
        with SessionLocal.begin() as db:
            result["conversation_purge"] = conversation_purge.run_once(db)
    return result


async def _loop() -> None:
    while True:
        try:
            # 数据库是同步的,丢到线程里跑,不卡住接口
            await asyncio.to_thread(run_once)
        except Exception:
            # 一次失败不能让定时任务死掉,下一分钟继续
            log.exception("结算这一轮出错了,一分钟后重试")
        await asyncio.sleep(TICK_SECONDS)


def start(app) -> asyncio.Task:
    """挂到 FastAPI 上,后端一起来它就开始转。"""
    task = asyncio.create_task(_loop())
    app.state.settle_task = task
    return task


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("这一轮:", run_once())
