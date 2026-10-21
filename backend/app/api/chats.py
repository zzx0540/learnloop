import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from app.agents.runtime import BudgetExceeded, Tier, runtime
from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import Chat, ChatMessage, Course, Job, PlanItem, User, WikiPage, utcnow
from app.schemas.chat import ChatContext, ChatCreate, ChatMessageCreate, ChatMessageOut, ChatOut
from app.services.companion import companion_events
from app.services.exam_guard import exam_chat_locked
from app.services.planner import PLANNER_CHAT_SYSTEM_V1
from app.services.rate_limit import CHAT_RATE_LIMITER

router = APIRouter()


async def _owned_chat(db: AsyncSession, chat_id: uuid.UUID, user: User) -> Chat:
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status_code=404, detail="对话不存在")
    course = await db.get(Course, chat.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="对话不存在")
    return chat


@router.get("/courses/{course_id}/chats", response_model=list[ChatOut])
async def list_chats(
    agent: str | None = None,
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[Chat]:
    stmt = select(Chat).where(Chat.course_id == course.id)
    if agent is not None:
        stmt = stmt.where(Chat.agent == agent)
    return list((await db.execute(stmt.order_by(Chat.last_message_at.desc()))).scalars())


@router.post("/courses/{course_id}/chats", response_model=ChatOut)
async def create_chat(
    data: ChatCreate,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Chat:
    if data.agent == "companion":
        context = data.context or ChatContext()
        if context.plan_item_id is not None:
            item = await db.get(PlanItem, context.plan_item_id)
            if item is None or item.course_id != course.id:
                raise HTTPException(status_code=404, detail="计划条目不存在")
        chat = Chat(
            course_id=course.id,
            user_id=user.id,
            agent=data.agent,
            title={
                "learning": "学习答疑",
                "review": "复习答疑",
                "general": "自由答疑",
            }[context.mode],
            context=context.model_dump(mode="json"),
        )
        db.add(chat)
        await db.commit()
        return chat
    chat = Chat(
        course_id=course.id,
        user_id=user.id,
        agent=data.agent,
        title="规划师对话",
    )
    db.add(chat)
    await db.commit()
    return chat


@router.get("/chats/{chat_id}/messages", response_model=list[ChatMessageOut])
async def list_chat_messages(
    chat_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatMessage]:
    chat = await _owned_chat(db, chat_id, user)
    return list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat.id)
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )


@router.post("/chat-messages/{message_id}/backfill")
async def backfill_chat_message(
    message_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    message = await db.get(ChatMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="回答不存在")
    chat = await _owned_chat(db, message.chat_id, user)
    if message.role != "assistant" or not message.content_md.strip():
        raise HTTPException(status_code=400, detail="只能回填非空的助手回答")
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat.id)
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )
    index = next((i for i, row in enumerate(messages) if row.id == message.id), -1)
    previous = messages[index - 1] if index > 0 else None
    if previous is None or previous.role != "user" or not previous.matched_kp_ids:
        raise HTTPException(status_code=400, detail="该回答未关联知识点，无法回填")
    dedupe_key = f"backfill:{message.id}"
    job = await enqueue(
        db,
        "wiki_backfill",
        {"message_id": str(message.id)},
        course_id=chat.course_id,
        user_id=user.id,
        dedupe_key=dedupe_key,
    )
    if job is None:
        job = (
            await db.execute(
                select(Job).where(
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_(("queued", "running")),
                )
            )
        ).scalar_one()
    await db.commit()
    return {"job_id": str(job.id)}


@router.post("/chats/{chat_id}/messages")
async def send_chat_message(
    chat_id: uuid.UUID,
    data: ChatMessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    chat = await _owned_chat(db, chat_id, user)
    if await exam_chat_locked(db, user.id):
        raise HTTPException(status_code=403, detail="正式考试进行中，聊天功能已锁定")
    course = await db.get(Course, chat.course_id)
    if not await CHAT_RATE_LIMITER.acquire(user.id):
        raise HTTPException(status_code=429, detail="提问太快了，休息一下再来")
    try:
        return await _build_chat_response(chat, course, data, user, db)
    except Exception:
        await CHAT_RATE_LIMITER.release(user.id)
        raise


async def _build_chat_response(
    chat: Chat,
    course: Course,
    data: ChatMessageCreate,
    user: User,
    db: AsyncSession,
) -> EventSourceResponse:
    if chat.agent == "companion":
        user_message = ChatMessage(chat_id=chat.id, role="user", content_md=data.content)
        db.add(user_message)
        chat.last_message_at = utcnow()
        await db.commit()
        history = list(
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.chat_id == chat.id, ChatMessage.content_md != "")
                    .order_by(ChatMessage.created_at, ChatMessage.id)
                )
            ).scalars()
        )
        assistant_message = ChatMessage(chat_id=chat.id, role="assistant", content_md="")
        db.add(assistant_message)
        await db.commit()
        return EventSourceResponse(
            companion_events(
                db,
                chat=chat,
                course=course,
                user_message=user_message,
                assistant_message=assistant_message,
                history=history,
            ),
            ping=15,
            background=BackgroundTask(CHAT_RATE_LIMITER.release, user.id),
        )
    user_message = ChatMessage(chat_id=chat.id, role="user", content_md=data.content)
    db.add(user_message)
    chat.last_message_at = utcnow()
    await db.commit()

    history = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat.id, ChatMessage.content_md != "")
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )
    index_page = (
        await db.execute(
            select(WikiPage).where(
                WikiPage.course_id == course.id,
                WikiPage.slug == "wiki-index",
            )
        )
    ).scalar_one_or_none()
    assistant_message = ChatMessage(chat_id=chat.id, role="assistant", content_md="")
    db.add(assistant_message)
    await db.commit()
    assistant_id = assistant_message.id
    messages = [{"role": message.role, "content": message.content_md} for message in history]
    stable_blocks = [
        f"# 课程目标\n{course.goal_text or course.name}",
        f"# Wiki 索引\n{index_page.content_md if index_page else '（暂无）'}",
    ]

    async def events():
        chunks: list[str] = []
        completed = False
        try:
            async for delta in runtime.run_stream(
                agent="planner",
                operation="planner_chat",
                tier=Tier.medium,
                system_prompt=PLANNER_CHAT_SYSTEM_V1,
                stable_blocks=stable_blocks,
                messages=messages,
                course_id=course.id,
                chat_message_id=assistant_id,
                max_tokens=2048,
                temperature=0.6,
                thinking=False,
            ):
                chunks.append(delta)
                yield {"event": "delta", "data": delta}
            completed = True
        except BudgetExceeded:
            yield {"event": "error", "data": "今日 AI 额度已用完，请明天再试"}
        except Exception:  # noqa: BLE001
            yield {"event": "error", "data": "AI 回复中断，请重试"}
        finally:
            assistant_message.content_md = "".join(chunks)
            chat.last_message_at = utcnow()
            await db.commit()
        if completed:
            yield {
                "event": "done",
                "data": json.dumps({"message_id": str(assistant_id)}, ensure_ascii=False),
            }

    return EventSourceResponse(
        events(),
        ping=15,
        background=BackgroundTask(CHAT_RATE_LIMITER.release, user.id),
    )
