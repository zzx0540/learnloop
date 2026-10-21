import hashlib
import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import AgentRuntime, LlmOutputError, Tier
from app.models import (
    Chat,
    ChatMessage,
    Job,
    KnowledgePoint,
    QuizAttempt,
    TangentItem,
    User,
    WikiLogEntry,
    WikiPage,
    WikiPageRevision,
    utcnow,
)
from app.services import companion as companion_svc
from app.services.companion import (
    COMPANION_CHAT_SYSTEM_V1,
    COMPANION_DEPTH_LEARNING_V1,
    COMPANION_DEPTH_REVIEW_V2,
    COMPANION_OFF_TOPIC_REPLY_V1,
    REVIEW_MODE_REMINDER_V2,
    ScopeClassifyOut,
    WikiBackfillOut,
)
from app.services.course_kind import KIND_HINT_V1


def test_material_question_omits_empty_reference_heading() -> None:
    content = companion_svc._material_question([], "这个知识点怎么理解？", "general")

    assert "# 参考资料（来自课程 Wiki）" not in content
    assert content == "# 用户问题\n这个知识点怎么理解？"


async def _create_companion_fixture(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    mode: str = "learning",
    kind: str = "knowledge",
) -> tuple[dict, KnowledgePoint, WikiPage, str]:
    response = await client.post(
        "/api/v1/courses",
        json={
            "name": "学习科学入门",
            "goal_text": "掌握核心学习方法",
            "daily_minutes": 45,
            "kind": kind,
        },
    )
    assert response.status_code == 200, response.text
    course = response.json()
    course_id = uuid.UUID(course["id"])
    page = WikiPage(
        course_id=course_id,
        slug="spacing-effect",
        title="间隔效应",
        kind="concept",
        content_md="间隔学习通过拉开练习时间，提高长期保持。",
    )
    db.add(page)
    await db.flush()
    kp = KnowledgePoint(
        course_id=course_id,
        slug="spacing-effect",
        title="间隔效应",
        summary="分散学习比集中学习更利于保持。",
        primary_wiki_page_id=page.id,
    )
    db.add(kp)
    await db.commit()
    chat_response = await client.post(
        f"/api/v1/courses/{course['id']}/chats",
        json={"agent": "companion", "context": {"mode": mode}},
    )
    assert chat_response.status_code == 200, chat_response.text
    assert chat_response.json()["title"] == {
        "learning": "学习答疑",
        "review": "复习答疑",
        "general": "自由答疑",
    }[mode]
    assert chat_response.json()["context"]["mode"] == mode
    return course, kp, page, chat_response.json()["id"]


