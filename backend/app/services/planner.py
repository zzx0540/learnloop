import json
import logging
import re
import uuid
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.queue import enqueue
from app.jobs.worker import job_handler
from app.models import (
    Chat,
    ChatMessage,
    Course,
    CourseBrief,
    Job,
    KnowledgePoint,
    KpEdge,
    KpWikiLink,
    Plan,
    PlanItem,
    PlanItemKp,
    QuizAttempt,
    ReviewCard,
    ReviewLog,
    WeaknessRecord,
    WikiPage,
)
from app.models.language import LanguageProfile, LanguageSource
from app.services import wiki as wiki_svc
from app.services.language_learning import ensure_plan_language_source, recent_language_performance
from app.services.mastery import (
    aggregate_course_date,
    evaluate_course_mastery,
    simulate_from_card,
    simulate_mastery_dates,
)
from app.services.reviews import card_from_dict

logger = logging.getLogger("learnloop.planner")
SHANGHAI = ZoneInfo("Asia/Shanghai")
EXTRACT_KPS_BATCH_SIZE = 80

PLANNER_CHAT_SYSTEM_V1 = """# 学习规划师 v1

你是专业、友善的学习规划师。你的职责是通过对话逐步弄清：学习目标、每日可用分钟、目标日期、已有基础和学习偏好。
每次只问 1-2 个最关键的问题，不要让用户一次填写长表格。结合课程资料说话，但不要虚构资料中没有的内容。
你不在聊天里直接生成计划；当信息已经足够时，清楚提示用户点击「生成学习计划」。"""

EXTRACT_KPS_SYSTEM_V1 = """# 知识点抽取器 v1

从课程 Wiki 中提炼可学习、可考核的知识点，并建立少量高质量关系。
优先复用现有知识点 slug，避免同义重复。prerequisite 的 src 是 dst 的前置知识。
每个知识点写一个不含答案、能促发主动回忆的 recall_prompt 问题。
只输出 JSON。"""

ONBOARDING_SUMMARY_SYSTEM_V1 = """# 学员画像整理器 v1

从规划师对话中提炼学习目标、每日可用分钟、目标日期、已有基础与偏好。
信息缺失时结合课程当前信息做保守总结，不要编造具体经历。只输出 JSON。"""

GENERATE_PLAN_SYSTEM_V1 = """# 学习计划生成器 v1

根据学员画像、知识点难度重要性和前置关系生成学习计划。
learn 项覆盖尚未归档的知识点且每个知识点只出现一次；前置知识必须更早安排。
每日总时长不得超过给定预算，新知安排只使用扣除 30% 复习预留后的分钟数。
每周至少一次 quiz；计划达到 14 天时每两周至少一次 stage_exam。只输出 JSON。"""

GENERATE_LANGUAGE_PLAN_SUFFIX_V1 = """# 英语课程计划策略 v1

英语课程不要求 learn 或知识点 slug。根据课程轨道、能力画像与已上传来源安排 reading 和 writing。
每 7 天至少安排 2 个 reading（精读/泛读均衡）和 1 个 writing；词汇复习由系统每日队列负责，不生成逐词条目。
IELTS、六级、考研英语贴合相应体裁但不得复制真题；新概念按材料顺序安排精读、复述和改写。"""

PROGRESS_SUMMARY_SYSTEM_V1 = """# 学习规划师周进度总结 v1

根据代码汇总的本周学习、复习、试卷、知识点达成和薄弱记录，写一份不超过 500 字的中文进度总结。
先概括进展，再指出最需要处理的 1-2 个问题，最后给出下周可执行建议。不得编造输入中没有的数据。"""


class KpItem(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9\-]{1,120}$")
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(max_length=300)
    recall_prompt: str = Field(default="", max_length=200)
    importance: int = Field(ge=1, le=5)
    difficulty: int = Field(ge=1, le=5)
    primary_wiki_slug: str | None = None


class KpEdgeItem(BaseModel):
    src_slug: str = Field(pattern=r"^[a-z0-9\-]{1,120}$")
    dst_slug: str = Field(pattern=r"^[a-z0-9\-]{1,120}$")
    relation: str = Field(pattern=r"^(prerequisite|related|part_of|example_of)$")


class ExtractKpsOut(BaseModel):
    knowledge_points: list[KpItem] = Field(max_length=80)
    edges: list[KpEdgeItem] = Field(max_length=120)


class OnboardingSummary(BaseModel):
    goals: str = Field(min_length=1, max_length=2000)
    daily_minutes: int = Field(ge=5, le=600)
    target_date: date | None = None
    prior_knowledge: str = Field(default="", max_length=2000)
    preferences: str = Field(default="", max_length=2000)


class PlanItemOut(BaseModel):
    date_offset: int = Field(ge=0, le=365)
    type: str = Field(pattern=r"^(learn|quiz|stage_exam|buffer)$")
    title: str = Field(min_length=1, max_length=256)
    kp_slugs: list[str] = Field(max_length=8)
    est_minutes: int = Field(ge=5, le=240)


class GeneratePlanOut(BaseModel):
    rationale_md: str
    items: list[PlanItemOut] = Field(max_length=400)


class LanguagePlanItemOut(BaseModel):
    date_offset: int = Field(ge=0, le=365)
    type: str = Field(pattern=r"^(reading|writing|quiz|stage_exam|buffer)$")
    title: str = Field(min_length=1, max_length=256)
    kp_slugs: list[str] = Field(max_length=8)
    est_minutes: int = Field(ge=5, le=240)


