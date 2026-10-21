import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.models import Course, Job, User
from app.schemas.course import JobOut

router = APIRouter()


@router.get("/courses/{course_id}/jobs", response_model=list[JobOut])
async def list_course_jobs(
    active: int = 0,
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[Job]:
    stmt = select(Job).where(Job.course_id == course.id)
    if active:
        stmt = stmt.where(Job.status.in_(("queued", "running")))
    stmt = stmt.order_by(Job.created_at.desc()).limit(50)
    return list((await db.execute(stmt)).scalars())


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.course_id is not None:
        course = await db.get(Course, job.course_id)
        if course is None or (course.user_id != user.id and user.role != "admin"):
            raise HTTPException(status_code=404, detail="任务不存在")
    elif user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job
