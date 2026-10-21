import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fsrs import Card, State
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Job,
    KnowledgePoint,
    Plan,
    PlanItem,
    PlanItemKp,
    PointsLedger,
    ReviewCard,
    ReviewLog,
    User,
    VocabEntry,
    WeaknessRecord,
    utcnow,
)
from app.schemas.vocab import NormalizeVocabOut, VocabItemOut
from app.services import examiner as examiner_svc
from app.services import planner as planner_svc
from app.services import reviews as reviews_svc
from app.services import vocab as vocab_svc


async def _create_course(
    client: AsyncClient, *, kind: str = "language", name: str = "词汇测试课"
) -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={"name": name, "goal_text": "积累词汇", "daily_minutes": 30, "kind": kind},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _review_card(course_id: uuid.UUID, *, kp_id=None, vocab_id=None, stability: float = 0) -> ReviewCard:
    now = utcnow()
    last_review = now - timedelta(days=1) if stability else None
    card = Card(
        due=now - timedelta(minutes=1),
        stability=stability,
        difficulty=5 if stability else 0,
        reps=3 if stability else 0,
        state=State.Review if stability else State.New,
        last_review=last_review,
    )
    return ReviewCard(
        course_id=course_id,
        kp_id=kp_id,
        vocab_id=vocab_id,
        stability=stability,
        difficulty=card.difficulty,
        due=card.due,
        state=card.state.value,
        reps=card.reps,
        last_review=last_review,
        fsrs_card=card.to_dict(),
    )


async def _run_import_job(db: AsyncSession, job_id: str) -> dict:
    job = await db.get(Job, uuid.UUID(job_id))
    assert job is not None
    return await vocab_svc.handle_vocab_import(db, job)


