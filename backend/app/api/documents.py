import hashlib
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import Course, Document, User
from app.schemas.course import DocumentOut
from app.services.settings_store import get_setting
from app.services.storage import ALLOWED_EXTENSIONS, doc_dir, sanitize_filename

router = APIRouter()


@router.get("/courses/{course_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[Document]:
    return list(
        (
            await db.execute(
                select(Document).where(Document.course_id == course.id).order_by(Document.created_at.desc())
            )
        ).scalars()
    )


@router.post("/courses/{course_id}/documents", response_model=DocumentOut)
async def upload_document(
    file: UploadFile,
    course: Course = Depends(get_owned_course),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    name = sanitize_filename(file.filename or "未命名")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型 {ext or '（无扩展名）'}，"
                "支持: pdf/epub/txt/md/doc/docx/ppt/pptx"
            ),
        )

    max_mb = int(await get_setting(db, "upload_max_mb"))
    quota_mb = int(await get_setting(db, "upload_quota_mb_per_user"))
    used = (
        await db.execute(
            select(func.coalesce(func.sum(Document.size_bytes), 0))
            .join(Course, Course.id == Document.course_id)
            .where(Course.user_id == user.id, Document.is_market_copy.is_(False))
        )
    ).scalar_one()

    doc_id = uuid.uuid4()
    target_dir = doc_dir(course.id, doc_id)
    target = target_dir / f"original{ext}"

    sha = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(target, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise HTTPException(status_code=400, detail=f"文件超过 {max_mb}MB 上限")
                if used + size > quota_mb * 1024 * 1024:
                    raise HTTPException(status_code=400, detail=f"已超出个人存储配额 {quota_mb}MB")
                sha.update(chunk)
                await out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="文件为空")
    except HTTPException:
        target.unlink(missing_ok=True)
        raise

    digest = sha.hexdigest()
    dup = (
        await db.execute(
            select(Document.id).where(Document.course_id == course.id, Document.sha256 == digest)
        )
    ).scalar_one_or_none()
    if dup is not None:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="该文件已上传过（内容相同）")

    doc = Document(
        id=doc_id,
        course_id=course.id,
        original_name=name,
        stored_path=str(target),
        mime=file.content_type or "",
        size_bytes=size,
        sha256=digest,
    )
    db.add(doc)
    await db.flush()
    await enqueue(
        db, "parse_document", {"document_id": str(doc.id)},
        course_id=course.id, user_id=user.id, dedupe_key=f"parse:{doc.id}",
    )
    await db.commit()
    return doc


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    course = await db.get(Course, doc.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.get("/documents/{document_id}/parsed")
async def get_document_parsed(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    doc = await get_document(document_id, user, db)
    if not doc.parsed_md_path or not Path(doc.parsed_md_path).exists():
        raise HTTPException(status_code=404, detail="尚未解析完成")
    return {"markdown": Path(doc.parsed_md_path).read_text(encoding="utf-8")[:200_000]}


@router.post("/documents/{document_id}/retry", response_model=DocumentOut)
async def retry_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    doc = await get_document(document_id, user, db)
    if doc.status != "failed":
        raise HTTPException(status_code=400, detail="仅失败的文档可以重试")
    if doc.parsed_md_path:
        # 解析产物还在：保留断点（ingest_chunk_done）续入库
        doc.status = "parsed"
        doc.error = None
        await db.flush()
        await enqueue(
            db, "ingest_document", {"course_id": str(doc.course_id)},
            course_id=doc.course_id, user_id=user.id, dedupe_key=f"ingest:{doc.course_id}",
        )
    else:
        doc.status = "uploaded"
        doc.error = None
        doc.ingest_chunk_done = 0
        await db.flush()
        await enqueue(
            db, "parse_document", {"document_id": str(doc.id)},
            course_id=doc.course_id, user_id=user.id, dedupe_key=f"parse:{doc.id}",
        )
    await db.commit()
    return doc


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    doc = await get_document(document_id, user, db)
    await db.delete(doc)
    await db.commit()
    return {"ok": True}
