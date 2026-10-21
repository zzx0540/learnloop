import os
import tempfile

from tests.safety import require_test_database

TEST_DATABASE_URL = require_test_database(
    os.environ.get("TEST_DATABASE_URL", ""), os.environ.get("LEARNLOOP_ALLOW_TEST_RESET", "")
)
# Bind service-owned sessions as well as route dependencies to the disposable database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DEMO_MODE"] = "false"
_test_files = tempfile.TemporaryDirectory(prefix="learnloop-test-")

os.environ["FILES_DIR"] = _test_files.name
os.environ["COOKIE_SECURE"] = "false"

from collections.abc import AsyncGenerator  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, InviteCode, User  # noqa: E402

test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture()
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def logged_in(client: AsyncClient, db: AsyncSession) -> User:
    """注册并登录一个用户，返回 User。"""
    db.add(InviteCode(code="TEST", max_uses=100))
    await db.commit()
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "password123", "nickname": "小明", "invite_code": "TEST"},
    )
    assert r.status_code == 200, r.text
    from sqlalchemy import select

    return (
        await db.execute(select(User).where(User.username == "student1"))
    ).scalar_one()


# 会话级 monkeypatch 需要的事件循环兼容：pytest-asyncio auto 模式已足够
