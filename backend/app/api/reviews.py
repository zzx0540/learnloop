import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.models import Course, User
from app.schemas.review import ReviewAnswerCreate, ReviewAnswerOut, ReviewQueueOut
from app.services.reviews import answer_review_card, get_review_queue

router = APIRouter()


@router.get("/courses/{course_id}/reviews/queue", response_model=ReviewQueueOut)
async def review_queue(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> ReviewQueueOut:
    return await get_review_queue(db, course=course)


@router.post("/reviews/{card_id}/answer", response_model=ReviewAnswerOut)
async def answer_review(
    card_id: uuid.UUID,
    data: ReviewAnswerCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewAnswerOut:
    result = await answer_review_card(db, card_id=card_id, user_id=user.id, rating=data.rating)
    if result is None:
        raise HTTPException(status_code=404, detail="复习卡不存在")
    return result