class GenerateLanguagePlanOut(BaseModel):
    rationale_md: str
    items: list[LanguagePlanItemOut] = Field(max_length=400)


PlanItemContract = PlanItemOut | LanguagePlanItemOut


def _normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def resolve_primary_wiki_page(
    item: KpItem,
    *,
    pages_by_slug: dict[str, WikiPage],
    pages_by_title: dict[str, WikiPage],
) -> WikiPage | None:
    for slug in (item.primary_wiki_slug, item.slug):
        if slug and slug in pages_by_slug:
            return pages_by_slug[slug]
    return pages_by_title.get(_normalize_title(item.title))


def _material_excerpt(page: WikiPage, *, max_chars: int = 720) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", page.content_md)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _learning_description(
    item: PlanItemContract,
    *,
    kp_by_slug: dict[str, KnowledgePoint],
    pages_by_id: dict[uuid.UUID, WikiPage],
) -> str:
    if item.type == "learn" and item.kp_slugs:
        kp = kp_by_slug.get(item.kp_slugs[0])
        if kp is not None:
            page = pages_by_id.get(kp.primary_wiki_page_id) if kp.primary_wiki_page_id else None
            reference = f"[{page.title}](wiki:{page.slug})" if page else kp.title
            recall = kp.recall_prompt or "合上资料，用自己的话解释这个概念，并举一个例子。"
            material = (
                f"阅读 {reference} 的正文，关注定义、关键步骤和适用边界。\n\n"
                f"### 本节材料摘要\n{_material_excerpt(page)}"
                if page is not None
                else f"阅读 {reference} 的正文，关注定义、关键步骤和适用边界。"
            )
            return (
                f"## 学习目标\n{kp.summary or kp.title}\n\n"
                f"## 学习材料\n{material}\n\n"
                f"## 主动回忆\n{recall}"
            )
    descriptions = {
        "quiz": "完成本阶段小测，检查今天学习内容的理解和迁移。",
        "stage_exam": "完成阶段考试，综合检验本阶段知识点并记录薄弱处。",
        "buffer": "用于补齐前面未完成的学习任务，或复习本阶段最薄弱的内容。",
        "reading": "按课程材料完成指定阅读，记录核心观点、证据和一个待澄清问题。",
        "writing": "围绕今日主题完成写作，并根据反馈至少修订一轮。",
    }
    return descriptions.get(item.type, "按计划完成本项学习，并记录一个可验证的学习产出。")


def build_learning_index(
    pages: list[WikiPage],
    kps: list[KnowledgePoint],
    core_kp_slugs: set[str] | None = None,
) -> str:
    def page_summary(page: WikiPage) -> str:
        return next(
            (line.strip().lstrip("# ") for line in page.content_md.splitlines() if line.strip()),
            "",
        )[:180]

    pages_by_id = {page.id: page for page in pages}
    core: dict[str, WikiPage] = {}
    selected_kps = [kp for kp in kps if core_kp_slugs is None or kp.slug in core_kp_slugs]
    if core_kp_slugs is None:
        selected_kps = [kp for kp in selected_kps if kp.importance >= 4]
    for kp in selected_kps:
        page = pages_by_id.get(kp.primary_wiki_page_id) if kp.primary_wiki_page_id else None
        if page is not None and page.kind == "concept":
            core[page.slug] = page
    chapters = sorted(
        (page for page in pages if page.kind == "chapter_summary"),
        key=lambda page: page.title.casefold(),
    )
    lines = [
        "# 课程知识索引",
        "",
        "本索引优先列出学习计划中的核心知识页和章节总结。其余概念页仍保留在 Wiki 中，可从侧栏搜索访问。",
        "",
        "## 核心学习路径",
    ]
    lines.extend(
        f"- [{page.title}](wiki:{page.slug}) — {page_summary(page)}"
        for page in sorted(core.values(), key=lambda page: page.title.casefold())
    )
    if not core:
        lines.append("- （知识点抽取完成后生成）")
    lines.extend(["", "## 章节总结"])
    lines.extend(
        f"- [{page.title}](wiki:{page.slug}) — {page_summary(page)}"
        for page in chapters
    )
    if not chapters:
        lines.append("- （暂无章节总结）")
    return "\n".join(lines) + "\n"


def _topological_order(slugs: list[str], edges: list[tuple[str, str]]) -> list[str]:
    position = {slug: index for index, slug in enumerate(slugs)}
    indegree = {slug: 0 for slug in slugs}
    outgoing: dict[str, set[str]] = defaultdict(set)
    for src, dst in edges:
        if src not in indegree or dst not in indegree or src == dst or dst in outgoing[src]:
            continue
        outgoing[src].add(dst)
        indegree[dst] += 1

    ready = deque(sorted((s for s, n in indegree.items() if n == 0), key=position.get))
    ordered: list[str] = []
    while ready:
        slug = ready.popleft()
        ordered.append(slug)
        for dst in sorted(outgoing[slug], key=position.get):
            indegree[dst] -= 1
            if indegree[dst] == 0:
                ready.append(dst)
    if len(ordered) != len(slugs):
        logger.warning("知识点前置关系存在环，按原顺序打破环后继续排期")
        ordered.extend(slug for slug in slugs if slug not in ordered)
    return ordered


