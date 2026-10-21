import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import runtime as runtime_module
from app.agents.runtime import AgentRuntime, BudgetExceeded
from app.models import AppSetting, Course, LlmCallLog, User


async def test_budget_exempts_admin_courses_only(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(runtime_module, "SessionLocal", session_factory)

    admin = User(username="budget_admin", password_hash="x", nickname="admin", role="admin")
    student = User(username="budget_student", password_hash="x", nickname="student")
    db.add_all([admin, student])
    await db.flush()
    admin_course = Course(user_id=admin.id, name="admin course")
    blocked_course = Course(user_id=student.id, name="blocked course")
    allowed_course = Course(user_id=student.id, name="allowed course")
    db.add_all([admin_course, blocked_course, allowed_course])
    await db.flush()
    db.add(AppSetting(key="llm_daily_token_budget_per_course", value={"v": 10}))
    for course in (admin_course, blocked_course):
        db.add(
            LlmCallLog(
                agent="test",
                operation="budget",
                model="test-model",
                tier="low",
                course_id=course.id,
                prompt_tokens=10,
                completion_tokens=1,
            )
        )
    await db.commit()

    runtime = AgentRuntime.__new__(AgentRuntime)
    await runtime._check_budget(admin_course.id)
    await runtime._check_budget(allowed_course.id)
    await runtime._check_budget(None)
    with pytest.raises(BudgetExceeded):
        await runtime._check_budget(blocked_course.id)
