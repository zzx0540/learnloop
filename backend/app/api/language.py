import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import Course, Document, Job, PlanItem, User, utcnow
from app.models.language import (
    LanguageAssessment,
    LanguageProfile,
    LanguageSource,
    ReadingAttempt,
    ReadingExercise,
    WritingExercise,
    WritingSubmission,
)
from app.schemas.language import (
    ChoiceReviewOut,
    LanguageAssessmentBundleV1,
    LanguageAssessmentCreate,
    LanguageAssessmentJobOut,
    LanguageAssessmentOut,
    LanguageAssessmentPublicOut,
    LanguageAssessmentReviewOut,
    LanguageAssessmentSubmit,
    LanguageProfileOut,
    LanguageProfileUpdate,
    LanguageSourceJobOut,
    LanguageSourceOut,
    LanguageSourceSearchCreate,
    ReadingAttemptCreate,
    ReadingAttemptOut,
    ReadingExerciseOut,
    ReadingGenerateCreate,
    ReadingGenerateJobOut,
    ReadingTextCreate,
    ReadingVocabAdd,
    VocabProbeOut,
    WritingExerciseOut,
    WritingGenerateCreate,
    WritingGenerateJobOut,
    WritingSubmissionCreate,
    WritingSubmissionOut,
)
from app.schemas.vocab import VocabItemOut
from app.services.language_learning import (
    english_word_count,
    ensure_language_profile,
    language_source_dir,
    refresh_language_recommendation,
    sha256_bytes,
    source_vocab_coverage,
)
from app.services.points import shanghai_day_bounds
from app.services.settings_store import get_setting
from app.services.stats import shanghai_date
from app.services.storage import sanitize_filename
from app.services.vocab import upsert_vocab_items

router = APIRouter()
READING_EXTENSIONS = {".pdf", ".epub", ".txt", ".md", ".markdown", ".doc", ".docx"}


def _require_language(course: Course) -> None:
    if course.kind != "language":
        raise HTTPException(status_code=400, detail="仅英语课程支持该功能")


async def _owned_source(
    db: AsyncSession, source_id: uuid.UUID, user_id: uuid.UUID
) -> LanguageSource:
    source = await db.get(LanguageSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="语言来源不存在")
    course = await db.get(Course, source.course_id)
    if course is None or course.user_id != user_id:
        raise HTTPException(status_code=404, detail="语言来源不存在")
    return source


async def _owned_assessment(
    db: AsyncSession, assessment_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
) -> LanguageAssessment:
    stmt = select(LanguageAssessment).where(LanguageAssessment.id == assessment_id)
    if lock:
        stmt = stmt.with_for_update()
    assessment = (await db.execute(stmt)).scalar_one_or_none()
    if assessment is None:
        raise HTTPException(status_code=404, detail="能力测评不存在")
    course = await db.get(Course, assessment.course_id)
    if course is None or course.user_id != user_id:
        raise HTTPException(status_code=404, detail="能力测评不存在")
    return assessment


async def _owned_reading(
    db: AsyncSession, exercise_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
) -> ReadingExercise:
    stmt = select(ReadingExercise).where(ReadingExercise.id == exercise_id)
    if lock:
        stmt = stmt.with_for_update()
    exercise = (await db.execute(stmt)).scalar_one_or_none()
    if exercise is None:
        raise HTTPException(status_code=404, detail="阅读练习不存在")
    course = await db.get(Course, exercise.course_id)
    if course is None or course.user_id != user_id:
        raise HTTPException(status_code=404, detail="阅读练习不存在")
    return exercise


async def _owned_writing(
    db: AsyncSession, exercise_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
) -> WritingExercise:
    stmt = select(WritingExercise).where(WritingExercise.id == exercise_id)
    if lock:
        stmt = stmt.with_for_update()
    exercise = (await db.execute(stmt)).scalar_one_or_none()
    if exercise is None:
        raise HTTPException(status_code=404, detail="写作练习不存在")
    course = await db.get(Course, exercise.course_id)
    if course is None or course.user_id != user_id:
        raise HTTPException(status_code=404, detail="写作练习不存在")
    return exercise


async def _validate_plan_item(
    db: AsyncSession,
    item_id: uuid.UUID | None,
    course_id: uuid.UUID,
    expected_type: str,
) -> None:
    if item_id is None:
        return
    item = await db.get(PlanItem, item_id)
    if item is None or item.course_id != course_id or item.type != expected_type:
        raise HTTPException(status_code=400, detail="计划条目不属于当前课程或类型不匹配")


