import uuid
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import (
    Course,
    CourseBrief,
    Job,
    KnowledgePoint,
    Plan,
    PlanItem,
    PlanItemKp,
    Quiz,
    QuizAttempt,
    User,
    WeaknessRecord,
    WikiPage,
    utcnow,
)
from app.models.language import LanguageProfile
from app.schemas.course import JobOut
from app.schemas.planning import (
    ActivePlanSummary,
    CourseOverviewOut,
    KpStatsOut,
    PlanDayOut,
    PlanDetailOut,
    PlanItemKpOut,
    PlanItemOut,
    PlanItemStudyKpOut,
    PlanItemStudyOut,
    PlanItemUpdate,
    TodayQuizOut,
)
from app.services.reviews import complete_learn_item

router = APIRouter()


async def _active_plan(db: AsyncSession, course_id: uuid.UUID) -> Plan | None:
    return (
        await db.execute(
            select(Plan)
            .where(Plan.course_id == course_id, Plan.status == "active")
            .order_by(Plan.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _plan_detail(db: AsyncSession, plan: Plan) -> PlanDetailOut:
    items = list(
        (
            await db.execute(
                select(PlanItem)
                .where(PlanItem.plan_id == plan.id)
                .order_by(PlanItem.scheduled_date, PlanItem.seq)
            )
        ).scalars()
    )
    kp_rows = (
        await db.execute(
            select(
                PlanItemKp.plan_item_id,
                KnowledgePoint.id,
                KnowledgePoint.slug,
                KnowledgePoint.title,
            )
            .join(KnowledgePoint, KnowledgePoint.id == PlanItemKp.kp_id)
            .join(PlanItem, PlanItem.id == PlanItemKp.plan_item_id)
            .where(PlanItem.plan_id == plan.id)
        )
    ).all()
    kps_by_item: dict[uuid.UUID, list[PlanItemKpOut]] = defaultdict(list)
    for item_id, kp_id, slug, title in kp_rows:
        kps_by_item[item_id].append(PlanItemKpOut(id=kp_id, slug=slug, title=title))
    days: dict = defaultdict(list)
    for item in items:
        days[item.scheduled_date].append(
            PlanItemOut(
                id=item.id,
                seq=item.seq,
                scheduled_date=item.scheduled_date,
                type=item.type,
                title=item.title,
                description_md=item.description_md,
                est_minutes=item.est_minutes,
                status=item.status,
                completed_at=item.completed_at,
                actual_minutes=item.actual_minutes,
                knowledge_points=kps_by_item[item.id],
            )
        )
    return PlanDetailOut(
        id=plan.id,
        version=plan.version,
        status=plan.status,
        goal_summary=plan.goal_summary,
        daily_minutes=plan.daily_minutes,
        start_date=plan.start_date,
        end_date_estimate=plan.end_date_estimate,
        projected_basic_date=plan.projected_basic_date,
        projected_full_date=plan.projected_full_date,
        rationale_md=plan.rationale_md,
        days=[PlanDayOut(date=day, items=day_items) for day, day_items in sorted(days.items())],
    )


@router.post("/courses/{course_id}/plan/generate", response_model=JobOut)
async def generate_plan(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> Job:
    if course.kind == "language":
        profile = await db.get(LanguageProfile, course.id)
        if profile is None or profile.assessment_status != "active":
            raise HTTPException(status_code=400, detail="请先完成英语课程能力测评")
    kind = "revise_plan" if await _active_plan(db, course.id) else "generate_plan"
    dedupe_key = f"{kind}:{course.id}"
    job = await enqueue(
        db,
        kind,
        {"course_id": str(course.id)},
        course_id=course.id,
        user_id=course.user_id,
        dedupe_key=dedupe_key,
    )
    if job is None:
        job = (
            await db.execute(
                select(Job).where(
                    Job.dedupe_key == dedupe_key, Job.status.in_(("queued", "running"))
                )
            )
        ).scalar_one()
    await db.commit()
    return job


@router.get("/courses/{course_id}/plan", response_model=PlanDetailOut | None)
async def get_plan(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> PlanDetailOut | None:
    plan = await _active_plan(db, course.id)
    return await _plan_detail(db, plan) if plan else None


@router.patch("/plan-items/{item_id}", response_model=PlanItemOut)
async def update_plan_item(
    item_id: uuid.UUID,
    data: PlanItemUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PlanItemOut:
    item = await db.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="计划条目不存在")
    course = await db.get(Course, item.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="计划条目不存在")
    allowed = {
        "pending": {"in_progress", "done", "skipped"},
        "in_progress": {"done", "skipped"},
        "done": set(),
        "skipped": set(),
    }
    if data.status == item.status or data.status not in allowed.get(item.status, set()):
        raise HTTPException(status_code=400, detail="不允许的计划条目状态流转")
    item.status = data.status
    if data.status == "done":
        item.completed_at = utcnow()
        item.actual_minutes = data.actual_minutes
        if item.type == "learn":
            await complete_learn_item(
                db,
                item_id=item.id,
                course_id=course.id,
                user_id=user.id,
                completed_at=item.completed_at,
            )
    await db.commit()
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .join(PlanItemKp, PlanItemKp.kp_id == KnowledgePoint.id)
                .where(PlanItemKp.plan_item_id == item.id)
            )
        ).scalars()
    )
    return PlanItemOut(
        id=item.id,
        seq=item.seq,
        scheduled_date=item.scheduled_date,
        type=item.type,
        title=item.title,
        description_md=item.description_md,
        est_minutes=item.est_minutes,
        status=item.status,
        completed_at=item.completed_at,
        actual_minutes=item.actual_minutes,
        knowledge_points=[PlanItemKpOut(id=kp.id, slug=kp.slug, title=kp.title) for kp in kps],
    )


@router.get("/plan-items/{item_id}", response_model=PlanItemStudyOut)
async def get_plan_item(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PlanItemStudyOut:
    item = await db.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="计划条目不存在")
    course = await db.get(Course, item.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="计划条目不存在")
    rows = (
        await db.execute(
            select(KnowledgePoint, WikiPage.slug)
            .join(PlanItemKp, PlanItemKp.kp_id == KnowledgePoint.id)
            .outerjoin(WikiPage, WikiPage.id == KnowledgePoint.primary_wiki_page_id)
            .where(PlanItemKp.plan_item_id == item.id)
            .order_by(KnowledgePoint.title)
        )
    ).all()
    return PlanItemStudyOut(
        id=item.id,
        plan_id=item.plan_id,
        course_id=item.course_id,
        seq=item.seq,
        scheduled_date=item.scheduled_date,
        type=item.type,
        title=item.title,
        description_md=item.description_md,
        est_minutes=item.est_minutes,
        status=item.status,
        completed_at=item.completed_at,
        actual_minutes=item.actual_minutes,
        kps=[
            PlanItemStudyKpOut(
                id=kp.id,
                slug=kp.slug,
                title=kp.title,
                summary=kp.summary,
                recall_prompt=kp.recall_prompt,
                primary_wiki_slug=wiki_slug,
                status=kp.status,
            )
            for kp, wiki_slug in rows
        ],
    )


@router.get("/courses/{course_id}/overview", response_model=CourseOverviewOut)
async def get_overview(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> CourseOverviewOut:
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint).where(KnowledgePoint.course_id == course.id)
            )
        ).scalars()
    )
    counts = Counter(kp.status for kp in kps)
    plan = await _active_plan(db, course.id)
    latest_summary = (
        await db.execute(
            select(CourseBrief.content_md)
            .where(
                CourseBrief.course_id == course.id,
                CourseBrief.kind == "progress_summary",
            )
            .order_by(CourseBrief.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    open_weakness_count = (
        await db.execute(
            select(func.count()).select_from(WeaknessRecord).where(
                WeaknessRecord.course_id == course.id,
                WeaknessRecord.status == "open",
            )
        )
    ).scalar_one()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    today_quiz = (
        await db.execute(
            select(Quiz)
            .where(
                Quiz.course_id == course.id,
                Quiz.kind == "daily",
                Quiz.scheduled_date == today,
            )
            .order_by(Quiz.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    today_attempt = (
        await db.execute(
            select(QuizAttempt).where(
                QuizAttempt.quiz_id == today_quiz.id,
                QuizAttempt.user_id == course.user_id,
            )
        )
    ).scalar_one_or_none() if today_quiz else None
    return CourseOverviewOut(
        course=course,
        active_plan=ActivePlanSummary(
            id=plan.id,
            version=plan.version,
            start_date=plan.start_date,
            end_date_estimate=plan.end_date_estimate,
            daily_minutes=plan.daily_minutes,
            rationale_md=plan.rationale_md,
        )
        if plan
        else None,
        kp_stats=KpStatsOut(total=len(kps), by_status=dict(counts)),
        projected_basic_date=plan.projected_basic_date if plan else None,
        projected_full_date=plan.projected_full_date if plan else None,
        assumption_text=(
            "按 FSRS 默认参数、复习均评为“良好”、目标保持率 90% 推算；"
            "实际日期会随学习与复习表现更新。"
        ),
        latest_progress_summary_md=latest_summary,
        open_weakness_count=open_weakness_count,
        today_quiz=TodayQuizOut(
            quiz_id=today_quiz.id,
            status=today_quiz.status,
            my_attempt_status=today_attempt.status if today_attempt else None,
            total_score=today_attempt.total_score if today_attempt else None,
            max_score=today_attempt.max_score if today_attempt else None,
        )
        if today_quiz
        else None,
    )
