import copy
import json
import math
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.worker import job_handler
from app.models import (
    Course,
    Job,
    KnowledgePoint,
    Plan,
    PlanItem,
    PlanItemKp,
    Question,
    Quiz,
    ReviewCard,
    WeaknessRecord,
    WikiPage,
)
from app.services.course_kind import append_kind_hint

SHANGHAI = ZoneInfo("Asia/Shanghai")

EXAMINER_SYSTEM_V2 = """# 出题教师 v2

你只负责按照代码给定的知识点和题型写题文，不得改变知识点、题型或题目数量。
每道题只能依据随题提供的课程 Wiki 材料，source_wiki_slugs 必须填写实际使用的页面 slug。
选择题选项应互斥且有辨析度；主观题的 rubric、reference_md 必须能据材料直接核验。
填空题题干必须用 ____ 表示空缺，禁止使用 {{}} 语法，禁止在题干中出现答案。只输出 JSON。"""

EXAMINER_QC_SYSTEM_V2 = """# 试题质检员 v2

逐题检查题干是否清晰、答案是否由给定 Wiki 材料支持、选项是否自洽、题型是否匹配。
填空题题干含答案或没有 ____ 空缺时必须判为不通过。
不改写题目，只给出 ok 与简短原因。材料无法支持、答案有歧义或题型不匹配时必须判为不通过。只输出 JSON。"""

RECALL_PROMPT_SYSTEM_V1 = """# 主动回忆提示生成器 v1

为每个知识点写一个不含答案、能促使学习者主动回忆核心内容的问题。每项不超过 200 字，只输出 JSON。"""

CLOZE_PATTERN = re.compile(r"\{\{c\d+::([^{}]+)\}\}")


class PoolEntry(BaseModel):
    kp_slug: str
    source: Literal["weakness", "yesterday", "weakest", "stage", "exam"]
    qtype: Literal[
        "single_choice", "multi_choice", "true_false", "cloze", "short_answer"
    ]
    weakness_kinds: list[str] = Field(default_factory=list)


class OptionOut(BaseModel):
    key: str
    text: str


class QuestionGenOut(BaseModel):
    kp_slug: str
    qtype: Literal[
        "single_choice", "multi_choice", "true_false", "cloze", "short_answer"
    ]
    stem_md: str = Field(min_length=10)
    options: list[OptionOut] = Field(default_factory=list, max_length=5)
    correct_options: list[str] = Field(default_factory=list)
    correct_bool: bool | None = None
    accept_texts: list[str] = Field(default_factory=list, max_length=8)
    rubric: str = ""
    reference_md: str = ""
    explanation_md: str = Field(min_length=1)
    source_wiki_slugs: list[str] = Field(min_length=1, max_length=3)


class GenerateQuizOut(BaseModel):
    questions: list[QuestionGenOut] = Field(min_length=3, max_length=25)


class QcItemOut(BaseModel):
    seq: int = Field(ge=1)
    ok: bool
    reason: str = Field(default="", max_length=200)


class QuestionQcOut(BaseModel):
    items: list[QcItemOut]


class RecallPromptItemOut(BaseModel):
    kp_slug: str
    recall_prompt: str = Field(min_length=1, max_length=200)


class RecallPromptsOut(BaseModel):
    items: list[RecallPromptItemOut]


def _daily_size(
    candidate_count: int, weakness_rows: list[dict], nonweak_count: int
) -> int:
    preferred = [8, 6, 5, 3]
    for size in preferred:
        if size > candidate_count:
            continue
        weakness_target = min(len(weakness_rows), math.ceil(size * 0.3))
        if nonweak_count < size - weakness_target:
            continue
        severe_count = sum(row["severity"] >= 2 for row in weakness_rows[:weakness_target])
        if severe_count <= 2:
            return size
    return min(candidate_count, 6)


