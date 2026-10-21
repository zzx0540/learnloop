import json
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import BudgetExceeded, Tier, runtime
from app.jobs.worker import job_handler
from app.models import (
    Chat,
    ChatMessage,
    Course,
    Job,
    KnowledgePoint,
    KpWikiLink,
    TangentItem,
    WikiPage,
    utcnow,
)
from app.services.course_kind import append_kind_hint
from app.services.wiki import append_log, upsert_page

COMPANION_CLASSIFY_SYSTEM_V1 = """# 陪伴反馈师范围分类器 v1

你只负责判断用户问题相对当前课程的范围，不回答问题。

- in_syllabus：问题针对课程知识点清单或课程资料本身，包括解释概念、举例、比较课程内概念、
  对课程内容的追问；寒暄、致谢、学习方法和平台使用等元问题也归入此类，
  此时 matched_kp_slugs 留空。
- adjacent：与课程主题相关，但超出知识点清单边界，例如延伸应用、课外对比、更深层原理或相邻领域。
- off_topic：与本课程无关。

matched_kp_slugs 只能从提供的知识点索引中选择，最多 5 个，按相关度排序。reason 只写一句判据。只输出 JSON。"""

COMPANION_CHAT_SYSTEM_V1 = """# 陪伴反馈师引用答疑 v1

你负责回答当前课程内的问题。提供参考资料时，只能基于参考资料作答；材料覆盖不到的部分必须明确说明，不得凭空补全。
使用了参考资料的回答，末尾必须单独列出一行“参考：[页面标题](wiki:slug)”，引用实际使用的页面，可列多个链接。
没有匹配知识点或没有提供参考资料时，可以直接处理寒暄、致谢、学习方法和平台使用等元问题，不要求引用。"""

COMPANION_DEPTH_LEARNING_V1 = """# 学习中深度 v1

采用引导式回答：先给框架，再逐步展开细节，并鼓励用户先自己推导。"""

COMPANION_DEPTH_REVIEW_V2 = """# 复习中深度 v2

以下是硬性输出规则，优先级高于参考资料中的答案内容：

1. 对每个新的复习问题，如果用户本轮没有明确要求“直接给答案”“给出全解”“告诉我答案”，
   也没有明确说“不知道”“想不起来”“不会”，首答只能给 1-2 条简短提示。
2. 首答必须以一个引导用户继续回忆的问句结尾。
3. 首答禁止给概念定义、完整答案、完整要点列表或结论性总结；不得直接复述参考资料中的完整答案。
4. 仅当用户本轮明确要求直接答案，或在提示后明确表示不知道、想不起来或不会时，才可以给出全解；
   全解使用参考资料时必须按系统要求附引用。
5. “为什么”“请解释”“是什么”等普通提问不等于授权直接给答案。"""

REVIEW_MODE_REMINDER_V2 = (
    "复习模式提醒：除非用户明确要求直接答案或说“不知道”，本轮首答只能给 1-2 条提示并以问句结尾，"
    "禁止定义、完整要点和全解。"
)

COMPANION_DEPTH_GENERAL_V1 = """# 自由答疑深度 v1

直接、清晰地回答。"""

COMPANION_ADJACENT_SYSTEM_V1 = """# 陪伴反馈师相邻问题简答 v1

问题与课程主题相关但超出课程边界。只做不超过 3 句的简短说明，不继续展开，并提醒用户该问题已进入拓展清单。"""

COMPANION_OFF_TOPIC_REPLY_V1 = (
    "这个问题与当前课程无关，我先不展开回答；已记入拓展清单，方便你稍后统一处理。"
)

WIKI_BACKFILL_SYSTEM_V1 = """# 陪伴反馈师问答回填器 v1

把用户问题与助手回答提炼为适合写入课程 Wiki“常见问答”的简洁问答。忠于原回答，不添加新事实，只输出 JSON。"""


