import hashlib
import math
import uuid
from pathlib import Path
from typing import Literal

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import Course, Job, ReviewCard, User, VocabEntry
from app.models.language import LanguageSource
from app.schemas.vocab import (
    VocabArchiveUpdate,
    VocabCardSummaryOut,
    VocabEntryOut,
    VocabImportJobOut,
    VocabSettingsUpdate,
    VocabStatsOut,
)
from app.services.storage import sanitize_filename, vocab_dir
from app.services.vocab import daily_new_words

router = APIRouter()
VOCAB_EXTENSIONS = {".csv", ".tsv", ".md", ".txt"}
MAX_VOCAB_BYTES = 5 * 1024 * 1024


def _vocab_out(entry: VocabEntry, card: ReviewCard | None = None) -> VocabEntryOut:
    return VocabEntryOut.model_validate(entry).model_copy(
        update={
            "card": VocabCardSummaryOut(
                stability=card.stability,
                due=card.due,
                state=card.state,
            ) if card else None
        }
    )


async def _owned_entry(db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID) -> VocabEntry:
    entry = await db.get(VocabEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="词条不存在")
    course = await db.get(Course, entry.course_id)
    if course is None or course.user_id != user_id:
        raise HTTPException(status_code=404, detail="词条不存在")
    return entry


@router.post("/courses/{course_id}/vocab/import", response_model=VocabImportJobOut)
async def import_vocab(
    file: UploadFile,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VocabImportJobOut:
    if course.kind != "language":
        raise HTTPException(status_code=400, detail="仅语言课程支持词表导入")
    name = sanitize_filename(file.filename or "未命名词表")
    ext = Path(name).suffix.lower()
    if ext not in VOCAB_EXTENSIONS:
        raise HTTPException(status_code=400, detail="词表仅支持 CSV / TSV / Markdown / TXT")
    directory = vocab_dir(course.id)
    temporary = directory / f"{uuid.uuid4()}.upload"
    sha = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(temporary, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VOCAB_BYTES:
                    raise HTTPException(status_code=400, detail="词表文件超过 5MB 上限")
                sha.update(chunk)
                await output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="词表文件为空")
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    digest = sha.hexdigest()
    target = directory / f"{digest}{ext}"
    temporary.replace(target)
    dedupe_key = f"vocab_import:{course.id}:{digest[:16]}"
    existing_source = (
        await db.execute(
            select(LanguageSource).where(
                LanguageSource.course_id == course.id,
                LanguageSource.kind == "vocab",
                LanguageSource.sha256 == digest,
            )
        )
    ).scalar_one_or_none()
    if existing_source is not None:
        active_job = (
            await db.execute(
                select(Job).where(
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_(("queued", "running")),
                )
            )
        ).scalar_one_or_none()
        if active_job is not None:
            return VocabImportJobOut(job_id=active_job.id, source_id=existing_source.id)
        raise HTTPException(status_code=409, detail="该词表已导入过")
    source = LanguageSource(
        course_id=course.id,
        kind="vocab",
        original_name=name,
        stored_path=str(target),
        mime=file.content_type or "",
        size_bytes=size,
        sha256=digest,
    )
    db.add(source)
    await db.flush()
    job = await enqueue(
        db,
        "vocab_import",
        {
            "course_id": str(course.id),
            "stored_path": str(target),
            "source_note": name,
            "format": ext.lstrip("."),
            "source_id": str(source.id),
        },
        course_id=course.id,
        user_id=user.id,
        dedupe_key=dedupe_key,
    )
    if job is None:
        job = (
            await db.execute(
                select(Job).where(
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_(("queued", "running")),
                )
            )
        ).scalar_one()
    await db.commit()
    return VocabImportJobOut(job_id=job.id, source_id=source.id)


@router.get("/courses/{course_id}/vocab/template.csv")
async def vocab_template(course: Course = Depends(get_owned_course)) -> Response:
    content = (
        "word,phonetic,meaning,example\n"
        "ephemeral,/ɪˈfemərəl/,短暂的,The moment was ephemeral.\n"
        "resilient,/rɪˈzɪliənt/,有韧性的,She remained resilient.\n"
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vocab-template.csv"'},
    )


@router.get("/courses/{course_id}/vocab", response_model=list[VocabEntryOut])
async def list_vocab(
    status: Literal["pending", "active", "archived"] | None = None,
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[VocabEntryOut]:
    stmt = (
        select(VocabEntry, ReviewCard)
        .outerjoin(ReviewCard, ReviewCard.vocab_id == VocabEntry.id)
        .where(VocabEntry.course_id == course.id)
    )
    if status is not None:
        stmt = stmt.where(VocabEntry.status == status)
    if q.strip():
        stmt = stmt.where(VocabEntry.word.istartswith(q.strip(), autoescape=True))
    rows = (
        await db.execute(stmt.order_by(VocabEntry.created_at, VocabEntry.word).limit(limit).offset(offset))
    ).all()
    return [_vocab_out(entry, card) for entry, card in rows]


@router.get("/courses/{course_id}/vocab/stats", response_model=VocabStatsOut)
async def vocab_stats(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> VocabStatsOut:
    status_rows = dict(
        (
            await db.execute(
                select(VocabEntry.status, func.count())
                .where(VocabEntry.course_id == course.id)
                .group_by(VocabEntry.status)
            )
        ).all()
    )
    total = sum(status_rows.values())
    mastered = (
        await db.execute(
            select(func.count())
            .select_from(ReviewCard)
            .where(
                ReviewCard.course_id == course.id,
                ReviewCard.vocab_id.is_not(None),
                ReviewCard.stability >= 21,
            )
        )
    ).scalar_one()
    quota = daily_new_words(course)
    pending = status_rows.get("pending", 0)
    # 词汇只展示预计引入天数，不生成海量 plan_items，避免大词表撑爆计划表。
    return VocabStatsOut(
        total=total,
        pending=pending,
        active=status_rows.get("active", 0),
        archived=status_rows.get("archived", 0),
        mastered=mastered,
        daily_new_words=quota,
        projected_days=math.ceil(pending / quota),
    )


@router.patch("/courses/{course_id}/vocab/settings", response_model=VocabSettingsUpdate)
async def update_vocab_settings(
    data: VocabSettingsUpdate,
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> VocabSettingsUpdate:
    course.settings = {**(course.settings or {}), "daily_new_words": data.daily_new_words}
    await db.commit()
    return data


@router.patch("/vocab/{entry_id}", response_model=VocabEntryOut)
async def archive_vocab(
    entry_id: uuid.UUID,
    data: VocabArchiveUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VocabEntryOut:
    entry = await _owned_entry(db, entry_id, user.id)
    entry.status = data.status
    card = (
        await db.execute(select(ReviewCard).where(ReviewCard.vocab_id == entry.id))
    ).scalar_one_or_none()
    if card is not None:
        await db.delete(card)
    await db.commit()
    return _vocab_out(entry)
