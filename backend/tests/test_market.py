import hashlib
import json
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Course,
    Document,
    DocumentImage,
    Job,
    KnowledgePoint,
    KpEdge,
    KpWikiLink,
    MarketAcquisition,
    MarketCourse,
    MarketCourseVersion,
    Plan,
    PointsLedger,
    ReviewCard,
    User,
    VocabEntry,
    WikiPage,
    WikiPageRevision,
)
from app.services.market import handle_market_acquire, handle_market_publish
from app.services.points import points_balance
from app.services.storage import course_dir


async def _seed_source_course(
    client: AsyncClient,
    db: AsyncSession,
    user: User,
    *,
    name: str,
) -> Course:
    response = await client.post(
        "/api/v1/courses",
        json={"name": name, "goal_text": "掌握课程内容", "daily_minutes": 40},
    )
    assert response.status_code == 200, response.text
    course = await db.get(Course, uuid.UUID(response.json()["id"]))
    assert course is not None
    course.description = "一门可发布的测试课程"
    root = course_dir(course.id)
    original = root / "raw" / "source" / "original.md"
    parsed = root / "raw" / "source" / "parsed.md"
    image = root / "images" / "source-image.png"
    original.parent.mkdir(parents=True, exist_ok=True)
    image.parent.mkdir(parents=True, exist_ok=True)
    original.write_text("# 原始资料\n\n课程正文", encoding="utf-8")
    image.write_bytes(b"market-image")
    parsed.write_text(
        f"# 解析资料\n\n![图](/api/v1/files/{course.id}/images/source-image.png)",
        encoding="utf-8",
    )
    doc = Document(
        course_id=course.id,
        original_name="source.md",
        stored_path=str(original),
        mime="text/markdown",
        size_bytes=original.stat().st_size,
        sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
        status="ingested",
        parsed_md_path=str(parsed),
        page_count=1,
    )
    db.add(doc)
    await db.flush()
    db.add(
        DocumentImage(
            course_id=course.id,
            document_id=doc.id,
            stored_path=str(image),
            caption="示意图",
        )
    )
    page = WikiPage(
        course_id=course.id,
        slug="market-wiki",
        title="市场知识",
        kind="concept",
        content_md=(
            f"正文\n\n![图](/api/v1/files/{course.id}/images/source-image.png)"
        ),
        out_links=[],
        source_refs=[{"document_id": str(doc.id), "locator": "全文"}],
        version=3,
    )
    db.add(page)
    await db.flush()
    first = KnowledgePoint(
        course_id=course.id,
        slug="market-kp-1",
        title="知识点一",
        summary="摘要一",
        recall_prompt="请回忆知识点一",
        primary_wiki_page_id=page.id,
        importance=4,
        difficulty=3,
        status="fully_mastered",
        mastery_score=99,
    )
    second = KnowledgePoint(
        course_id=course.id,
        slug="market-kp-2",
        title="知识点二",
        summary="摘要二",
        primary_wiki_page_id=page.id,
        importance=3,
        difficulty=2,
        status="learning",
        mastery_score=50,
    )
    db.add_all([first, second])
    await db.flush()
    db.add_all(
        [
            KpEdge(
                course_id=course.id,
                src_kp_id=first.id,
                dst_kp_id=second.id,
                relation="prerequisite",
            ),
            KpWikiLink(kp_id=first.id, wiki_page_id=page.id, role="primary"),
            KpWikiLink(kp_id=second.id, wiki_page_id=page.id, role="mention"),
            VocabEntry(
                course_id=course.id,
                word="loop",
                phonetic="/luːp/",
                meaning_md="循环",
                example_md="A learning loop.",
                status="active",
            ),
        ]
    )
    await db.commit()
    return course