def _insert_required_event(
    items: list[PlanItemContract],
    *,
    event_type: str,
    title: str,
    window_start: int,
    window_end: int,
    daily_minutes: int,
    minutes: int = 5,
    skip_if_present: bool = True,
) -> None:
    if skip_if_present and any(
        item.type == event_type and window_start <= item.date_offset <= window_end
        for item in items
    ):
        return
    loads = defaultdict(int)
    for item in items:
        loads[item.date_offset] += item.est_minutes
    target = next(
        (
            day
            for day in range(window_end, window_start - 1, -1)
            if daily_minutes - loads[day] >= minutes
        ),
        None,
    )
    if target is None:
        target = window_end
        for item in items:
            if item.date_offset >= target:
                item.date_offset += 1
    contract = LanguagePlanItemOut if event_type in ("reading", "writing") else PlanItemOut
    items.append(
        contract(
            date_offset=target,
            type=event_type,
            title=title,
            kp_slugs=[],
            est_minutes=minutes,
        )
    )


def _ensure_event_count(
    items: list[PlanItemContract],
    *,
    event_type: str,
    titles: list[str],
    required: int,
    window_start: int,
    window_end: int,
    daily_minutes: int,
    minutes: int,
) -> None:
    present = sum(
        item.type == event_type and window_start <= item.date_offset <= window_end
        for item in items
    )
    for index in range(present, required):
        _insert_required_event(
            items,
            event_type=event_type,
            title=titles[index % len(titles)],
            window_start=window_start,
            window_end=window_end,
            daily_minutes=daily_minutes,
            minutes=minutes,
            skip_if_present=False,
        )


