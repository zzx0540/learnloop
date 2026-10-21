import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.models import Course, User, VocabEntry
from app.models.language import (
    LanguageAssessment,
    LanguageProfile,
    LanguageSource,
    ReadingExercise,
    WritingExercise,
)
from app.schemas.course import CourseCreate, CourseOut, CourseUpdate
from app.services.storage import course_dir
from app.services.wiki import seed_course_wiki

router = APIRouter()


@router.get("", response_model=list[CourseOut])
async def list_courses(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Course]:
    return list(
        (
            await db.execute(
                select(Course).where(Course.user_id == user.id).order_by(Course.created_at.desc())
            )
        ).scalars()
    )


@router.post("", response_model=CourseOut)
async def create_course(
    data: CourseCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Course:
    language_track = data.language_track or ("custom" if data.kind == "language" else None)
    course = Course(
        user_id=user.id,
        name=data.name,
        goal_text=data.goal_text,
        kind=data.kind,
        language_track=language_track,
        daily_minutes=data.daily_minutes,
        target_date=data.target_date,
    )
    db.add(course)
    await db.flush()
    if course.kind == "language":
        db.add(
            LanguageProfile(
                course_id=course.id,
                assessment_status=(
                    "required" if "language_track" in data.model_fields_set else "active"
                ),
            )
        )
    await seed_course_wiki(db, course)
    await db.commit()
    return course


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(course: Course = Depends(get_owned_course)) -> Course:
    return course


@router.patch("/{course_id}", response_model=CourseOut)
async def update_course(
    data: CourseUpdate, course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> Course:
    payload = data.model_dump(exclude_unset=True)
    next_kind = payload.get("kind", course.kind)
    next_track = payload.get("language_track", course.language_track)
    if next_kind == "language" and next_track is None:
        raise HTTPException(status_code=400, detail="英语课程必须选择课程轨道")
    if next_kind != "language" and next_track is not None:
        raise HTTPException(status_code=400, detail="仅英语课程可以设置课程轨道")
    if course.kind == "language" and next_kind != "language":
        language_rows = 0
        for model in (
            VocabEntry,
            LanguageSource,
            LanguageAssessment,
            ReadingExercise,
            WritingExercise,
        ):
            language_rows += int(
                (
                    await db.execute(
                        select(func.count()).select_from(model).where(model.course_id == course.id)
                    )
                ).scalar_one()
            )
        if language_rows:
            raise HTTPException(status_code=400, detail="课程已有英语学习数据，不能更改课程类型")
    for field, value in payload.items():
        setattr(course, field, value)
    profile = await db.get(LanguageProfile, course.id)
    if next_kind == "language" and profile is None:
        db.add(LanguageProfile(course_id=course.id))
    if next_kind != "language" and profile is not None:
        await db.delete(profile)
    await db.commit()
    return course


@router.delete("/{course_id}")
async def delete_course(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> dict:
    directory = course_dir(course.id)
    await db.delete(course)
    await db.commit()
    shutil.rmtree(directory, ignore_errors=True)
    return {"ok": True}
