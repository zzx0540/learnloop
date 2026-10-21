import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WeaknessRecord, utcnow


@dataclass(frozen=True)
class WeaknessEvidence:
    kp_id: uuid.UUID
    kind: str
    severity: int
    description: str


@dataclass(frozen=True)
class AnswerEvidence:
    kp_id: uuid.UUID
    correct: bool


async def record_weaknesses(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    items: list[WeaknessEvidence],
) -> None:
    now = utcnow()
    cutoff = now - timedelta(days=14)
    for item in items:
        record = (
            await db.execute(
                select(WeaknessRecord)
                .where(
                    WeaknessRecord.course_id == course_id,
                    WeaknessRecord.kp_id == item.kp_id,
                    WeaknessRecord.kind == item.kind,
                    WeaknessRecord.status != "resolved",
                    WeaknessRecord.last_seen_at >= cutoff,
                )
                .order_by(WeaknessRecord.last_seen_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            db.add(
                WeaknessRecord(
                    course_id=course_id,
                    kp_id=item.kp_id,
                    kind=item.kind,
                    severity=item.severity,
                    description=item.description,
                    last_seen_at=now,
                )
            )
            continue
        record.seen_count += 1
        record.severity = max(record.severity, item.severity)
        record.last_seen_at = now
        record.correct_streak = 0
        record.status = "open"
        if item.description:
            record.description = item.description


async def apply_answer_evidence(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    items: list[AnswerEvidence],
) -> None:
    for item in items:
        records = list(
            (
                await db.execute(
                    select(WeaknessRecord)
                    .where(
                        WeaknessRecord.course_id == course_id,
                        WeaknessRecord.kp_id == item.kp_id,
                        WeaknessRecord.status != "resolved",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for record in records:
            if not item.correct:
                record.correct_streak = 0
                record.status = "open"
                continue
            record.correct_streak += 1
            record.status = "resolved" if record.correct_streak >= 2 else "improving"