def normalize_plan_items(
    generated: GeneratePlanOut | GenerateLanguagePlanOut,
    *,
    known_slugs: set[str],
    prerequisite_edges: list[tuple[str, str]],
    daily_minutes: int,
    course_kind: str = "knowledge",
) -> list[PlanItemContract]:
    learned: set[str] = set()
    learn_meta: dict[str, tuple[str, int]] = {}
    nonlearn: list[PlanItemContract] = []
    learn_order: list[str] = []

    for item in generated.items:
        valid = [slug for slug in item.kp_slugs if slug in known_slugs]
        if item.type != "learn":
            nonlearn.append(item.model_copy(update={"kp_slugs": list(dict.fromkeys(valid))}))
            continue
        unique = [slug for slug in valid if slug not in learned]
        if not unique:
            continue
        per_kp = max(5, round(item.est_minutes / len(unique)))
        for slug in unique:
            learned.add(slug)
            learn_order.append(slug)
            learn_meta[slug] = (item.title, per_kp)

    ordered_slugs = _topological_order(learn_order, prerequisite_edges)
    base_items = [
        PlanItemOut(
            date_offset=0,
            type="learn",
            title=learn_meta[slug][0],
            kp_slugs=[slug],
            est_minutes=learn_meta[slug][1],
        )
        for slug in ordered_slugs
    ]
    base_items.extend(nonlearn)
    if not base_items:
        raise ValueError("规划模型未生成可用计划条目")

    reserve = 10 if daily_minutes >= 15 else 5 if daily_minutes >= 10 else 0
    base_capacity = max(5, daily_minutes - reserve)
    learn_capacity = max(5, int(daily_minutes * 0.7))
    normalized: list[PlanItemContract] = []
    day = 0
    used = 0
    learned_minutes = 0
    for item in base_items:
        item_capacity = learn_capacity if item.type == "learn" else base_capacity
        minutes = min(item.est_minutes, item_capacity)
        exceeds_learn = item.type == "learn" and learned_minutes + minutes > learn_capacity
        if used and (used + minutes > base_capacity or exceeds_learn):
            day += 1
            used = 0
            learned_minutes = 0
        normalized.append(item.model_copy(update={"date_offset": day, "est_minutes": minutes}))
        used += minutes
        if item.type == "learn":
            learned_minutes += minutes

    window_start = 0
    while window_start <= max(item.date_offset for item in normalized):
        window_end = (
            window_start + 6
            if course_kind == "language"
            else min(window_start + 6, max(item.date_offset for item in normalized))
        )
        if course_kind == "language":
            _ensure_event_count(
                normalized,
                event_type="reading",
                titles=["课程材料精读", "课程材料泛读"],
                required=2,
                window_start=window_start,
                window_end=window_end,
                daily_minutes=daily_minutes,
                minutes=min(30, max(10, daily_minutes // 2)),
            )
            _ensure_event_count(
                normalized,
                event_type="writing",
                titles=["英语写作与修订"],
                required=1,
                window_start=window_start,
                window_end=window_end,
                daily_minutes=daily_minutes,
                minutes=min(30, max(10, daily_minutes // 2)),
            )
        _insert_required_event(
            normalized,
            event_type="quiz",
            title="阶段小测",
            window_start=window_start,
            window_end=window_end,
            daily_minutes=daily_minutes,
        )
        window_start += 7

    block_start = 0
    while max(item.date_offset for item in normalized) + 1 >= 14 and block_start <= max(
        item.date_offset for item in normalized
    ):
        block_end = min(block_start + 13, max(item.date_offset for item in normalized))
        _insert_required_event(
            normalized,
            event_type="stage_exam",
            title="阶段考试",
            window_start=block_start,
            window_end=block_end,
            daily_minutes=daily_minutes,
        )
        block_start += 14

    return sorted(normalized, key=lambda item: item.date_offset)


async def _repair_course_views(
    db: AsyncSession,
    course_id: uuid.UUID,
    job_id: uuid.UUID,
) -> dict[str, int]:
    all_pages = list(
        (await db.execute(select(WikiPage).where(WikiPage.course_id == course_id))).scalars()
    )
    all_kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == course_id)
                .order_by(KnowledgePoint.slug)
            )
        ).scalars()
    )
    pages_by_slug = {page.slug: page for page in all_pages if page.kind == "concept"}
    pages_by_title = {
        _normalize_title(page.title): page
        for page in all_pages
        if page.kind == "concept" and _normalize_title(page.title)
    }
    repaired_links = 0
    for kp in all_kps:
        if kp.primary_wiki_page_id is not None:
            continue
        page = resolve_primary_wiki_page(
            KpItem(
                slug=kp.slug,
                title=kp.title,
                summary=kp.summary,
                recall_prompt=kp.recall_prompt,
                importance=kp.importance,
                difficulty=kp.difficulty,
            ),
            pages_by_slug=pages_by_slug,
            pages_by_title=pages_by_title,
        )
        if page is None:
            continue
        kp.primary_wiki_page_id = page.id
        link = await db.get(KpWikiLink, (kp.id, page.id))
        if link is None:
            db.add(KpWikiLink(kp_id=kp.id, wiki_page_id=page.id, role="primary"))
        else:
            link.role = "primary"
        repaired_links += 1

    active_plan = (
        await db.execute(
            select(Plan).where(Plan.course_id == course_id, Plan.status == "active").limit(1)
        )
    ).scalar_one_or_none()
    core_kp_slugs: set[str] | None = None
    if active_plan is not None:
        core_kp_slugs = set(
            (
                await db.execute(
                    select(KnowledgePoint.slug)
                    .join(PlanItemKp, PlanItemKp.kp_id == KnowledgePoint.id)
                    .join(PlanItem, PlanItem.id == PlanItemKp.plan_item_id)
                    .where(PlanItem.plan_id == active_plan.id)
                )
            ).scalars()
        )
    index_page = next((page for page in all_pages if page.kind == "index"), None)
    if index_page is not None:
        await wiki_svc.upsert_page(
            db,
            course_id,
            slug=wiki_svc.INDEX_SLUG,
            title="课程知识索引",
            kind="index",
            content_md=build_learning_index(all_pages, all_kps, core_kp_slugs),
            change_summary="按核心学习路径重建分层索引",
            updated_by="planner",
            job_id=job_id,
        )

    hydrated = 0
    if active_plan is not None:
        plan_items = list(
            (
                await db.execute(
                    select(PlanItem).where(PlanItem.plan_id == active_plan.id).order_by(PlanItem.seq)
                )
            ).scalars()
        )
        item_kps = (
            await db.execute(
                select(PlanItemKp.plan_item_id, KnowledgePoint.slug)
                .join(KnowledgePoint, KnowledgePoint.id == PlanItemKp.kp_id)
                .where(PlanItemKp.plan_item_id.in_([item.id for item in plan_items]))
            )
        ).all()
        slugs_by_item: dict[uuid.UUID, list[str]] = defaultdict(list)
        for item_id, slug in item_kps:
            slugs_by_item[item_id].append(slug)
        pages_by_id = {page.id: page for page in all_pages}
        kp_by_slug = {kp.slug: kp for kp in all_kps}
        for item in plan_items:
            item.description_md = _learning_description(
                PlanItemOut(
                    date_offset=0,
                    type=item.type,
                    title=item.title,
                    kp_slugs=slugs_by_item[item.id],
                    est_minutes=item.est_minutes,
                ),
                kp_by_slug=kp_by_slug,
                pages_by_id=pages_by_id,
            )
            hydrated += 1

    await db.commit()
    return {"repaired_links": repaired_links, "hydrated_plan_items": hydrated}


