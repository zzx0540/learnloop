import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fsrs import Card, State
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Job,
    KnowledgePoint,
    Plan,
    PlanItem,
    PlanItemKp,
    ReviewCard,
    ReviewLog,
    User,
    UserDailyStat,
    WikiPage,
    utcnow,
)
from app.services import planner as planner_svc

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def _create_review_fixture(
    client: AsyncClient, db: AsyncSession
) -> tuple[dict, KnowledgePoint, PlanItem, WikiPage]:
    response = await client.post(
        "/api/v1/courses",
        json={"name": "复习测试课", "goal_text": "掌握间隔学习", "daily_minutes": 30},
    )
    assert response.status_code == 200, response.text
    course = response.json()
    course_id = uuid.UUID(course["id"])
    page = WikiPage(
        course_id=course_id,
        slug="spacing-effect",
        title="间隔效应",
        kind="concept",
        content_md="间隔学习能提升长期保持。",
    )
    kp = KnowledgePoint(
        course_id=course_id,
        slug="spacing-effect",
        title="间隔效应",
        summary="分散安排复习。",
        recall_prompt="为什么分散复习比集中复习更有利于长期保持？",
        primary_wiki_page_id=None,
    )
    plan = Plan(
        course_id=course_id,
        version=1,
        status="active",
        goal_summary="掌握间隔学习",
        daily_minutes=30,
        start_date=date.today(),
        rationale_md="测试计划",
    )
    db.add_all([page, kp, plan])
    await db.flush()
    kp.primary_wiki_page_id = page.id
    item = PlanItem(
        plan_id=plan.id,
        course_id=course_id,
        seq=1,
        scheduled_date=date.today(),
        type="learn",
        title="学习间隔效应",
        est_minutes=20,
    )
    db.add(item)
    await db.flush()
    db.add(PlanItemKp(plan_item_id=item.id, kp_id=kp.id))
    await db.commit()
    return course, kp, item, page


async def test_learn_done_creates_card_queue_and_review_transitions(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course, kp, item, page = await _create_review_fixture(client, db)
    user_id = logged_in.id
    kp_id = kp.id
    item_id = item.id
    expected_kp = {
        "id": str(kp.id),
        "slug": kp.slug,
        "title": kp.title,
        "summary": kp.summary,
        "recall_prompt": kp.recall_prompt,
        "primary_wiki_slug": page.slug,
        "status": "learning",
    }
    response = await client.patch(
        f"/api/v1/plan-items/{item_id}",
        json={"status": "done", "actual_minutes": 18},
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    card = (await db.execute(select(ReviewCard).where(ReviewCard.kp_id == kp_id))).scalar_one()
    card_id = card.id
    refreshed_kp = await db.get(KnowledgePoint, kp_id)
    assert refreshed_kp.status == "learning"
    assert refreshed_kp.first_learned_at is not None
    first_learned_at = refreshed_kp.first_learned_at
    expected_due_date = first_learned_at.astimezone(SHANGHAI).date() + timedelta(days=1)
    expected_due = datetime.combine(expected_due_date, time.min, SHANGHAI).astimezone(timezone.utc)
    assert card.due == expected_due
    assert card.state == State.New.value
    assert card.fsrs_card["state"] == State.New.value
    daily = await db.get(UserDailyStat, (user_id, first_learned_at.astimezone(SHANGHAI).date()))
    assert daily.learn_items_done == 1

    repeated = await client.patch(
        f"/api/v1/plan-items/{item_id}",
        json={"status": "done", "actual_minutes": 18},
    )
    assert repeated.status_code == 400
    db.expire_all()
    assert (await db.get(KnowledgePoint, kp_id)).first_learned_at == first_learned_at
    assert (
        await db.execute(select(func.count()).select_from(ReviewCard).where(ReviewCard.kp_id == kp_id))
    ).scalar_one() == 1

    detail = await client.get(f"/api/v1/plan-items/{item_id}")
    assert detail.status_code == 200
    assert detail.json()["kps"] == [expected_kp]

    card = await db.get(ReviewCard, card_id)
    card.due = utcnow() - timedelta(minutes=1)
    await db.commit()
    queue = await client.get(f"/api/v1/courses/{course['id']}/reviews/queue")
    assert queue.status_code == 200, queue.text
    queue_data = queue.json()
    assert queue_data["due_count"] == 1
    assert queue_data["reviewed_today"] == 0
    assert set(queue_data["cards"][0]["intervals"]) == {
        "again_days",
        "hard_days",
        "good_days",
        "easy_days",
    }

    good = await client.post(f"/api/v1/reviews/{card_id}/answer", json={"rating": 3})
    assert good.status_code == 200, good.text
    assert datetime.fromisoformat(good.json()["next_due"]) > utcnow()
    db.expire_all()
    reviewed_card = await db.get(ReviewCard, card_id)
    assert reviewed_card.stability > 0
    log = (
        await db.execute(
            select(ReviewLog).where(ReviewLog.card_id == card_id).order_by(ReviewLog.created_at)
        )
    ).scalars().first()
    assert log.state_before["stability"] == 0
    assert log.state_after["stability"] == reviewed_card.stability

    again = await client.post(f"/api/v1/reviews/{card_id}/answer", json={"rating": 1})
    assert again.status_code == 200
    assert again.json()["kp_status"] == "needs_reinforce"
    back_to_learning = await client.post(
        f"/api/v1/reviews/{card_id}/answer", json={"rating": 3}
    )
    assert back_to_learning.status_code == 200
    assert back_to_learning.json()["kp_status"] == "learning"
    db.expire_all()
    today_stat = await db.get(UserDailyStat, (user_id, datetime.now(SHANGHAI).date()))
    assert today_stat.review_count == 3


async def test_project_mastery_prefers_real_card_state(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course, kp, _, _ = await _create_review_fixture(client, db)
    kp_id = kp.id
    now = utcnow()
    fsrs_card = Card(
        due=now + timedelta(days=1),
        stability=5,
        difficulty=5,
        reps=3,
        state=State.Review,
        last_review=now - timedelta(days=1),
    )
    db.add(
        ReviewCard(
            course_id=uuid.UUID(course["id"]),
            kp_id=kp_id,
            stability=5,
            difficulty=5,
            due=fsrs_card.due,
            state=State.Review.value,
            reps=3,
            last_review=fsrs_card.last_review,
            fsrs_card=fsrs_card.to_dict(),
        )
    )
    job = Job(
        kind="project_mastery",
        payload={"course_id": course["id"]},
        course_id=uuid.UUID(course["id"]),
    )
    db.add(job)
    await db.flush()
    await planner_svc.handle_project_mastery(db, job)
    db.expire_all()
    projected = await db.get(KnowledgePoint, kp_id)
    assert projected.projected_basic_at == datetime.now(SHANGHAI).date()
    assert projected.projected_full_at > projected.projected_basic_at