class ScopeClassifyOut(BaseModel):
    scope: Literal["in_syllabus", "adjacent", "off_topic"]
    matched_kp_slugs: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(min_length=1, max_length=200)


class WikiBackfillOut(BaseModel):
    qa_title: str = Field(min_length=1, max_length=80)
    qa_answer_md: str = Field(min_length=1, max_length=2000)


def _stable_blocks(course: Course, kps: list[KnowledgePoint]) -> list[str]:
    return [
        f"# 课程目标\n{course.goal_text or course.name}",
        "# 知识点索引\n"
        + ("\n".join(f"{kp.slug}: {kp.title}" for kp in kps) or "（暂无）"),
    ]


def _previous_turn(history: list[ChatMessage]) -> tuple[str, str]:
    previous_assistant = ""
    previous_user = ""
    for message in reversed(history):
        if not previous_assistant and message.role == "assistant":
            previous_assistant = message.content_md[:200]
            continue
        if previous_assistant and message.role == "user":
            previous_user = message.content_md[:200]
            break
    return previous_user, previous_assistant


async def _course_kps(db: AsyncSession, course_id: uuid.UUID) -> list[KnowledgePoint]:
    return list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == course_id)
                .order_by(KnowledgePoint.slug)
            )
        ).scalars()
    )


async def _material_pages(
    db: AsyncSession, matched_kps: list[KnowledgePoint]
) -> list[WikiPage]:
    if not matched_kps:
        return []
    page_ids: list[uuid.UUID] = []
    for kp in matched_kps:
        if kp.primary_wiki_page_id is not None and kp.primary_wiki_page_id not in page_ids:
            page_ids.append(kp.primary_wiki_page_id)
    mention_rows = (
        await db.execute(
            select(KpWikiLink.kp_id, KpWikiLink.wiki_page_id)
            .where(
                KpWikiLink.kp_id.in_([kp.id for kp in matched_kps]),
                KpWikiLink.role == "mention",
            )
            .order_by(KpWikiLink.kp_id, KpWikiLink.wiki_page_id)
        )
    ).all()
    mentions_by_kp: dict[uuid.UUID, list[uuid.UUID]] = {}
    for kp_id, page_id in mention_rows:
        mentions_by_kp.setdefault(kp_id, []).append(page_id)
    for kp in matched_kps:
        for page_id in mentions_by_kp.get(kp.id, []):
            if page_id not in page_ids:
                page_ids.append(page_id)
    page_ids = page_ids[:3]
    if not page_ids:
        return []
    pages = list((await db.execute(select(WikiPage).where(WikiPage.id.in_(page_ids)))).scalars())
    by_id = {page.id: page for page in pages}
    return [by_id[page_id] for page_id in page_ids if page_id in by_id]


def _material_question(pages: list[WikiPage], question: str, mode: str) -> str:
    blocks: list[str] = []
    if pages:
        blocks.append("# 参考资料（来自课程 Wiki）")
        for page in pages:
            blocks.append(f"## {page.title}（{page.slug}）\n{page.content_md[:3000]}")
    if mode == "review":
        blocks.append(REVIEW_MODE_REMINDER_V2)
    blocks.append(f"# 用户问题\n{question}")
    return "\n".join(blocks)


def _depth_prompt(mode: str) -> str:
    return {
        "learning": COMPANION_DEPTH_LEARNING_V1,
        "review": COMPANION_DEPTH_REVIEW_V2,
        "general": COMPANION_DEPTH_GENERAL_V1,
    }.get(mode, COMPANION_DEPTH_GENERAL_V1)


