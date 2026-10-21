import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fsrs import FSRS, Card, Rating
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Course,
    KnowledgePoint,
    PlanItemKp,
    ReviewCard,
    ReviewLog,
    UserDailyStat,
    VocabEntry,
    WikiPage,
    utcnow,
)
from app.models.language import LanguageProfile
from app.schemas.review import (
    ReviewAnswerOut,
    ReviewCardStateOut,
    ReviewIntervalsOut,
    ReviewKpOut,
    ReviewQueueCardOut,
    ReviewQueueOut,
    ReviewVocabOut,
)
from app.services.points import POINTS_BASE, award_points, shanghai_day_bounds
from app.services.stats import increment_daily_stat, shanghai_date
from app.services.vocab import daily_new_words

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEDULER = FSRS(request_retention=0.9)


def card_to_dict(card: Card) -> dict:
    return card.to_dict()


def card_from_dict(data: dict) -> Card:
    return Card.from_dict(data)


def _sync_review_card(review_card: ReviewCard, card: Card) -> None:
    review_card.stability = card.stability
    review_card.difficulty = card.difficulty
    review_card.due = card.due
    review_card.state = int(card.state)
    review_card.reps = card.reps
    review_card.lapses = card.lapses
    review_card.last_review = card.last_review
    review_card.fsrs_card = card_to_dict(card)
    review_card.updated_at = utcnow()


async def complete_learn_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    completed_at: datetime,
) -> None:
    stat_date = shanghai_date(completed_at)
    await increment_daily_stat(db, user_id, stat_date, learn_items_done=1)
    await award_points(
        db,
        user_id=user_id,
        course_id=course_id,
        kind="item_done",
        base_amount=POINTS_BASE["item_done"],
        note=f"item:{item_id}",
        point_date=stat_date,
        ref_id=item_id,
    )
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .join(PlanItemKp, PlanItemKp.kp_id == KnowledgePoint.id)
                .where(PlanItemKp.plan_item_id == item_id)
            )
        ).scalars()
    )
    if not kps:
        return
    existing_kp_ids = set(
        (
            await db.execute(select(ReviewCard.kp_id).where(ReviewCard.kp_id.in_([kp.id for kp in kps])))
        ).scalars()
    )
    due_date = completed_at.astimezone(SHANGHAI).date() + timedelta(days=1)
    due = datetime.combine(due_date, time.min, SHANGHAI).astimezone(timezone.utc)
    for kp in kps:
        if kp.id not in existing_kp_ids:
            card = Card(due=due)
            db.add(
                ReviewCard(
                    course_id=course_id,
                    kp_id=kp.id,
                    due=card.due,
                    fsrs_card=card_to_dict(card),
                )
            )
        if kp.status == "pending":
            kp.status = "learning"
        if kp.first_learned_at is None:
            kp.first_learned_at = completed_at


def _interval_days(card: Card, now: datetime) -> ReviewIntervalsOut:
    schedules = SCHEDULER.repeat(card, now)

    def days(rating: Rating) -> float:
        value = max(0.0, (schedules[rating].card.due - now).total_seconds() / 86400)
        return round(value, 1)

    return ReviewIntervalsOut(
        again_days=days(Rating.Again),
        hard_days=days(Rating.Hard),
        good_days=days(Rating.Good),
        easy_days=days(Rating.Easy),
    )


