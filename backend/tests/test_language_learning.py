import hashlib
import json
import uuid
from datetime import timedelta

from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InviteCode, Job, PointsLedger, ReviewCard, User, VocabEntry, utcnow
from app.models.language import (
    LanguageAssessment,
    LanguageProfile,
    LanguageSource,
    ReadingAttempt,
    ReadingExercise,
    VocabSourceEntry,
    WritingExercise,
    WritingSubmission,
)
from app.schemas.language import (
    AssessmentWritingPrompt,
    CandidateWord,
    ReadingExerciseGenerated,
    ReadingPassageAnalysis,
    ReadingQuestionGenerated,
    ReadingQuestionPrivate,
    ReadingSourceAnalysisV1,
    WritingGradeOut,
    WritingPromptGenerated,
)
from app.services import language_learning as language_svc
from app.services import planner as planner_svc
from app.services import vocab as vocab_svc


async def _create_language_course(client: AsyncClient, *, track: str = "ielts") -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={
            "name": "英语读写测试课",
            "goal_text": "提高课程目标词汇、阅读与写作能力",
            "daily_minutes": 30,
            "kind": "language",
            "language_track": track,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_extensive_reading_contract_normalizes_extra_questions() -> None:
    questions = [
        ReadingQuestionGenerated(
            dimension=dimension,
            prompt=f"Choose the correct {dimension} answer.",
            options=["Correct", "Wrong A", "Wrong B", "Wrong C"],
            correct_index=0,
            explanation_md="原文直接支持 Correct，其他选项与文章信息不符。",
        )
        for dimension in (
            "main_idea",
            "detail",
            "detail",
            "detail",
            "inference",
            "inference",
            "context_vocab",
            "context_vocab",
        )
    ]
    generated = language_svc.ExtensiveReadingExerciseGenerated(
        title="Extensive reading check",
        questions=questions,
        focus_vocab=[],
    )
    assert [question.dimension for question in generated.questions] == [
        "main_idea",
        "detail",
        "inference",
        "context_vocab",
    ]


def test_periodic_assessment_keeps_active_profile_and_public_search_guards() -> None:
    course_id = uuid.uuid4()
    profile = LanguageProfile(
        course_id=course_id,
        assessment_status="active",
        latest_assessment_at=utcnow(),
    )
    periodic = LanguageAssessment(course_id=course_id, kind="periodic")
    assert language_svc.assessment_profile_status_after_build(profile, periodic) == "active"
    baseline = LanguageAssessment(course_id=course_id, kind="baseline")
    assert language_svc.assessment_profile_status_after_build(profile, baseline) == "ready"
    assert language_svc._public_ip("8.8.8.8")
    assert not language_svc._public_ip("127.0.0.1")


def test_vocab_only_assessment_rejects_short_passage_and_scales_to_large_vocab() -> None:
    payload = {
        "title": "Too short",
        "passage_md": " ".join(["word"] * 127),
        "estimated_level": "B1",
        "questions": [item.model_dump(mode="json") for item in _assessment_questions()],
        "writing": {
            "prompt_md": "Explain how regular study supports long-term language learning.",
            "requirements_md": "Write a structured response with examples and a clear conclusion.",
            "min_words": 50,
            "max_words": 300,
        },
    }
    try:
        language_svc.SyntheticAssessmentOut.model_validate(payload)
    except ValidationError as exc:
        assert "300-700" in str(exc)
    else:
        raise AssertionError("127 词的测评短文不应通过结构化校验")

    candidates = [
        CandidateWord(word=f"word{index}", meaning=f"释义 {index}")
        for index in range(9400)
    ]
    probes = language_svc._vocab_probes(candidates, uuid.uuid4())
    assert len(probes) == 40
    assert all(len(set(probe.options)) == 4 for probe in probes)


def test_performance_factor_and_vocab_coverage_thresholds() -> None:
    assert language_svc.performance_factor([]) == 1.0
    assert language_svc.performance_factor([59]) == 0.8
    assert language_svc.performance_factor([60, 84]) == 1.0
    assert language_svc.performance_factor([85, 100]) == 1.1

    course_id = uuid.uuid4()
    profile = LanguageProfile(
        course_id=course_id,
        vocab_total=100,
        vocab_known_estimate=50,
    )

    def recommendation_for(candidate_occurrences: int) -> tuple[float | None, str | None]:
        content = " ".join(
            ["rare"] * candidate_occurrences + ["common"] * (100 - candidate_occurrences)
        )
        source = LanguageSource(
            course_id=course_id,
            kind="reading",
            original_name="coverage.md",
            stored_path="/tmp/coverage.md",
            size_bytes=500,
            sha256="d" * 64,
            status="ready",
            word_count=100,
            analysis=ReadingSourceAnalysisV1(
                title="Coverage",
                estimated_level="B1",
                word_count=100,
                candidate_words=[CandidateWord(word="rare", meaning="罕见的")],
                passages=[
                    ReadingPassageAnalysis(
                        title="Coverage",
                        content_md=content,
                        word_count=100,
                    )
                ],
            ).model_dump(mode="json"),
        )
        return language_svc.source_vocab_coverage(source, profile)

    assert recommendation_for(4) == (98.0, "extensive")
    assert recommendation_for(6) == (97.0, "assisted_extensive")
    assert recommendation_for(12) == (94.0, "intensive_challenge")


def _assessment_questions() -> list[ReadingQuestionPrivate]:
    dimensions = [
        "main_idea",
        "detail",
        "detail",
        "detail",
        "inference",
        "inference",
        "context_vocab",
        "context_vocab",
    ]
    return [
        ReadingQuestionPrivate(
            id=f"r{index}",
            dimension=dimension,
            prompt=f"Question {index}: choose the supported answer.",
            options=["Correct", "Wrong A", "Wrong B", "Wrong C"],
            correct_index=0,
            explanation_md="原文直接支持 Correct，其他选项与文章信息不符。",
        )
        for index, dimension in enumerate(dimensions, start=1)
    ]


def test_legacy_assessment_questions_receive_safe_explanations() -> None:
    questions = [item.model_dump(mode="json") for item in _assessment_questions()]
    for question in questions:
        question.pop("explanation_md")
    bundle = language_svc.LanguageAssessmentBundleV1.model_validate(
        {
            "vocab_items": [],
            "vocab_population": 0,
            "reading": {
                "title": "Legacy reading",
                "passage_md": " ".join(["Legacy reading content supports the marked answer."] * 20),
                "estimated_level": "B1",
                "questions": questions,
            },
            "writing": {
                "prompt_md": "Explain the main idea of the reading passage in your own words.",
                "requirements_md": "Write a structured response with evidence from the passage.",
                "min_words": 50,
                "max_words": 300,
            },
        }
    )
    assert all("正确答案" in item.explanation_md for item in bundle.reading.questions)


async def test_language_track_validation_and_legacy_course_compatibility(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_language_course(client, track="cet6")
    assert course["language_track"] == "cet6"
    profile = await db.get(LanguageProfile, uuid.UUID(course["id"]))
    assert profile is not None and profile.assessment_status == "required"

    rejected = await client.post(
        "/api/v1/courses",
        json={"name": "非法课程", "kind": "knowledge", "language_track": "ielts"},
    )
    assert rejected.status_code == 422

    legacy = await client.post(
        "/api/v1/courses",
        json={"name": "TASK-015 兼容课程", "kind": "language", "daily_minutes": 30},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["language_track"] == "custom"
    legacy_profile = await db.get(LanguageProfile, uuid.UUID(legacy.json()["id"]))
    assert legacy_profile is not None and legacy_profile.assessment_status == "active"


async def test_adaptive_intensity_recent_results_and_planner_context(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course_data = await _create_language_course(client)
    course_id = uuid.UUID(course_data["id"])
    profile = await db.get(LanguageProfile, course_id)
    assert profile is not None
    profile.assessment_status = "active"
    profile.overall_level = "B1"
    profile.reading_level = "B1"
    profile.writing_level = "B1"
    profile.vocab_total = 100
    profile.vocab_known_estimate = 50
    source = LanguageSource(
        course_id=course_id,
        kind="reading",
        original_name="adaptive.md",
        stored_path="/tmp/adaptive.md",
        size_bytes=1000,
        sha256="e" * 64,
        status="ready",
        word_count=100,
        estimated_level="B1",
        analysis=ReadingSourceAnalysisV1(
            title="Adaptive Reading",
            estimated_level="B1",
            word_count=100,
            candidate_words=[CandidateWord(word="rare", meaning="罕见的")],
            passages=[
                ReadingPassageAnalysis(
                    title="Adaptive Reading",
                    content_md=" ".join(["rare"] * 6 + ["common"] * 94),
                    word_count=100,
                )
            ],
        ).model_dump(mode="json"),
    )
    db.add(source)
    await db.flush()
    reading = ReadingExercise(
        course_id=course_id,
        source_id=source.id,
        mode="intensive",
        title="Recent performance",
        passage_md=" ".join(["A recent reading result supports adaptive intensity."] * 60),
        questions=[],
        focus_vocab=[],
        status="graded",
    )
    db.add(reading)
    await db.flush()
    db.add_all(
        [
            ReadingAttempt(
                exercise_id=reading.id,
                attempt_no=1,
                answers=[],
                response_md="Recent response with useful details.",
                duration_seconds=60,
                score=90,
                feedback_md="Strong recent reading result.",
            ),
            ReadingAttempt(
                exercise_id=reading.id,
                attempt_no=2,
                answers=[],
                response_md="Old response outside the adaptive window.",
                duration_seconds=60,
                score=0,
                feedback_md="Old result.",
                created_at=utcnow() - timedelta(days=8),
            ),
        ]
    )
    await db.commit()

    adaptive = await client.patch(
        f"/api/v1/courses/{course_id}/language/profile",
        json={"intensity_mode": "adaptive"},
    )
    assert adaptive.status_code == 200, adaptive.text
    assert adaptive.json()["recommended_daily_words"] == 10
    assert adaptive.json()["daily_new_words"] == 10

    manual = await client.patch(
        f"/api/v1/courses/{course_id}/language/profile",
        json={"intensity_mode": "manual", "daily_new_words": 6},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["recommended_daily_words"] == 10
    assert manual.json()["daily_new_words"] == 6
    assert manual.json()["intensity_mode"] == "manual"

    sources = await client.get(f"/api/v1/courses/{course_id}/language/sources")
    assert sources.status_code == 200, sources.text
    assert sources.json()[0]["vocab_coverage_percent"] == 97.0
    assert sources.json()[0]["recommended_mode"] == "assisted_extensive"

    captured: dict = {}

    async def fake_planner_runtime(*, response_model, **kwargs):
        if response_model is planner_svc.OnboardingSummary:
            return planner_svc.OnboardingSummary(
                goals="根据课程材料提升读写",
                daily_minutes=30,
                target_date=None,
                prior_knowledge="B1",
                preferences="精读与修订",
            )
        captured["stable_blocks"] = kwargs["stable_blocks"]
        return planner_svc.GenerateLanguagePlanOut(
            rationale_md="根据近期表现安排读写。",
            items=[
                planner_svc.LanguagePlanItemOut(
                    date_offset=0,
                    type="reading",
                    title="材料精读",
                    kp_slugs=[],
                    est_minutes=20,
                )
            ],
        )

    monkeypatch.setattr(planner_svc.runtime, "run_structured", fake_planner_runtime)
    job = Job(kind="generate_plan", payload={"course_id": str(course_id)}, course_id=course_id)
    db.add(job)
    await db.flush()
    await planner_svc.handle_generate_plan(db, job)
    assert len(captured["stable_blocks"]) == 2
    assert "近7日读写结果" in captured["stable_blocks"][1]
    assert "阅读 90分" in captured["stable_blocks"][1]
    assert "阅读 0分" not in captured["stable_blocks"][1]


async def test_search_source_enqueues_a_course_owned_job(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_language_course(client)
    course_id = uuid.UUID(course["id"])
    response = await client.post(
        f"/api/v1/courses/{course_id}/language/sources/search",
        json={"query": "climate adaptation for B1 learners"},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    source = await db.get(LanguageSource, uuid.UUID(payload["source_id"]))
    job = await db.get(Job, uuid.UUID(payload["job_id"]))
    assert source is not None and source.status == "uploaded"
    assert job is not None and job.kind == "search_language_source"
    assert job.course_id == course_id and job.user_id == logged_in.id


async def test_language_plan_endpoint_allows_auto_material_without_import(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_language_course(client)
    course_id = uuid.UUID(course["id"])
    profile = await db.get(LanguageProfile, course_id)
    assert profile is not None
    profile.assessment_status = "active"
    await db.commit()
    response = await client.post(f"/api/v1/courses/{course_id}/plan/generate")
    assert response.status_code == 200, response.text
    job = await db.get(Job, uuid.UUID(response.json()["id"]))
    assert job is not None and job.kind == "generate_plan" and job.user_id == logged_in.id


async def test_language_ownership_limits_and_course_kind_guard(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_language_course(client)
    course_id = uuid.UUID(course["id"])
    source_id = uuid.uuid4()
    source = LanguageSource(
        id=source_id,
        course_id=course_id,
        kind="reading",
        original_name="owned.md",
        stored_path="/tmp/owned.md",
        size_bytes=500,
        sha256="b" * 64,
        status="ready",
        word_count=600,
        estimated_level="B1",
        analysis={},
    )
    assessment = LanguageAssessment(course_id=course_id, kind="baseline", status="ready")
    reading = ReadingExercise(
        course_id=course_id,
        source_id=source_id,
        mode="intensive",
        title="Owned reading",
        passage_md=" ".join(["This passage belongs to the first learner."] * 60),
        questions=[],
        focus_vocab=[],
    )
    writing = WritingExercise(
        course_id=course_id,
        prompt_md="Write about a useful study routine.",
        requirements_md="Write a clear response with supporting details.",
    )
    db.add_all([source, assessment, reading, writing])
    profile = await db.get(LanguageProfile, course_id)
    assert profile is not None
    profile.assessment_status = "active"
    for index in range(5):
        db.add(Job(kind="generate_language_reading", course_id=course_id, payload={"n": index}))
    for index in range(3):
        db.add(Job(kind="generate_language_writing", course_id=course_id, payload={"n": index}))
    await db.commit()

    blocked_kind = await client.patch(
        f"/api/v1/courses/{course_id}",
        json={"kind": "knowledge", "language_track": None},
    )
    assert blocked_kind.status_code == 400
    assert "已有英语学习数据" in blocked_kind.json()["detail"]
    reading_limit = await client.post(
        f"/api/v1/courses/{course_id}/readings/generate",
        json={"source_id": str(source_id), "mode": "intensive"},
    )
    writing_limit = await client.post(
        f"/api/v1/courses/{course_id}/writings/generate",
        json={},
    )
    assert reading_limit.status_code == 429
    assert writing_limit.status_code == 429

    db.add(InviteCode(code="OTHER", max_uses=1))
    await db.commit()
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "student2",
            "password": "password123",
            "nickname": "另一位学生",
            "invite_code": "OTHER",
        },
    )
    assert registered.status_code == 200
    for path in (
        f"/api/v1/language/assessments/{assessment.id}",
        f"/api/v1/readings/{reading.id}",
        f"/api/v1/writings/{writing.id}",
    ):
        assert (await client.get(path)).status_code == 404


async def test_language_final_failure_restores_retryable_state(
    db: AsyncSession,
    logged_in: User,
) -> None:
    course_id = uuid.uuid4()
    from app.models import Course

    db.add(
        Course(
            id=course_id,
            user_id=logged_in.id,
            name="失败恢复测试课",
            kind="language",
            language_track="ielts",
        )
    )
    db.add(LanguageProfile(course_id=course_id, assessment_status="building"))
    assessment = LanguageAssessment(course_id=course_id, kind="baseline", status="building")
    source_id = uuid.uuid4()
    source = LanguageSource(
        id=source_id,
        course_id=course_id,
        kind="reading",
        original_name="retry.md",
        stored_path="/tmp/retry.md",
        size_bytes=500,
        sha256="c" * 64,
        status="ready",
        word_count=500,
    )
    reading = ReadingExercise(
        course_id=course_id,
        source_id=source_id,
        mode="intensive",
        title="Retry reading",
        passage_md=" ".join(["A retry should restore the exercise state."] * 60),
        questions=[],
        focus_vocab=[],
        status="submitted",
    )
    writing = WritingExercise(
        course_id=course_id,
        prompt_md="Write a retry response.",
        requirements_md="Write a clear response with evidence.",
        status="grading",
    )
    db.add_all([assessment, source, reading, writing])
    await db.flush()
    attempt = ReadingAttempt(
        exercise_id=reading.id,
        attempt_no=1,
        answers=[],
        response_md="A valid retry response with enough detail.",
        duration_seconds=60,
    )
    submission = WritingSubmission(
        exercise_id=writing.id,
        version=1,
        submission_md="A valid submission that should become editable again after final failure.",
    )
    db.add_all([attempt, submission])
    await db.commit()
    assessment_id = assessment.id
    reading_id = reading.id
    attempt_id = attempt.id
    writing_id = writing.id
    submission_id = submission.id

    await language_svc._fail_assessment(
        db,
        Job(payload={"assessment_id": str(assessment_id)}),
        "final assessment failure",
    )
    await language_svc.fail_language_reading(
        db,
        Job(payload={"attempt_id": str(attempt_id)}),
        "final reading failure",
    )
    await language_svc.fail_language_writing(
        db,
        Job(payload={"submission_id": str(submission_id)}),
        "final writing failure",
    )
    await db.commit()
    db.expire_all()
    assert (await db.get(LanguageAssessment, assessment_id)).status == "failed"
    assert (await db.get(LanguageProfile, course_id)).assessment_status == "required"
    assert (await db.get(ReadingExercise, reading_id)).status == "open"
    assert await db.get(ReadingAttempt, attempt_id) is None
    assert (await db.get(WritingExercise, writing_id)).status == "open"
    assert await db.get(WritingSubmission, submission_id) is None


async def test_language_assessment_reading_and_revision_closed_loop(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_language_course(client)
    course_id = uuid.UUID(course["id"])
    csv_data = (
        "word,phonetic,meaning,example,rank,level\n"
        + "\n".join(
            f"target{index},,目标释义{index},This is target word {index}.,{index},B1"
            for index in range(1, 25)
        )
    ).encode()
    uploaded = await client.post(
        f"/api/v1/courses/{course_id}/vocab/import",
        files={"file": ("targets.csv", csv_data, "text/csv")},
    )
    assert uploaded.status_code == 200, uploaded.text
    source_id = uuid.UUID(uploaded.json()["source_id"])
    import_job = await db.get(Job, uuid.UUID(uploaded.json()["job_id"]))
    assert import_job is not None
    import_result = await vocab_svc.handle_vocab_import(db, import_job)
    assert import_result["imported"] == 24
    source = await db.get(LanguageSource, source_id)
    assert source is not None and source.status == "ready" and source.word_count == 24
    links = list(
        (
            await db.execute(
                select(VocabSourceEntry)
                .where(VocabSourceEntry.source_id == source_id)
                .order_by(VocabSourceEntry.source_rank)
            )
        ).scalars()
    )
    assert len(links) == 24
    assert links[0].source_rank == 1 and links[0].level_hint == "B1"

    before_assessment = await client.get(f"/api/v1/courses/{course_id}/reviews/queue")
    assert before_assessment.status_code == 200
    assert before_assessment.json()["activated_today"] == 0
    assert (
        await db.execute(
            select(func.count()).select_from(ReviewCard).where(ReviewCard.course_id == course_id)
        )
    ).scalar_one() == 0

    async def fake_runtime(*, response_model, **kwargs):
        if response_model is language_svc.SyntheticAssessmentOut:
            return language_svc.SyntheticAssessmentOut(
                title="A Balanced Study Routine",
                passage_md=" ".join(["Learners improve through regular reading and careful revision."] * 45),
                estimated_level="B1",
                questions=_assessment_questions(),
                writing=AssessmentWritingPrompt(
                    prompt_md="Explain how a learner can build a balanced English study routine.",
                    requirements_md="Write a clear response with an introduction and supporting details.",
                    min_words=50,
                    max_words=300,
                ),
            )
        if response_model is WritingGradeOut:
            return WritingGradeOut(
                task_fulfillment=20,
                organization=20,
                grammar=20,
                vocabulary=20,
                feedback_md=(
                    "The response answers the task and has a visible structure. "
                    "Keep the clear topic sentence, then revise two repeated expressions and connect "
                    "the final example more explicitly to the claim."
                ),
                suggested_level="B2",
            )
        if response_model in {
            ReadingExerciseGenerated,
            language_svc.IntensiveReadingExerciseGenerated,
            language_svc.ExtensiveReadingExerciseGenerated,
        }:
            return ReadingExerciseGenerated(
                title="Extensive reading check",
                questions=[
                    ReadingQuestionGenerated(
                        dimension=dimension,
                        prompt=f"Choose the correct {dimension} answer.",
                        options=["Correct", "Wrong A", "Wrong B", "Wrong C"],
                        correct_index=0,
                        explanation_md="原文直接支持 Correct，其他选项与文章信息不符。",
                    )
                    for dimension in ("main_idea", "detail", "inference", "context_vocab")
                ],
                focus_vocab=[
                    CandidateWord(
                        word="revision",
                        meaning="修订",
                        context="Careful revision improves a draft.",
                    )
                ],
            )
        if response_model is language_svc.ReadingResponseGradeOut:
            return language_svc.ReadingResponseGradeOut(
                summary_score=20,
                feedback_md="The response accurately captures the main claim and supporting detail.",
            )
        if response_model is WritingPromptGenerated:
            return WritingPromptGenerated(
                prompt_md="Write a short response explaining why revision matters.",
                requirements_md="Use evidence from the reading and organize the response in two paragraphs.",
            )
        raise AssertionError(f"unexpected response model: {response_model}")

    monkeypatch.setattr(language_svc.runtime, "run_structured", fake_runtime)
    assessment_response = await client.post(
        f"/api/v1/courses/{course_id}/language/assessments",
        json={"kind": "baseline", "source_ids": [str(source_id)]},
    )
    assert assessment_response.status_code == 202, assessment_response.text
    assessment_id = uuid.UUID(assessment_response.json()["assessment_id"])
    build_job = await db.get(Job, uuid.UUID(assessment_response.json()["job_id"]))
    assert build_job is not None
    await language_svc.handle_build_language_assessment(db, build_job)

    public_assessment = await client.get(f"/api/v1/language/assessments/{assessment_id}")
    assert public_assessment.status_code == 200
    assert "correct_index" not in public_assessment.text
    assert "explanation_md" not in public_assessment.text
    assert public_assessment.json()["review"] is None
    private = await db.get(LanguageAssessment, assessment_id)
    assert private is not None
    bundle = language_svc.LanguageAssessmentBundleV1.model_validate(private.item_bundle)
    writing = " ".join(["Regular reading and revision improve English skills."] * 12)
    submitted = await client.post(
        f"/api/v1/language/assessments/{assessment_id}/submit",
        json={
            "vocab_answers": [
                {"item_id": item.id, "choice_index": item.correct_index}
                for item in bundle.vocab_items
            ],
            "reading_answers": [
                {"item_id": item.id, "choice_index": item.correct_index}
                for item in bundle.reading.questions
            ],
            "writing_md": writing,
        },
    )
    assert submitted.status_code == 202, submitted.text
    submitted_assessment = await client.get(f"/api/v1/language/assessments/{assessment_id}")
    assert submitted_assessment.status_code == 200
    assert submitted_assessment.json()["review"] is None
    db.expire_all()
    grade_job = await db.get(Job, uuid.UUID(submitted.json()["job_id"]))
    assert grade_job is not None
    await language_svc.handle_grade_language_assessment(db, grade_job)
    profile = await db.get(LanguageProfile, course_id)
    assert profile is not None
    assert profile.assessment_status == "active"
    assert profile.overall_level == "B2"
    assert profile.vocab_known_estimate == 24
    graded_assessment = await client.get(f"/api/v1/language/assessments/{assessment_id}")
    assert graded_assessment.status_code == 200
    assessment_review = graded_assessment.json()["review"]
    assert len(assessment_review["vocab"]) == len(bundle.vocab_items)
    assert len(assessment_review["reading"]) == 8
    assert all(item["is_correct"] for item in assessment_review["reading"])
    assert assessment_review["reading"][0]["correct_index"] == 0
    assert "原文" in assessment_review["reading"][0]["explanation_md"]
    assert assessment_review["writing_md"] == writing

    after_assessment = await client.get(f"/api/v1/courses/{course_id}/reviews/queue")
    assert after_assessment.status_code == 200
    assert after_assessment.json()["activated_today"] == profile.recommended_daily_words

    reading_source = LanguageSource(
        course_id=course_id,
        kind="reading",
        original_name="article.md",
        stored_path="/tmp/article.md",
        size_bytes=1000,
        sha256="a" * 64,
        status="ready",
        word_count=720,
        estimated_level="B1",
        analysis=ReadingSourceAnalysisV1(
            title="Why Revision Matters",
            estimated_level="B1",
            word_count=720,
            candidate_words=[],
            passages=[
                ReadingPassageAnalysis(
                    title="Why Revision Matters",
                    content_md=" ".join(
                        ["Writers improve ideas when they reread and revise a draft."] * 75
                    ),
                    word_count=750,
                )
            ],
        ).model_dump(mode="json"),
    )
    db.add(reading_source)
    await db.commit()
    reading_job_response = await client.post(
        f"/api/v1/courses/{course_id}/readings/generate",
        json={"source_id": str(reading_source.id), "mode": "extensive"},
    )
    assert reading_job_response.status_code == 202, reading_job_response.text
    reading_job = await db.get(Job, uuid.UUID(reading_job_response.json()["job_id"]))
    result = await language_svc.handle_generate_language_reading(db, reading_job)
    reading_id = uuid.UUID(result["exercise_id"])
    assert (await language_svc.handle_generate_language_reading(db, reading_job))["exercise_id"] == str(
        reading_id
    )
    reading_public = await client.get(f"/api/v1/readings/{reading_id}")
    assert reading_public.status_code == 200
    assert "correct_index" not in reading_public.text
    assert "explanation_md" not in reading_public.text
    private_reading = await db.get(ReadingExercise, reading_id)
    answers = [
        {"item_id": question["id"], "choice_index": question["correct_index"]}
        for question in private_reading.questions
    ]
    answers[0]["choice_index"] = (answers[0]["choice_index"] + 1) % 4
    attempt_response = await client.post(
        f"/api/v1/readings/{reading_id}/attempts",
        json={
            "answers": answers,
            "response_md": (
                "The passage explains that rereading helps writers notice weak ideas and improve them. "
                "It also shows that careful revision can make the central claim clearer for readers. "
                "Writers should compare each example with the main point before finishing the draft."
            ),
            "duration_seconds": 300,
        },
    )
    assert attempt_response.status_code == 202, attempt_response.text
    submitted_reading = await client.get(f"/api/v1/readings/{reading_id}")
    assert submitted_reading.status_code == 200
    assert submitted_reading.json()["attempts"][-1]["reviews"] == []
    db.expire_all()
    attempt_job = await db.get(Job, uuid.UUID(attempt_response.json()["job_id"]))
    attempt_result = await language_svc.handle_grade_language_reading(db, attempt_job)
    assert attempt_result["score"] == 80
    graded_reading = await client.get(f"/api/v1/readings/{reading_id}")
    assert graded_reading.status_code == 200
    reading_reviews = graded_reading.json()["attempts"][-1]["reviews"]
    assert len(reading_reviews) == 4
    assert reading_reviews[0]["is_correct"] is False
    assert all(item["is_correct"] for item in reading_reviews[1:])
    assert reading_reviews[0]["explanation_md"].startswith("原文")
    added = await client.post(
        f"/api/v1/readings/{reading_id}/vocab",
        json={"words": ["revision"]},
    )
    assert added.status_code == 200
    assert (
        await db.execute(
            select(func.count()).select_from(VocabEntry).where(
                VocabEntry.course_id == course_id,
                VocabEntry.word == "revision",
            )
        )
    ).scalar_one() == 1

    writing_job_response = await client.post(
        f"/api/v1/courses/{course_id}/writings/generate",
        json={"source_reading_id": str(reading_id)},
    )
    assert writing_job_response.status_code == 202, writing_job_response.text
    writing_job = await db.get(Job, uuid.UUID(writing_job_response.json()["job_id"]))
    writing_result = await language_svc.handle_generate_language_writing(db, writing_job)
    writing_id = uuid.UUID(writing_result["exercise_id"])
    assert (await language_svc.handle_generate_language_writing(db, writing_job))[
        "exercise_id"
    ] == str(writing_id)
    for version in (1, 2):
        submission_text = " ".join(
            [
                "Revision helps a writer improve ideas clearly."
                if version == 1
                else "Careful revision helps writers organize stronger ideas clearly."
            ]
            * 10
        )
        response = await client.post(
            f"/api/v1/writings/{writing_id}/submissions",
            json={"submission_md": submission_text},
        )
        assert response.status_code == 202, response.text
        db.expire_all()
        job = await db.get(Job, uuid.UUID(response.json()["job_id"]))
        result = await language_svc.handle_grade_language_writing(db, job)
        assert result["version"] == version
    exercise = await db.get(WritingExercise, writing_id)
    assert exercise is not None and exercise.status == "completed"
    assert (
        await db.execute(
            select(func.count()).select_from(PointsLedger).where(
                PointsLedger.kind == "writing_done",
                PointsLedger.note == f"writing:{writing_id}",
            )
        )
    ).scalar_one() == 1


def test_language_plan_normalizer_forces_two_readings_and_one_writing() -> None:
    generated = planner_svc.GenerateLanguagePlanOut(
        rationale_md="课程级英语安排",
        items=[
            planner_svc.LanguagePlanItemOut(
                date_offset=0,
                type="reading",
                title="材料精读",
                kp_slugs=[],
                est_minutes=20,
            )
        ],
    )
    normalized = planner_svc.normalize_plan_items(
        generated,
        known_slugs=set(),
        prerequisite_edges=[],
        daily_minutes=45,
        course_kind="language",
    )
    assert sum(item.type == "reading" for item in normalized) >= 2
    assert sum(item.type == "writing" for item in normalized) >= 1
    assert any(item.type == "quiz" for item in normalized)
    assert max(item.date_offset for item in normalized) <= 6
    assert all(
        sum(row.est_minutes for row in normalized if row.date_offset == day) <= 45
        for day in range(7)
    )


def test_existing_plan_prompt_contract_bytes_are_unchanged() -> None:
    prompt = (
        planner_svc.GENERATE_PLAN_SYSTEM_V1
        + "\n\n# 输出 JSON Schema（字段名必须完全一致）\n"
        + json.dumps(planner_svc.GeneratePlanOut.model_json_schema(), ensure_ascii=False)
    )
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "0313e2217880982d907cdc038de603acd9b0060765b876ad53f07cacb8296b57"
    )
    assert "reading" not in prompt and "writing" not in prompt


def test_reading_passage_bounds_and_vocab_probe_options_are_enforced() -> None:
    passage = ReadingPassageAnalysis(
        title="Long material",
        content_md=" ".join(["word"] * 3200),
        word_count=3200,
    )
    analysis = ReadingSourceAnalysisV1(
        title="Long material",
        estimated_level="B2",
        word_count=3200,
        candidate_words=[],
        passages=[passage],
    )
    intensive = language_svc._passage_for_mode(analysis, "intensive", 800)
    extensive = language_svc._passage_for_mode(analysis, "extensive", 2500)
    assert intensive.word_count == 800
    assert extensive.word_count == 3000

    repeated_meanings = [
        CandidateWord(word=f"word{index}", meaning="同一释义") for index in range(4)
    ]
    assert language_svc._vocab_probes(repeated_meanings, uuid.uuid4()) == []
