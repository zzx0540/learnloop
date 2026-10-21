import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import hash_password
from app.models import InviteCode, User

logger = logging.getLogger("learnloop.bootstrap")


async def bootstrap(db: AsyncSession) -> None:
    settings = get_settings()

    admin = (
        await db.execute(select(User).where(User.username == settings.admin_username))
    ).scalar_one_or_none()
    if admin is None and settings.admin_password:
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            nickname="管理员",
            role="admin",
        )
        db.add(admin)
        logger.info("已创建管理员账号: %s", settings.admin_username)

    active_codes = (
        await db.execute(select(func.count()).select_from(InviteCode).where(InviteCode.is_active.is_(True)))
    ).scalar_one()
    if active_codes == 0:
        code = settings.initial_invite_code or secrets.token_urlsafe(8)
        db.add(InviteCode(code=code, max_uses=50))
        logger.info("已创建初始邀请码: %s", code)

    await db.commit()
