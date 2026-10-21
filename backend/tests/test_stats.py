import uuid
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Plan, PlanItem, StudySession, User, UserDailyStat, utcnow
from app.services import stats as stats_svc


async def test_heartbeat_accumulates_and_stale_session_restarts(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    user_id = logged_in.id
    response = await client.post(
        "/api/v1/courses",
        json={"name": "计时测试课", "goal_text": "验证心跳", "daily_minutes": 30},
    )
    assert response.status_code == 200
    course_id = response.json()["id"]
    base = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
    times = iter(
        [
            base,
            base + timedelta(seconds=60),
            base + timedelta(seconds=301),
            base + timedelta(seconds=301),
        ]
    )
    monkeypatch.setattr(stats_svc, "utcnow", lambda: next(times))

    first = await client.post(
        "/api/v1/stats/heartbeat", json={"course_id": course_id, "kind": "learn"}
    )
    second = await client.post(
        "/api/v1/stats/heartbeat", json={"course_id": course_id, "kind": "learn"}
    )
    stale = await client.post(
        "/api/v1/stats/heartbeat", json={"course_id": course_id, "kind": "learn"}
    )
    assert first.json()["today_study_seconds"] == 0
    assert second.json()["today_study_seconds"] == 60
    assert stale.json()["today_study_seconds"] == 60

    db.expire_all()
    sessions = list(
        (
            await db.execute(
                select(StudySession)
                .where(StudySession.user_id == user_id)
                .order_by(StudySession.started_at)
            )
        ).scalars()
    )
    assert len(sessions) == 2
    assert sessions[0].seconds == 60
    assert sessions[1].seconds == 0
    assert (
        await db.execute(select(func.sum(UserDailyStat.study_seconds)))
    ).scalar_one() == 60

    stats = await client.get("/api/v1/stats/me?days=30")
    assert stats.status_code == 200
    assert stats.json() == {
        "days": [
            {
                "date": "2026-07-16",
                "study_seconds": 60,
                "review_count": 0,
                "learn_items_done": 0,
            }
        ],
        "streak": 1,
    }


async def test_stats_366_and_completed_items_are_owned_and_sorted(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    first = (
        await client.post(
            "/api/v1/courses",
            json={"name": "已完成课程 A", "goal_text": "测试", "daily_minutes": 30},
        )
    ).json()
    second = (
        await client.post(
            "/api/v1/courses",
            json={"name": "已完成课程 B", "goal_text": "测试", "daily_minutes": 30},
        )
    ).json()
    outsider = User(
        username="stats-outsider",
        password_hash="not-used",
        nickname="其他用户",
    )
    db.add(outsider)
    await db.flush()
    outsider_course = Course(user_id=outsider.id, name="他人课程")
    db.add(outsider_course)
    await db.flush()
    now = utcnow()
    courses = [
        (uuid.UUID(first["id"]), "较早条目", now - timedelta(hours=2), 12),
        (uuid.UUID(second["id"]), "较新条目", now - timedelta(hours=1), 18),
        (outsider_course.id, "他人条目", now, 30),
    ]
    for index, (course_id, title, completed_at, actual_minutes) in enumerate(courses):
        plan = Plan(
            course_id=course_id,
            version=1,
            status="active",
            goal_summary="测试",
            daily_minutes=30,
            start_date=completed_at.date(),
            rationale_md="测试",
        )
        db.add(plan)
        await db.flush()
        db.add(
            PlanItem(
                plan_id=plan.id,
                course_id=course_id,
                seq=index + 1,
                scheduled_date=completed_at.date(),
                type="learn",
                title=title,
                est_minutes=10,
                status="done",
                completed_at=completed_at,
                actual_minutes=actual_minutes,
            )
        )
    await db.commit()

    stats = await client.get("/api/v1/stats/me?days=366")
    assert stats.status_code == 200
    assert (await client.get("/api/v1/stats/me?days=367")).status_code == 422
    completed = await client.get("/api/v1/me/completed-items")
    assert completed.status_code == 200
    assert [item["title"] for item in completed.json()] == ["较新条目", "较早条目"]
    assert [item["course_name"] for item in completed.json()] == [
        "已完成课程 B",
        "已完成课程 A",
    ]