@job_handler("extract_kps", heavy=True)
async def handle_extract_kps(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}
    pages = list(
        (
            await db.execute(
                select(WikiPage)
                .where(WikiPage.course_id == course_id)
                .order_by(WikiPage.kind, WikiPage.title)
            )
        ).scalars()
    )
    concepts = [page for page in pages if page.kind == "concept"]
    existing = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == course_id)
                .order_by(KnowledgePoint.slug)
            )
        ).scalars()
    )
    pages_by_slug = {page.slug: page for page in concepts}
    pages_by_title = {
        _normalize_title(page.title): page for page in concepts if _normalize_title(page.title)
    }
    kp_by_slug = {kp.slug: kp for kp in existing}
    created = 0
    all_edges: list[KpEdgeItem] = []
    batch_count = 0
    for offset in range(0, len(concepts), EXTRACT_KPS_BATCH_SIZE):
        batch = concepts[offset : offset + EXTRACT_KPS_BATCH_SIZE]
        batch_count += 1
        batch_lines = "\n".join(
            f"- {page.slug}: {page.title} — {page.content_md[:900]}" for page in batch
        )
        existing_lines = "\n".join(f"- {kp.slug}: {kp.title}" for kp in kp_by_slug.values()) or "（无）"
        volatile = (
            f"# 本批 Wiki 概念页（{offset + 1}-{offset + len(batch)}/{len(concepts)}）\n"
            f"{batch_lines or '（无）'}\n\n"
            f"# 现有知识点（全局，必须复用同义 slug）\n{existing_lines}\n\n"
            "请只从本批页面提取重要、可学习、可考核的知识点。"
            "primary_wiki_slug 必须填写本批中最匹配的页面 slug；若没有合适页面不要编造 slug。"
            "关系只填写本批或现有知识点之间能够从资料支持的关系。输出 JSON。"
        )
        output: ExtractKpsOut = await runtime.run_structured(
            agent="planner",
            operation="extract_kps_batch",
            tier=Tier.medium,
            response_model=ExtractKpsOut,
            system_prompt=EXTRACT_KPS_SYSTEM_V1,
            stable_blocks=None,
            volatile=volatile,
            course_id=course_id,
            job_id=job.id,
            max_tokens=16384,
            temperature=0.3,
            thinking=False,
        )
        all_edges.extend(output.edges)
        for item in output.knowledge_points:
            kp = kp_by_slug.get(item.slug)
            if kp is None:
                kp = KnowledgePoint(
                    course_id=course_id,
                    slug=item.slug,
                    title=item.title,
                    summary=item.summary,
                    recall_prompt=item.recall_prompt,
                    importance=item.importance,
                    difficulty=item.difficulty,
                )
                db.add(kp)
                await db.flush()
                kp_by_slug[item.slug] = kp
                created += 1
            else:
                kp.summary = item.summary or kp.summary
                if not kp.recall_prompt and item.recall_prompt:
                    kp.recall_prompt = item.recall_prompt
                kp.importance = max(kp.importance, item.importance)
                kp.difficulty = max(kp.difficulty, item.difficulty)

            page = resolve_primary_wiki_page(
                item, pages_by_slug=pages_by_slug, pages_by_title=pages_by_title
            )
            if page is not None:
                kp.primary_wiki_page_id = page.id
                await db.execute(
                    delete(KpWikiLink).where(
                        KpWikiLink.kp_id == kp.id,
                        KpWikiLink.role == "primary",
                        KpWikiLink.wiki_page_id != page.id,
                    )
                )
                link = await db.get(KpWikiLink, (kp.id, page.id))
                if link is None:
                    db.add(KpWikiLink(kp_id=kp.id, wiki_page_id=page.id, role="primary"))
                else:
                    link.role = "primary"

    await db.flush()
    existing_edges = set(
        (
            await db.execute(
                select(KpEdge.src_kp_id, KpEdge.dst_kp_id, KpEdge.relation).where(
                    KpEdge.course_id == course_id
                )
            )
        ).all()
    )
    edge_created = 0
    for edge in all_edges:
        src = kp_by_slug.get(edge.src_slug)
        dst = kp_by_slug.get(edge.dst_slug)
        if src is None or dst is None or src.id == dst.id:
            continue
        key = (src.id, dst.id, edge.relation)
        if key in existing_edges:
            continue
        db.add(
            KpEdge(
                course_id=course_id,
                src_kp_id=src.id,
                dst_kp_id=dst.id,
                relation=edge.relation,
            )
        )
        existing_edges.add(key)
        edge_created += 1

    repair = await _repair_course_views(db, course_id, job.id)
    linked = int(
        (
            await db.execute(
                select(func.count()).select_from(KnowledgePoint).where(
                    KnowledgePoint.course_id == course_id,
                    KnowledgePoint.primary_wiki_page_id.is_not(None),
                )
            )
        ).scalar_one()
    )
    source_locators = {
        ref.get("locator")
        for page in pages
        if page.kind in ("concept", "chapter_summary")
        for ref in (page.source_refs or [])
        if isinstance(ref, dict) and ref.get("locator")
    }
    return {
        "knowledge_points": len(kp_by_slug),
        "created": created,
        "edges_created": edge_created,
        "concept_pages": len(concepts),
        "batches": batch_count,
        "linked_primary_wiki": linked,
        "hydrated_plan_items": repair["hydrated_plan_items"],
        "repaired_links": repair["repaired_links"],
        "source_locators": len(source_locators),
    }


@job_handler("rebuild_learning_index")
async def handle_rebuild_learning_index(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}
    return await _repair_course_views(db, course_id, job.id)


def _profile_markdown(profile: OnboardingSummary) -> str:
    return (
        f"# 学员画像\n\n- 学习目标：{profile.goals}\n"
        f"- 每日可用：{profile.daily_minutes} 分钟\n"
        f"- 目标日期：{profile.target_date.isoformat() if profile.target_date else '未指定'}\n"
        f"- 已有基础：{profile.prior_knowledge or '未说明'}\n"
        f"- 学习偏好：{profile.preferences or '未说明'}"
    )


async def _chat_history(db: AsyncSession, course: Course) -> str:
    chat = (
        await db.execute(
            select(Chat)
            .where(Chat.course_id == course.id, Chat.agent == "planner")
            .order_by(Chat.last_message_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if chat is None:
        return f"用户：课程当前目标是“{course.goal_text or course.name}”。"
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat.id)
                .order_by(ChatMessage.created_at, ChatMessage.id)
            )
        ).scalars()
    )
    return "\n".join(f"{message.role}：{message.content_md}" for message in messages if message.content_md)