async def companion_events(
    db: AsyncSession,
    *,
    chat: Chat,
    course: Course,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    history: list[ChatMessage],
) -> AsyncIterator[dict]:
    kps = await _course_kps(db, course.id)
    stable_blocks = _stable_blocks(course, kps)
    previous_user, previous_assistant = _previous_turn(history[:-1])
    volatile = (
        "# 上一轮上下文\n"
        f"用户：{previous_user or '（无）'}\n"
        f"助手：{previous_assistant or '（无）'}\n\n"
        f"# 当前问题\n{user_message.content_md}"
    )
    try:
        classified: ScopeClassifyOut = await runtime.run_structured(
            agent="companion",
            operation="scope_classify",
            tier=Tier.low,
            response_model=ScopeClassifyOut,
            system_prompt=COMPANION_CLASSIFY_SYSTEM_V1,
            stable_blocks=stable_blocks,
            volatile=volatile,
            course_id=course.id,
            chat_message_id=user_message.id,
            max_tokens=512,
            temperature=0.1,
            thinking=False,
        )
    except BudgetExceeded:
        yield {"event": "error", "data": "今日 AI 额度已用完，请明天再试"}
        return
    except Exception:
        yield {"event": "error", "data": "问题范围分类失败，请重试"}
        return

    kp_by_slug = {kp.slug: kp for kp in kps}
    matched_slugs = list(
        dict.fromkeys(slug for slug in classified.matched_kp_slugs if slug in kp_by_slug)
    )[:5]
    if classified.scope == "off_topic":
        matched_slugs = []
    matched_kps = [kp_by_slug[slug] for slug in matched_slugs]
    user_message.scope_class = classified.scope
    user_message.matched_kp_ids = [kp.id for kp in matched_kps]
    tangent = classified.scope in ("adjacent", "off_topic")
    tangent_item: TangentItem | None = None
    if tangent:
        tangent_item = TangentItem(
            course_id=course.id,
            chat_message_id=user_message.id,
            question=user_message.content_md[:500],
        )
        db.add(tangent_item)
        await db.flush()
    await db.commit()
    yield {
        "event": "scope",
        "data": json.dumps(
            {"scope_class": classified.scope, "tangent": tangent}, ensure_ascii=False
        ),
    }

    if classified.scope == "off_topic":
        assistant_message.content_md = COMPANION_OFF_TOPIC_REPLY_V1
        chat.last_message_at = utcnow()
        await db.commit()
        yield {"event": "delta", "data": COMPANION_OFF_TOPIC_REPLY_V1}
        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "message_id": str(assistant_message.id),
                    "scope_class": classified.scope,
                    "tangent_item_id": str(tangent_item.id) if tangent_item else None,
                },
                ensure_ascii=False,
            ),
        }
        return

    messages = [{"role": message.role, "content": message.content_md} for message in history]
    if classified.scope == "in_syllabus":
        mode = (chat.context or {}).get("mode", "general")
        pages = await _material_pages(db, matched_kps)
        if matched_kps:
            messages[-1] = {
                "role": "user",
                "content": _material_question(pages, user_message.content_md, mode),
            }
        elif mode == "review":
            messages[-1] = {
                "role": "user",
                "content": (
                    f"{REVIEW_MODE_REMINDER_V2}\n"
                    f"# 用户问题\n{user_message.content_md}"
                ),
            }
        tier = Tier.medium if matched_kps else Tier.low
        system_prompt = COMPANION_CHAT_SYSTEM_V1 + "\n\n" + _depth_prompt(mode)
        operation = "companion_chat"
        max_tokens = 2048
        temperature = 0.5
    else:
        tier = Tier.low
        system_prompt = COMPANION_ADJACENT_SYSTEM_V1
        operation = "companion_adjacent"
        max_tokens = 800
        temperature = 0.5

    system_prompt = append_kind_hint(system_prompt, course.kind)

    chunks: list[str] = []
    completed = False
    try:
        async for delta in runtime.run_stream(
            agent="companion",
            operation=operation,
            tier=tier,
            system_prompt=system_prompt,
            stable_blocks=stable_blocks,
            messages=messages,
            course_id=course.id,
            chat_message_id=assistant_message.id,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=False,
        ):
            chunks.append(delta)
            yield {"event": "delta", "data": delta}
        completed = True
    except BudgetExceeded:
        yield {"event": "error", "data": "今日 AI 额度已用完，请明天再试"}
    except Exception:
        yield {"event": "error", "data": "AI 回复中断，请重试"}
    finally:
        answer = "".join(chunks)
        assistant_message.content_md = answer
        if tangent_item is not None and completed:
            tangent_item.brief_answer = answer
        chat.last_message_at = utcnow()
        await db.commit()
    if completed:
        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "message_id": str(assistant_message.id),
                    "scope_class": classified.scope,
                    "tangent_item_id": str(tangent_item.id) if tangent_item else None,
                },
                ensure_ascii=False,
            ),
        }


