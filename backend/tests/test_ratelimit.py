import asyncio
import json

import pytest_asyncio
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import chats as chats_api
from app.main import unhandled_exception_handler
from app.models import ChatMessage, User
from app.services import rate_limit as rate_limit_svc
from app.services.rate_limit import CHAT_RATE_LIMITER


@pytest_asyncio.fixture(autouse=True)
async def reset_chat_limiter():
    await CHAT_RATE_LIMITER.reset()
    yield
    await CHAT_RATE_LIMITER.reset()


async def _planner_chat(client: AsyncClient) -> str:
    course = await client.post(
        "/api/v1/courses",
        json={"name": "限流测试课", "goal_text": "验证限流", "daily_minutes": 30},
    )
    chat = await client.post(
        f"/api/v1/courses/{course.json()['id']}/chats", json={"agent": "planner"}
    )
    assert chat.status_code == 200
    return chat.json()["id"]


async def test_seventh_message_is_limited_and_window_recovers(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    chat_id = await _planner_chat(client)
    clock = {"now": 100.0}
    monkeypatch.setattr(rate_limit_svc.time, "monotonic", lambda: clock["now"])

    async def fake_stream(**kwargs):
        yield "已回答。"

    monkeypatch.setattr(chats_api.runtime, "run_stream", fake_stream)
    for index in range(6):
        response = await client.post(
            f"/api/v1/chats/{chat_id}/messages", json={"content": f"问题 {index}"}
        )
        assert response.status_code == 200
    limited = await client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "第七条"}
    )
    assert limited.status_code == 429
    assert limited.json()["detail"] == "提问太快了，休息一下再来"
    assert (
        await db.execute(select(func.count()).select_from(ChatMessage))
    ).scalar_one() == 12

    clock["now"] = 160.0
    recovered = await client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "窗口后恢复"}
    )
    assert recovered.status_code == 200


async def test_second_concurrent_sse_is_limited_without_persisting_message(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    chat_id = await _planner_chat(client)
    started = asyncio.Event()
    release = asyncio.Event()

    async def held_stream(**kwargs):
        started.set()
        await release.wait()
        yield "第一条完成。"

    monkeypatch.setattr(chats_api.runtime, "run_stream", held_stream)
    first_task = asyncio.create_task(
        client.post(f"/api/v1/chats/{chat_id}/messages", json={"content": "第一条"})
    )
    await asyncio.wait_for(started.wait(), timeout=3)
    second = await client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "并发第二条"}
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "提问太快了，休息一下再来"
    release.set()
    first = await asyncio.wait_for(first_task, timeout=3)
    assert first.status_code == 200
    assert (
        await db.execute(select(func.count()).select_from(ChatMessage))
    ).scalar_one() == 2


async def test_global_500_handler_returns_unified_detail() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/failure",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
            "root_path": "",
            "http_version": "1.1",
        }
    )
    response = await unhandled_exception_handler(request, RuntimeError("sensitive stack"))
    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": "服务器开小差了，请稍后再试"}