@job_handler("revise_plan", heavy=True)
@job_handler("generate_plan", heavy=True)
async def handle_generate_plan(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}

    history = await _chat_history(db, course)
    profile: OnboardingSummary = await runtime.run_structured(
        agent="planner",
        operation="onboarding_summary",
        tier=Tier.medium,
        response_model=OnboardingSummary,
        system_prompt=ONBOARDING_SUMMARY_SYSTEM_V1,
        stable_blocks=[
            f"# 课程当前信息\n名称：{course.name}\n目标：{course.goal_text}\n"
            f"当前每日可用：{course.daily_minutes} 分钟\n"
            f"当前目标日期：{course.target_date.isoformat() if course.target_date else '未设置'}"
        ],
        volatile=f"# 规划师对话\n{history}\n\n请输出学员画像 JSON。",
        course_id=course_id,
        job_id=job.id,
        max_tokens=2048,
        temperature=0.2,
        thinking=False,
    )
    brief_version = (
        await db.execute(
            select(func.coalesce(func.max(CourseBrief.version), 0)).where(
                CourseBrief.course_id == course_id,
                CourseBrief.kind == "student_profile",
            )
        )
    ).scalar_one() + 1
    profile_md = _profile_markdown(profile)
    db.add(
        CourseBrief(
            course_id=course_id,
            kind="student_profile",
            content_md=profile_md,
            version=brief_version,
        )
    )
    course.daily_minutes = profile.daily_minutes
    course.target_date = profile.target_date
    course.goal_text = profile.goals
    course.status = "active"
    await db.commit()

    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == course_id, KnowledgePoint.status != "archived")
                .order_by(KnowledgePoint.importance.desc(), KnowledgePoint.slug)
            )
        ).scalars()
    )
    if not kps and course.kind != "language":
        raise ValueError("课程暂无知识点，请先完成资料入库与知识点抽取")
    edges = list((await db.execute(select(KpEdge).where(KpEdge.course_id == course_id))).scalars())
    kp_by_id = {kp.id: kp for kp in kps}
    edge_lines = [
        f"- {kp_by_id[edge.src_kp_id].slug} -> {kp_by_id[edge.dst_kp_id].slug} ({edge.relation})"
        for edge in edges
        if edge.src_kp_id in kp_by_id and edge.dst_kp_id in kp_by_id
    ]
    old_plan = (
        await db.execute(
            select(Plan).where(Plan.course_id == course_id, Plan.status == "active").limit(1)
        )
    ).scalar_one_or_none()
    old_items: list[PlanItem] = []
    if old_plan is not None:
        old_items = list(
            (
                await db.execute(
                    select(PlanItem).where(PlanItem.plan_id == old_plan.id).order_by(PlanItem.seq)
                )
            ).scalars()
        )
    language_profile = await db.get(LanguageProfile, course_id) if course.kind == "language" else None
    language_block = ""
    if course.kind == "language":
        if language_profile is None or language_profile.assessment_status != "active":
            raise ValueError("请先完成英语课程能力测评")
        source_rows = list(
            (
                await db.execute(
                    select(LanguageSource)
                    .where(LanguageSource.course_id == course_id, LanguageSource.status == "ready")
                    .order_by(LanguageSource.created_at.desc())
                    .limit(20)
                )
            ).scalars()
        )
        if not source_rows:
            await ensure_plan_language_source(db, course, language_profile, job)
            source_rows = list(
                (
                    await db.execute(
                        select(LanguageSource)
                        .where(
                            LanguageSource.course_id == course_id,
                            LanguageSource.status == "ready",
                        )
                        .order_by(LanguageSource.created_at.desc())
                        .limit(20)
                    )
                ).scalars()
            )
        if not source_rows:
            raise ValueError("计划自动准备英语阅读材料失败")
        recent = await recent_language_performance(db, course_id)
        recent_lines = [
            *(f"- 阅读 {item['score']}分（{item['at'][:10]}）" for item in recent["reading"]),
            *(f"- 写作 {item['score']}分（{item['at'][:10]}）" for item in recent["writing"]),
        ]
        language_block = (
            "# 英语课程稳定块\n"
            f"轨道：{course.language_track or 'custom'}；总体：{language_profile.overall_level}；"
            f"阅读：{language_profile.reading_level}/{language_profile.reading_score}；"
            f"写作：{language_profile.writing_level}/{language_profile.writing_score}；"
            f"课程词库预计已知：{language_profile.vocab_known_estimate}/{language_profile.vocab_total}；"
            f"每日新词：{language_profile.recommended_daily_words}；"
            f"近7日平均：{recent['average_score'] if recent['average_score'] is not None else '暂无'}；"
            f"强度系数：{recent['factor']}。\n"
            "来源：\n"
            + "\n".join(
                f"- {row.kind} {row.original_name}，{row.word_count}词，难度{row.estimated_level or '未知'}"
                for row in source_rows
            )
            + "\n近7日读写结果：\n"
            + ("\n".join(recent_lines) or "- 暂无已批改练习")
        )
    volatile = (
        "# 知识点\n"
        + "\n".join(
            f"- {kp.slug}: {kp.title}（重要度 {kp.importance}，难度 {kp.difficulty}，状态 {kp.status}）"
            for kp in kps
        )
        + "\n\n# 关系\n"
        + ("\n".join(edge_lines) or "（无）")
        + "\n\n# 每日预算\n"
        + f"总计 {profile.daily_minutes} 分钟；"
        + f"新知最多 {int(profile.daily_minutes * 0.7)} 分钟。"
        + "\n\n# 旧计划（如有）\n"
        + (
            "\n".join(
                f"- {item.scheduled_date}: {item.type} {item.title} {item.est_minutes}分钟"
                for item in old_items
            )
            or "（无）"
        )
        + "\n\n请输出新计划 JSON。"
    )
    # 课程设计需要跨知识点依赖和时间预算推理，因此按任务书显式启用高档思考。
    response_model = GenerateLanguagePlanOut if course.kind == "language" else GeneratePlanOut
    generated: GeneratePlanOut | GenerateLanguagePlanOut = await runtime.run_structured(
        agent="planner",
        operation="revise_plan" if old_plan else "generate_plan",
        tier=Tier.high,
        response_model=response_model,
        system_prompt=(
            GENERATE_PLAN_SYSTEM_V1
            if course.kind != "language"
            else f"{GENERATE_PLAN_SYSTEM_V1}\n\n{GENERATE_LANGUAGE_PLAN_SUFFIX_V1}"
        ),
        stable_blocks=[profile_md, language_block] if language_block else [profile_md],
        volatile=volatile,
        course_id=course_id,
        job_id=job.id,
        max_tokens=16384,
        temperature=0.3,
        thinking=True,
    )
    prerequisite_edges = [
        (kp_by_id[edge.src_kp_id].slug, kp_by_id[edge.dst_kp_id].slug)
        for edge in edges
        if edge.relation == "prerequisite"
        and edge.src_kp_id in kp_by_id
        and edge.dst_kp_id in kp_by_id
    ]
    normalized = normalize_plan_items(
        generated,
        known_slugs={kp.slug for kp in kps},
        prerequisite_edges=prerequisite_edges,
        daily_minutes=profile.daily_minutes,
        course_kind=course.kind,
    )
    version = (
        await db.execute(
            select(func.coalesce(func.max(Plan.version), 0)).where(Plan.course_id == course_id)
        )
    ).scalar_one() + 1
    start_date = datetime.now(SHANGHAI).date() + timedelta(days=1)
    await db.execute(
        update(Plan)
        .where(Plan.course_id == course_id, Plan.status == "active")
        .values(status="superseded")
    )
    plan = Plan(
        course_id=course_id,
        version=version,
        status="active",
        goal_summary=profile.goals,
        daily_minutes=profile.daily_minutes,
        start_date=start_date,
        end_date_estimate=start_date + timedelta(days=max(item.date_offset for item in normalized)),
        rationale_md=(
            f"{generated.rationale_md}\n\n"
            f"> 系统校验后实际排期为 {max(item.date_offset for item in normalized) + 1} 天；"
            + (
                "已强制保证每周至少 2 次阅读和 1 次写作，词汇由每日复习队列安排。"
                if course.kind == "language"
                else f"已强制满足前置顺序、每日总计不超过 {profile.daily_minutes} 分钟，"
                f"且每日新知不超过 {int(profile.daily_minutes * 0.7)} 分钟。"
            )
        ),
        created_by_job_id=job.id,
    )
    db.add(plan)
    await db.flush()
    kp_by_slug = {kp.slug: kp for kp in kps}
    pages_by_id = {
        page.id: page
        for page in (
            await db.execute(select(WikiPage).where(WikiPage.course_id == course_id))
        ).scalars()
    }
    for seq, item_out in enumerate(normalized, start=1):
        item = PlanItem(
            plan_id=plan.id,
            course_id=course_id,
            seq=seq,
            scheduled_date=start_date + timedelta(days=item_out.date_offset),
            type=item_out.type,
            title=item_out.title,
            description_md=_learning_description(
                item_out, kp_by_slug=kp_by_slug, pages_by_id=pages_by_id
            ),
            est_minutes=item_out.est_minutes,
        )
        db.add(item)
        await db.flush()
        for slug in item_out.kp_slugs:
            db.add(PlanItemKp(plan_item_id=item.id, kp_id=kp_by_slug[slug].id))
    if course.kind != "language":
        await enqueue(
            db,
            "project_mastery",
            {"course_id": str(course_id)},
            course_id=course_id,
            dedupe_key=f"project_mastery:{course_id}:{version}",
        )
    await db.commit()
    return {"plan_id": str(plan.id), "version": version, "items": len(normalized)}