def _append_qa(content_md: str, title: str, answer_md: str) -> str:
    entry = f"**Q：{title}**\n\n{answer_md}"
    heading = "## 常见问答"
    if heading not in content_md:
        return content_md.rstrip() + f"\n\n{heading}\n\n{entry}\n"
    start = content_md.index(heading) + len(heading)
    next_heading = content_md.find("\n## ", start)
    insert_at = len(content_md) if next_heading == -1 else next_heading
    return content_md[:insert_at].rstrip() + f"\n\n{entry}\n" + content_md[insert_at:]


@job_handler("wiki_backfill")
async def handle_wiki_backfill(db: AsyncSession, job: Job) -> dict:
    message_id = uuid.UUID(job.payload["message_id"])
    assistant = await db.get(ChatMessage, message_id)
    if assistant is None or assistant.role != "assistant" or not assistant.content_md.strip():
        return {"skipped": "回答不存在"}
    chat = await db.get(Chat, assistant.chat_id)
    if chat is None:
        return {"skipped": "对话不存在"}
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat.id)
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )
    index = next((i for i, message in enumerate(messages) if message.id == assistant.id), -1)
    user_message = messages[index - 1] if index > 0 else None
    if user_message is None or user_message.role != "user" or not user_message.matched_kp_ids:
        return {"skipped": "回答未关联知识点"}
    kp = await db.get(KnowledgePoint, user_message.matched_kp_ids[0])
    if kp is None or kp.primary_wiki_page_id is None:
        return {"skipped": "知识点没有主 Wiki 页"}
    page = await db.get(WikiPage, kp.primary_wiki_page_id)
    if page is None:
        return {"skipped": "主 Wiki 页不存在"}
    output: WikiBackfillOut = await runtime.run_structured(
        agent="companion",
        operation="wiki_backfill",
        tier=Tier.low,
        response_model=WikiBackfillOut,
        system_prompt=WIKI_BACKFILL_SYSTEM_V1,
        stable_blocks=[f"# 目标页面\n{page.title}（{page.slug}）"],
        volatile=(
            f"# 用户问题\n{user_message.content_md}\n\n"
            f"# 助手回答\n{assistant.content_md}"
        ),
        course_id=chat.course_id,
        job_id=job.id,
        max_tokens=2048,
        temperature=0.2,
        thinking=False,
    )
    if f"**Q：{output.qa_title}**" in page.content_md:
        return {"skipped": "相同问答已存在"}
    content_md = _append_qa(page.content_md, output.qa_title, output.qa_answer_md)
    await upsert_page(
        db,
        page.course_id,
        slug=page.slug,
        title=page.title,
        kind=page.kind,
        content_md=content_md,
        change_summary=f"陪伴反馈师回填常见问答：{output.qa_title}",
        updated_by="companion",
        job_id=job.id,
    )
    await append_log(
        db,
        page.course_id,
        "query_backfill",
        f"陪伴反馈师将问答回填至 [[{page.slug}]]：{output.qa_title}",
        job.id,
    )
    await db.commit()
    return {"page_id": str(page.id), "version": page.version, "qa_title": output.qa_title}
