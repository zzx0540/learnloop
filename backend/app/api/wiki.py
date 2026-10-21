import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.models import Course, User, WikiLintFinding, WikiLogEntry, WikiPage, WikiPageRevision
from app.schemas.course import WikiLogOut, WikiPageBrief, WikiPageOut, WikiRevisionOut
from app.schemas.wiki_lint import WikiLintFindingOut, WikiLintFindingUpdate
from app.services import wiki as wiki_svc

router = APIRouter()


def _finding_out(finding: WikiLintFinding, page: WikiPage | None) -> WikiLintFindingOut:
    return WikiLintFindingOut(
        id=finding.id,
        page_id=finding.page_id,
        page_slug=page.slug if page else None,
        page_title=page.title if page else None,
        kind=finding.kind,
        detail_md=finding.detail_md,
        status=finding.status,
        created_at=finding.created_at,
    )


@router.get("/courses/{course_id}/wiki/pages", response_model=list[WikiPageBrief])
async def list_wiki_pages(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[WikiPage]:
    return await wiki_svc.list_pages(db, course.id)


@router.get("/courses/{course_id}/wiki/pages/{slug}", response_model=WikiPageOut)
async def get_wiki_page(
    slug: str, course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> WikiPage:
    page = await wiki_svc.get_page(db, course.id, slug)
    if page is None:
        raise HTTPException(status_code=404, detail="页面不存在")
    return page


@router.get("/courses/{course_id}/wiki/pages/{slug}/revisions", response_model=list[WikiRevisionOut])
async def get_wiki_page_revisions(
    slug: str, course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[WikiPageRevision]:
    page = await wiki_svc.get_page(db, course.id, slug)
    if page is None:
        raise HTTPException(status_code=404, detail="页面不存在")
    return list(
        (
            await db.execute(
                select(WikiPageRevision)
                .where(WikiPageRevision.page_id == page.id)
                .order_by(WikiPageRevision.version.desc())
                .limit(50)
            )
        ).scalars()
    )


@router.get("/courses/{course_id}/wiki/log", response_model=list[WikiLogOut])
async def get_wiki_log(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> list[WikiLogEntry]:
    return list(
        (
            await db.execute(
                select(WikiLogEntry)
                .where(WikiLogEntry.course_id == course.id)
                .order_by(WikiLogEntry.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )


@router.get(
    "/courses/{course_id}/wiki/lint-findings",
    response_model=list[WikiLintFindingOut],
)
async def list_lint_findings(
    status: str | None = None,
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[WikiLintFindingOut]:
    if status is not None and status not in {"open", "resolved", "dismissed"}:
        raise HTTPException(status_code=422, detail="体检项状态不正确")
    stmt = (
        select(WikiLintFinding, WikiPage)
        .outerjoin(WikiPage, WikiPage.id == WikiLintFinding.page_id)
        .where(WikiLintFinding.course_id == course.id)
    )
    if status:
        stmt = stmt.where(WikiLintFinding.status == status)
    rows = (
        await db.execute(stmt.order_by(WikiLintFinding.created_at.desc()))
    ).all()
    return [_finding_out(finding, page) for finding, page in rows]


@router.patch("/wiki/lint-findings/{finding_id}", response_model=WikiLintFindingOut)
async def update_lint_finding(
    finding_id: uuid.UUID,
    data: WikiLintFindingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiLintFindingOut:
    row = (
        await db.execute(
            select(WikiLintFinding, WikiPage)
            .outerjoin(WikiPage, WikiPage.id == WikiLintFinding.page_id)
            .join(Course, Course.id == WikiLintFinding.course_id)
            .where(WikiLintFinding.id == finding_id, Course.user_id == user.id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="体检项不存在")
    finding, page = row
    finding.status = data.status
    await db.commit()
    return _finding_out(finding, page)
