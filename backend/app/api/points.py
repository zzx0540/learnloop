import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db import get_db
from app.models import PointsLedger, Redemption, Reward, User
from app.schemas.points import (
    PointsLedgerOut,
    PointsSummaryOut,
    RedemptionOut,
    RewardOut,
)
from app.services.points import (
    add_ledger_entry,
    current_streak,
    is_active_day,
    multiplier,
    points_balance,
    shanghai_day_bounds,
)
from app.services.stats import shanghai_date

router = APIRouter()


@router.get("/me/points", response_model=PointsSummaryOut)
async def get_my_points(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> PointsSummaryOut:
    today = shanghai_date()
    streak = await current_streak(db, user.id, today)
    multiplier_streak = streak if await is_active_day(db, user.id, today) else streak + 1
    start, end = shanghai_day_bounds(today)
    today_earned = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PointsLedger.amount), 0)).where(
                    PointsLedger.user_id == user.id,
                    PointsLedger.amount > 0,
                    PointsLedger.created_at >= start,
                    PointsLedger.created_at < end,
                )
            )
        ).scalar_one()
    )
    return PointsSummaryOut(
        balance=await points_balance(db, user.id),
        streak=streak,
        today_multiplier=multiplier(multiplier_streak),
        today_earned=today_earned,
    )


@router.get("/me/points/ledger", response_model=list[PointsLedgerOut])
async def get_my_points_ledger(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PointsLedger]:
    return list(
        (
            await db.execute(
                select(PointsLedger)
                .where(PointsLedger.user_id == user.id)
                .order_by(PointsLedger.created_at.desc(), PointsLedger.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


@router.get("/rewards", response_model=list[RewardOut])
async def list_active_rewards(db: AsyncSession = Depends(get_db)) -> list[Reward]:
    return list(
        (
            await db.execute(
                select(Reward)
                .where(Reward.is_active.is_(True))
                .order_by(Reward.created_at.desc())
            )
        ).scalars()
    )


@router.post("/rewards/{reward_id}/redeem", response_model=RedemptionOut)
async def redeem_reward(
    reward_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Redemption:
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    reward = await db.get(Reward, reward_id)
    if reward is None or not reward.is_active:
        raise HTTPException(status_code=404, detail="奖励不存在")
    if await points_balance(db, user.id) < reward.cost:
        raise HTTPException(status_code=400, detail="积分不足")
    redemption = Redemption(
        user_id=user.id,
        reward_id=reward.id,
        title_snapshot=reward.title,
        cost_snapshot=reward.cost,
    )
    db.add(redemption)
    await db.flush()
    ledger = await add_ledger_entry(
        db,
        user_id=user.id,
        course_id=None,
        kind="redeem",
        amount=-reward.cost,
        applied_multiplier=1.0,
        note=f"redeem:{redemption.id}",
        ref_id=redemption.id,
    )
    if ledger is None:
        raise HTTPException(status_code=409, detail="奖励已兑换")
    await db.commit()
    return redemption


@router.get("/me/redemptions", response_model=list[RedemptionOut])
async def get_my_redemptions(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Redemption]:
    return list(
        (
            await db.execute(
                select(Redemption)
                .where(Redemption.user_id == user.id)
                .order_by(Redemption.created_at.desc())
            )
        ).scalars()
    )