async def _publish(
    client: AsyncClient,
    db: AsyncSession,
    source: Course,
    *,
    price: int,
) -> tuple[MarketCourse, MarketCourseVersion]:
    created = await client.post(
        "/api/v1/admin/market/courses",
        json={
            "source_course_id": str(source.id),
            "title": source.name,
            "description": "市场测试简介",
            "price_points": price,
        },
    )
    assert created.status_code == 200, created.text
    market_id = uuid.UUID(created.json()["id"])
    publish = await client.post(f"/api/v1/admin/market/courses/{market_id}/publish")
    assert publish.status_code == 202, publish.text
    job = await db.get(Job, uuid.UUID(publish.json()["job_id"]))
    assert job is not None
    await handle_market_publish(db, job)
    market = await db.get(MarketCourse, market_id)
    version = (
        await db.execute(
            select(MarketCourseVersion).where(
                MarketCourseVersion.market_course_id == market_id,
                MarketCourseVersion.version == 1,
            )
        )
    ).scalar_one()
    assert market is not None and market.status == "published"
    assert version.status == "ready"
    return market, version


async def _register_buyer(client: AsyncClient, db: AsyncSession, username: str) -> User:
    await client.post("/api/v1/auth/logout")
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "password123",
            "nickname": "购买者",
            "invite_code": "TEST",
        },
    )
    assert response.status_code == 200, response.text
    return (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one()


async def _run_acquisition(
    client: AsyncClient,
    db: AsyncSession,
    market_id: uuid.UUID,
) -> MarketAcquisition:
    response = await client.post(f"/api/v1/market/courses/{market_id}/acquire")
    assert response.status_code == 202, response.text
    job = await db.get(Job, uuid.UUID(response.json()["job_id"]))
    assert job is not None
    await handle_market_acquire(db, job)
    acquisition = await db.get(MarketAcquisition, uuid.UUID(response.json()["id"]))
    assert acquisition is not None
    return acquisition


async def test_free_market_clone_is_private_and_resets_user_state(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    logged_in.role = "admin"
    await db.commit()
    source = await _seed_source_course(client, db, logged_in, name="免费市场课")
    market, version = await _publish(client, db, source, price=0)
    manifest_text = json.dumps(version.manifest, ensure_ascii=False)
    assert str(source.id) not in manifest_text
    assert "document_id" not in manifest_text

    buyer = await _register_buyer(client, db, "marketbuyer1")
    listing = await client.get("/api/v1/market/courses")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["title"] == "免费市场课"
    assert listing.json()["items"][0]["price_points"] == 0
    filtered = await client.get("/api/v1/market/courses?kind=language")
    assert filtered.status_code == 200
    assert filtered.json() == {"items": [], "total": 0}
    acquisition = await _run_acquisition(client, db, market.id)
    assert acquisition.status == "completed"
    assert acquisition.target_course_id is not None
    assert await points_balance(db, buyer.id) == 0

    target = await db.get(Course, acquisition.target_course_id)
    assert target is not None
    assert target.user_id == buyer.id
    assert target.status == "onboarding"
    assert target.target_date is None
    target_documents = list(
        (
            await db.execute(
                select(Document).where(Document.course_id == target.id)
            )
        ).scalars()
    )
    assert len(target_documents) == 1
    assert target_documents[0].is_market_copy is True
    assert str(source.id) not in target_documents[0].stored_path
    parsed = Path(target_documents[0].parsed_md_path or "").read_text(encoding="utf-8")
    assert str(target.id) in parsed
    assert str(source.id) not in parsed

    target_page = (
        await db.execute(
            select(WikiPage).where(
                WikiPage.course_id == target.id,
                WikiPage.slug == "market-wiki",
            )
        )
    ).scalar_one()
    assert target_page.version == 1
    assert str(target.id) in target_page.content_md
    assert str(source.id) not in target_page.content_md
    assert target_page.source_refs[0]["document_id"] == str(target_documents[0].id)
    revisions = int(
        (
            await db.execute(
                select(func.count())
                .select_from(WikiPageRevision)
                .where(WikiPageRevision.page_id == target_page.id)
            )
        ).scalar_one()
    )
    assert revisions == 1
    target_kps = list(
        (
            await db.execute(
                select(KnowledgePoint).where(KnowledgePoint.course_id == target.id)
            )
        ).scalars()
    )
    assert len(target_kps) == 2
    assert all(kp.status == "pending" and kp.mastery_score == 0 for kp in target_kps)
    target_vocab = (
        await db.execute(select(VocabEntry).where(VocabEntry.course_id == target.id))
    ).scalar_one()
    assert target_vocab.status == "pending" and target_vocab.activated_at is None
    assert (
        await db.execute(select(func.count()).select_from(Plan).where(Plan.course_id == target.id))
    ).scalar_one() == 0
    assert (
        await db.execute(
            select(func.count()).select_from(ReviewCard).where(ReviewCard.course_id == target.id)
        )
    ).scalar_one() == 0

    target_page_content = target_page.content_md
    target_file_sha = hashlib.sha256(
        Path(target_documents[0].parsed_md_path or "").read_bytes()
    ).hexdigest()
    source_page = (
        await db.execute(
            select(WikiPage).where(
                WikiPage.course_id == source.id,
                WikiPage.slug == "market-wiki",
            )
        )
    ).scalar_one()
    source_page.content_md += "\n\nv2 新内容"
    second_version = MarketCourseVersion(
        market_course_id=market.id,
        version=2,
        status="building",
    )
    db.add(second_version)
    await db.flush()
    second_job = Job(
        kind="market_publish",
        payload={
            "market_course_id": str(market.id),
            "market_version_id": str(second_version.id),
        },
        course_id=source.id,
        user_id=logged_in.id,
    )
    db.add(second_job)
    await db.commit()
    await handle_market_publish(db, second_job)
    await db.refresh(target_page)
    assert target_page.content_md == target_page_content
    assert hashlib.sha256(
        Path(target_documents[0].parsed_md_path or "").read_bytes()
    ).hexdigest() == target_file_sha

    assert (await client.get(f"/api/v1/courses/{source.id}")).status_code == 404
    assert (await client.get(f"/api/v1/courses/{target.id}")).status_code == 200


async def test_paid_acquire_uses_price_snapshot_and_is_idempotent(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    logged_in.role = "admin"
    await db.commit()
    source = await _seed_source_course(client, db, logged_in, name="积分市场课")
    market, _ = await _publish(client, db, source, price=7)
    buyer = await _register_buyer(client, db, "marketbuyer2")
    db.add(
        PointsLedger(
            user_id=buyer.id,
            course_id=None,
            kind="item_done",
            amount=10,
            multiplier=1,
            note="market-test-credit",
        )
    )
    await db.commit()
    response = await client.post(f"/api/v1/market/courses/{market.id}/acquire")
    assert response.status_code == 202
    acquisition_id = uuid.UUID(response.json()["id"])
    repeated = await client.post(f"/api/v1/market/courses/{market.id}/acquire")
    assert repeated.status_code == 202
    assert repeated.json()["id"] == response.json()["id"]
    assert repeated.json()["job_id"] == response.json()["job_id"]
    job = await db.get(Job, uuid.UUID(response.json()["job_id"]))
    assert job is not None
    market.price_points = 50
    await db.commit()
    await handle_market_acquire(db, job)
    acquisition = await db.get(MarketAcquisition, acquisition_id)
    assert acquisition is not None and acquisition.price_snapshot == 7
    assert await points_balance(db, buyer.id) == 3
    debits = list(
        (
            await db.execute(
                select(PointsLedger).where(
                    PointsLedger.user_id == buyer.id,
                    PointsLedger.kind == "market_course",
                )
            )
        ).scalars()
    )
    assert len(debits) == 1 and debits[0].amount == -7
    assert debits[0].course_id is None
    await handle_market_acquire(db, job)
    assert await points_balance(db, buyer.id) == 3
    assert (await client.post(f"/api/v1/market/courses/{market.id}/acquire")).status_code == 409

    assert acquisition is not None and acquisition.target_course_id is not None
    target_course_id = acquisition.target_course_id
    await db.commit()
    deleted = await client.delete(f"/api/v1/courses/{target_course_id}")
    assert deleted.status_code == 200
    assert await points_balance(db, buyer.id) == 3
    await db.refresh(acquisition)
    assert acquisition.status == "completed"
    assert acquisition.target_course_id is None
    listing_after_delete = await client.get("/api/v1/market/courses")
    assert listing_after_delete.status_code == 200
    assert listing_after_delete.json()["items"][0]["acquisition_status"] == "completed"
    assert listing_after_delete.json()["items"][0]["owned_course_id"] is None
    restored = await client.post(f"/api/v1/market/courses/{market.id}/acquire")
    assert restored.status_code == 202
    restore_job = await db.get(Job, uuid.UUID(restored.json()["job_id"]))
    assert restore_job is not None
    await handle_market_acquire(db, restore_job)
    assert await points_balance(db, buyer.id) == 3
    restored_debits = list(
        (
            await db.execute(
                select(PointsLedger).where(
                    PointsLedger.user_id == buyer.id,
                    PointsLedger.kind == "market_course",
                )
            )
        ).scalars()
    )
    assert len(restored_debits) == 1


async def test_insufficient_points_and_corrupt_package_leave_no_course_or_debit(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    logged_in.role = "admin"
    await db.commit()
    source = await _seed_source_course(client, db, logged_in, name="损坏市场课")
    market, version = await _publish(client, db, source, price=5)
    buyer = await _register_buyer(client, db, "marketbuyer3")
    buyer_id = buyer.id
    insufficient = await client.post(f"/api/v1/market/courses/{market.id}/acquire")
    assert insufficient.status_code == 400
    assert insufficient.json()["detail"] == "积分不足"

    db.add(
        PointsLedger(
            user_id=buyer.id,
            course_id=None,
            kind="item_done",
            amount=8,
            multiplier=1,
            note="market-corrupt-credit",
        )
    )
    await db.commit()
    response = await client.post(f"/api/v1/market/courses/{market.id}/acquire")
    assert response.status_code == 202
    job = await db.get(Job, uuid.UUID(response.json()["job_id"]))
    assert job is not None
    job.attempts = job.max_attempts
    first_file = version.manifest["files"][0]["relative_path"]
    (Path(version.package_path) / first_file).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        await handle_market_acquire(db, job)
    acquisition = await db.get(MarketAcquisition, uuid.UUID(response.json()["id"]))
    assert acquisition is not None and acquisition.status == "failed"
    assert acquisition.target_course_id is None
    assert await points_balance(db, buyer_id) == 8
    assert (
        await db.execute(
            select(func.count()).select_from(PointsLedger).where(
                PointsLedger.user_id == buyer_id,
                PointsLedger.kind == "market_course",
            )
        )
    ).scalar_one() == 0


async def test_external_source_is_read_only_for_market_upload(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    external_owner = User(
        username="externalowner",
        password_hash=logged_in.password_hash,
        nickname="外部用户",
        role="user",
    )
    db.add(external_owner)
    await db.flush()
    external = Course(user_id=external_owner.id, name="外部源", status="active")
    db.add(external)
    logged_in.role = "admin"
    await db.commit()
    market = MarketCourse(
        source_course_id=external.id,
        created_by_user_id=logged_in.id,
        title="外部只读源",
        price_points=0,
    )
    db.add(market)
    await db.commit()
    response = await client.post(
        f"/api/v1/admin/market/courses/{market.id}/documents",
        files={"file": ("new.md", b"cannot mutate", "text/markdown")},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "外部用户源课程只允许只读发布"
