import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuizAttempt, utcnow


async def exam_chat_locked(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return (
        await db.execute(
            select(QuizAttempt.id)
            .where(
                QuizAttempt.user_id == user_id,
                QuizAttempt.kind == "formal_exam",
                QuizAttempt.status == "in_progress",
                QuizAttempt.deadline_at > utcnow(),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None
