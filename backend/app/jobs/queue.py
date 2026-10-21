import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, utcnow

RETRY_BASE_SECONDS = 30


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict | None = None,
    *,
    course_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    priority: int = 0,
    run_after: datetime | None = None,
    max_attempts: int = 3,
) -> Job | None:
    """入队任务；dedupe_key 冲突（已有同 key 的排队/运行中任务）时返回 None。"""
    if dedupe_key is not None:
        existing = (
            await db.execute(
                select(Job.id).where(
                    Job.dedupe_key == dedupe_key, Job.status.in_(("queued", "running"))
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None

    job = Job(
        kind=kind,
        payload=payload or {},
        course_id=course_id,
        user_id=user_id,
        dedupe_key=dedupe_key,
        priority=priority,
        run_after=run_after or utcnow(),
        max_attempts=max_attempts,
    )
    db.add(job)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    return job


async def claim_next(db: AsyncSession, worker_id: str, kinds: list[str] | None = None) -> Job | None:
    """FOR UPDATE SKIP LOCKED 领取一个到期任务。调用方负责 commit。"""
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= utcnow())
        .order_by(Job.priority.desc(), Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if kinds is not None:
        stmt = stmt.where(Job.kind.in_(kinds))
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.locked_at = utcnow()
    job.locked_by = worker_id
    job.started_at = job.started_at or utcnow()
    job.attempts += 1
    await db.commit()
    return job


async def mark_succeeded(db: AsyncSession, job_id: uuid.UUID, result: dict | None = None) -> None:
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="succeeded",
            result=result,
            error=None,
            finished_at=utcnow(),
            locked_at=None,
            locked_by=None,
        )
    )
    await db.commit()


async def mark_failed_or_retry(db: AsyncSession, job: Job, error: str) -> None:
    if job.attempts >= job.max_attempts:
        values = dict(status="failed", error=error[:4000], finished_at=utcnow())
    else:
        backoff = timedelta(seconds=RETRY_BASE_SECONDS * (3 ** (job.attempts - 1)))
        values = dict(status="queued", error=error[:4000], run_after=utcnow() + backoff)
    values.update(locked_at=None, locked_by=None)
    await db.execute(update(Job).where(Job.id == job.id).values(**values))
    await db.commit()


async def update_progress(db: AsyncSession, job_id: uuid.UUID, progress: dict) -> None:
    await db.execute(update(Job).where(Job.id == job_id).values(result=progress))
    await db.commit()


async def requeue_stale_running(db: AsyncSession) -> int:
    """启动时把上次进程崩溃遗留的 running 任务重新排队（单副本前提）。"""
    result = await db.execute(
        update(Job)
        .where(Job.status == "running")
        .values(status="queued", locked_at=None, locked_by=None, run_after=utcnow())
    )
    await db.commit()
    return result.rowcount or 0
