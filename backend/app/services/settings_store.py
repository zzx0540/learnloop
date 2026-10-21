from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

# 运行时可调参数默认值（存于 app_settings，键 -> {"v": 值}）
DEFAULTS: dict[str, Any] = {
    # 每课程每日 token 预算（prompt+completion 合计）；书籍入库是大头，预算耗尽任务会延后 1 小时
    "llm_daily_token_budget_per_course": 2_000_000,
    # 各模型价格（美元 / 百万 token）：miss=未命中输入 hit=缓存命中输入 out=输出
    "llm_pricing": {
        "deepseek-v4-flash": {"miss": 0.14, "hit": 0.0028, "out": 0.28},
        "deepseek-v4-pro": {"miss": 0.435, "hit": 0.003625, "out": 0.87},
        "gpt-5.6-sol": {"miss": 5.0, "hit": 0.5, "out": 15.0},
    },
    # 上传限制
    "upload_max_mb": 100,
    "upload_quota_mb_per_user": 2048,
}


async def get_setting(db: AsyncSession, key: str) -> Any:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is not None and isinstance(row.value, dict) and "v" in row.value:
        return row.value["v"]
    return DEFAULTS.get(key)


async def set_setting(db: AsyncSession, key: str, value: Any) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
    await db.commit()
