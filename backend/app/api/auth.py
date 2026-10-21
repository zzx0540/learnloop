from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import COOKIE_NAME, get_current_user
from app.core.security import hash_password, hash_token, new_session_token, verify_password
from app.db import get_db
from app.models import InviteCode, User, UserSession, utcnow
from app.schemas.auth import LoginIn, RegisterIn, UserOut

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def _create_session(db: AsyncSession, user: User, request: Request) -> str:
    token, token_hash = new_session_token()
    db.add(
        UserSession(
            id=token_hash,
            user_id=user.id,
            expires_at=utcnow() + timedelta(days=get_settings().session_ttl_days),
            last_seen_at=utcnow(),
            user_agent=(request.headers.get("user-agent") or "")[:256],
        )
    )
    return token


@router.post("/register", response_model=UserOut)
async def register(
    data: RegisterIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> User:
    invite = (
        await db.execute(select(InviteCode).where(InviteCode.code == data.invite_code).with_for_update())
    ).scalar_one_or_none()
    now = utcnow()
    if (
        invite is None
        or not invite.is_active
        or invite.used_count >= invite.max_uses
        or (invite.expires_at is not None and invite.expires_at < now)
    ):
        raise HTTPException(status_code=400, detail="邀请码无效或已失效")

    existing = (await db.execute(select(User.id).where(User.username == data.username))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail="用户名已被占用")

    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        nickname=data.nickname,
        last_login_at=now,
    )
    db.add(user)
    invite.used_count += 1
    await db.flush()
    token = await _create_session(db, user, request)
    await db.commit()
    _set_session_cookie(response, token)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    data: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> User:
    user = (await db.execute(select(User).where(User.username == data.username))).scalar_one_or_none()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user.last_login_at = utcnow()
    token = await _create_session(db, user, request)
    await db.commit()
    _set_session_cookie(response, token)
    return user


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        session = await db.get(UserSession, hash_token(token))
        if session is not None:
            await db.delete(session)
            await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