@job_handler("project_mastery")
async def handle_project_mastery(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    await evaluate_course_mastery(db, course_id)
    plan = (
        await db.execute(
            select(Plan).where(Plan.course_id == course_id, Plan.status == "active").limit(1)
        )
    ).scalar_one_or_none()
    if plan is None:
        await db.commit()
        return {"skipped": "无生效计划"}
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint).where(KnowledgePoint.course_id == course_id)
            )
        ).scalars()
    )
    learn_dates = dict(
        (
            await db.execute(
                select(PlanItemKp.kp_id, func.min(PlanItem.scheduled_date))
                .join(PlanItem, PlanItem.id == PlanItemKp.plan_item_id)
                .where(PlanItem.plan_id == plan.id, PlanItem.type == "learn")
                .group_by(PlanItemKp.kp_id)
            )
        ).all()
    )
    review_cards = {
        card.kp_id: card
        for card in (
            await db.execute(
                select(ReviewCard).where(
                    ReviewCard.course_id == course_id,
                    ReviewCard.kp_id.is_not(None),
                )
            )
        ).scalars()
    }
    today = datetime.now(SHANGHAI).date()
    active_kps: list[KnowledgePoint] = []
    for kp in kps:
        if kp.status == "archived":
            kp.projected_basic_at = None
            kp.projected_full_at = None
            continue
        active_kps.append(kp)
        review_card = review_cards.get(kp.id)
        if review_card is not None:
            kp.projected_basic_at, kp.projected_full_at = simulate_from_card(
                card_from_dict(review_card.fsrs_card), today
            )
            continue
        learn_date = learn_dates.get(kp.id)
        if learn_date is None:
            kp.projected_basic_at = None
            kp.projected_full_at = None
            continue
        kp.projected_basic_at, kp.projected_full_at = simulate_mastery_dates(learn_date)

    plan.projected_basic_date = aggregate_course_date(
        [(kp.projected_basic_at, kp.importance) for kp in active_kps], 0.90
    )
    plan.projected_full_date = aggregate_course_date(
        [(kp.projected_full_at, kp.importance) for kp in active_kps], 0.95
    )
    await db.commit()
    return {
        "projected_basic_date": plan.projected_basic_date.isoformat()
        if plan.projected_basic_date
        else None,
        "projected_full_date": plan.projected_full_date.isoformat()
        if plan.projected_full_date
        else None,
    }