def _assign_daily_qtypes(rows: list[dict]) -> list[PoolEntry]:
    entries: list[PoolEntry] = []
    short_count = 0
    for row in rows:
        qtype = "multi_choice"
        if row["source"] == "weakness" and row["severity"] >= 2:
            qtype = "short_answer"
            short_count += 1
        entries.append(
            PoolEntry(
                kp_slug=row["kp"].slug,
                source=row["source"],
                qtype=qtype,
                weakness_kinds=row["weakness_kinds"],
            )
        )

    if short_count == 0:
        promote = next(
            (index for index, row in enumerate(rows) if row["source"] == "weakness"),
            0,
        )
        entries[promote].qtype = "short_answer"

    rotation = ("single_choice", "true_false", "cloze")
    rotation_index = 0
    for index, row in enumerate(rows):
        if entries[index].qtype == "short_answer" or row["source"] == "weakness":
            continue
        entries[index].qtype = rotation[rotation_index % len(rotation)]
        rotation_index += 1
    return entries


async def select_daily_pool(
    db: AsyncSession, course_id: uuid.UUID, quiz_date: date
) -> list[PoolEntry] | None:
    weakness_rows = (
        await db.execute(
            select(WeaknessRecord, KnowledgePoint)
            .join(KnowledgePoint, KnowledgePoint.id == WeaknessRecord.kp_id)
            .where(
                WeaknessRecord.course_id == course_id,
                WeaknessRecord.status.in_(("open", "improving")),
                KnowledgePoint.status != "archived",
            )
            .order_by(WeaknessRecord.severity.desc(), WeaknessRecord.last_seen_at.desc())
        )
    ).all()
    grouped_weaknesses: dict[uuid.UUID, dict] = {}
    for weakness, kp in weakness_rows:
        row = grouped_weaknesses.setdefault(
            kp.id,
            {
                "kp": kp,
                "source": "weakness",
                "severity": weakness.severity,
                "last_seen_at": weakness.last_seen_at,
                "weakness_kinds": [],
            },
        )
        row["severity"] = max(row["severity"], weakness.severity)
        row["last_seen_at"] = max(row["last_seen_at"], weakness.last_seen_at)
        if weakness.kind not in row["weakness_kinds"]:
            row["weakness_kinds"].append(weakness.kind)
    weak = sorted(
        grouped_weaknesses.values(),
        key=lambda row: (row["severity"], row["last_seen_at"]),
        reverse=True,
    )

    yesterday = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .join(PlanItemKp, PlanItemKp.kp_id == KnowledgePoint.id)
                .join(PlanItem, PlanItem.id == PlanItemKp.plan_item_id)
                .join(Plan, Plan.id == PlanItem.plan_id)
                .where(
                    KnowledgePoint.course_id == course_id,
                    KnowledgePoint.status != "archived",
                    Plan.status == "active",
                    PlanItem.type == "learn",
                    PlanItem.status == "done",
                    PlanItem.scheduled_date == quiz_date - timedelta(days=1),
                )
                .order_by(KnowledgePoint.importance.desc(), KnowledgePoint.slug)
            )
        ).scalars()
    )
    weakest = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .join(ReviewCard, ReviewCard.kp_id == KnowledgePoint.id)
                .where(
                    KnowledgePoint.course_id == course_id,
                    KnowledgePoint.status.in_(("learning", "needs_reinforce")),
                )
                .order_by(ReviewCard.stability, KnowledgePoint.importance.desc())
            )
        ).scalars()
    )

    candidates: dict[uuid.UUID, dict] = {row["kp"].id: row for row in weak}
    for source, kps in (("yesterday", yesterday), ("weakest", weakest)):
        for kp in kps:
            candidates.setdefault(
                kp.id,
                {
                    "kp": kp,
                    "source": source,
                    "severity": 0,
                    "last_seen_at": None,
                    "weakness_kinds": [],
                },
            )
    if len(candidates) < 3:
        return None

    nonweak = [row for row in candidates.values() if row["source"] != "weakness"]
    total = _daily_size(len(candidates), weak, len(nonweak))
    minimum_weakness = math.ceil(total * 0.3)
    weakness_target = min(len(weak), minimum_weakness)
    selected = weak[:weakness_target]
    selected_ids = {row["kp"].id for row in selected}
    for source, kps in (("yesterday", yesterday), ("weakest", weakest)):
        for kp in kps:
            if len(selected) >= total:
                break
            row = candidates[kp.id]
            if kp.id in selected_ids or row["source"] == "weakness":
                continue
            selected.append(row)
            selected_ids.add(kp.id)
    return _assign_daily_qtypes(selected)


