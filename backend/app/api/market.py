import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import list_documents, upload_document
from app.core.deps import get_current_user, require_admin
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import (
    Course,
    Document,
    Job,
    MarketAcquisition,
    MarketCourse,
    MarketCourseVersion,
    User,
)
from app.schemas.course import DocumentOut
from app.schemas.market import (
    MarketAcquisitionOut,
    MarketCourseAdminOut,
    MarketCourseCreate,
    MarketCourseListOut,
    MarketCourseOut,
    MarketCourseUpdate,
    MarketPublishOut,
    MarketSourceCourseOut,
)
from app.services.market import _ensure_source_ready
from app.services.points import points_balance

router = APIRouter()


async def _admin_market(
    db: AsyncSession, market_course_id: uuid.UUID
) -> MarketCourse:
    market = await db.get(MarketCourse, market_course_id)
    if market is None:
        raise HTTPException(status_code=404, detail="市场课程不存在")
    return market


def _manifest_counts(version: MarketCourseVersion | None) -> tuple[int, int, int, str]:
    manifest = version.manifest if version and version.status == "ready" else {}
    course = manifest.get("course") or {}
    return (
        len(manifest.get("documents") or []),
        len(manifest.get("knowledge_points") or []),
        len(manifest.get("vocab_entries") or []),
        course.get("kind") or "knowledge",
    )


async def _admin_out(
    db: AsyncSession, market: MarketCourse, admin_id: uuid.UUID
) -> MarketCourseAdminOut:
    source = await db.get(Course, market.source_course_id) if market.source_course_id else None
    owner = await db.get(User, source.user_id) if source else None
    version = (
        await db.execute(
            select(MarketCourseVersion).where(
                MarketCourseVersion.market_course_id == market.id,
                MarketCourseVersion.version == market.current_version,
            )
        )
    ).scalar_one_or_none()
    documents, knowledge_points, vocab, _ = _manifest_counts(version)
    return MarketCourseAdminOut(
        id=market.id,
        source_course_id=market.source_course_id,
        source_course_name=source.name if source else None,
        source_owner_username=owner.username if owner else None,
        source_owned_by_admin=source is not None and source.user_id == admin_id,
        title=market.title,
        description=market.description,
        price_points=market.price_points,
        status=market.status,
        current_version=market.current_version,
        version_status=version.status if version else None,
        document_count=documents,
        knowledge_point_count=knowledge_points,
        vocab_count=vocab,
        published_at=market.published_at,
        created_at=market.created_at,
        updated_at=market.updated_at,
    )


async def _current_ready_version(
    db: AsyncSession, market: MarketCourse
) -> MarketCourseVersion | None:
    if market.current_version <= 0:
        return None
    return (
        await db.execute(
            select(MarketCourseVersion).where(
                MarketCourseVersion.market_course_id == market.id,
                MarketCourseVersion.version == market.current_version,
                MarketCourseVersion.status == "ready",
            )
        )
    ).scalar_one_or_none()


def _acquisition_out(acquisition: MarketAcquisition) -> MarketAcquisitionOut:
    return MarketAcquisitionOut(
        id=acquisition.id,
        market_course_id=acquisition.market_course_id,
        target_course_id=acquisition.target_course_id,
        title_snapshot=acquisition.title_snapshot,
        price_snapshot=acquisition.price_snapshot,
        version_snapshot=acquisition.version_snapshot,
        status=acquisition.status,
        error=acquisition.error,
        job_id=acquisition.created_by_job_id,
        created_at=acquisition.created_at,
        updated_at=acquisition.updated_at,
    )


def _market_out(
    market: MarketCourse,
    version: MarketCourseVersion,
    acquisition: MarketAcquisition | None,
) -> MarketCourseOut:
    documents, knowledge_points, vocab, kind = _manifest_counts(version)
    return MarketCourseOut(
        id=market.id,
        title=market.title,
        description=market.description,
        kind=kind,
        price_points=market.price_points,
        current_version=market.current_version,
        document_count=documents,
        knowledge_point_count=knowledge_points,
        vocab_count=vocab,
        acquisition_id=acquisition.id if acquisition else None,
        acquisition_status=acquisition.status if acquisition else None,
        owned_course_id=acquisition.target_course_id if acquisition else None,
        acquisition_error=acquisition.error if acquisition else None,
        published_at=market.published_at,
    )


@router.get("/admin/market/source-courses", response_model=list[MarketSourceCourseOut])
async def list_market_source_courses(
    q: str = Query(default="", max_length=128),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MarketSourceCourseOut]:
    stmt = (
        select(Course, User.username, func.count(Document.id))
        .join(User, User.id == Course.user_id)
        .outerjoin(Document, Document.course_id == Course.id)
        .group_by(Course.id, User.username)
        .order_by(Course.created_at.desc())
        .limit(100)
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Course.name.ilike(pattern), User.username.ilike(pattern)))
    rows = (await db.execute(stmt)).all()
    return [
        MarketSourceCourseOut(
            id=course.id,
            name=course.name,
            owner_username=username,
            kind=course.kind,
            status=course.status,
            document_count=int(document_count),
            is_owned_by_admin=course.user_id == admin.id,
        )
        for course, username, document_count in rows
    ]


