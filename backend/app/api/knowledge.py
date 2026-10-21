import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.models import Course, KnowledgePoint, KpEdge, User
from app.schemas.knowledge import (
    GraphEdgeOut,
    GraphNodeOut,
    GraphOut,
    KnowledgePointOut,
    KnowledgePointUpdate,
)

router = APIRouter()


@router.get("/courses/{course_id}/kps", response_model=list[KnowledgePointOut])
async def list_knowledge_points(
    status: str | None = Query(default=None),
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgePoint]:
    stmt = select(KnowledgePoint).where(KnowledgePoint.course_id == course.id)
    if status is not None:
        stmt = stmt.where(KnowledgePoint.status == status)
    return list(
        (
            await db.execute(
                stmt.order_by(KnowledgePoint.importance.desc(), KnowledgePoint.title)
            )
        ).scalars()
    )


@router.patch("/kps/{kp_id}", response_model=KnowledgePointOut)
async def update_knowledge_point(
    kp_id: uuid.UUID,
    data: KnowledgePointUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgePoint:
    kp = await db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    course = await db.get(Course, kp.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="知识点不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(kp, field, value)
    await db.commit()
    return kp


@router.get("/courses/{course_id}/graph", response_model=GraphOut)
async def get_knowledge_graph(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> GraphOut:
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == course.id)
                .order_by(KnowledgePoint.title)
            )
        ).scalars()
    )
    edges = list(
        (
            await db.execute(
                select(KpEdge).where(KpEdge.course_id == course.id).order_by(KpEdge.relation)
            )
        ).scalars()
    )
    return GraphOut(
        nodes=[
            GraphNodeOut(
                id=kp.id,
                slug=kp.slug,
                title=kp.title,
                summary=kp.summary,
                status=kp.status,
                mastery_score=kp.mastery_score,
                importance=kp.importance,
                difficulty=kp.difficulty,
                projected_basic_at=kp.projected_basic_at,
                projected_full_at=kp.projected_full_at,
                primary_wiki_page_id=kp.primary_wiki_page_id,
            )
            for kp in kps
        ],
        edges=[
            GraphEdgeOut(
                id=edge.id,
                source=edge.src_kp_id,
                target=edge.dst_kp_id,
                relation=edge.relation,
            )
            for edge in edges
        ],
    )