async def select_stage_pool(
    db: AsyncSession, course_id: uuid.UUID, plan_item_id: uuid.UUID
) -> list[PoolEntry] | None:
    item = await db.get(PlanItem, plan_item_id)
    if item is None or item.course_id != course_id or item.type != "stage_exam":
        return None
    previous = (
        await db.execute(
            select(PlanItem)
            .where(
                PlanItem.course_id == course_id,
                PlanItem.type == "stage_exam",
                PlanItem.scheduled_date < item.scheduled_date,
            )
            .order_by(PlanItem.scheduled_date.desc(), PlanItem.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    stmt = select(KnowledgePoint).where(
        KnowledgePoint.course_id == course_id,
        KnowledgePoint.status != "archived",
        KnowledgePoint.first_learned_at.is_not(None),
    )
    if previous is not None:
        since = datetime.combine(
            previous.scheduled_date + timedelta(days=1), time.min, SHANGHAI
        ).astimezone(timezone.utc)
        stmt = stmt.where(KnowledgePoint.first_learned_at >= since)
    kps = list(
        (
            await db.execute(
                stmt.order_by(KnowledgePoint.importance.desc(), KnowledgePoint.slug).limit(25)
            )
        ).scalars()
    )
    if len(kps) < 8:
        return None
    return _build_stage_pool(kps, source="stage")


def _build_stage_pool(
    kps: list[KnowledgePoint],
    *,
    source: Literal["stage", "exam"],
    minimum_questions: int = 0,
) -> list[PoolEntry]:
    total = min(25, max(len(kps), minimum_questions))
    short_count = 2 if total < 15 else 3 if total <= 20 else 4
    rotation = ("single_choice", "true_false", "cloze", "multi_choice")
    return [
        PoolEntry(
            kp_slug=kps[index % len(kps)].slug,
            source=source,
            qtype="short_answer" if index < short_count else rotation[(index - short_count) % 4],
        )
        for index in range(total)
    ]


async def select_formal_pool(
    db: AsyncSession, course_id: uuid.UUID
) -> list[PoolEntry] | None:
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(
                    KnowledgePoint.course_id == course_id,
                    KnowledgePoint.status != "archived",
                    KnowledgePoint.first_learned_at.is_not(None),
                )
                .order_by(KnowledgePoint.importance.desc(), KnowledgePoint.slug)
                .limit(25)
            )
        ).scalars()
    )
    if len(kps) < 10:
        return None
    return _build_stage_pool(kps, source="exam", minimum_questions=15)


def calculate_time_limit(objective_count: int, subjective_count: int) -> int:
    return min(7200, max(1800, objective_count * 90 + subjective_count * 480))


def _question_answer(item: QuestionGenOut) -> dict:
    if item.qtype in ("single_choice", "multi_choice"):
        return {"correct_options": item.correct_options}
    if item.qtype == "true_false":
        return {"correct_bool": item.correct_bool}
    if item.qtype == "cloze":
        return {"accept_texts": item.accept_texts}
    return {"rubric": item.rubric, "reference_md": item.reference_md}


