import secrets
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.core.security import hash_password
from app.db import get_db
from app.models import InviteCode, LlmCallLog, Reward, User, UserSession, utcnow
from app.schemas.points import RewardCreate, RewardOut, RewardUpdate
from app.services.settings_store import DEFAULTS, get_setting, set_setting

router = APIRouter(dependencies=[Depends(require_admin)])


class InviteCreate(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    max_uses: int = Field(default=10, ge=1, le=1000)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


def _validate_positive_int(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HTTPException(status_code=400, detail=f"{key} 必须是正整数")
    return value


def _validate_setting(key: str, value: Any) -> Any:
    if key in {
        "llm_daily_token_budget_per_course",
        "upload_max_mb",
        "upload_quota_mb_per_user",
    }:
        return _validate_positive_int(key, value)
    if key == "llm_pricing":
        if not isinstance(value, dict) or not value:
            raise HTTPException(status_code=400, detail="llm_pricing 格式不正确")
        for model, prices in value.items():
            if not isinstance(model, str) or not isinstance(prices, dict):
                raise HTTPException(status_code=400, detail="llm_pricing 格式不正确")
            if set(prices) != {"miss", "hit", "out"} or any(
                isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0
                for price in prices.values()
            ):
                raise HTTPException(status_code=400, detail="llm_pricing 格式不正确")
        return value
    raise HTTPException(status_code=400, detail=f"不允许修改设置项：{key}")


@router.post("/admin/invites")
async def create_invite(data: InviteCreate, db: AsyncSession = Depends(get_db)) -> dict:
    code = data.code or secrets.token_urlsafe(8)
    invite = InviteCode(
        code=code,
        max_uses=data.max_uses,
        expires_at=utcnow() + timedelta(days=data.expires_in_days) if data.expires_in_days else None,
    )
    db.add(invite)
    await db.commit()
    return {"code": code, "max_uses": data.max_uses}


@router.get("/admin/invites")
async def list_invites(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(InviteCode).order_by(InviteCode.created_at.desc()))).scalars()
    return [
        {
            "code": r.code,
            "used": r.used_count,
            "max_uses": r.max_uses,
            "is_active": r.is_active,
            "expires_at": r.expires_at,
        }
        for r in rows
    ]


@router.get("/admin/users")
async def list_users(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(User).order_by(User.created_at))).scalars()
    return [
        {
            "id": str(u.id),
            "username": u.username,
            "nickname": u.nickname,
            "role": u.role,
            "created_at": u.created_at,
            "last_login_at": u.last_login_at,
        }
        for u in rows
    ]


@router.post("/admin/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    password = "".join(secrets.choice(alphabet) for _ in range(12))
    user.password_hash = hash_password(password)
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.commit()
    return {"password": password}


@router.get("/admin/settings")
async def get_admin_settings(db: AsyncSession = Depends(get_db)) -> dict:
    return {key: await get_setting(db, key) for key in DEFAULTS}


@router.patch("/admin/settings")
async def update_admin_settings(
    data: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict:
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"不允许修改设置项：{sorted(unknown)[0]}")
    validated = {key: _validate_setting(key, value) for key, value in data.items()}
    for key, value in validated.items():
        await set_setting(db, key, value)
    return {key: await get_setting(db, key) for key in DEFAULTS}


@router.get("/admin/rewards", response_model=list[RewardOut])
async def list_admin_rewards(db: AsyncSession = Depends(get_db)) -> list[Reward]:
    return list(
        (
            await db.execute(select(Reward).order_by(Reward.created_at.desc()))
        ).scalars()
    )


@router.post("/admin/rewards", response_model=RewardOut)
async def create_reward(
    data: RewardCreate, db: AsyncSession = Depends(get_db)
) -> Reward:
    reward = Reward(**data.model_dump())
    db.add(reward)
    await db.commit()
    return reward


@router.patch("/admin/rewards/{reward_id}", response_model=RewardOut)
async def update_reward(
    reward_id: uuid.UUID,
    data: RewardUpdate,
    db: AsyncSession = Depends(get_db),
) -> Reward:
    reward = await db.get(Reward, reward_id)
    if reward is None:
        raise HTTPException(status_code=404, detail="奖励不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(reward, field, value)
    await db.commit()
    return reward


@router.get("/admin/llm-costs")
async def llm_costs(days: int = 7, db: AsyncSession = Depends(get_db)) -> dict:
    since = utcnow() - timedelta(days=min(days, 90))
    day = func.to_char(func.timezone("Asia/Shanghai", LlmCallLog.created_at), "YYYY-MM-DD")
    rows = (
        await db.execute(
            select(
                day.label("day"),
                LlmCallLog.model,
                LlmCallLog.agent,
                func.count().label("calls"),
                func.sum(LlmCallLog.prompt_tokens).label("prompt"),
                func.sum(LlmCallLog.cached_tokens).label("cached"),
                func.sum(LlmCallLog.completion_tokens).label("completion"),
                func.sum(LlmCallLog.cost_estimate).label("cost"),
                func.sum(cast(LlmCallLog.status != "ok", Integer)).label("errors"),
            )
            .where(LlmCallLog.created_at >= since)
            .group_by("day", LlmCallLog.model, LlmCallLog.agent)
            .order_by(day.desc())
        )
    ).all()
    items = [
        {
            "day": r.day,
            "model": r.model,
            "agent": r.agent,
            "calls": r.calls,
            "prompt_tokens": int(r.prompt or 0),
            "cached_tokens": int(r.cached or 0),
            "completion_tokens": int(r.completion or 0),
            "cost_usd": float(r.cost or 0),
            "errors": int(r.errors or 0),
            "cache_hit_ratio": round((r.cached or 0) / r.prompt, 3) if r.prompt else 0,
        }
        for r in rows
    ]
    return {
        "items": items,
        "total_cost_usd": round(sum(i["cost_usd"] for i in items), 4),
    }