def _public_assessment_bundle(bundle: LanguageAssessmentBundleV1) -> LanguageAssessmentPublicOut:
    return LanguageAssessmentPublicOut(
        version=bundle.version,
        vocab_items=[VocabProbeOut.model_validate(item.model_dump()) for item in bundle.vocab_items],
        vocab_population=bundle.vocab_population,
        reading={
            "title": bundle.reading.title,
            "passage_md": bundle.reading.passage_md,
            "estimated_level": bundle.reading.estimated_level,
            "questions": [
                {
                    "id": item.id,
                    "dimension": item.dimension,
                    "prompt": item.prompt,
                    "options": item.options,
                }
                for item in bundle.reading.questions
            ],
        },
        writing=bundle.writing,
    )


def _choice_reviews(items: list, answers: list[dict]) -> list[ChoiceReviewOut]:
    answer_map = {
        str(answer["item_id"]): int(answer["choice_index"])
        for answer in answers
    }
    reviews = []
    for item in items:
        item_id = str(item.get("id") if isinstance(item, dict) else item.id)
        if item_id not in answer_map:
            continue
        options = item.get("options", []) if isinstance(item, dict) else item.options
        correct_index = int(
            item.get("correct_index") if isinstance(item, dict) else item.correct_index
        )
        selected_index = answer_map[item_id]
        explanation = str(
            item.get("explanation_md", "")
            if isinstance(item, dict)
            else getattr(item, "explanation_md", "")
        ).strip()
        if not explanation:
            correct = options[correct_index] if 0 <= correct_index < len(options) else "标注选项"
            explanation = f"正确答案是“{correct}”。请结合题干和原文核对相关信息。"
        reviews.append(
            ChoiceReviewOut(
                item_id=item_id,
                selected_index=selected_index,
                correct_index=correct_index,
                is_correct=selected_index == correct_index,
                explanation_md=explanation,
            )
        )
    return reviews


def _assessment_out(assessment: LanguageAssessment) -> LanguageAssessmentOut:
    items = None
    review = None
    if assessment.item_bundle:
        bundle = LanguageAssessmentBundleV1.model_validate(assessment.item_bundle)
        items = _public_assessment_bundle(bundle)
        if assessment.status == "graded" and assessment.response_bundle:
            review = LanguageAssessmentReviewOut(
                vocab=_choice_reviews(
                    bundle.vocab_items,
                    assessment.response_bundle.get("vocab_answers", []),
                ),
                reading=_choice_reviews(
                    bundle.reading.questions,
                    assessment.response_bundle.get("reading_answers", []),
                ),
                writing_md=str(assessment.response_bundle.get("writing_md", "")),
            )
    return LanguageAssessmentOut(
        id=assessment.id,
        course_id=assessment.course_id,
        kind=assessment.kind,
        status=assessment.status,
        source_ids=assessment.source_ids,
        items=items,
        result=assessment.result,
        review=review,
        created_by_job_id=assessment.created_by_job_id,
        created_at=assessment.created_at,
        submitted_at=assessment.submitted_at,
        graded_at=assessment.graded_at,
        error=assessment.error,
    )


def _reading_out(exercise: ReadingExercise, attempts: list[ReadingAttempt]) -> ReadingExerciseOut:
    public_questions = [
        {
            key: value
            for key, value in question.items()
            if key not in {"correct_index", "explanation_md"}
        }
        for question in exercise.questions
    ]
    public_attempts = []
    for attempt in attempts:
        reviews = (
            _choice_reviews(exercise.questions, attempt.answers)
            if exercise.status == "graded"
            else []
        )
        public_attempts.append(
            ReadingAttemptOut.model_validate(attempt).model_copy(
                update={"reviews": reviews}
            )
        )
    return ReadingExerciseOut.model_validate(exercise).model_copy(
        update={
            "questions": public_questions,
            "attempts": public_attempts,
        }
    )


def _writing_out(
    exercise: WritingExercise, submissions: list[WritingSubmission]
) -> WritingExerciseOut:
    return WritingExerciseOut.model_validate(exercise).model_copy(
        update={
            "submissions": [
                WritingSubmissionOut.model_validate(submission) for submission in submissions
            ]
        }
    )