async def test_strict_import_dedupe_encoding_and_upsert_only_fills_empty_fields(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    first_csv = (
        "word,phonetic,meaning,example\n"
        "alpha,,第一义,\n"
        "beta,/ˈbiːtə/,第二义,Beta example.\n"
        ",,缺词,\n"
        f"{'x' * 65},,过长,\n"
    ).encode()
    first = await client.post(
        f"/api/v1/courses/{course['id']}/vocab/import",
        files={"file": ("first.csv", first_csv, "text/csv")},
    )
    assert first.status_code == 200, first.text
    duplicate_job = await client.post(
        f"/api/v1/courses/{course['id']}/vocab/import",
        files={"file": ("same.csv", first_csv, "text/csv")},
    )
    assert duplicate_job.status_code == 200
    assert duplicate_job.json()["job_id"] == first.json()["job_id"]

    first_result = await _run_import_job(db, first.json()["job_id"])
    assert first_result == {
        "imported": 2,
        "updated": 0,
        "duplicates": 0,
        "dropped": 2,
        "format": "csv",
    }
    alpha = (
        await db.execute(
            select(VocabEntry).where(
                VocabEntry.course_id == uuid.UUID(course["id"]),
                VocabEntry.word == "alpha",
            )
        )
    ).scalar_one()
    alpha_id = alpha.id
    alpha.status = "archived"
    await db.commit()

    second_csv = (
        "word,phonetic,meaning,example\n"
        "alpha,/ˈælfə/,不应覆盖第一义,Alpha example.\n"
        "beta,/should-not-replace/,不应覆盖第二义,Should not replace.\n"
        "gamma,/ˈɡæmə/,第三义,Gamma example.\n"
    ).encode()
    second = await client.post(
        f"/api/v1/courses/{course['id']}/vocab/import",
        files={"file": ("second.csv", second_csv, "text/csv")},
    )
    assert second.status_code == 200
    second_result = await _run_import_job(db, second.json()["job_id"])
    assert second_result == {
        "imported": 1,
        "updated": 1,
        "duplicates": 1,
        "dropped": 0,
        "format": "csv",
    }
    db.expire_all()
    alpha = await db.get(VocabEntry, alpha_id)
    assert (alpha.phonetic, alpha.meaning_md, alpha.example_md, alpha.status) == (
        "/ˈælfə/",
        "第一义",
        "Alpha example.",
        "archived",
    )
    beta = (
        await db.execute(select(VocabEntry).where(VocabEntry.word == "beta"))
    ).scalar_one()
    assert (beta.phonetic, beta.meaning_md, beta.example_md) == (
        "/ˈbiːtə/",
        "第二义",
        "Beta example.",
    )

    encoded = "word\tphonetic\tmeaning\texample\n韧性\t\t抗压能力\t\n".encode("gb18030")
    parsed, dropped = vocab_svc.parse_strict_vocab(encoded, delimiter="\t")
    assert dropped == 0
    assert parsed == [VocabItemOut(word="韧性", meaning_md="抗压能力")]

    template = await client.get(f"/api/v1/courses/{course['id']}/vocab/template.csv")
    assert template.status_code == 200
    assert template.text.splitlines()[0] == "word,phonetic,meaning,example"
    assert len(template.text.splitlines()) == 3


async def test_freeform_import_batches_low_tier_and_non_language_rejected(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    knowledge = await _create_course(client, kind="knowledge", name="非语言课程")
    rejected = await client.post(
        f"/api/v1/courses/{knowledge['id']}/vocab/import",
        files={"file": ("words.md", b"alpha - first", "text/markdown")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "仅语言课程支持词表导入"

    course = await _create_course(client, name="自由词表课")
    lines = [f"word-{index} - 释义 {index}" for index in range(55)]
    uploaded = await client.post(
        f"/api/v1/courses/{course['id']}/vocab/import",
        files={"file": ("words.md", "\n".join(lines).encode(), "text/markdown")},
    )
    assert uploaded.status_code == 200
    calls: list[dict] = []

    async def fake_run_structured(**kwargs):
        calls.append(kwargs)
        batch = kwargs["volatile"].split("原始词表行：\n", 1)[1].splitlines()
        return NormalizeVocabOut(
            items=[
                VocabItemOut(word=line.split(" - ", 1)[0], meaning_md=line.split(" - ", 1)[1])
                for line in batch
            ]
        )

    monkeypatch.setattr(vocab_svc.runtime, "run_structured", fake_run_structured)
    result = await _run_import_job(db, uploaded.json()["job_id"])
    assert result == {
        "imported": 55,
        "updated": 0,
        "duplicates": 0,
        "dropped": 0,
        "format": "md",
    }
    assert [len(call["volatile"].splitlines()) - 1 for call in calls] == [50, 5]
    assert all(call["tier"].value == "low" for call in calls)
    assert all(call["thinking"] is False for call in calls)


async def test_daily_activation_is_same_day_idempotent_and_respects_new_quota(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client, name="激活节流课")
    course_id = uuid.UUID(course["id"])
    db.add_all(
        [
            VocabEntry(course_id=course_id, word=f"word-{index:02}", meaning_md=f"释义 {index}")
            for index in range(20)
        ]
    )
    await db.commit()
    setting = await client.patch(
        f"/api/v1/courses/{course['id']}/vocab/settings",
        json={"daily_new_words": 8},
    )
    assert setting.status_code == 200
    first_day = datetime(2026, 7, 17, 2, tzinfo=UTC)
    monkeypatch.setattr(reviews_svc, "utcnow", lambda: first_day)

    first = await client.get(f"/api/v1/courses/{course['id']}/reviews/queue")
    assert first.status_code == 200, first.text
    assert first.json()["activated_today"] == 8
    assert first.json()["due_count"] == 8
    assert len(first.json()["cards"]) == 8
    assert all(card["kp"] is None and card["vocab"] is not None for card in first.json()["cards"])

    second = await client.get(f"/api/v1/courses/{course['id']}/reviews/queue")
    assert second.status_code == 200
    assert second.json()["activated_today"] == 8
    assert (
        await db.execute(
            select(func.count()).select_from(ReviewCard).where(ReviewCard.course_id == course_id)
        )
    ).scalar_one() == 8

    smaller = await client.patch(
        f"/api/v1/courses/{course['id']}/vocab/settings",
        json={"daily_new_words": 5},
    )
    assert smaller.status_code == 200
    same_day = await client.get(f"/api/v1/courses/{course['id']}/reviews/queue")
    assert same_day.json()["activated_today"] == 8
    assert same_day.json()["due_count"] == 8

    monkeypatch.setattr(reviews_svc, "utcnow", lambda: first_day + timedelta(days=1))
    next_day = await client.get(f"/api/v1/courses/{course['id']}/reviews/queue")
    assert next_day.status_code == 200
    assert next_day.json()["activated_today"] == 5
    assert next_day.json()["due_count"] == 13
    assert (
        await db.execute(
            select(func.count()).select_from(ReviewCard).where(ReviewCard.course_id == course_id)
        )
    ).scalar_one() == 13


async def test_mixed_queue_scoring_keeps_kp_state_machine_and_vocab_source_separate(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client, name="双源混合课")
    course_id = uuid.UUID(course["id"])
    user_id = logged_in.id
    kp = KnowledgePoint(
        course_id=course_id,
        slug="existing-kp",
        title="既有知识点",
        summary="原摘要",
        recall_prompt="原回忆提示",
        status="learning",
    )
    vocab = VocabEntry(
        course_id=course_id,
        word="resilient",
        phonetic="/rɪˈzɪliənt/",
        meaning_md="有韧性的",
        example_md="She remained resilient.",
        status="active",
        activated_at=utcnow(),
    )
    db.add_all([kp, vocab])
    await db.flush()
    kp_id = kp.id
    vocab_id = vocab.id
    kp_card = _review_card(course_id, kp_id=kp.id)
    vocab_card = _review_card(course_id, vocab_id=vocab.id)
    db.add_all([kp_card, vocab_card])
    await db.flush()
    kp_card_id = kp_card.id
    vocab_card_id = vocab_card.id
    await db.commit()

    queue = await client.get(f"/api/v1/courses/{course['id']}/reviews/queue")
    assert queue.status_code == 200, queue.text
    cards = queue.json()["cards"]
    kp_payload = next(card for card in cards if card["kp"] is not None)
    assert kp_payload["kp"] == {
        "id": str(kp_id),
        "slug": "existing-kp",
        "title": "既有知识点",
        "summary": "原摘要",
        "recall_prompt": "原回忆提示",
        "primary_wiki_slug": None,
        "status": "learning",
    }
    assert kp_payload["vocab"] is None

    kp_answer = await client.post(f"/api/v1/reviews/{kp_card_id}/answer", json={"rating": 1})
    assert kp_answer.status_code == 200
    assert kp_answer.json()["kp_status"] == "needs_reinforce"
    db.expire_all()
    kp_after = await db.get(KnowledgePoint, kp_id)
    kp_snapshot = (kp_after.status, kp_after.mastery_score)

    vocab_answer = await client.post(
        f"/api/v1/reviews/{vocab_card_id}/answer", json={"rating": 1}
    )
    assert vocab_answer.status_code == 200
    assert vocab_answer.json()["kp_status"] is None
    db.expire_all()
    assert (await db.get(VocabEntry, vocab_id)).status == "active"
    kp_after_vocab = await db.get(KnowledgePoint, kp_id)
    assert (kp_after_vocab.status, kp_after_vocab.mastery_score) == kp_snapshot
    logs = list((await db.execute(select(ReviewLog).order_by(ReviewLog.created_at))).scalars())
    assert {(log.kp_id, log.vocab_id) for log in logs} == {
        (kp_id, None),
        (None, vocab_id),
    }
    ledger = list(
        (
            await db.execute(
                select(PointsLedger).where(
                    PointsLedger.user_id == user_id,
                    PointsLedger.kind == "review_done",
                )
            )
        ).scalars()
    )
    assert len(ledger) == 2


async def test_vocab_cards_do_not_change_mastery_projection_or_exam_pool(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client, name="词卡隔离课")
    course_id = uuid.UUID(course["id"])
    plan = Plan(
        course_id=course_id,
        version=1,
        status="active",
        goal_summary="测试隔离",
        daily_minutes=30,
        start_date=date.today() - timedelta(days=2),
        rationale_md="测试",
    )
    kps = [
        KnowledgePoint(
            course_id=course_id,
            slug=f"kp-{index}",
            title=f"知识点 {index}",
            status="learning",
        )
        for index in range(3)
    ]
    db.add_all([plan, *kps])
    await db.flush()
    for index, kp in enumerate(kps):
        item = PlanItem(
            plan_id=plan.id,
            course_id=course_id,
            seq=index + 1,
            scheduled_date=date.today() - timedelta(days=1),
            type="learn",
            title=kp.title,
            est_minutes=10,
            status="done",
        )
        db.add(item)
        await db.flush()
        db.add(PlanItemKp(plan_item_id=item.id, kp_id=kp.id))
        db.add(_review_card(course_id, kp_id=kp.id, stability=5 + index))
        db.add(
            WeaknessRecord(
                course_id=course_id,
                kp_id=kp.id,
                kind="concept_confusion",
                severity=1,
            )
        )
    await db.commit()
    job = Job(kind="project_mastery", payload={"course_id": str(course_id)}, course_id=course_id)
    db.add(job)
    await db.flush()
    without_vocab = await planner_svc.handle_project_mastery(db, job)
    projection_snapshot = [(kp.projected_basic_at, kp.projected_full_at) for kp in kps]

    vocab = VocabEntry(course_id=course_id, word="isolated", meaning_md="隔离的", status="active")
    db.add(vocab)
    await db.flush()
    db.add(_review_card(course_id, vocab_id=vocab.id, stability=100))
    for kp in kps:
        kp.projected_basic_at = None
        kp.projected_full_at = None
    plan.projected_basic_date = None
    plan.projected_full_date = None
    await db.commit()
    with_vocab = await planner_svc.handle_project_mastery(db, job)
    assert with_vocab == without_vocab
    assert [(kp.projected_basic_at, kp.projected_full_at) for kp in kps] == projection_snapshot

    pool = await examiner_svc.select_daily_pool(db, course_id, date.today())
    assert pool is not None
    assert {entry.kp_slug for entry in pool}.issubset({kp.slug for kp in kps})
    assert "isolated" not in {entry.kp_slug for entry in pool}


async def test_archive_retains_logs_stats_settings_and_source_check(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client, name="词汇统计课")
    course_id = uuid.UUID(course["id"])
    entries = [
        VocabEntry(course_id=course_id, word=f"pending-{index}", meaning_md="待引入")
        for index in range(21)
    ]
    active = VocabEntry(course_id=course_id, word="active", meaning_md="学习中", status="active")
    mastered = VocabEntry(course_id=course_id, word="mastered", meaning_md="已掌握", status="active")
    archived = VocabEntry(course_id=course_id, word="archived", meaning_md="已归档", status="archived")
    db.add_all([*entries, active, mastered, archived])
    await db.flush()
    active_card = _review_card(course_id, vocab_id=active.id, stability=2)
    mastered_card = _review_card(course_id, vocab_id=mastered.id, stability=21)
    active_id = active.id
    db.add_all([active_card, mastered_card])
    await db.flush()
    active_card_id = active_card.id
    await db.commit()

    stats = await client.get(f"/api/v1/courses/{course['id']}/vocab/stats")
    assert stats.status_code == 200
    assert stats.json() == {
        "total": 24,
        "pending": 21,
        "active": 2,
        "archived": 1,
        "mastered": 1,
        "daily_new_words": 20,
        "projected_days": 2,
    }
    assert (await client.patch(
        f"/api/v1/courses/{course['id']}/vocab/settings", json={"daily_new_words": 4}
    )).status_code == 422
    assert (await client.patch(
        f"/api/v1/courses/{course['id']}/vocab/settings", json={"daily_new_words": 101}
    )).status_code == 422
    valid_setting = await client.patch(
        f"/api/v1/courses/{course['id']}/vocab/settings", json={"daily_new_words": 5}
    )
    assert valid_setting.status_code == 200

    reviewed = await client.post(f"/api/v1/reviews/{active_card_id}/answer", json={"rating": 3})
    assert reviewed.status_code == 200
    archived_response = await client.patch(
        f"/api/v1/vocab/{active_id}", json={"status": "archived"}
    )
    assert archived_response.status_code == 200
    assert archived_response.json()["card"] is None
    db.expire_all()
    assert await db.get(ReviewCard, active_card_id) is None
    log = (
        await db.execute(select(ReviewLog).where(ReviewLog.vocab_id == active_id))
    ).scalar_one()
    assert log.card_id is None
    assert log.vocab_id == active_id

    kp = KnowledgePoint(course_id=course_id, slug="check-kp", title="检查约束")
    check_vocab = VocabEntry(course_id=course_id, word="check", meaning_md="检查")
    db.add_all([kp, check_vocab])
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                ReviewCard(
                    course_id=course_id,
                    due=utcnow(),
                    fsrs_card=Card(due=utcnow()).to_dict(),
                )
            )
            await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                ReviewCard(
                    course_id=course_id,
                    kp_id=kp.id,
                    vocab_id=check_vocab.id,
                    due=utcnow(),
                    fsrs_card=Card(due=utcnow()).to_dict(),
                )
            )
            await db.flush()