async def _activate_vocab_for_today(db: AsyncSession, course: Course, now: datetime) -> int:
    if course.kind != "language":
        return 0
    profile = await db.get(LanguageProfile, course.id)
    if profile is None or profile.assessment_status != "active":
        return 0
    locked_course = (
        await db.execute(select(Course).where(Course.id == course.id).with_for_update())
    ).scalar_one()
    start, end = shanghai_day_bounds(shanghai_date(now))
    activated_today = (
        await db.execute(
            select(func.count()).select_from(VocabEntry).where(
                VocabEntry.course_id == locked_course.id,
                VocabEntry.activated_at >= start,
                VocabEntry.activated_at < end,
            )
        )
    ).scalar_one()
    remaining = max(0, daily_new_words(locked_course) - activated_today)
    if remaining == 0:
        return activated_today
    entries = list(
        (
            await db.execute(
                select(VocabEntry)
                .where(
                    VocabEntry.course_id == locked_course.id,
                    VocabEntry.status == "pending",
                )
                .order_by(VocabEntry.created_at, VocabEntry.id)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for entry in entries:
        entry.status = "active"
        entry.activated_at = now
        card = Card(due=now)
        db.add(
            ReviewCard(
                course_id=locked_course.id,
                vocab_id=entry.id,
                due=card.due,
                fsrs_card=card_to_dict(card),
            )
        )
    if entries:
        await db.flush()
    return activated_today + len(entries)


async def get_review_queue(
    db: AsyncSession, *, course: Course
) -> ReviewQueueOut:
    now = utcnow()
    activated_today = await _activate_vocab_for_today(db, course, now)
    due_count = (
        await db.execute(
            select(func.count()).select_from(ReviewCard).where(
                ReviewCard.course_id == course.id,
                ReviewCard.due <= now,
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(ReviewCard, KnowledgePoint, WikiPage.slug, VocabEntry)
            .outerjoin(KnowledgePoint, KnowledgePoint.id == ReviewCard.kp_id)
            .outerjoin(VocabEntry, VocabEntry.id == ReviewCard.vocab_id)
            .outerjoin(WikiPage, WikiPage.id == KnowledgePoint.primary_wiki_page_id)
            .where(ReviewCard.course_id == course.id, ReviewCard.due <= now)
            .order_by(ReviewCard.due)
            .limit(50)
        )
    ).all()
    reviewed_today = (
        await db.execute(
            select(UserDailyStat.review_count).where(
                UserDailyStat.user_id == course.user_id,
                UserDailyStat.date == shanghai_date(now),
            )
        )
    ).scalar_one_or_none() or 0
    cards = [
        ReviewQueueCardOut(
            card_id=review_card.id,
            due=review_card.due,
            state=review_card.state,
            reps=review_card.reps,
            kp=ReviewKpOut(
                id=kp.id,
                slug=kp.slug,
                title=kp.title,
                summary=kp.summary,
                recall_prompt=kp.recall_prompt,
                primary_wiki_slug=wiki_slug,
                status=kp.status,
            ) if kp is not None else None,
            vocab=ReviewVocabOut(
                id=vocab.id,
                word=vocab.word,
                phonetic=vocab.phonetic,
                meaning_md=vocab.meaning_md,
                example_md=vocab.example_md,
            ) if vocab is not None else None,
            intervals=_interval_days(card_from_dict(review_card.fsrs_card), now),
        )
        for review_card, kp, wiki_slug, vocab in rows
    ]
    if activated_today:
        await db.commit()
    return ReviewQueueOut(
        cards=cards,
        due_count=due_count,
        reviewed_today=reviewed_today,
        activated_today=activated_today,
    )


async def answer_review_card(
    db: AsyncSession,
    *,
    card_id: uuid.UUID,
    user_id: uuid.UUID,
    rating: int,
) -> ReviewAnswerOut | None:
    review_card = (
        await db.execute(
            select(ReviewCard).where(ReviewCard.id == card_id).with_for_update()
        )
    ).scalar_one_or_none()
    if review_card is None:
        return None
    course = await db.get(Course, review_card.course_id)
    if course is None or course.user_id != user_id:
        return None
    kp = await db.get(KnowledgePoint, review_card.kp_id) if review_card.kp_id is not None else None
    if review_card.kp_id is not None and kp is None:
        return None
    now = utcnow()
    old_card = card_from_dict(review_card.fsrs_card)
    state_before = card_to_dict(old_card)
    new_card = SCHEDULER.repeat(old_card, now)[Rating(rating)].card
    state_after = card_to_dict(new_card)
    review_log = ReviewLog(
        course_id=review_card.course_id,
        card_id=review_card.id,
        kp_id=review_card.kp_id,
        vocab_id=review_card.vocab_id,
        rating=rating,
        state_before=state_before,
        state_after=state_after,
        created_at=now,
    )
    db.add(review_log)
    _sync_review_card(review_card, new_card)
    if kp is not None:
        if rating == Rating.Again.value:
            kp.status = "needs_reinforce"
        elif (
            rating >= Rating.Good.value
            and kp.status == "needs_reinforce"
            and kp.basic_mastered_at is None
        ):
            kp.status = "learning"
        kp.mastery_score = round(min(new_card.stability, 21) / 21 * 100)
    review_date = shanghai_date(now)
    await increment_daily_stat(db, user_id, review_date, review_count=1)
    await db.flush()
    start, end = shanghai_day_bounds(review_date)
    review_count = (
        await db.execute(
            select(func.count()).select_from(ReviewLog).where(
                ReviewLog.card_id == review_card.id,
                ReviewLog.created_at >= start,
                ReviewLog.created_at < end,
            )
        )
    ).scalar_one()
    if review_count <= 1:
        await award_points(
            db,
            user_id=user_id,
            course_id=review_card.course_id,
            kind="review_done",
            base_amount=POINTS_BASE["review_done"],
            note=f"card:{review_card.id}:{review_date.isoformat()}",
            point_date=review_date,
            ref_id=review_card.id,
        )
    await db.commit()
    return ReviewAnswerOut(
        card=ReviewCardStateOut(
            id=review_card.id,
            stability=review_card.stability,
            difficulty=review_card.difficulty,
            due=review_card.due,
            state=review_card.state,
            reps=review_card.reps,
            lapses=review_card.lapses,
            last_review=review_card.last_review,
        ),
        kp_status=kp.status if kp is not None else None,
        next_due=review_card.due,
    )