def _validate_generated(
    item: QuestionGenOut,
    entry: PoolEntry,
    valid_wiki_slugs: set[str],
) -> tuple[dict | None, str | None]:
    if item.kp_slug != entry.kp_slug:
        return None, "知识点不匹配"
    if item.qtype != entry.qtype:
        return None, "题型不匹配"
    option_keys = [option.key.strip() for option in item.options]
    if item.qtype in ("single_choice", "multi_choice"):
        if not 3 <= len(item.options) <= 5 or any(not key for key in option_keys):
            return None, "选项数量或 key 不合法"
        if len(set(option_keys)) != len(option_keys):
            return None, "选项 key 重复"
        correct = list(dict.fromkeys(item.correct_options))
        expected = 1 if item.qtype == "single_choice" else 2
        if len(correct) < expected or any(key not in option_keys for key in correct):
            return None, "正确选项不合法"
        if item.qtype == "single_choice" and len(correct) != 1:
            return None, "单选题必须恰有一个正确选项"
        item.correct_options = correct
    elif item.qtype == "true_false" and item.correct_bool is None:
        return None, "判断题缺少正确答案"
    elif item.qtype == "cloze":
        matches = list(CLOZE_PATTERN.finditer(item.stem_md))
        if matches:
            first_answer = matches[0].group(1).strip()
            if not first_answer:
                return None, "填空题语法不合法"
            first = True

            def replace_cloze(match: re.Match[str]) -> str:
                nonlocal first
                text = match.group(1).strip()
                if not text:
                    raise ValueError
                if first:
                    first = False
                    return "____"
                return text

            try:
                item.stem_md = CLOZE_PATTERN.sub(replace_cloze, item.stem_md)
            except ValueError:
                return None, "填空题语法不合法"
            item.accept_texts.insert(0, first_answer)
        if "{{" in item.stem_md:
            return None, "填空题语法不合法"
        if "____" not in item.stem_md:
            return None, "填空题缺少空缺标记"
        item.accept_texts = list(dict.fromkeys(text.strip() for text in item.accept_texts if text.strip()))
        if not item.accept_texts:
            return None, "填空题缺少可接受答案"
    elif item.qtype == "short_answer" and (
        not item.rubric.strip() or not item.reference_md.strip()
    ):
        return None, "主观题缺少评分标准或参考答案"

    source_slugs = list(
        dict.fromkeys(slug for slug in item.source_wiki_slugs if slug in valid_wiki_slugs)
    )
    if not source_slugs:
        return None, "无课程内有效溯源"
    return {
        "item": item,
        "options": [option.model_dump() for option in item.options]
        if item.qtype in ("single_choice", "multi_choice")
        else [],
        "answer": _question_answer(item),
        "source_refs": [{"wiki_slug": slug} for slug in source_slugs],
    }, None


def _generation_input(
    quiz: Quiz,
    pool: list[PoolEntry],
    kps: dict[str, KnowledgePoint],
    pages: dict[uuid.UUID, WikiPage],
) -> str:
    rows = []
    for entry in pool:
        kp = kps[entry.kp_slug]
        page = pages.get(kp.primary_wiki_page_id)
        rows.append(
            {
                **entry.model_dump(),
                "title": kp.title,
                "summary": kp.summary,
                "recall_prompt": kp.recall_prompt,
                "primary_wiki": {
                    "slug": page.slug,
                    "title": page.title,
                    "content_md": page.content_md[:3000],
                }
                if page
                else None,
            }
        )
    return (
        f"# 试卷信息\n类型：{quiz.kind}\n日期：{quiz.scheduled_date.isoformat()}\n\n"
        "# 代码指定的逐题规格与材料\n"
        + json.dumps(rows, ensure_ascii=False)
        + "\n\n严格按数组顺序为每项生成一道题。"
    )


async def _get_or_create_quiz(
    db: AsyncSession, job: Job, course_id: uuid.UUID, scheduled_date: date
) -> Quiz:
    quiz = (
        await db.execute(select(Quiz).where(Quiz.created_by_job_id == job.id).limit(1))
    ).scalar_one_or_none()
    plan_item_id = uuid.UUID(job.payload["plan_item_id"]) if job.payload.get("plan_item_id") else None
    kind = job.payload.get("exam_kind") or ("stage" if plan_item_id else "daily")
    title_kind = {"stage": "阶段考", "formal_exam": "正式考试"}.get(kind, "每日测验")
    if quiz is None:
        quiz = Quiz(
            course_id=course_id,
            kind=kind,
            title=f"{scheduled_date.isoformat()} {title_kind}",
            scheduled_date=scheduled_date,
            plan_item_id=plan_item_id,
            created_by_job_id=job.id,
        )
        db.add(quiz)
    else:
        await db.execute(delete(Question).where(Question.quiz_id == quiz.id))
        quiz.status = "generating"
        quiz.time_limit_seconds = None
    await db.commit()
    return quiz