async def test_in_syllabus_material_history_and_sse_order(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, kp, page, chat_id = await _create_companion_fixture(client, db, logged_in)
    kp_id = kp.id
    kp_slug = kp.slug
    page_title = page.title
    page_slug = page.slug
    page_content = page.content_md
    chat = await db.get(Chat, uuid.UUID(chat_id))
    db.add_all(
        [
            ChatMessage(chat_id=chat.id, role="user", content_md="上一轮干净问题"),
            ChatMessage(chat_id=chat.id, role="assistant", content_md="上一轮干净回答"),
        ]
    )
    await db.commit()
    captured: dict = {}

    async def fake_structured(self, **kwargs):
        captured["classify"] = kwargs
        return ScopeClassifyOut(
            scope="in_syllabus",
            matched_kp_slugs=["unknown-slug", kp_slug],
            reason="问题直接询问课程知识点。",
        )

    async def fake_stream(self, **kwargs):
        captured["stream"] = kwargs
        yield "间隔效应是分散安排学习。"
        yield f"\n\n参考：[{page_title}](wiki:{page_slug})"

    monkeypatch.setattr(AgentRuntime, "run_structured", fake_structured)
    monkeypatch.setattr(AgentRuntime, "run_stream", fake_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "什么是间隔效应？"},
    )
    assert response.status_code == 200
    assert response.text.index("event: scope") < response.text.index("event: delta")
    assert response.text.index("event: delta") < response.text.index("event: done")
    assert captured["classify"]["tier"] is Tier.low
    assert captured["classify"]["thinking"] is False
    assert captured["stream"]["tier"] is Tier.medium
    knowledge_prompt = COMPANION_CHAT_SYSTEM_V1 + "\n\n" + COMPANION_DEPTH_LEARNING_V1
    assert captured["stream"]["system_prompt"] == knowledge_prompt
    assert hashlib.sha256(knowledge_prompt.encode()).hexdigest() == (
        "688ebf5b15a040c03f6f1f318e9210bc79d25dc05768c4d5089d1ea4ae68e312"
    )
    assert captured["stream"]["messages"][0]["content"] == "上一轮干净问题"
    assert captured["stream"]["messages"][1]["content"] == "上一轮干净回答"
    current = captured["stream"]["messages"][-1]["content"]
    assert "# 参考资料（来自课程 Wiki）" in current
    assert page_content in current
    assert "# 用户问题\n什么是间隔效应？" in current

    db.expire_all()
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == uuid.UUID(chat_id))
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )
    user_message = messages[-2]
    assistant_message = messages[-1]
    assert user_message.content_md == "什么是间隔效应？"
    assert user_message.scope_class == "in_syllabus"
    assert user_message.matched_kp_ids == [kp_id]
    assert assistant_message.content_md.endswith(f"参考：[{page_title}](wiki:{page_slug})")


async def test_language_companion_appends_kind_strategy(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    _, kp, _, chat_id = await _create_companion_fixture(
        client, db, logged_in, kind="language"
    )
    captured = {}

    async def fake_structured(self, **kwargs):
        return ScopeClassifyOut(
            scope="in_syllabus",
            matched_kp_slugs=[kp.slug],
            reason="语言课程问题。",
        )

    async def fake_stream(self, **kwargs):
        captured.update(kwargs)
        yield "这是一个语言例句。"

    monkeypatch.setattr(AgentRuntime, "run_structured", fake_structured)
    monkeypatch.setattr(AgentRuntime, "run_stream", fake_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "请给一个例句。"}
    )
    assert response.status_code == 200
    assert captured["system_prompt"].endswith(KIND_HINT_V1["language"])


async def test_review_mode_injects_hard_depth_rules(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    _, kp, _, chat_id = await _create_companion_fixture(
        client, db, logged_in, mode="review"
    )
    captured: dict = {}

    async def fake_structured(self, **kwargs):
        return ScopeClassifyOut(
            scope="in_syllabus",
            matched_kp_slugs=[kp.slug],
            reason="问题直接询问课程知识点。",
        )

    async def fake_stream(self, **kwargs):
        captured.update(kwargs)
        yield "先想想复习间隔改变了哪一种记忆过程？"

    monkeypatch.setattr(AgentRuntime, "run_structured", fake_structured)
    monkeypatch.setattr(AgentRuntime, "run_stream", fake_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "间隔学习为什么有效？"},
    )
    assert response.status_code == 200
    assert COMPANION_DEPTH_REVIEW_V2 in captured["system_prompt"]
    current = captured["messages"][-1]["content"]
    assert REVIEW_MODE_REMINDER_V2 in current
    assert current.index(REVIEW_MODE_REMINDER_V2) < current.index("# 用户问题")