def _profile_out(course: Course, profile: LanguageProfile) -> LanguageProfileOut:
    return LanguageProfileOut.model_validate(profile).model_copy(
        update={
            "track": course.language_track or "custom",
            "daily_new_words": int(
                (course.settings or {}).get("daily_new_words", profile.recommended_daily_words)
            ),
        }
    )


def _source_out(source: LanguageSource, profile: LanguageProfile) -> LanguageSourceOut:
    coverage, recommended_mode = source_vocab_coverage(source, profile)
    return LanguageSourceOut.model_validate(source).model_copy(
        update={
            "vocab_coverage_percent": coverage,
            "recommended_mode": recommended_mode,
        }
    )


async def _generation_count(
    db: AsyncSession, *, course_id: uuid.UUID, kind: str
) -> int:
    start, end = shanghai_day_bounds(shanghai_date(utcnow()))
    return int(
        (
            await db.execute(
                select(func.count()).select_from(Job).where(
                    Job.course_id == course_id,
                    Job.kind == kind,
                    Job.created_at >= start,
                    Job.created_at < end,
                )
            )
        ).scalar_one()
    )


@router.get("/courses/{course_id}/language/profile", response_model=LanguageProfileOut)
async def get_language_profile(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> LanguageProfileOut:
    _require_language(course)
    profile = await ensure_language_profile(db, course)
    await db.commit()
    return _profile_out(course, profile)


@router.patch("/courses/{course_id}/language/profile", response_model=LanguageProfileOut)
async def update_language_profile(
    data: LanguageProfileUpdate,
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> LanguageProfileOut:
    _require_language(course)
    profile = await ensure_language_profile(db, course)
    payload = data.model_dump(exclude_unset=True)
    if "track" in payload:
        course.language_track = payload["track"]
    if "intensity_mode" in payload:
        profile.intensity_mode = payload["intensity_mode"]
    if data.daily_new_words is not None:
        profile.intensity_mode = "manual"
        course.settings = {**(course.settings or {}), "daily_new_words": data.daily_new_words}
    elif data.intensity_mode == "adaptive":
        await refresh_language_recommendation(db, course, profile)
    await db.commit()
    return _profile_out(course, profile)


@router.get("/courses/{course_id}/language/sources", response_model=list[LanguageSourceOut])
async def list_language_sources(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[LanguageSourceOut]:
    _require_language(course)
    profile = await ensure_language_profile(db, course)
    sources = list(
        (
            await db.execute(
                select(LanguageSource)
                .where(LanguageSource.course_id == course.id)
                .order_by(LanguageSource.created_at.desc())
            )
        ).scalars()
    )
    return [_source_out(source, profile) for source in sources]


async def _create_reading_source(
    db: AsyncSession,
    *,
    course: Course,
    user: User,
    name: str,
    mime: str,
    data: bytes,
) -> LanguageSourceJobOut:
    digest = sha256_bytes(data)
    duplicate = (
        await db.execute(
            select(LanguageSource.id).where(
                LanguageSource.course_id == course.id,
                LanguageSource.kind == "reading",
                LanguageSource.sha256 == digest,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该阅读材料已导入过")
    quota_mb = int(await get_setting(db, "upload_quota_mb_per_user"))
    document_bytes = (
        await db.execute(
            select(func.coalesce(func.sum(Document.size_bytes), 0))
            .join(Course, Course.id == Document.course_id)
            .where(Course.user_id == user.id, Document.is_market_copy.is_(False))
        )
    ).scalar_one()
    language_bytes = (
        await db.execute(
            select(func.coalesce(func.sum(LanguageSource.size_bytes), 0))
            .join(Course, Course.id == LanguageSource.course_id)
            .where(Course.user_id == user.id)
        )
    ).scalar_one()
    if document_bytes + language_bytes + len(data) > quota_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"已超出个人存储配额 {quota_mb}MB")
    source = LanguageSource(
        course_id=course.id,
        kind="reading",
        original_name=name,
        mime=mime,
        size_bytes=len(data),
        sha256=digest,
    )
    db.add(source)
    await db.flush()
    path = language_source_dir(course.id, source.id) / f"original{Path(name).suffix.lower()}"
    async with aiofiles.open(path, "wb") as output:
        await output.write(data)
    source.stored_path = str(path)
    job = await enqueue(
        db,
        "analyze_language_source",
        {"source_id": str(source.id)},
        course_id=course.id,
        user_id=user.id,
        dedupe_key=f"analyze_language_source:{source.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="阅读材料正在处理")
    await db.commit()
    return LanguageSourceJobOut(source_id=source.id, job_id=job.id)


@router.post(
    "/courses/{course_id}/language/sources/reading",
    response_model=LanguageSourceJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_reading_source(
    file: UploadFile,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageSourceJobOut:
    _require_language(course)
    name = sanitize_filename(file.filename or "未命名阅读材料")
    ext = Path(name).suffix.lower()
    if ext not in READING_EXTENSIONS:
        raise HTTPException(status_code=400, detail="阅读材料仅支持 PDF/EPUB/TXT/MD/DOC/DOCX")
    max_mb = int(await get_setting(db, "upload_max_mb"))
    data = await file.read(max_mb * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(status_code=400, detail="阅读材料为空")
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"文件超过 {max_mb}MB 上限")
    return await _create_reading_source(
        db,
        course=course,
        user=user,
        name=name,
        mime=file.content_type or "",
        data=data,
    )


@router.post(
    "/courses/{course_id}/language/sources/text",
    response_model=LanguageSourceJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def paste_reading_source(
    data: ReadingTextCreate,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageSourceJobOut:
    _require_language(course)
    return await _create_reading_source(
        db,
        course=course,
        user=user,
        name=f"{sanitize_filename(data.title)}.md",
        mime="text/markdown",
        data=data.content_md.encode("utf-8"),
    )


@router.post(
    "/courses/{course_id}/language/sources/search",
    response_model=LanguageSourceJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def search_reading_source(
    data: LanguageSourceSearchCreate,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageSourceJobOut:
    _require_language(course)
    query = " ".join(data.query.split())
    digest = sha256_bytes(f"web-search:{course.id}:{query.casefold()}".encode())
    source = (
        await db.execute(
            select(LanguageSource).where(
                LanguageSource.course_id == course.id,
                LanguageSource.kind == "reading",
                LanguageSource.sha256 == digest,
            )
        )
    ).scalar_one_or_none()
    if source is not None and source.status == "ready":
        raise HTTPException(status_code=409, detail="相同检索需求已有可用材料")
    if source is None:
        source = LanguageSource(
            course_id=course.id,
            kind="reading",
            original_name=f"AI检索：{query}"[:256],
            mime="text/markdown",
            sha256=digest,
        )
        db.add(source)
        await db.flush()
    else:
        source.status = "uploaded"
        source.error = None
        source.analysis = {}
    job = await enqueue(
        db,
        "search_language_source",
        {"source_id": str(source.id), "query": query},
        course_id=course.id,
        user_id=user.id,
        dedupe_key=f"search_language_source:{source.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="该检索任务正在处理")
    await db.commit()
    return LanguageSourceJobOut(source_id=source.id, job_id=job.id)


@router.post(
    "/courses/{course_id}/language/assessments",
    response_model=LanguageAssessmentJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_language_assessment(
    data: LanguageAssessmentCreate,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageAssessmentJobOut:
    _require_language(course)
    profile = await ensure_language_profile(db, course)
    active = (
        await db.execute(
            select(LanguageAssessment.id).where(
                LanguageAssessment.course_id == course.id,
                LanguageAssessment.status.in_(("building", "ready", "submitted")),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise HTTPException(status_code=409, detail="已有未完成的能力测评")
    if data.kind == "baseline" and profile.latest_assessment_at is not None:
        raise HTTPException(status_code=400, detail="已有基线结果，请使用周期测评")
    if data.source_ids:
        count = (
            await db.execute(
                select(func.count()).select_from(LanguageSource).where(
                    LanguageSource.course_id == course.id,
                    LanguageSource.id.in_(data.source_ids),
                    LanguageSource.status == "ready",
                )
            )
        ).scalar_one()
        if count != len(set(data.source_ids)):
            raise HTTPException(status_code=400, detail="测评来源不存在或尚未处理完成")
    assessment = LanguageAssessment(
        course_id=course.id,
        kind=data.kind,
        source_ids=[str(source_id) for source_id in data.source_ids],
    )
    db.add(assessment)
    await db.flush()
    job = await enqueue(
        db,
        "build_language_assessment",
        {"assessment_id": str(assessment.id)},
        course_id=course.id,
        user_id=user.id,
        dedupe_key=f"build_language_assessment:{course.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="能力测评正在生成")
    assessment.created_by_job_id = job.id
    if not (
        data.kind == "periodic"
        and profile.latest_assessment_at is not None
        and profile.assessment_status == "active"
    ):
        profile.assessment_status = "building"
    await db.commit()
    return LanguageAssessmentJobOut(assessment_id=assessment.id, job_id=job.id)


@router.get(
    "/courses/{course_id}/language/assessments/current",
    response_model=LanguageAssessmentOut | None,
)
async def get_current_language_assessment(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> LanguageAssessmentOut | None:
    _require_language(course)
    assessment = (
        await db.execute(
            select(LanguageAssessment)
            .where(LanguageAssessment.course_id == course.id)
            .order_by(LanguageAssessment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return _assessment_out(assessment) if assessment is not None else None


@router.get("/language/assessments/{assessment_id}", response_model=LanguageAssessmentOut)
async def get_language_assessment(
    assessment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageAssessmentOut:
    return _assessment_out(await _owned_assessment(db, assessment_id, user.id))


@router.post(
    "/language/assessments/{assessment_id}/submit",
    response_model=LanguageAssessmentJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_language_assessment(
    assessment_id: uuid.UUID,
    data: LanguageAssessmentSubmit,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageAssessmentJobOut:
    assessment = await _owned_assessment(db, assessment_id, user.id, lock=True)
    if assessment.status != "ready":
        raise HTTPException(status_code=400, detail="能力测评当前不可提交")
    bundle = LanguageAssessmentBundleV1.model_validate(assessment.item_bundle)
    vocab_ids = [answer.item_id for answer in data.vocab_answers]
    reading_ids = [answer.item_id for answer in data.reading_answers]
    if len(vocab_ids) != len(set(vocab_ids)) or set(vocab_ids) != {
        item.id for item in bundle.vocab_items
    }:
        raise HTTPException(status_code=400, detail="词汇作答项与题目不一致")
    if len(reading_ids) != len(set(reading_ids)) or set(reading_ids) != {
        item.id for item in bundle.reading.questions
    }:
        raise HTTPException(status_code=400, detail="阅读作答项与题目不一致")
    word_count = english_word_count(data.writing_md)
    if not bundle.writing.min_words <= word_count <= bundle.writing.max_words:
        raise HTTPException(
            status_code=400,
            detail=f"写作需为 {bundle.writing.min_words}-{bundle.writing.max_words} 个英文词",
        )
    assessment.response_bundle = data.model_dump(mode="json")
    assessment.status = "submitted"
    assessment.submitted_at = utcnow()
    job = await enqueue(
        db,
        "grade_language_assessment",
        {"assessment_id": str(assessment.id)},
        course_id=assessment.course_id,
        user_id=user.id,
        dedupe_key=f"grade_language_assessment:{assessment.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="能力测评正在批改")
    await db.commit()
    return LanguageAssessmentJobOut(assessment_id=assessment.id, job_id=job.id)


@router.get("/courses/{course_id}/readings", response_model=list[ReadingExerciseOut])
async def list_readings(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[ReadingExerciseOut]:
    _require_language(course)
    exercises = list(
        (
            await db.execute(
                select(ReadingExercise)
                .where(ReadingExercise.course_id == course.id)
                .order_by(ReadingExercise.created_at.desc())
            )
        ).scalars()
    )
    attempts_by_exercise: dict[uuid.UUID, list[ReadingAttempt]] = {
        exercise.id: [] for exercise in exercises
    }
    if exercises:
        attempts = list(
            (
                await db.execute(
                    select(ReadingAttempt)
                    .where(ReadingAttempt.exercise_id.in_(attempts_by_exercise))
                    .order_by(ReadingAttempt.created_at)
                )
            ).scalars()
        )
        for attempt in attempts:
            attempts_by_exercise[attempt.exercise_id].append(attempt)
    return [_reading_out(item, attempts_by_exercise[item.id]) for item in exercises]


@router.post(
    "/courses/{course_id}/readings/generate",
    response_model=ReadingGenerateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_reading(
    data: ReadingGenerateCreate,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadingGenerateJobOut:
    _require_language(course)
    profile = await ensure_language_profile(db, course)
    if profile.assessment_status != "active":
        raise HTTPException(status_code=400, detail="请先完成当前课程能力测评")
    source = await _owned_source(db, data.source_id, user.id)
    if source.course_id != course.id or source.kind != "reading" or source.status != "ready":
        raise HTTPException(status_code=400, detail="阅读来源不属于当前课程或尚未就绪")
    await _validate_plan_item(db, data.plan_item_id, course.id, "reading")
    if await _generation_count(db, course_id=course.id, kind="generate_language_reading") >= 5:
        raise HTTPException(status_code=429, detail="今日阅读练习生成已达上限")
    job = await enqueue(
        db,
        "generate_language_reading",
        {
            "course_id": str(course.id),
            "source_id": str(source.id),
            "mode": data.mode,
            "plan_item_id": str(data.plan_item_id) if data.plan_item_id else None,
        },
        course_id=course.id,
        user_id=user.id,
        dedupe_key=f"generate_language_reading:{course.id}:{source.id}:{data.mode}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="阅读练习正在生成")
    await db.commit()
    return ReadingGenerateJobOut(job_id=job.id)


@router.get("/readings/{exercise_id}", response_model=ReadingExerciseOut)
async def get_reading(
    exercise_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadingExerciseOut:
    exercise = await _owned_reading(db, exercise_id, user.id)
    attempts = list(
        (
            await db.execute(
                select(ReadingAttempt)
                .where(ReadingAttempt.exercise_id == exercise.id)
                .order_by(ReadingAttempt.attempt_no)
            )
        ).scalars()
    )
    return _reading_out(exercise, attempts)


@router.post(
    "/readings/{exercise_id}/attempts",
    response_model=ReadingGenerateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_reading_attempt(
    exercise_id: uuid.UUID,
    data: ReadingAttemptCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadingGenerateJobOut:
    exercise = await _owned_reading(db, exercise_id, user.id, lock=True)
    if exercise.status == "submitted":
        raise HTTPException(status_code=409, detail="阅读练习正在批改")
    expected_ids = {str(question["id"]) for question in exercise.questions}
    answer_ids = {answer.item_id for answer in data.answers}
    if len(answer_ids) != len(data.answers) or answer_ids != expected_ids:
        raise HTTPException(status_code=400, detail="阅读作答项与题目不一致")
    response_words = english_word_count(data.response_md)
    minimum_words = 30 if exercise.mode == "intensive" else 20
    if not minimum_words <= response_words <= 300:
        raise HTTPException(
            status_code=400,
            detail=f"{('精读摘要' if exercise.mode == 'intensive' else '泛读回应')}需为 "
            f"{minimum_words}-300 个英文词",
        )
    attempt_no = int(
        (
            await db.execute(
                select(func.coalesce(func.max(ReadingAttempt.attempt_no), 0)).where(
                    ReadingAttempt.exercise_id == exercise.id
                )
            )
        ).scalar_one()
    ) + 1
    attempt = ReadingAttempt(
        exercise_id=exercise.id,
        attempt_no=attempt_no,
        answers=data.model_dump(mode="json")["answers"],
        response_md=data.response_md,
        duration_seconds=data.duration_seconds,
    )
    db.add(attempt)
    await db.flush()
    exercise.status = "submitted"
    job = await enqueue(
        db,
        "grade_language_reading",
        {"attempt_id": str(attempt.id)},
        course_id=exercise.course_id,
        user_id=user.id,
        dedupe_key=f"grade_language_reading:{attempt.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="阅读练习正在批改")
    await db.commit()
    return ReadingGenerateJobOut(job_id=job.id)


@router.post("/readings/{exercise_id}/vocab")
async def add_reading_vocab(
    exercise_id: uuid.UUID,
    data: ReadingVocabAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    exercise = await _owned_reading(db, exercise_id, user.id)
    by_word = {str(item["word"]).casefold(): item for item in exercise.focus_vocab}
    requested = list(dict.fromkeys(word.strip().casefold() for word in data.words))
    if any(word not in by_word for word in requested):
        raise HTTPException(status_code=400, detail="只能加入当前阅读练习列出的重点词")
    items = [
        VocabItemOut(
            word=str(by_word[word]["word"]),
            meaning_md=str(by_word[word]["meaning"]),
            example_md=str(by_word[word].get("context", "")),
        )
        for word in requested
    ]
    counts = await upsert_vocab_items(
        db,
        course_id=exercise.course_id,
        items=items,
        source_note=f"阅读：{exercise.title}"[:120],
    )
    await db.commit()
    return counts


@router.get("/courses/{course_id}/writings", response_model=list[WritingExerciseOut])
async def list_writings(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[WritingExerciseOut]:
    _require_language(course)
    exercises = list(
        (
            await db.execute(
                select(WritingExercise)
                .where(WritingExercise.course_id == course.id)
                .order_by(WritingExercise.created_at.desc())
            )
        ).scalars()
    )
    submissions_by_exercise: dict[uuid.UUID, list[WritingSubmission]] = {
        exercise.id: [] for exercise in exercises
    }
    if exercises:
        submissions = list(
            (
                await db.execute(
                    select(WritingSubmission)
                    .where(WritingSubmission.exercise_id.in_(submissions_by_exercise))
                    .order_by(WritingSubmission.version)
                )
            ).scalars()
        )
        for submission in submissions:
            submissions_by_exercise[submission.exercise_id].append(submission)
    return [_writing_out(item, submissions_by_exercise[item.id]) for item in exercises]


@router.post(
    "/courses/{course_id}/writings/generate",
    response_model=WritingGenerateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_writing(
    data: WritingGenerateCreate,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WritingGenerateJobOut:
    _require_language(course)
    profile = await ensure_language_profile(db, course)
    if profile.assessment_status != "active":
        raise HTTPException(status_code=400, detail="请先完成当前课程能力测评")
    await _validate_plan_item(db, data.plan_item_id, course.id, "writing")
    if data.source_reading_id is not None:
        reading = await _owned_reading(db, data.source_reading_id, user.id)
        if reading.course_id != course.id:
            raise HTTPException(status_code=400, detail="阅读练习不属于当前课程")
    if await _generation_count(db, course_id=course.id, kind="generate_language_writing") >= 3:
        raise HTTPException(status_code=429, detail="今日写作题目已达上限")
    job = await enqueue(
        db,
        "generate_language_writing",
        {
            "course_id": str(course.id),
            "source_reading_id": (
                str(data.source_reading_id) if data.source_reading_id else None
            ),
            "plan_item_id": str(data.plan_item_id) if data.plan_item_id else None,
        },
        course_id=course.id,
        user_id=user.id,
        dedupe_key=f"generate_language_writing:{course.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="写作题目正在生成")
    await db.commit()
    return WritingGenerateJobOut(job_id=job.id)


@router.get("/writings/{exercise_id}", response_model=WritingExerciseOut)
async def get_writing(
    exercise_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WritingExerciseOut:
    exercise = await _owned_writing(db, exercise_id, user.id)
    submissions = list(
        (
            await db.execute(
                select(WritingSubmission)
                .where(WritingSubmission.exercise_id == exercise.id)
                .order_by(WritingSubmission.version)
            )
        ).scalars()
    )
    return _writing_out(exercise, submissions)


@router.post(
    "/writings/{exercise_id}/submissions",
    response_model=WritingGenerateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_writing(
    exercise_id: uuid.UUID,
    data: WritingSubmissionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WritingGenerateJobOut:
    exercise = await _owned_writing(db, exercise_id, user.id, lock=True)
    if exercise.status not in ("open", "revision_due"):
        raise HTTPException(status_code=400, detail="写作练习当前不可提交")
    version = int(
        (
            await db.execute(
                select(func.coalesce(func.max(WritingSubmission.version), 0)).where(
                    WritingSubmission.exercise_id == exercise.id
                )
            )
        ).scalar_one()
    ) + 1
    if version > 1:
        previous = (
            await db.execute(
                select(WritingSubmission.submission_md).where(
                    WritingSubmission.exercise_id == exercise.id,
                    WritingSubmission.version == version - 1,
                )
            )
        ).scalar_one_or_none()
        if previous is not None and " ".join(previous.split()) == " ".join(data.submission_md.split()):
            raise HTTPException(status_code=400, detail="修订稿不能与上一版相同")
    submission = WritingSubmission(
        exercise_id=exercise.id,
        version=version,
        submission_md=data.submission_md,
    )
    db.add(submission)
    await db.flush()
    exercise.status = "grading"
    job = await enqueue(
        db,
        "grade_language_writing",
        {"submission_id": str(submission.id)},
        course_id=exercise.course_id,
        user_id=user.id,
        dedupe_key=f"grade_language_writing:{submission.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="写作练习正在批改")
    await db.commit()
    return WritingGenerateJobOut(job_id=job.id)
