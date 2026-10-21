import uuid
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.db import get_db
from app.models import Course, User, UserSession, utcnow

COOKIE_NAME = "ll_session"
SEEN_REFRESH_INTERVAL = timedelta(minutes=10)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    session = await db.get(UserSession, hash_token(token))
    now = utcnow()
    if session is None or session.expires_at < now:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = await db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    if session.last_seen_at is None or now - session.last_seen_at > SEEN_REFRESH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def get_owned_course(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Course:
    course = await db.get(Course, course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="课程不存在")
    return course