async def test_adjacent_and_off_topic_routes(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, kp, _, chat_id = await _create_companion_fixture(client, db, logged_in)
    outputs = [
        ScopeClassifyOut(
            scope="adjacent",
            matched_kp_slugs=[kp.slug],
            reason="属于课程主题的延伸应用。",
        ),
        ScopeClassifyOut(
            scope="off_topic",
            matched_kp_slugs=[kp.slug],
            reason="与课程无关。",
        ),
    ]
    stream_calls = 0

    async def fake_structured(self, **kwargs):
        return outputs.pop(0)

    async def fake_stream(self, **kwargs):
        nonlocal stream_calls
        stream_calls += 1
        assert kwargs["tier"] is Tier.low
        assert kwargs["max_tokens"] == 800
        yield "这是课程外的延伸方向。"
        yield "已放入拓展清单。"

    monkeypatch.setattr(AgentRuntime, "run_structured", fake_structured)
    monkeypatch.setattr(AgentRuntime, "run_stream", fake_stream)
    adjacent = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "间隔效应在广告投放中怎么用？"},
    )
    assert adjacent.status_code == 200
    assert '"tangent": true' in adjacent.text
    assert "event: done" in adjacent.text
    off_topic = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "帮我推荐晚餐。"},
    )
    assert off_topic.status_code == 200
    assert COMPANION_OFF_TOPIC_REPLY_V1 in off_topic.text
    assert stream_calls == 1

    db.expire_all()
    tangents = list(
        (
            await db.execute(
                select(TangentItem)
                .where(TangentItem.course_id == uuid.UUID(course["id"]))
                .order_by(TangentItem.created_at)
            )
        ).scalars()
    )
    assert len(tangents) == 2
    assert tangents[0].brief_answer == "这是课程外的延伸方向。已放入拓展清单。"
    assert tangents[1].brief_answer == ""
    off_topic_user = await db.get(ChatMessage, tangents[1].chat_message_id)
    assert off_topic_user.scope_class == "off_topic"
    assert off_topic_user.matched_kp_ids == []
    listed = await client.get(f"/api/v1/courses/{course['id']}/tangents?status=parked")
    assert listed.status_code == 200
    assert len(listed.json()) == 2


async def test_interrupted_adjacent_stream_does_not_persist_partial_answer(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, kp, _, chat_id = await _create_companion_fixture(client, db, logged_in)

    async def fake_structured(self, **kwargs):
        return ScopeClassifyOut(
            scope="adjacent",
            matched_kp_slugs=[kp.slug],
            reason="属于课程主题的延伸应用。",
        )

    async def interrupted_stream(self, **kwargs):
        yield "尚未完成的局部回答"
        raise RuntimeError("stream interrupted")

    monkeypatch.setattr(AgentRuntime, "run_structured", fake_structured)
    monkeypatch.setattr(AgentRuntime, "run_stream", interrupted_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "间隔效应还有哪些相邻应用？"},
    )

    assert response.status_code == 200
    assert "尚未完成的局部回答" in response.text
    assert "event: error" in response.text
    assert "event: done" not in response.text
    db.expire_all()
    tangent = (
        await db.execute(
            select(TangentItem).where(
                TangentItem.course_id == uuid.UUID(course["id"])
            )
        )
    ).scalar_one()
    assert tangent.brief_answer == ""


async def test_scope_failure_is_fail_closed(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    _, _, _, chat_id = await _create_companion_fixture(client, db, logged_in)

    async def broken_structured(self, **kwargs):
        raise LlmOutputError("invalid scope JSON")

    async def forbidden_stream(self, **kwargs):
        raise AssertionError("分类失败后不得调用回答模型")
        yield

    monkeypatch.setattr(AgentRuntime, "run_structured", broken_structured)
    monkeypatch.setattr(AgentRuntime, "run_stream", forbidden_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "这个问题会失败"},
    )
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "event: scope" not in response.text
    assert "event: delta" not in response.text
    assert "event: done" not in response.text
    db.expire_all()
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == uuid.UUID(chat_id))
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )
    assert messages[-2].scope_class is None
    assert messages[-1].role == "assistant"
    assert messages[-1].content_md == ""