@job_handler("generate_quiz", heavy=True)
async def handle_generate_quiz(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}
    scheduled_date = date.fromisoformat(job.payload["quiz_date"])
    quiz = await _get_or_create_quiz(db, job, course_id, scheduled_date)
    quiz_id = quiz.id
    if quiz.kind == "formal_exam":
        pool = await select_formal_pool(db, course_id)
    elif quiz.plan_item_id:
        pool = await select_stage_pool(db, course_id, quiz.plan_item_id)
    else:
        pool = await select_daily_pool(db, course_id, scheduled_date)
    if pool is None:
        await db.delete(quiz)
        await db.commit()
        return {
            "skipped": "已学知识点不足，无法组卷"
            if quiz.kind == "formal_exam"
            else "可考知识点不足"
        }

    weakness_count = sum(entry.source == "weakness" for entry in pool)
    gen_spec = {
        "pool": [entry.model_dump() for entry in pool],
        "weakness_ratio_target": [0.3, 0.4],
        "weakness_count": weakness_count,
        "dropped": [],
        "qc_dropped": [],
        "requested_question_count": len(pool),
    }
    quiz.gen_spec = copy.deepcopy(gen_spec)
    await db.commit()
    try:
        kps = list(
            (
                await db.execute(
                    select(KnowledgePoint).where(
                        KnowledgePoint.course_id == course_id,
                        KnowledgePoint.slug.in_([entry.kp_slug for entry in pool]),
                    )
                )
            ).scalars()
        )
        kp_by_slug = {kp.slug: kp for kp in kps}
        wiki_pages = list(
            (await db.execute(select(WikiPage).where(WikiPage.course_id == course_id))).scalars()
        )
        pages_by_id = {page.id: page for page in wiki_pages}
        valid_wiki_slugs = {page.slug for page in wiki_pages}
        output: GenerateQuizOut = await runtime.run_structured(
            agent="examiner",
            operation="generate_quiz",
            tier=Tier.high if quiz.kind in ("stage", "formal_exam") else Tier.medium,
            response_model=GenerateQuizOut,
            system_prompt=append_kind_hint(EXAMINER_SYSTEM_V2, course.kind),
            stable_blocks=[
                f"# 课程目标\n{course.goal_text or course.name}",
                "# 知识点索引\n"
                + "\n".join(f"{kp.slug}: {kp.title}" for kp in sorted(kps, key=lambda x: x.slug)),
            ],
            volatile=_generation_input(quiz, pool, kp_by_slug, pages_by_id),
            course_id=course_id,
            job_id=job.id,
            max_tokens=16384,
            temperature=0.3,
            thinking=False,
        )
        entry_by_spec = {(entry.kp_slug, entry.qtype): entry for entry in pool}
        validated: list[dict] = []
        seen_specs: set[tuple[str, str]] = set()
        returned_specs: set[tuple[str, str]] = set()
        for item in output.questions:
            spec = (item.kp_slug, item.qtype)
            entry = entry_by_spec.get(spec)
            if entry is None:
                known_kp = any(candidate.kp_slug == item.kp_slug for candidate in pool)
                gen_spec["dropped"].append(
                    {
                        "kp_slug": item.kp_slug,
                        "reason": "题型不匹配" if known_kp else "知识点不在代码选池",
                    }
                )
                continue
            returned_specs.add(spec)
            if spec in seen_specs:
                gen_spec["dropped"].append(
                    {"kp_slug": item.kp_slug, "reason": "同一知识点题型重复出题"}
                )
                continue
            checked, reason = _validate_generated(item, entry, valid_wiki_slugs)
            if checked is None:
                gen_spec["dropped"].append({"kp_slug": item.kp_slug, "reason": reason})
                continue
            seen_specs.add(spec)
            checked["kp"] = kp_by_slug[item.kp_slug]
            validated.append(checked)
        for entry in pool:
            if (entry.kp_slug, entry.qtype) not in returned_specs:
                gen_spec["dropped"].append(
                    {
                        "kp_slug": entry.kp_slug,
                        "qtype": entry.qtype,
                        "reason": "模型未返回题目",
                    }
                )

        qc_input = [
            {
                "seq": index,
                "kp_slug": row["item"].kp_slug,
                "qtype": row["item"].qtype,
                "stem_md": row["item"].stem_md,
                "options": row["options"],
                "answer": row["answer"],
                "explanation_md": row["item"].explanation_md,
                "source_refs": row["source_refs"],
                "wiki_materials": [
                    {
                        "slug": slug,
                        "content_md": next(
                            page.content_md[:3000] for page in wiki_pages if page.slug == slug
                        ),
                    }
                    for slug in (ref["wiki_slug"] for ref in row["source_refs"])
                ],
            }
            for index, row in enumerate(validated, start=1)
        ]
        qc: QuestionQcOut = await runtime.run_structured(
            agent="examiner",
            operation="quiz_qc",
            tier=Tier.low,
            response_model=QuestionQcOut,
            system_prompt=EXAMINER_QC_SYSTEM_V2,
            stable_blocks=None,
            volatile=json.dumps(qc_input, ensure_ascii=False),
            course_id=course_id,
            job_id=job.id,
            max_tokens=2048,
            temperature=0.1,
            thinking=False,
        )
        qc_by_seq = {item.seq: item for item in qc.items}
        surviving: list[dict] = []
        for index, row in enumerate(validated, start=1):
            judgement = qc_by_seq.get(index)
            if judgement is None or not judgement.ok:
                gen_spec["qc_dropped"].append(
                    {
                        "kp_slug": row["item"].kp_slug,
                        "reason": judgement.reason if judgement else "质检未返回结果",
                    }
                )
                continue
            surviving.append(row)

        required = max(15 if quiz.kind == "formal_exam" else 3, math.ceil(len(pool) * 0.6))
        if len(surviving) < required:
            raise ValueError(f"质检后仅存活 {len(surviving)} 题，低于最低要求 {required} 题")
        for seq, row in enumerate(surviving, start=1):
            item = row["item"]
            db.add(
                Question(
                    quiz_id=quiz.id,
                    course_id=course_id,
                    kp_id=row["kp"].id,
                    seq=seq,
                    qtype=item.qtype,
                    stem_md=item.stem_md,
                    options=row["options"],
                    answer=row["answer"],
                    explanation_md=item.explanation_md,
                    source_refs=row["source_refs"],
                )
            )
        if quiz.kind == "formal_exam":
            subjective_count = sum(row["item"].qtype == "short_answer" for row in surviving)
            objective_count = len(surviving) - subjective_count
            quiz.time_limit_seconds = calculate_time_limit(objective_count, subjective_count)
            gen_spec["time_limit_seconds"] = quiz.time_limit_seconds
            gen_spec["objective_count"] = objective_count
            gen_spec["subjective_count"] = subjective_count
        quiz.gen_spec = copy.deepcopy(gen_spec)
        quiz.status = "ready"
        await db.commit()
        return {
            "quiz_id": str(quiz.id),
            "questions": len(surviving),
            "time_limit_seconds": quiz.time_limit_seconds,
        }
    except Exception:
        await db.rollback()
        quiz = await db.get(Quiz, quiz_id)
        if quiz is not None:
            quiz.status = "failed"
            quiz.gen_spec = copy.deepcopy(gen_spec)
            await db.commit()
        raise