@router.post("/admin/market/courses", response_model=MarketCourseAdminOut)
async def create_market_course(
    data: MarketCourseCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MarketCourseAdminOut:
    source = await db.get(Course, data.source_course_id)
    if source is None:
        raise HTTPException(status_code=404, detail="源课程不存在")
    existing = (
        await db.execute(
            select(MarketCourse.id).where(MarketCourse.source_course_id == source.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="该源课程已加入市场")
    market = MarketCourse(
        source_course_id=source.id,
        created_by_user_id=admin.id,
        title=data.title,
        description=data.description,
        price_points=data.price_points,
    )
    db.add(market)
    await db.commit()
    return await _admin_out(db, market, admin.id)


@router.get("/admin/market/courses", response_model=list[MarketCourseAdminOut])
async def list_admin_market_courses(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[MarketCourseAdminOut]:
    markets = list(
        (
            await db.execute(select(MarketCourse).order_by(MarketCourse.created_at.desc()))
        ).scalars()
    )
    return [await _admin_out(db, market, admin.id) for market in markets]


@router.get("/admin/market/courses/{market_course_id}", response_model=MarketCourseAdminOut)
async def get_admin_market_course(
    market_course_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MarketCourseAdminOut:
    return await _admin_out(db, await _admin_market(db, market_course_id), admin.id)


@router.patch("/admin/market/courses/{market_course_id}", response_model=MarketCourseAdminOut)
async def update_market_course(
    market_course_id: uuid.UUID,
    data: MarketCourseUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MarketCourseAdminOut:
    market = await _admin_market(db, market_course_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(market, field, value)
    await db.commit()
    return await _admin_out(db, market, admin.id)


@router.get(
    "/admin/market/courses/{market_course_id}/documents",
    response_model=list[DocumentOut],
)
async def list_market_documents(
    market_course_id: uuid.UUID,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Document]:
    market = await _admin_market(db, market_course_id)
    source = await db.get(Course, market.source_course_id) if market.source_course_id else None
    if source is None:
        raise HTTPException(status_code=409, detail="市场源课程已删除")
    return await list_documents(course=source, db=db)


@router.post(
    "/admin/market/courses/{market_course_id}/documents",
    response_model=DocumentOut,
)
async def upload_market_document(
    market_course_id: uuid.UUID,
    file: UploadFile,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Document:
    market = await _admin_market(db, market_course_id)
    source = await db.get(Course, market.source_course_id) if market.source_course_id else None
    if source is None:
        raise HTTPException(status_code=409, detail="市场源课程已删除")
    if source.user_id != admin.id:
        raise HTTPException(status_code=409, detail="外部用户源课程只允许只读发布")
    return await upload_document(file=file, course=source, user=admin, db=db)


@router.post(
    "/admin/market/courses/{market_course_id}/publish",
    response_model=MarketPublishOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_market_course(
    market_course_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MarketPublishOut:
    market = (
        await db.execute(
            select(MarketCourse).where(MarketCourse.id == market_course_id).with_for_update()
        )
    ).scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail="市场课程不存在")
    if market.source_course_id is None:
        raise HTTPException(status_code=409, detail="市场源课程已删除")
    try:
        await _ensure_source_ready(db, market.source_course_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    active = (
        await db.execute(
            select(Job.id).where(
                Job.kind == "market_publish",
                Job.status.in_(("queued", "running")),
                Job.payload["market_course_id"].astext == str(market.id),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise HTTPException(status_code=409, detail="该市场课程正在发布")
    latest = int(
        (
            await db.execute(
                select(func.coalesce(func.max(MarketCourseVersion.version), 0)).where(
                    MarketCourseVersion.market_course_id == market.id
                )
            )
        ).scalar_one()
    )
    version = MarketCourseVersion(
        market_course_id=market.id,
        version=latest + 1,
        status="building",
    )
    db.add(version)
    await db.flush()
    job = await enqueue(
        db,
        "market_publish",
        {"market_course_id": str(market.id), "market_version_id": str(version.id)},
        course_id=market.source_course_id,
        user_id=admin.id,
        dedupe_key=f"market_publish:{market.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="该市场课程正在发布")
    version.created_by_job_id = job.id
    await db.commit()
    return MarketPublishOut(job_id=job.id, version=version.version)


@router.post("/admin/market/courses/{market_course_id}/archive", response_model=MarketCourseAdminOut)
async def archive_market_course(
    market_course_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MarketCourseAdminOut:
    market = await _admin_market(db, market_course_id)
    market.status = "archived"
    await db.commit()
    return await _admin_out(db, market, admin.id)


@router.get("/market/courses", response_model=MarketCourseListOut)
async def list_market_courses(
    q: str = Query(default="", max_length=128),
    kind: str | None = Query(default=None, pattern=r"^(knowledge|language|programming)$"),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarketCourseListOut:
    stmt = (
        select(MarketCourse, MarketCourseVersion)
        .join(
            MarketCourseVersion,
            (MarketCourseVersion.market_course_id == MarketCourse.id)
            & (MarketCourseVersion.version == MarketCourse.current_version),
        )
        .where(
            MarketCourse.status == "published",
            MarketCourseVersion.status == "ready",
        )
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(MarketCourse.title.ilike(pattern), MarketCourse.description.ilike(pattern))
        )
    if kind is not None:
        stmt = stmt.where(MarketCourseVersion.manifest["course"]["kind"].astext == kind)
    total = int(
        (
            await db.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
    )
    rows = (
        await db.execute(
            stmt.order_by(MarketCourse.published_at.desc()).offset(offset).limit(limit)
        )
    ).all()
    markets = [market for market, _ in rows]
    acquisitions = {
        row.market_course_id: row
        for row in (
            await db.execute(
                select(MarketAcquisition).where(
                    MarketAcquisition.user_id == user.id,
                    MarketAcquisition.market_course_id.in_([row.id for row in markets]),
                )
            )
        ).scalars()
    } if markets else {}
    items = [
        _market_out(market, version, acquisitions.get(market.id))
        for market, version in rows
    ]
    return MarketCourseListOut(items=items, total=total)


@router.get("/market/courses/{market_course_id}", response_model=MarketCourseOut)
async def get_market_course(
    market_course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarketCourseOut:
    market = await db.get(MarketCourse, market_course_id)
    if market is None or market.status != "published":
        raise HTTPException(status_code=404, detail="市场课程不存在")
    version = await _current_ready_version(db, market)
    if version is None:
        raise HTTPException(status_code=404, detail="市场课程不存在")
    acquisition = (
        await db.execute(
            select(MarketAcquisition).where(
                MarketAcquisition.user_id == user.id,
                MarketAcquisition.market_course_id == market.id,
            )
        )
    ).scalar_one_or_none()
    return _market_out(market, version, acquisition)


@router.post(
    "/market/courses/{market_course_id}/acquire",
    response_model=MarketAcquisitionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def acquire_market_course(
    market_course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarketAcquisitionOut:
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    market = await db.get(MarketCourse, market_course_id)
    if market is None or market.status != "published":
        raise HTTPException(status_code=404, detail="市场课程不存在")
    version = await _current_ready_version(db, market)
    if version is None:
        raise HTTPException(status_code=404, detail="市场课程不存在")
    existing = (
        await db.execute(
            select(MarketAcquisition).where(
                MarketAcquisition.user_id == user.id,
                MarketAcquisition.market_course_id == market.id,
            )
        )
    ).scalar_one_or_none()
    restoring = False
    if existing is not None and existing.status == "completed":
        target = await db.get(Course, existing.target_course_id) if existing.target_course_id else None
        if target is not None:
            raise HTTPException(status_code=409, detail="该课程已获取")
        restoring = True
        existing.target_course_id = None
        existing.status = "pending"
        existing.error = None
    if existing is not None and existing.status == "pending":
        job = await db.get(Job, existing.created_by_job_id) if existing.created_by_job_id else None
        if job is not None and job.status in {"queued", "running"}:
            return _acquisition_out(existing)
    if (
        not restoring
        and market.price_points > 0
        and await points_balance(db, user.id) < market.price_points
    ):
        raise HTTPException(status_code=400, detail="积分不足")

    acquisition = existing or MarketAcquisition(
        user_id=user.id,
        market_course_id=market.id,
        market_version_id=version.id,
        title_snapshot=market.title,
        price_snapshot=market.price_points,
        version_snapshot=version.version,
    )
    if existing is None:
        db.add(acquisition)
    elif not restoring:
        acquisition.market_version_id = version.id
        acquisition.title_snapshot = market.title
        acquisition.price_snapshot = market.price_points
        acquisition.version_snapshot = version.version
        acquisition.status = "pending"
        acquisition.error = None
    await db.flush()
    job = await enqueue(
        db,
        "market_acquire",
        {"acquisition_id": str(acquisition.id)},
        user_id=user.id,
        dedupe_key=f"market_acquire:{user.id}:{market.id}",
    )
    if job is None:
        raise HTTPException(status_code=409, detail="该课程正在获取")
    acquisition.created_by_job_id = job.id
    await db.commit()
    return _acquisition_out(acquisition)


@router.get("/market/acquisitions/{acquisition_id}", response_model=MarketAcquisitionOut)
async def get_market_acquisition(
    acquisition_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarketAcquisitionOut:
    acquisition = await db.get(MarketAcquisition, acquisition_id)
    if acquisition is None or acquisition.user_id != user.id:
        raise HTTPException(status_code=404, detail="获取记录不存在")
    return _acquisition_out(acquisition)