@job_handler("weekly_summary")
async def handle_weekly_summary(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}
    now_local = datetime.now(SHANGHAI)
    week_start = now_local.date() - timedelta(days=now_local.weekday())
    week_start_utc = datetime.combine(week_start, datetime.min.time(), SHANGHAI).astimezone(
        ZoneInfo("UTC")
    )
    item_rows = (
        await db.execute(
            select(PlanItem.status, PlanItem.actual_minutes)
            .where(
                PlanItem.course_id == course_id,
                PlanItem.scheduled_date >= week_start,
                PlanItem.scheduled_date <= now_local.date(),
                PlanItem.status.in_(("done", "skipped")),
            )
        )
    ).all()
    review_ratings = list(
        (
            await db.execute(
                select(ReviewLog.rating).where(
                    ReviewLog.course_id == course_id,
                    ReviewLog.created_at >= week_start_utc,
                )
            )
        ).scalars()
    )
    attempts = list(
        (
            await db.execute(
                select(QuizAttempt).where(
                    QuizAttempt.course_id == course_id,
                    QuizAttempt.status == "graded",
                    QuizAttempt.graded_at >= week_start_utc,
                )
            )
        ).scalars()
    )
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint).where(KnowledgePoint.course_id == course_id)
            )
        ).scalars()
    )
    weak_rows = (
        await db.execute(
            select(WeaknessRecord, KnowledgePoint.title)
            .join(KnowledgePoint, KnowledgePoint.id == WeaknessRecord.kp_id)
            .where(
                WeaknessRecord.course_id == course_id,
                WeaknessRecord.status == "open",
            )
            .order_by(WeaknessRecord.severity.desc(), WeaknessRecord.last_seen_at.desc())
            .limit(5)
        )
    ).all()
    scores = [
        attempt.total_score / attempt.max_score
        for attempt in attempts
        if attempt.total_score is not None and attempt.max_score
    ]
    progress = {
        "week_start": week_start.isoformat(),
        "plan_items": {
            "done": sum(status == "done" for status, _ in item_rows),
            "skipped": sum(status == "skipped" for status, _ in item_rows),
            "actual_minutes": sum(minutes or 0 for _, minutes in item_rows),
        },
        "reviews": {
            "count": len(review_ratings),
            "again_ratio": round(review_ratings.count(1) / len(review_ratings), 3)
            if review_ratings
            else 0,
        },
        "quizzes": {
            "count": len(scores),
            "average_ratio": round(sum(scores) / len(scores), 3) if scores else None,
        },
        "kp_changes": {
            "first_learned": sum(
                kp.first_learned_at is not None and kp.first_learned_at >= week_start_utc
                for kp in kps
            ),
            "basic_mastered": sum(
                kp.basic_mastered_at is not None and kp.basic_mastered_at >= week_start_utc
                for kp in kps
            ),
            "fully_mastered": sum(
                kp.fully_mastered_at is not None and kp.fully_mastered_at >= week_start_utc
                for kp in kps
            ),
            "currently_needs_reinforce": sum(kp.status == "needs_reinforce" for kp in kps),
        },
        "open_weakness_top5": [
            {
                "kp_title": title,
                "kind": weakness.kind,
                "severity": weakness.severity,
                "seen_count": weakness.seen_count,
            }
            for weakness, title in weak_rows
        ],
    }
    summary = await runtime.run_text(
        agent="planner",
        operation="progress_summary",
        tier=Tier.low,
        system_prompt=PROGRESS_SUMMARY_SYSTEM_V1,
        stable_blocks=[f"# 课程\n{course.name}\n目标：{course.goal_text}"],
        volatile="# 本周代码汇总\n" + json.dumps(progress, ensure_ascii=False),
        course_id=course_id,
        job_id=job.id,
        max_tokens=1024,
        temperature=0.3,
        thinking=False,
    )
    version = (
        await db.execute(
            select(func.coalesce(func.max(CourseBrief.version), 0)).where(
                CourseBrief.course_id == course_id,
                CourseBrief.kind == "progress_summary",
            )
        )
    ).scalar_one() + 1
    brief = CourseBrief(
        course_id=course_id,
        kind="progress_summary",
        content_md=summary.strip()[:500],
        version=version,
    )
    db.add(brief)
    await db.commit()
    return {"brief_id": str(brief.id), "version": version}
