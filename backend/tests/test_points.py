import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from fsrs import Card
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models import (
    Plan,
    PlanItem,
    PointsLedger,
    Quiz,
    QuizAttempt,
    Redemption,
    ReviewCard,
    Reward,
    User,
    UserDailyStat,
)
from app.services import reviews as reviews_svc
from app.services.points import (
    add_ledger_entry,
    award_points,
    award_quiz_points,
    current_streak,
    multiplier,
    points_balance,
)
from app.services.stats import award_study_buckets


async def _create_course(client: AsyncClient, name: str = "积分测试课") -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={"name": name, "goal_text": "验证积分闭环", "daily_minutes": 30},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_streak_states_and_multiplier_cap(
    db: AsyncSession, logged_in: User
) -> None:
    today = date(2026, 7, 17)
    db.add_all(
        [
            UserDailyStat(user_id=logged_in.id, date=today - timedelta(days=4), review_count=1),
            UserDailyStat(user_id=logged_in.id, date=today - timedelta(days=2), study_seconds=60),
            UserDailyStat(user_id=logged_in.id, date=today - timedelta(days=1), learn_items_done=1),
        ]
    )
    await db.flush()
    assert await current_streak(db, logged_in.id, today) == 2

    db.add(UserDailyStat(user_id=logged_in.id, date=today, study_seconds=1))
    await db.flush()
    assert await current_streak(db, logged_in.id, today) == 3

    await db.execute(
        UserDailyStat.__table__.delete().where(UserDailyStat.user_id == logged_in.id)
    )
    db.add_all(
        [
            UserDailyStat(user_id=logged_in.id, date=today - timedelta(days=offset), study_seconds=1)
            for offset in range(12)
        ]
    )
    await db.flush()
    assert await current_streak(db, logged_in.id, today) == 12
    assert multiplier(12) == 2.0


