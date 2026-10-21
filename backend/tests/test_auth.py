from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InviteCode, utcnow

REG = {"username": "student1", "password": "password123", "nickname": "小明", "invite_code": "WELCOME"}


async def seed_invite(db: AsyncSession, code: str = "WELCOME", max_uses: int = 5, **kwargs) -> None:
    db.add(InviteCode(code=code, max_uses=max_uses, **kwargs))
    await db.commit()


async def test_register_login_me_logout(client: AsyncClient, db: AsyncSession) -> None:
    await seed_invite(db)

    r = await client.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "student1"
    assert "ll_session" in r.cookies

    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["nickname"] == "小明"

    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 200

    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401

    r = await client.post("/api/v1/auth/login", json={"username": "student1", "password": "password123"})
    assert r.status_code == 200
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200


async def test_register_invalid_invite(client: AsyncClient, db: AsyncSession) -> None:
    r = await client.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 400
    assert "邀请码" in r.json()["detail"]


async def test_register_expired_invite(client: AsyncClient, db: AsyncSession) -> None:
    await seed_invite(db, expires_at=utcnow() - timedelta(days=1))
    r = await client.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 400


async def test_register_exhausted_invite(client: AsyncClient, db: AsyncSession) -> None:
    await seed_invite(db, max_uses=1)
    r = await client.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 200
    r = await client.post(
        "/api/v1/auth/register",
        json={**REG, "username": "student2"},
    )
    assert r.status_code == 400


async def test_register_duplicate_username(client: AsyncClient, db: AsyncSession) -> None:
    await seed_invite(db)
    r = await client.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 200
    r = await client.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 400
    assert "用户名" in r.json()["detail"]


async def test_login_wrong_password(client: AsyncClient, db: AsyncSession) -> None:
    await seed_invite(db)
    await client.post("/api/v1/auth/register", json=REG)
    r = await client.post("/api/v1/auth/login", json={"username": "student1", "password": "wrong-pass"})
    assert r.status_code == 401


async def test_weak_password_rejected(client: AsyncClient, db: AsyncSession) -> None:
    await seed_invite(db)
    r = await client.post("/api/v1/auth/register", json={**REG, "password": "short"})
    assert r.status_code == 422
