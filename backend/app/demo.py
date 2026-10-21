"""Original, pre-authored demo content. No model calls or real user data."""

import hashlib
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fsrs import Card
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.core.security import hash_password
from app.models import (
    Course,
    Document,
    KnowledgePoint,
    KpEdge,
    KpWikiLink,
    Plan,
    PlanItem,
    PlanItemKp,
    ReviewCard,
    User,
    UserDailyStat,
    WikiPage,
    utcnow,
)

DEMO_PASSWORD = "learnloop-demo-only"
LESSONS = [
    (
        "select",
        "选择你需要的列",
        "SELECT name, minutes FROM study_sessions;",
        "SELECT 决定结果中有哪些列，FROM 指定来源表。先明确问题需要什么，再选择列。",
        "SELECT 与 FROM 分别负责什么？",
    ),
    (
        "where",
        "筛选符合条件的记录",
        "SELECT name, minutes FROM study_sessions WHERE minutes >= 20;",
        "WHERE 对每一行应用条件。此例只保留学习时长至少20分钟的记录，不会修改原表。",
        "如果想只看至少20分钟的记录，条件怎么写？",
    ),
    (
        "group-by",
        "按类别汇总",
        "SELECT topic, SUM(minutes) FROM study_sessions GROUP BY topic;",
        "GROUP BY 将同一类别的行放在一起；SUM 对每组求和。分组后结果是一组一行。",
        "为什么 GROUP BY 后的行数可能比原表少？",
    ),
]


def validate_demo_target(url: str) -> None:
    target = make_url(url)
    if target.database != "learnloop_demo" or target.username != "learnloop_demo" or target.query:
        raise RuntimeError("Demo requires its own learnloop_demo database and role, without URL options.")


async def ensure_demo_index(db, course_id) -> None:
    index = (
        await db.execute(
            select(WikiPage).where(WikiPage.course_id == course_id, WikiPage.slug == "wiki-index")
        )
    ).scalar_one_or_none()
    if index is None:
        body = "# SQL 学习路线\n\n原创预置演示：先选择列，再筛选行，最后按类别汇总。\n\n"
        body += "\n".join(f"- [{title}](wiki:{slug})" for slug, title, *_ in LESSONS)
        body += "\n\n示例汇总：$20 + 30 = 50$ 分钟。\n\n> 此页面为人工编写的演示内容，不是模型输出。"
        db.add(
            WikiPage(
                course_id=course_id,
                slug="wiki-index",
                title="SQL 学习路线",
                kind="index",
                content_md=body,
                out_links=[item[0] for item in LESSONS],
                updated_by="demo",
            )
        )


async def seed_demo(db) -> None:
    settings = get_settings()
    validate_demo_target(settings.database_url)
    existing = (await db.execute(select(User))).scalars().all()
    if existing:
        if (
            len(existing) != 1
            or existing[0].username != "demo"
            or existing[0].settings.get("public_demo") != 1
        ):
            raise RuntimeError("Demo refuses to seed an existing non-demo database.")
        course = (await db.execute(select(Course).where(Course.user_id == existing[0].id))).scalar_one()
        await ensure_demo_index(db, course.id)
        await db.commit()
        return
    if (await db.execute(select(func.count()).select_from(Course))).scalar_one():
        raise RuntimeError("Demo requires an empty database.")
    now = utcnow()
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    user = User(
        username="demo",
        password_hash=hash_password(DEMO_PASSWORD),
        nickname="演示学习者",
        role="admin",
        settings={"public_demo": 1},
    )
    db.add(user)
    await db.flush()
    course = Course(
        user_id=user.id,
        name="SQL 入门：从查询到汇总",
        status="active",
        daily_minutes=20,
        goal_text="用三节短课理解 SELECT、WHERE 与 GROUP BY；这是人工预置演示。",
        description="原创虚构示例，无真实用户数据，不调用 AI。",
    )
    db.add(course)
    await db.flush()
    source = "# SQL 入门：从查询到汇总\n\n原创演示教材。示例数据均为虚构。\n\n"
    for _, title, query, explanation, question in LESSONS:
        source += f"## {title}\n\n{explanation}\n\n```sql\n{query}\n```\n\n自测：{question}\n\n"
    path = Path(settings.files_dir) / str(course.id) / "raw" / "demo" / "sql-basics.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    doc = Document(
        course_id=course.id,
        original_name="sql-basics.md",
        stored_path=str(path),
        parsed_md_path=str(path),
        mime="text/markdown",
        size_bytes=len(source.encode()),
        sha256=hashlib.sha256(source.encode()).hexdigest(),
        status="ingested",
    )
    db.add(doc)
    plan = Plan(
        course_id=course.id,
        version=1,
        status="active",
        goal_summary="三天掌握基础查询并安排复习",
        daily_minutes=20,
        start_date=today,
        end_date_estimate=today + timedelta(days=2),
        rationale_md="这是人工编写的演示计划，展示学习环的页面与数据关联，不代表 AI 实际生成结果。",
    )
    db.add(plan)
    await db.flush()
    await ensure_demo_index(db, course.id)
    previous = None
    for index, (slug, title, query, explanation, question) in enumerate(LESSONS):
        content = (
            f"# {title}\n\n{explanation}\n\n```sql\n{query}\n```\n\n"
            f"## 试着回忆\n\n{question}\n\n> 人工预置演示内容；来源：原创 sql-basics.md。"
        )
        page = WikiPage(
            course_id=course.id,
            slug=slug,
            title=title,
            content_md=content,
            source_refs=[{"document_id": str(doc.id), "heading": title}],
            updated_by="demo",
        )
        db.add(page)
        await db.flush()
        kp = KnowledgePoint(
            course_id=course.id,
            slug=slug,
            title=title,
            summary=explanation,
            recall_prompt=question,
            primary_wiki_page_id=page.id,
            status="learning",
            mastery_score=30,
            importance=4,
            difficulty=2,
            created_by="demo",
        )
        db.add(kp)
        await db.flush()
        db.add(KpWikiLink(kp_id=kp.id, wiki_page_id=page.id, role="primary"))
        if previous:
            db.add(
                KpEdge(
                    course_id=course.id,
                    src_kp_id=previous.id,
                    dst_kp_id=kp.id,
                    relation="prerequisite",
                    created_by="demo",
                )
            )
        previous = kp
        item = PlanItem(
            plan_id=plan.id,
            course_id=course.id,
            seq=index + 1,
            scheduled_date=today + timedelta(days=index),
            type="learn",
            title=title,
            description_md=content,
            est_minutes=20,
        )
        db.add(item)
        await db.flush()
        db.add(PlanItemKp(plan_item_id=item.id, kp_id=kp.id))
        card = Card(due=now - timedelta(hours=1))
        db.add(ReviewCard(course_id=course.id, kp_id=kp.id, due=card.due, fsrs_card=card.to_dict()))
    for offset, minutes in [(1, 20), (2, 15), (4, 25), (6, 10)]:
        db.add(
            UserDailyStat(
                user_id=user.id,
                date=today - timedelta(days=offset),
                study_seconds=minutes * 60,
                review_count=3,
                learn_items_done=1,
            )
        )
    await db.commit()