async def test_points_summary_preserves_streak_but_previews_today_multiplier(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    today = date.today()
    db.add_all(
        [
            UserDailyStat(user_id=logged_in.id, date=today - timedelta(days=2), study_seconds=1),
            UserDailyStat(user_id=logged_in.id, date=today - timedelta(days=1), study_seconds=1),
        ]
    )
    await db.commit()
    summary = await client.get("/api/v1/me/points")
    assert summary.status_code == 200
    assert summary.json()["streak"] == 2
    assert summary.json()["today_multiplier"] == 1.3


async def test_study_buckets_and_unique_note_are_idempotent(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    today = date(2026, 7, 17)
    db.add(UserDailyStat(user_id=logged_in.id, date=today, study_seconds=1300))
    await db.flush()

    await award_study_buckets(
        db,
        user_id=logged_in.id,
        course_id=course_id,
        stat_date=today,
        previous_total=0,
        total=1300,
    )
    await award_study_buckets(
        db,
        user_id=logged_in.id,
        course_id=course_id,
        stat_date=today,
        previous_total=0,
        total=1300,
    )
    rows = list(
        (
            await db.execute(
                select(PointsLedger)
                .where(PointsLedger.user_id == logged_in.id)
                .order_by(PointsLedger.note)
            )
        ).scalars()
    )
    assert [(row.kind, row.amount, row.multiplier, row.note) for row in rows] == [
        ("study_minutes", 6, 1.1, "2026-07-17:1"),
        ("study_minutes", 6, 1.1, "2026-07-17:2"),
    ]
    assert await points_balance(db, logged_in.id) == 12

    first = await award_points(
        db,
        user_id=logged_in.id,
        course_id=course_id,
        kind="item_done",
        base_amount=10,
        note="item:fixed",
        point_date=today,
    )
    second = await award_points(
        db,
        user_id=logged_in.id,
        course_id=course_id,
        kind="item_done",
        base_amount=10,
        note="item:fixed",
        point_date=today,
    )
    assert first is not None and second is None
    assert await points_balance(db, logged_in.id) == 23


async def test_item_review_and_quiz_award_paths(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client, "四路径积分课")
    course_id = uuid.UUID(course["id"])
    plan = Plan(
        course_id=course_id,
        version=1,
        status="active",
        goal_summary="测试",
        daily_minutes=30,
        start_date=date.today(),
        rationale_md="测试",
    )
    db.add(plan)
    await db.flush()
    item = PlanItem(
        plan_id=plan.id,
        course_id=course_id,
        seq=1,
        scheduled_date=date.today(),
        type="learn",
        title="完成学习条目",
        est_minutes=10,
    )
    db.add(item)
    await db.commit()
    completed = await client.patch(
        f"/api/v1/plan-items/{item.id}",
        json={"status": "done", "actual_minutes": 8},
    )
    assert completed.status_code == 200, completed.text

    now = datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc)
    card_data = Card(due=now)
    card = ReviewCard(
        course_id=course_id,
        kp_id=uuid.uuid4(),
        due=card_data.due,
        fsrs_card=card_data.to_dict(),
    )
    from app.models import KnowledgePoint

    db.add(
        KnowledgePoint(
            id=card.kp_id,
            course_id=course_id,
            slug="points-review",
            title="积分复习知识点",
        )
    )
    db.add(card)
    await db.commit()
    monkeypatch.setattr(reviews_svc, "utcnow", lambda: now)
    assert await reviews_svc.answer_review_card(
        db, card_id=card.id, user_id=logged_in.id, rating=3
    )
    assert await reviews_svc.answer_review_card(
        db, card_id=card.id, user_id=logged_in.id, rating=3
    )
    next_day = now + timedelta(days=1)
    monkeypatch.setattr(reviews_svc, "utcnow", lambda: next_day)
    assert await reviews_svc.answer_review_card(
        db, card_id=card.id, user_id=logged_in.id, rating=3
    )

    daily_quiz = Quiz(
        course_id=course_id,
        kind="daily",
        title="每日积分卷",
        scheduled_date=date.today(),
        status="ready",
    )
    stage_quiz = Quiz(
        course_id=course_id,
        kind="stage",
        title="阶段积分卷",
        scheduled_date=date.today(),
        status="ready",
    )
    db.add_all([daily_quiz, stage_quiz])
    await db.flush()
    daily_attempt = QuizAttempt(
        course_id=course_id,
        user_id=logged_in.id,
        quiz_id=daily_quiz.id,
        kind="daily",
        status="graded",
        total_score=1,
        max_score=2,
        graded_at=next_day,
    )
    stage_attempt = QuizAttempt(
        course_id=course_id,
        user_id=logged_in.id,
        quiz_id=stage_quiz.id,
        kind="stage",
        status="graded",
        total_score=1,
        max_score=2,
        graded_at=next_day,
    )
    db.add_all([daily_attempt, stage_attempt])
    await db.flush()
    await award_quiz_points(db, daily_attempt)
    await award_quiz_points(db, stage_attempt)
    await award_quiz_points(db, stage_attempt)
    await db.commit()

    rows = list(
        (
            await db.execute(
                select(PointsLedger)
                .where(PointsLedger.user_id == logged_in.id)
                .order_by(PointsLedger.created_at, PointsLedger.id)
            )
        ).scalars()
    )
    by_kind: dict[str, list[PointsLedger]] = {}
    for row in rows:
        by_kind.setdefault(row.kind, []).append(row)
    assert by_kind["item_done"][0].amount == 11
    assert [row.multiplier for row in by_kind["review_done"]] == [1.1, 1.2]
    assert len(by_kind["review_done"]) == 2
    assert [row.amount for row in by_kind["quiz_score"]] == [12, 24]
    assert len(by_kind["quiz_score"]) == 2


async def test_redeem_balance_snapshots_and_concurrent_lock(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    await add_ledger_entry(
        db,
        user_id=logged_in.id,
        course_id=None,
        kind="item_done",
        amount=100,
        applied_multiplier=1.0,
        note="seed:redeem",
    )
    first_reward = Reward(title="一小时自由安排", description="兑换测试", cost=30)
    db.add(first_reward)
    await db.commit()

    redeemed = await client.post(f"/api/v1/rewards/{first_reward.id}/redeem")
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["title_snapshot"] == "一小时自由安排"
    assert redeemed.json()["cost_snapshot"] == 30
    ledger = (
        await db.execute(
            select(PointsLedger).where(
                PointsLedger.user_id == logged_in.id,
                PointsLedger.kind == "redeem",
            )
        )
    ).scalar_one()
    assert ledger.amount == -30 and ledger.multiplier == 1.0
    assert await points_balance(db, logged_in.id) == 70

    expensive = Reward(title="余额不足奖励", cost=80)
    concurrent = Reward(title="并发兑换奖励", cost=60)
    db.add_all([expensive, concurrent])
    await db.commit()
    insufficient = await client.post(f"/api/v1/rewards/{expensive.id}/redeem")
    assert insufficient.status_code == 400
    assert insufficient.json()["detail"] == "积分不足"

    results = await asyncio.gather(
        client.post(f"/api/v1/rewards/{concurrent.id}/redeem"),
        client.post(f"/api/v1/rewards/{concurrent.id}/redeem"),
    )
    assert sorted(response.status_code for response in results) == [200, 400]
    assert await points_balance(db, logged_in.id) == 10
    assert (
        await db.execute(
            select(func.count()).select_from(Redemption).where(
                Redemption.user_id == logged_in.id,
                Redemption.reward_id == concurrent.id,
            )
        )
    ).scalar_one() == 1

    summary = await client.get("/api/v1/me/points")
    assert summary.status_code == 200
    assert summary.json()["balance"] == 10
    assert len((await client.get("/api/v1/me/redemptions")).json()) == 2


async def test_admin_rewards_settings_and_one_time_password(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    user_id = logged_in.id
    logged_in.role = "admin"
    await db.commit()
    settings = await client.get("/api/v1/admin/settings")
    assert settings.status_code == 200
    assert "llm_daily_token_budget_per_course" in settings.json()
    assert (await client.patch("/api/v1/admin/settings", json={"unknown": 1})).status_code == 400
    assert (
        await client.patch("/api/v1/admin/settings", json={"upload_max_mb": "100"})
    ).status_code == 400
    updated = await client.patch(
        "/api/v1/admin/settings", json={"upload_max_mb": 120}
    )
    assert updated.status_code == 200 and updated.json()["upload_max_mb"] == 120

    reward = await client.post(
        "/api/v1/admin/rewards",
        json={"title": "管理员奖励", "description": "测试", "cost": 50},
    )
    assert reward.status_code == 200
    patched = await client.patch(
        f"/api/v1/admin/rewards/{reward.json()['id']}", json={"is_active": False}
    )
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    assert len((await client.get("/api/v1/admin/rewards")).json()) == 1

    reset = await client.post(f"/api/v1/admin/users/{user_id}/reset-password")
    assert reset.status_code == 200
    password = reset.json()["password"]
    assert len(password) == 12
    db.expire_all()
    user = await db.get(User, user_id)
    assert verify_password(password, user.password_hash)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
