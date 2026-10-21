from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import (
    claim_next,
    enqueue,
    mark_failed_or_retry,
    mark_succeeded,
    requeue_stale_running,
)
from app.jobs.worker import (
    should_run_examiner,
    should_run_lint,
    should_run_mastery,
    should_run_summary,
)
from app.models import Job, utcnow


async def test_enqueue_and_dedupe(db: AsyncSession) -> None:
    j1 = await enqueue(db, "demo", {"a": 1}, dedupe_key="k1")
    await db.commit()
    assert j1 is not None
    j2 = await enqueue(db, "demo", {"a": 2}, dedupe_key="k1")
    assert j2 is None  # 去重

    await mark_succeeded(db, j1.id, {"ok": True})
    j3 = await enqueue(db, "demo", {"a": 3}, dedupe_key="k1")
    await db.commit()
    assert j3 is not None  # 完成后可再入队


async def test_claim_and_success(db: AsyncSession) -> None:
    await enqueue(db, "demo", {"n": 1})
    await db.commit()
    job = await claim_next(db, "w1", kinds=["demo"])
    assert job is not None and job.status == "running" and job.attempts == 1

    none = await claim_next(db, "w2", kinds=["demo"])
    assert none is None  # 已被领走

    job.error = "上一次尝试失败"
    await db.commit()
    await mark_succeeded(db, job.id, {"done": 1})
    fresh = await db.get(Job, job.id)
    await db.refresh(fresh)
    assert fresh.status == "succeeded" and fresh.result == {"done": 1} and fresh.error is None


async def test_retry_then_fail(db: AsyncSession) -> None:
    await enqueue(db, "demo", max_attempts=2)
    await db.commit()

    job = await claim_next(db, "w1", kinds=["demo"])
    await mark_failed_or_retry(db, job, "第一次失败")
    await db.refresh(job)
    assert job.status == "queued" and job.run_after > utcnow()  # 退避重排

    job.run_after = utcnow()
    await db.commit()
    job = await claim_next(db, "w1", kinds=["demo"])
    assert job.attempts == 2
    await mark_failed_or_retry(db, job, "第二次失败")
    await db.refresh(job)
    assert job.status == "failed"


async def test_requeue_stale_running(db: AsyncSession) -> None:
    j = await enqueue(db, "demo")
    await db.commit()
    job = await claim_next(db, "w1", kinds=["demo"])
    assert job.status == "running"

    n = await requeue_stale_running(db)
    assert n == 1
    stale = (await db.execute(select(Job).where(Job.id == j.id))).scalar_one()
    assert stale.status == "queued" and stale.locked_by is None


async def test_kind_filter(db: AsyncSession) -> None:
    await enqueue(db, "heavy_kind")
    await db.commit()
    assert await claim_next(db, "w1", kinds=["other"]) is None
    assert (await claim_next(db, "w1", kinds=["heavy_kind"])) is not None


def test_mastery_cron_runs_after_target_time_once_per_day() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    day = date(2026, 7, 17)
    assert not should_run_mastery(datetime(2026, 7, 17, 2, 29, tzinfo=shanghai), None)
    assert should_run_mastery(datetime(2026, 7, 17, 2, 31, tzinfo=shanghai), None)
    assert not should_run_mastery(datetime(2026, 7, 17, 8, 0, tzinfo=shanghai), day)


def test_examiner_and_summary_cron_guards() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    sunday = date(2026, 7, 19)
    assert not should_run_examiner(datetime(2026, 7, 19, 2, 9, tzinfo=shanghai), None)
    assert should_run_examiner(datetime(2026, 7, 19, 2, 10, tzinfo=shanghai), None)
    assert not should_run_examiner(datetime(2026, 7, 19, 8, 0, tzinfo=shanghai), sunday)
    assert not should_run_lint(datetime(2026, 7, 18, 3, 0, tzinfo=shanghai), None)
    assert not should_run_lint(datetime(2026, 7, 19, 2, 39, tzinfo=shanghai), None)
    assert should_run_lint(datetime(2026, 7, 19, 2, 40, tzinfo=shanghai), None)
    assert not should_run_lint(datetime(2026, 7, 19, 8, 0, tzinfo=shanghai), sunday)
    assert not should_run_summary(datetime(2026, 7, 18, 3, 0, tzinfo=shanghai), None)
    assert not should_run_summary(datetime(2026, 7, 19, 2, 49, tzinfo=shanghai), None)
    assert should_run_summary(datetime(2026, 7, 19, 2, 50, tzinfo=shanghai), None)
    assert not should_run_summary(datetime(2026, 7, 19, 8, 0, tzinfo=shanghai), sunday)
