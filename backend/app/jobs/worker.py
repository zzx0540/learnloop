import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.jobs.queue import claim_next, mark_failed_or_retry, mark_succeeded, requeue_stale_running
from app.models import Job

logger = logging.getLogger("learnloop.jobs")

Handler = Callable[[AsyncSession, Job], Awaitable[dict | None]]
FailureHandler = Callable[[AsyncSession, Job, str], Awaitable[None]]

HANDLERS: dict[str, Handler] = {}
FAILURE_HANDLERS: dict[str, FailureHandler] = {}

# 重活（解析/入库等长耗时任务）单独一条道，避免堵住轻任务
HEAVY_KINDS: set[str] = {
    "parse_document",
    "ingest_document",
    "analyze_language_source",
    "search_language_source",
}

IDLE_SLEEP_SECONDS = 1.5


def job_handler(kind: str, *, heavy: bool = False) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        if heavy:
            HEAVY_KINDS.add(kind)
        return fn

    return decorator


def job_failure_handler(kind: str) -> Callable[[FailureHandler], FailureHandler]:
    def decorator(fn: FailureHandler) -> FailureHandler:
        FAILURE_HANDLERS[kind] = fn
        return fn

    return decorator


async def _run_one(job: Job) -> None:
    from datetime import timedelta

    from sqlalchemy import update

    from app.agents.runtime import BudgetExceeded
    from app.models import utcnow

    handler = HANDLERS.get(job.kind)
    async with SessionLocal() as db:
        if handler is None:
            await mark_failed_or_retry(db, job, f"未注册的任务类型: {job.kind}")
            return
        try:
            result = await handler(db, job)
            await mark_succeeded(db, job.id, result)
            logger.info("任务完成 %s %s", job.kind, job.id)
        except BudgetExceeded as e:
            # 预算耗尽不算失败：延后 1 小时且不消耗重试次数
            await db.rollback()
            logger.warning("任务延后（预算耗尽）%s %s: %s", job.kind, job.id, e)
            await db.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(
                    status="queued", attempts=job.attempts - 1, error=str(e),
                    run_after=utcnow() + timedelta(hours=1), locked_at=None, locked_by=None,
                )
            )
            await db.commit()
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}"
            logger.error("任务失败 %s %s (第%d次): %s", job.kind, job.id, job.attempts, detail)
            if job.attempts >= job.max_attempts and job.kind in FAILURE_HANDLERS:
                try:
                    await FAILURE_HANDLERS[job.kind](db, job, detail)
                except Exception:  # noqa: BLE001
                    await db.rollback()
                    logger.exception("任务最终失败状态恢复异常 %s %s", job.kind, job.id)
            await mark_failed_or_retry(db, job, detail)


async def worker_loop(worker_id: str, *, heavy: bool) -> None:
    while True:
        try:
            kinds = list(HEAVY_KINDS) if heavy else [k for k in HANDLERS if k not in HEAVY_KINDS]
            if not kinds:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
                continue
            async with SessionLocal() as db:
                job = await claim_next(db, worker_id, kinds=kinds)
            if job is None:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
                continue
            await _run_one(job)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("worker %s 循环异常，5 秒后继续", worker_id)
            await asyncio.sleep(5)


def start_workers() -> list[asyncio.Task]:
    tasks = [
        asyncio.create_task(worker_loop("heavy-1", heavy=True), name="job-worker-heavy-1"),
        asyncio.create_task(worker_loop("light-1", heavy=False), name="job-worker-light-1"),
        asyncio.create_task(worker_loop("light-2", heavy=False), name="job-worker-light-2"),
    ]
    logger.info("任务 worker 已启动（1 重活道 + 2 轻活道）")
    return tasks


async def recover_on_startup() -> None:
    async with SessionLocal() as db:
        n = await requeue_stale_running(db)
        if n:
            logger.warning("已重新排队 %d 个上次遗留的 running 任务", n)


SWEEP_INTERVAL_SECONDS = 120
EXAMINER_RUN_TIME = time(2, 10)
MASTERY_RUN_TIME = time(2, 30)
LINT_RUN_TIME = time(2, 40)
SUMMARY_RUN_TIME = time(2, 50)


def should_run_examiner(now: datetime, last_examiner_date: date | None) -> bool:
    return now.time() >= EXAMINER_RUN_TIME and last_examiner_date != now.date()


def should_run_mastery(now: datetime, last_mastery_date: date | None) -> bool:
    return now.time() >= MASTERY_RUN_TIME and last_mastery_date != now.date()


def should_run_lint(now: datetime, last_lint_date: date | None) -> bool:
    return (
        now.weekday() == 6
        and now.time() >= LINT_RUN_TIME
        and last_lint_date != now.date()
    )


def should_run_summary(now: datetime, last_summary_date: date | None) -> bool:
    return (
        now.weekday() == 6
        and now.time() >= SUMMARY_RUN_TIME
        and last_summary_date != now.date()
    )