@job_handler("backfill_recall_prompts")
async def handle_backfill_recall_prompts(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(
                    KnowledgePoint.course_id == course_id,
                    KnowledgePoint.recall_prompt == "",
                    KnowledgePoint.status != "archived",
                )
                .order_by(KnowledgePoint.slug)
            )
        ).scalars()
    )
    if not kps:
        return {"skipped": "没有待补全知识点"}
    output: RecallPromptsOut = await runtime.run_structured(
        agent="examiner",
        operation="recall_prompts",
        tier=Tier.low,
        response_model=RecallPromptsOut,
        system_prompt=RECALL_PROMPT_SYSTEM_V1,
        stable_blocks=None,
        volatile=json.dumps(
            [{"kp_slug": kp.slug, "title": kp.title, "summary": kp.summary} for kp in kps],
            ensure_ascii=False,
        ),
        course_id=course_id,
        job_id=job.id,
        max_tokens=4096,
        temperature=0.3,
        thinking=False,
    )
    kp_by_slug = {kp.slug: kp for kp in kps}
    updated = 0
    for item in output.items:
        kp = kp_by_slug.get(item.kp_slug)
        if kp is not None and not kp.recall_prompt:
            kp.recall_prompt = item.recall_prompt
            updated += 1
    await db.commit()
    return {"updated": updated}