async def test_formal_exam_lock_precedes_llm_and_message_persistence(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, _, _, companion_chat_id = await _create_companion_fixture(client, db, logged_in)
    planner = await client.post(
        f"/api/v1/courses/{course['id']}/chats", json={"agent": "planner"}
    )
    planner_chat_id = planner.json()["id"]
    attempt = QuizAttempt(
        course_id=uuid.UUID(course["id"]),
        user_id=logged_in.id,
        kind="formal_exam",
        status="in_progress",
        deadline_at=utcnow() + timedelta(hours=1),
    )
    db.add(attempt)
    await db.commit()

    async def forbidden(self, *args, **kwargs):
        raise AssertionError("考试态不得调用 LLM")
        yield

    monkeypatch.setattr(AgentRuntime, "run_stream", forbidden)
    for chat_id in (planner_chat_id, companion_chat_id):
        locked = await client.post(
            f"/api/v1/chats/{chat_id}/messages", json={"content": "考试中提问"}
        )
        assert locked.status_code == 403
        assert locked.json()["detail"] == "正式考试进行中，聊天功能已锁定"
    assert (
        await db.execute(select(func.count()).select_from(ChatMessage))
    ).scalar_one() == 0

    attempt.deadline_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    captured = {}

    async def planner_stream(self, **kwargs):
        captured.update(kwargs)
        yield "已恢复聊天。"

    monkeypatch.setattr(AgentRuntime, "run_stream", planner_stream)
    unlocked = await client.post(
        f"/api/v1/chats/{planner_chat_id}/messages", json={"content": "考试结束了吗？"}
    )
    assert unlocked.status_code == 200
    assert captured["system_prompt"].startswith("# 学习规划师 v1")
    assert captured["messages"] == [{"role": "user", "content": "考试结束了吗？"}]


async def test_backfill_wiki_is_versioned_and_idempotent(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, kp, page, chat_id = await _create_companion_fixture(client, db, logged_in)
    page_id = page.id
    chat = await db.get(Chat, uuid.UUID(chat_id))
    user_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content_md="间隔效应为什么有效？",
        scope_class="in_syllabus",
        matched_kp_ids=[kp.id],
    )
    assistant = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content_md="它通过增加提取难度强化长期记忆。",
    )
    db.add_all([user_message, assistant])
    await db.commit()
    assistant_id = assistant.id

    response = await client.post(f"/api/v1/chat-messages/{assistant_id}/backfill")
    assert response.status_code == 200, response.text
    job = await db.get(Job, uuid.UUID(response.json()["job_id"]))

    async def fake_structured(self, **kwargs):
        return WikiBackfillOut(
            qa_title="间隔效应为什么有效？",
            qa_answer_md="它通过增加提取难度强化长期记忆。",
        )

    monkeypatch.setattr(AgentRuntime, "run_structured", fake_structured)
    result = await companion_svc.handle_wiki_backfill(db, job)
    assert result["version"] == 2
    db.expire_all()
    updated_page = await db.get(WikiPage, page_id)
    assert "## 常见问答" in updated_page.content_md
    assert "**Q：间隔效应为什么有效？**" in updated_page.content_md
    assert updated_page.updated_by == "companion"
    assert (
        await db.execute(
            select(func.count()).select_from(WikiPageRevision).where(WikiPageRevision.page_id == page_id)
        )
    ).scalar_one() == 1
    assert (
        await db.execute(
            select(func.count())
            .select_from(WikiLogEntry)
            .where(WikiLogEntry.course_id == uuid.UUID(course["id"]))
        )
    ).scalar_one() == 1

    second_job = Job(
        kind="wiki_backfill",
        payload={"message_id": str(assistant_id)},
        course_id=uuid.UUID(course["id"]),
    )
    db.add(second_job)
    await db.flush()
    skipped = await companion_svc.handle_wiki_backfill(db, second_job)
    assert skipped == {"skipped": "相同问答已存在"}

    no_match_user = ChatMessage(chat_id=chat.id, role="user", content_md="谢谢")
    no_match_answer = ChatMessage(chat_id=chat.id, role="assistant", content_md="不客气")
    db.add_all([no_match_user, no_match_answer])
    await db.commit()
    invalid = await client.post(f"/api/v1/chat-messages/{no_match_answer.id}/backfill")
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "该回答未关联知识点，无法回填"