async def _enqueue_once(db: AsyncSession, kind: str, payload: dict, **kwargs) -> Job | None:
    from sqlalchemy import select

    from app.jobs.queue import enqueue

    dedupe_key = kwargs["dedupe_key"]
    existing = (
        await db.execute(select(Job).where(Job.dedupe_key == dedupe_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await enqueue(db, kind, payload, **kwargs)


async def sweeper_loop() -> None:
    """兜底扫描：有已解析未入库文档的课程，确保存在 ingest 任务（去重键防重复）。"""
    from sqlalchemy import select

    from app.jobs.queue import enqueue
    from app.models import Document

    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            async with SessionLocal() as db:
                course_ids = (
                    (
                        await db.execute(
                            select(Document.course_id)
                            .where(Document.status.in_(("parsed", "ingesting")))
                            .distinct()
                        )
                    )
                    .scalars()
                    .all()
                )
                for cid in course_ids:
                    await enqueue(
                        db, "ingest_document", {"course_id": str(cid)},
                        course_id=cid, dedupe_key=f"ingest:{cid}",
                    )
                if course_ids:
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("sweeper 循环异常")


async def scan_expired_formal_exams(db: AsyncSession) -> int:
    from sqlalchemy import select

    from app.models import ExamEvent, QuizAttempt, utcnow
    from app.services.grading import submit_attempt

    attempts = list(
        (
            await db.execute(
                select(QuizAttempt)
                .where(
                    QuizAttempt.kind == "formal_exam",
                    QuizAttempt.status == "in_progress",
                    QuizAttempt.deadline_at < utcnow(),
                )
                .order_by(QuizAttempt.deadline_at)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for attempt in attempts:
        await submit_attempt(db, attempt=attempt)
        attempt.auto_submitted = True
        db.add(ExamEvent(attempt_id=attempt.id, kind="forced_submit"))
    if attempts:
        await db.commit()
    return len(attempts)


async def cron_loop() -> None:
    from sqlalchemy import select

    from app.models import Course, KnowledgePoint, Plan, PlanItem

    last_examiner_date: date | None = None
    last_mastery_date: date | None = None
    last_lint_date: date | None = None
    last_summary_date: date | None = None
    shanghai = ZoneInfo("Asia/Shanghai")
    while True:
        try:
            now = datetime.now(shanghai)
            async with SessionLocal() as db:
                await scan_expired_formal_exams(db)
            if should_run_examiner(now, last_examiner_date):
                async with SessionLocal() as db:
                    courses = list(
                        (
                            await db.execute(select(Course).where(Course.status == "active"))
                        ).scalars()
                    )
                    for course in courses:
                        day = now.date().isoformat()
                        await _enqueue_once(
                            db,
                            "generate_quiz",
                            {"course_id": str(course.id), "quiz_date": day},
                            course_id=course.id,
                            user_id=course.user_id,
                            dedupe_key=f"daily_quiz:{course.id}:{day}",
                        )
                        missing_recall = (
                            await db.execute(
                                select(KnowledgePoint.id)
                                .where(
                                    KnowledgePoint.course_id == course.id,
                                    KnowledgePoint.recall_prompt == "",
                                    KnowledgePoint.status != "archived",
                                )
                                .limit(1)
                            )
                        ).scalar_one_or_none()
                        if missing_recall is not None:
                            await _enqueue_once(
                                db,
                                "backfill_recall_prompts",
                                {"course_id": str(course.id)},
                                course_id=course.id,
                                user_id=course.user_id,
                                dedupe_key=f"recall:{course.id}:{day}",
                            )
                    stage_rows = (
                        await db.execute(
                            select(PlanItem, Course.user_id)
                            .join(Plan, Plan.id == PlanItem.plan_id)
                            .join(Course, Course.id == PlanItem.course_id)
                            .where(
                                Course.status == "active",
                                Plan.status == "active",
                                PlanItem.type == "stage_exam",
                                PlanItem.status == "pending",
                                PlanItem.scheduled_date == now.date() + timedelta(days=1),
                            )
                        )
                    ).all()
                    for item, user_id in stage_rows:
                        await _enqueue_once(
                            db,
                            "generate_quiz",
                            {
                                "course_id": str(item.course_id),
                                "quiz_date": item.scheduled_date.isoformat(),
                                "plan_item_id": str(item.id),
                            },
                            course_id=item.course_id,
                            user_id=user_id,
                            dedupe_key=f"stage_quiz:{item.id}",
                        )
                    await db.commit()
                last_examiner_date = now.date()
            if should_run_mastery(now, last_mastery_date):
                async with SessionLocal() as db:
                    course_ids = list(
                        (
                            await db.execute(select(Course.id).where(Course.status == "active"))
                        ).scalars()
                    )
                    for course_id in course_ids:
                        dedupe_key = f"mastery:{course_id}:{now.date().isoformat()}"
                        exists = (
                            await db.execute(select(Job.id).where(Job.dedupe_key == dedupe_key).limit(1))
                        ).scalar_one_or_none()
                        if exists is None:
                            await _enqueue_once(
                                db,
                                "project_mastery",
                                {"course_id": str(course_id)},
                                course_id=course_id,
                                dedupe_key=dedupe_key,
                            )
                    await db.commit()
                last_mastery_date = now.date()
            if should_run_lint(now, last_lint_date):
                async with SessionLocal() as db:
                    courses = list(
                        (
                            await db.execute(select(Course).where(Course.status == "active"))
                        ).scalars()
                    )
                    for course in courses:
                        await _enqueue_once(
                            db,
                            "wiki_lint",
                            {"course_id": str(course.id)},
                            course_id=course.id,
                            user_id=course.user_id,
                            dedupe_key=f"lint:{course.id}:{now.date().isoformat()}",
                        )
                    await db.commit()
                last_lint_date = now.date()
            if should_run_summary(now, last_summary_date):
                async with SessionLocal() as db:
                    courses = list(
                        (
                            await db.execute(select(Course).where(Course.status == "active"))
                        ).scalars()
                    )
                    for course in courses:
                        await _enqueue_once(
                            db,
                            "weekly_summary",
                            {"course_id": str(course.id)},
                            course_id=course.id,
                            user_id=course.user_id,
                            dedupe_key=f"weekly_summary:{course.id}:{now.date().isoformat()}",
                        )
                    await db.commit()
                last_summary_date = now.date()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("cron 循环异常")
            await asyncio.sleep(60)
