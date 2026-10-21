import asyncio
import hashlib
import ipaddress
import math
import random
import re
import socket
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.worker import job_failure_handler, job_handler
from app.models import Course, Job, PlanItem, VocabEntry, utcnow
from app.models.language import (
    LanguageAssessment,
    LanguageProfile,
    LanguageSource,
    ReadingAttempt,
    ReadingExercise,
    WritingExercise,
    WritingSubmission,
)
from app.schemas.language import (
    AssessmentReadingPrivate,
    AssessmentWritingPrompt,
    CandidateWord,
    LanguageAssessmentBundleV1,
    ReadingExerciseGenerated,
    ReadingPassageAnalysis,
    ReadingQuestionGenerated,
    ReadingQuestionPrivate,
    ReadingSourceAnalysisV1,
    SearchCandidates,
    VocabProbePrivate,
    WritingGradeOut,
    WritingPromptGenerated,
)
from app.services.parsing import parse_file
from app.services.points import award_points
from app.services.stats import shanghai_date

LEVELS = ["A1", "A2", "B1", "B2", "C1"]
TRACK_LABELS = {
    "ielts": "雅思",
    "cet6": "大学英语六级",
    "postgraduate": "考研英语",
    "new_concept": "新概念英语",
    "custom": "自定义英语",
}
LEVEL_TARGETS = {
    "A1": (5, 300, 600, 80),
    "A2": (8, 400, 800, 120),
    "B1": (12, 600, 1200, 180),
    "B2": (15, 800, 1800, 280),
    "C1": (20, 800, 2500, 400),
}

READING_ANALYSIS_SYSTEM_V1 = """你是英语阅读材料分析器。版本：READING_ANALYSIS_SYSTEM_V1。
只分析用户提供的原文，不补写原文内容。按 CEFR A1/A2/B1/B2/C1 给出保守难度估计。
提取最多 200 个值得学习的原形词，释义使用中文，context 必须来自原文短句。
课程分级只用于学习强度，不是官方 CEFR 认证。"""

LANGUAGE_SEARCH_SYSTEM_V1 = """你是英语学习材料检索器。版本：LANGUAGE_SEARCH_SYSTEM_V1。
使用联网搜索寻找公开可访问、适合英语学习的英文文章。优先公共机构、大学、非营利组织、公共领域或明确开放许可来源；不要选择付费墙、登录墙、搜索结果页、论坛或明显的广告页。
只返回候选文章的标题、完整 HTTPS URL 和一句选择理由。不得编造 URL；URL 必须来自搜索结果。"""

ASSESSMENT_BUILD_SYSTEM_V2 = """你是英语课程入门测评出题器。版本：ASSESSMENT_BUILD_SYSTEM_V2。
阅读题必须基于给定文章，恰好 8 道四选一：主旨 1、细节 3、推断 2、语境词义 2。
选项必须只有一个正确答案；每题 explanation_md 用中文说明答案依据，并指出原文线索。
写作题贴合课程轨道与文章主题，不复制真实考试题。
只输出结构化结果。"""

WRITING_GRADE_SYSTEM_V1 = """你是英语写作反馈师。版本：WRITING_GRADE_SYSTEM_V1。
按任务完成、组织衔接、语法准确、词汇范围与控制四维各 0-25 分。
反馈必须引用学生原文证据，指出最优先的 2-4 个修改点，保留好句并给局部改写示例。
不要虚构学生未写的内容，不要只给一篇替代范文。CEFR 档位仅作学习建议。"""

READING_EXERCISE_SYSTEM_V2 = """你是英语阅读练习出题器。版本：READING_EXERCISE_SYSTEM_V2。
所有题目只能依据给定原文，四选一且只有一个正确答案。
每题 explanation_md 用中文解释正确选项为什么成立，并指出原文依据或合理推断链。
精读输出 8 题：主旨 1、细节 3、推断 2、语境词义 2；泛读输出 4 题，每维各 1。
focus_vocab 只选原文真实出现且对理解重要的词。"""

READING_RESPONSE_GRADE_SYSTEM_V1 = """你是英语阅读回应反馈师。版本：READING_RESPONSE_GRADE_SYSTEM_V1。
根据原文与题目要求评价摘要或短回应，summary_score 为 0-20。
反馈指出遗漏、误读与一个最值得改进的表达，不得引入原文之外的事实。"""

WRITING_PROMPT_SYSTEM_V1 = """你是英语课程写作出题器。版本：WRITING_PROMPT_SYSTEM_V1。
题目必须匹配课程轨道、当前学习级别和建议字数；不得复制受版权保护的真实考试题。
如果提供阅读材料，应设计摘要、回应、复述或改写任务，并要求引用材料信息。"""


class ReadingMetadataOut(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    estimated_level: str = Field(pattern=r"^(A1|A2|B1|B2|C1)$")
    candidate_words: list[CandidateWord] = Field(max_length=200)


class AssessmentGeneratedOut(BaseModel):
    questions: list[ReadingQuestionPrivate] = Field(min_length=8, max_length=8)
    writing: AssessmentWritingPrompt


class SyntheticAssessmentOut(AssessmentGeneratedOut):
    title: str = Field(min_length=1, max_length=256)
    passage_md: str = Field(
        min_length=100,
        max_length=8000,
        description="必须包含 300-700 个英文单词",
    )
    estimated_level: str = Field(pattern=r"^(A1|A2|B1|B2|C1)$")

    @field_validator("passage_md")
    @classmethod
    def passage_has_assessment_length(cls, value: str) -> str:
        word_count = len(re.findall(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b", value))
        if not 300 <= word_count <= 700:
            raise ValueError(f"原创测评短文必须为 300-700 个英文词，实际 {word_count} 词")
        return value


class ReadingResponseGradeOut(BaseModel):
    summary_score: int = Field(ge=0, le=20)
    feedback_md: str = Field(min_length=40, max_length=4000)


class IntensiveReadingExerciseGenerated(ReadingExerciseGenerated):
    questions: list[ReadingQuestionGenerated] = Field(min_length=8, max_length=8)


class ExtensiveReadingExerciseGenerated(ReadingExerciseGenerated):
    questions: list[ReadingQuestionGenerated] = Field(min_length=4, max_length=4)

    @field_validator("questions", mode="before")
    @classmethod
    def keep_one_question_per_dimension(cls, questions):
        if not isinstance(questions, list) or len(questions) <= 4:
            return questions
        selected = []
        for dimension in ("main_idea", "detail", "inference", "context_vocab"):
            match = next(
                (
                    question
                    for question in questions
                    if (
                        question.get("dimension")
                        if isinstance(question, dict)
                        else getattr(question, "dimension", None)
                    )
                    == dimension
                ),
                None,
            )
            if match is not None:
                selected.append(match)
        return selected if len(selected) == 4 else questions


def language_source_dir(course_id: uuid.UUID, source_id: uuid.UUID) -> Path:
    from app.services.storage import course_dir

    directory = course_dir(course_id) / "language" / str(source_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _public_ip(address: str) -> bool:
    value = ipaddress.ip_address(address)
    return not (
        value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_reserved
        or value.is_unspecified
    )


async def _assert_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("检索来源必须是公开 HTTPS 页面")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "metadata.google.internal"}:
        raise ValueError("检索来源地址不安全")
    addresses = await asyncio.to_thread(
        lambda: {info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    )
    if not addresses or not all(_public_ip(address) for address in addresses):
        raise ValueError("检索来源地址不安全")


async def _fetch_search_page(url: str) -> tuple[str, str, str]:
    await _assert_public_https(url)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=8.0),
        headers={"User-Agent": "LearnLoop/1.0 educational material fetcher"},
    ) as client:
        response = await client.get(url)
    await _assert_public_https(str(response.url))
    if response.status_code != 200:
        raise ValueError(f"来源返回 HTTP {response.status_code}")
    if len(response.content) > 2_000_000:
        raise ValueError("来源正文超过 2MB")
    content_type = response.headers.get("content-type", "").casefold()
    if content_type and not any(kind in content_type for kind in ("text/html", "text/plain")):
        raise ValueError("来源不是可读文本页面")
    soup = BeautifulSoup(response.content, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    paragraphs = [node.get_text(" ", strip=True) for node in soup.find_all(["h1", "h2", "h3", "p", "li"])]
    content = "\n\n".join(dict.fromkeys(text for text in paragraphs if len(text) >= 20))
    if english_word_count(content) < 100:
        raise ValueError("来源正文不足 100 个英文词")
    return title[:256] or "AI 检索英语材料", content[:180_000], str(response.url)


def english_word_count(text: str) -> int:
    return len(re.findall(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b", text))


def assessment_profile_status_after_build(profile: LanguageProfile, assessment: LanguageAssessment) -> str:
    if assessment.kind == "periodic" and profile.latest_assessment_at is not None:
        return profile.assessment_status
    return "ready"


def _split_passages(markdown: str, *, target_words: int = 1200) -> list[ReadingPassageAnalysis]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]
    passages: list[ReadingPassageAnalysis] = []
    current: list[str] = []
    current_words = 0
    for block in blocks:
        block_words = english_word_count(block)
        if current and current_words + block_words > target_words:
            content = "\n\n".join(current)
            content = content[:20_000]
            if len(content.strip()) >= 100:
                passages.append(
                    ReadingPassageAnalysis(
                        title=f"第 {len(passages) + 1} 部分",
                        content_md=content,
                        word_count=english_word_count(content),
                    )
                )
            current = []
            current_words = 0
        current.append(block)
        current_words += block_words
        if len(passages) >= 99:
            break
    if current:
        content = "\n\n".join(current)
        content = content[:20_000]
        if len(content.strip()) >= 100:
            passages.append(
                ReadingPassageAnalysis(
                    title=f"第 {len(passages) + 1} 部分",
                    content_md=content,
                    word_count=english_word_count(content),
                )
            )
    return [
        passage
        for passage in passages
        if passage.word_count > 0 and len(passage.content_md.strip()) >= 100
    ]


def _track_context(course: Course, profile: LanguageProfile) -> str:
    track = course.language_track or "custom"
    return f"课程轨道：{TRACK_LABELS[track]}；当前级别：{profile.overall_level or '未分级'}"


def level_from_score(score: int) -> str:
    if score >= 85:
        return "C1"
    if score >= 70:
        return "B2"
    if score >= 55:
        return "B1"
    if score >= 40:
        return "A2"
    return "A1"


def adjusted_reading_level(base_level: str, score: int) -> str:
    shift = 1 if score >= 88 else 0 if score >= 68 else -1 if score >= 48 else -2
    return LEVELS[max(0, min(len(LEVELS) - 1, LEVELS.index(base_level) + shift))]


def lower_level(*levels: str) -> str:
    return min(levels, key=LEVELS.index)


def wilson_estimate(population: int, correct: int, sample_size: int) -> tuple[int, int, int]:
    if population <= 0 or sample_size <= 0:
        return 0, 0, 0
    z = 1.96
    proportion = correct / sample_size
    denominator = 1 + z * z / sample_size
    center = (proportion + z * z / (2 * sample_size)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / sample_size + z * z / (4 * sample_size * sample_size)
    ) / denominator
    estimate = round(population * proportion)
    return estimate, max(0, round(population * (center - margin))), min(
        population, round(population * (center + margin))
    )


def recommendation(
    level: str, daily_minutes: int, performance_factor: float = 1.0
) -> tuple[int, int, int, int]:
    words, intensive, extensive, writing = LEVEL_TARGETS[level]
    time_factor = max(0.75, min(1.5, daily_minutes / 45))
    daily_words = max(5, min(30, round(words * time_factor * performance_factor)))
    return daily_words, intensive, extensive, writing


def performance_factor(scores: list[int]) -> float:
    if not scores:
        return 1.0
    average = sum(scores) / len(scores)
    if average < 60:
        return 0.8
    if average >= 85:
        return 1.1
    return 1.0


async def recent_language_performance(
    db: AsyncSession, course_id: uuid.UUID, *, now: datetime | None = None
) -> dict:
    cutoff = (now or utcnow()) - timedelta(days=7)
    reading_rows = list(
        (
            await db.execute(
                select(ReadingAttempt.score, ReadingAttempt.created_at)
                .join(ReadingExercise, ReadingExercise.id == ReadingAttempt.exercise_id)
                .where(
                    ReadingExercise.course_id == course_id,
                    ReadingAttempt.created_at >= cutoff,
                    ReadingAttempt.feedback_md != "",
                )
                .order_by(ReadingAttempt.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    writing_rows = list(
        (
            await db.execute(
                select(WritingSubmission.score, WritingSubmission.graded_at)
                .join(WritingExercise, WritingExercise.id == WritingSubmission.exercise_id)
                .where(
                    WritingExercise.course_id == course_id,
                    WritingSubmission.graded_at.is_not(None),
                    WritingSubmission.graded_at >= cutoff,
                )
                .order_by(WritingSubmission.graded_at.desc())
                .limit(20)
            )
        ).all()
    )
    reading = [
        {"score": int(score), "at": created_at.isoformat()}
        for score, created_at in reading_rows
    ]
    writing = [
        {"score": int(score), "at": graded_at.isoformat()}
        for score, graded_at in writing_rows
        if graded_at is not None
    ]
    scores = [item["score"] for item in [*reading, *writing]]
    return {
        "reading": reading,
        "writing": writing,
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "factor": performance_factor(scores),
    }


async def refresh_language_recommendation(
    db: AsyncSession, course: Course, profile: LanguageProfile
) -> int:
    if profile.overall_level not in LEVEL_TARGETS:
        return profile.recommended_daily_words
    recent = await recent_language_performance(db, course.id)
    daily_words, _, _, _ = recommendation(
        profile.overall_level, course.daily_minutes, float(recent["factor"])
    )
    profile.recommended_daily_words = daily_words
    if profile.intensity_mode == "adaptive":
        course.settings = {**(course.settings or {}), "daily_new_words": daily_words}
    return daily_words


def source_vocab_coverage(
    source: LanguageSource, profile: LanguageProfile
) -> tuple[float | None, str | None]:
    if profile.vocab_total <= 0 or not source.analysis:
        return None, None
    try:
        analysis = ReadingSourceAnalysisV1.model_validate(source.analysis)
    except ValueError:
        return None, None
    candidate_tokens = {
        match.group(0).casefold()
        for candidate in analysis.candidate_words
        for match in re.finditer(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b", candidate.word)
    }
    if not candidate_tokens:
        return None, None
    passage_tokens = [
        match.group(0).casefold()
        for passage in analysis.passages
        for match in re.finditer(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b", passage.content_md)
    ]
    if not passage_tokens:
        return None, None
    candidate_occurrences = sum(token in candidate_tokens for token in passage_tokens)
    known_rate = max(0.0, min(1.0, profile.vocab_known_estimate / profile.vocab_total))
    estimated_unknown = candidate_occurrences * (1 - known_rate)
    coverage = round(max(0.0, min(100.0, (1 - estimated_unknown / len(passage_tokens)) * 100)), 1)
    if coverage >= 98:
        mode = "extensive"
    elif coverage >= 95:
        mode = "assisted_extensive"
    else:
        mode = "intensive_challenge"
    return coverage, mode


async def ensure_language_profile(db: AsyncSession, course: Course) -> LanguageProfile:
    profile = await db.get(LanguageProfile, course.id)
    if profile is None:
        if course.kind != "language":
            raise ValueError("仅英语课程支持能力画像")
        profile = LanguageProfile(
            course_id=course.id,
            assessment_status="active" if course.language_track is None else "required",
        )
        db.add(profile)
        await db.flush()
    return profile


async def _analyze_source(
    db: AsyncSession, job: Job, *, source_id: uuid.UUID | None = None
) -> dict:
    source_id = source_id or uuid.UUID(job.payload["source_id"])
    source = await db.get(LanguageSource, source_id)
    if source is None:
        return {"skipped": "语言来源不存在"}
    if source.status == "ready":
        return {"source_id": str(source.id), "status": "ready"}
    source.status = "processing"
    source.error = None
    await db.commit()
    try:
        path = Path(source.stored_path)
        result = await parse_file(
            path,
            path.suffix.lower(),
            source.course_id,
            source.id,
            source.original_name,
        )
        parsed_path = language_source_dir(source.course_id, source.id) / "parsed.md"
        parsed_path.write_text(result.markdown, encoding="utf-8")
        word_count = english_word_count(result.markdown)
        if word_count < 50:
            raise ValueError("阅读材料英文内容不足 50 词")
        passages = _split_passages(result.markdown)
        if not passages:
            raise ValueError("阅读材料无法切分为有效文章")
        metadata: ReadingMetadataOut = await runtime.run_structured(
            agent="planner",
            operation="analyze_language_source",
            tier=Tier.low,
            response_model=ReadingMetadataOut,
            system_prompt=READING_ANALYSIS_SYSTEM_V1,
            stable_blocks=None,
            volatile=(
                f"文件名：{source.original_name}\n总词数：{word_count}\n\n"
                f"# 原文节选\n{result.markdown[:30_000]}"
            ),
            course_id=source.course_id,
            job_id=job.id,
            max_tokens=8192,
            temperature=0.1,
            thinking=False,
        )
        analysis = ReadingSourceAnalysisV1(
            title=metadata.title,
            estimated_level=metadata.estimated_level,
            word_count=word_count,
            candidate_words=metadata.candidate_words,
            passages=passages,
        )
        source.parsed_md_path = str(parsed_path)
        source.word_count = word_count
        source.estimated_level = metadata.estimated_level
        provenance = source.analysis.get("provenance") if isinstance(source.analysis, dict) else None
        source.analysis = analysis.model_dump(mode="json")
        if isinstance(provenance, dict):
            source.analysis["provenance"] = provenance
        source.status = "ready"
        profile = await db.get(LanguageProfile, source.course_id)
        if profile is not None and not (
            isinstance(provenance, dict) and provenance.get("kind") == "plan_auto_search"
        ):
            profile.assessment_status = "stale" if profile.latest_assessment_at else "required"
        await db.commit()
        return {
            "source_id": str(source.id),
            "word_count": word_count,
            "estimated_level": metadata.estimated_level,
            "passages": len(passages),
            "candidate_words": len(metadata.candidate_words),
        }
    except Exception as exc:
        await db.rollback()
        source = await db.get(LanguageSource, source_id)
        if source is not None:
            source.status = "failed"
            source.error = f"阅读材料分析失败: {type(exc).__name__}: {exc}"[:2000]
            await db.commit()
        raise


@job_handler("analyze_language_source", heavy=True)
async def handle_analyze_language_source(db: AsyncSession, job: Job) -> dict:
    return await _analyze_source(db, job)


async def _search_candidates(
    *, course_id: uuid.UUID, job_id: uuid.UUID, query: str, operation: str
) -> SearchCandidates:
    return await runtime.run_structured(
        agent="planner",
        operation=operation,
        tier=Tier.high,
        response_model=SearchCandidates,
        system_prompt=LANGUAGE_SEARCH_SYSTEM_V1,
        volatile=f"学习者需求：{query}\n请检索最多 5 篇候选文章。",
        course_id=course_id,
        job_id=job_id,
        max_tokens=2048,
        temperature=0.1,
        thinking=True,
        web_search=True,
    )


async def ensure_plan_language_source(
    db: AsyncSession, course: Course, profile: LanguageProfile, job: Job
) -> LanguageSource:
    ready = (
        await db.execute(
            select(LanguageSource)
            .where(LanguageSource.course_id == course.id, LanguageSource.kind == "reading")
            .where(LanguageSource.status == "ready")
            .order_by(LanguageSource.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ready is not None:
        return ready
    level = profile.overall_level or "B1"
    track = TRACK_LABELS.get(course.language_track or "custom", "英语")
    query = (
        f"a long-form public English article for {track} learners at {level} level; "
        "clear factual or explanatory topic, suitable for both intensive and extensive reading, "
        "at least 900 English words"
    )
    candidates = await _search_candidates(
        course_id=course.id, job_id=job.id, query=query, operation="plan_auto_search"
    )
    chosen: tuple[str, str, str, str] | None = None
    failures: list[str] = []
    for candidate in candidates.candidates:
        try:
            page_title, content, final_url = await _fetch_search_page(candidate.url)
            if english_word_count(content) < 900:
                raise ValueError("正文不足 900 个英文词")
            chosen = (page_title, content, final_url, candidate.reason)
            break
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{candidate.url}: {type(exc).__name__}")
    if chosen is None:
        raise ValueError("计划自动检索未找到足够长的公开文章：" + "; ".join(failures)[:1200])
    page_title, content, final_url, reason = chosen
    digest = sha256_bytes(f"plan-auto:{course.id}:{final_url}".encode())
    source = (
        await db.execute(
            select(LanguageSource).where(
                LanguageSource.course_id == course.id,
                LanguageSource.kind == "reading",
                LanguageSource.sha256 == digest,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = LanguageSource(
            course_id=course.id,
            kind="reading",
            original_name=f"计划自动材料：{page_title}"[:256],
            mime="text/markdown",
            sha256=digest,
        )
        db.add(source)
        await db.flush()
    source.status = "processing"
    source.error = None
    source.original_name = f"计划自动材料：{page_title}"[:256]
    source.mime = "text/markdown"
    source.size_bytes = len(content.encode("utf-8"))
    source.analysis = {
        "provenance": {
            "kind": "plan_auto_search",
            "query": query,
            "url": final_url,
            "title": page_title,
            "selection_reason": reason,
            "retrieved_at": utcnow().isoformat(),
        }
    }
    directory = language_source_dir(course.id, source.id)
    parsed_path = directory / "original.md"
    parsed_path.write_text(content, encoding="utf-8")
    source.stored_path = str(parsed_path)
    await db.commit()
    await _analyze_source(db, job, source_id=source.id)
    return source


async def _search_language_source(db: AsyncSession, job: Job) -> dict:
    source_id = uuid.UUID(job.payload["source_id"])
    source = await db.get(LanguageSource, source_id)
    if source is None:
        return {"skipped": "语言来源不存在"}
    if source.status == "ready":
        return {"source_id": str(source.id), "status": "ready"}
    source.status = "processing"
    source.error = None
    await db.commit()
    query = str(job.payload["query"]).strip()
    candidates = await _search_candidates(
        course_id=source.course_id,
        job_id=job.id,
        query=query,
        operation="search_language_source",
    )
    chosen: tuple[str, str, str, str] | None = None
    failures: list[str] = []
    for candidate in candidates.candidates:
        try:
            page_title, content, final_url = await _fetch_search_page(candidate.url)
            chosen = (page_title, content, final_url, candidate.reason)
            break
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{candidate.url}: {type(exc).__name__}")
    if chosen is None:
        raise ValueError("检索到的候选文章均无法安全读取：" + "; ".join(failures)[:1200])
    page_title, content, final_url, reason = chosen
    directory = language_source_dir(source.course_id, source.id)
    parsed_path = directory / "original.md"
    parsed_path.write_text(content, encoding="utf-8")
    source.original_name = f"{page_title}（AI检索）.md"[:256]
    source.mime = "text/markdown"
    source.size_bytes = len(content.encode("utf-8"))
    source.stored_path = str(parsed_path)
    source.analysis = {
        "provenance": {
            "kind": "web_search",
            "query": query,
            "url": final_url,
            "title": page_title,
            "selection_reason": reason,
            "retrieved_at": utcnow().isoformat(),
        }
    }
    await db.commit()
    result = await _analyze_source(db, job)
    result["url"] = final_url
    return result


@job_handler("search_language_source", heavy=True)
async def handle_search_language_source(db: AsyncSession, job: Job) -> dict:
    return await _search_language_source(db, job)


@job_failure_handler("search_language_source")
async def fail_search_language_source(db: AsyncSession, job: Job, error: str) -> None:
    source = await db.get(LanguageSource, uuid.UUID(job.payload["source_id"]))
    if source is not None:
        source.status = "failed"
        source.error = f"英语材料检索失败: {error}"[:2000]
        await db.commit()


async def _assessment_candidates(
    db: AsyncSession, course_id: uuid.UUID, sources: list[LanguageSource]
) -> list[CandidateWord]:
    entries = list(
        (
            await db.execute(
                select(VocabEntry)
                .where(VocabEntry.course_id == course_id, VocabEntry.status != "archived")
                .order_by(VocabEntry.created_at, VocabEntry.id)
            )
        ).scalars()
    )
    by_word: dict[str, CandidateWord] = {
        entry.word.casefold(): CandidateWord(
            word=entry.word,
            meaning=entry.meaning_md,
            context=entry.example_md,
        )
        for entry in entries
        if entry.word.strip() and entry.meaning_md.strip()
    }
    for source in sources:
        if source.kind != "reading" or not source.analysis:
            continue
        analysis = ReadingSourceAnalysisV1.model_validate(source.analysis)
        for candidate in analysis.candidate_words:
            by_word.setdefault(candidate.word.casefold(), candidate)
    return list(by_word.values())


def _vocab_probes(candidates: list[CandidateWord], assessment_id: uuid.UUID) -> list[VocabProbePrivate]:
    meanings = list(dict.fromkeys(candidate.meaning for candidate in candidates))
    meaning_set = set(meanings)
    eligible = [
        candidate
        for candidate in candidates
        if len(meanings) - int(candidate.meaning in meaning_set) >= 3
    ]
    if len(eligible) < 4:
        return []
    rng = random.Random(assessment_id.int)
    sampled = eligible if len(eligible) <= 40 else rng.sample(eligible, 40)
    probes: list[VocabProbePrivate] = []
    for index, candidate in enumerate(sampled, start=1):
        distractor_pool = [meaning for meaning in meanings if meaning != candidate.meaning]
        distractors = rng.sample(distractor_pool, 3)
        options = [candidate.meaning, *distractors]
        rng.shuffle(options)
        probes.append(
            VocabProbePrivate(
                id=f"v{index}",
                word=candidate.word,
                options=options,
                correct_index=options.index(candidate.meaning),
                explanation_md=(
                    f"**{candidate.word}** 在本课程材料中的正确释义是："
                    f"{candidate.meaning}"
                ),
            )
        )
    return probes


def _choose_assessment_passage(sources: list[LanguageSource]) -> tuple[str, str, str] | None:
    choices: list[tuple[int, str, str, str]] = []
    for source in sources:
        if source.kind != "reading" or not source.analysis:
            continue
        analysis = ReadingSourceAnalysisV1.model_validate(source.analysis)
        for passage in analysis.passages:
            if 150 <= passage.word_count <= 2500:
                choices.append(
                    (
                        abs(passage.word_count - 700),
                        passage.title,
                        passage.content_md,
                        analysis.estimated_level,
                    )
                )
    if not choices:
        return None
    _, title, content, level = min(choices, key=lambda item: item[0])
    return title, content, level


async def _build_assessment(db: AsyncSession, job: Job) -> dict:
    assessment_id = uuid.UUID(job.payload["assessment_id"])
    assessment = await db.get(LanguageAssessment, assessment_id)
    if assessment is None:
        return {"skipped": "测评不存在"}
    if assessment.status == "ready":
        return {"assessment_id": str(assessment.id), "status": "ready"}
    course = await db.get(Course, assessment.course_id)
    profile = await db.get(LanguageProfile, assessment.course_id)
    if course is None or profile is None:
        return {"skipped": "英语课程画像不存在"}
    source_ids = [uuid.UUID(value) for value in assessment.source_ids]
    stmt = select(LanguageSource).where(
        LanguageSource.course_id == course.id,
        LanguageSource.status == "ready",
    )
    if source_ids:
        stmt = stmt.where(LanguageSource.id.in_(source_ids))
    sources = list((await db.execute(stmt.order_by(LanguageSource.created_at))).scalars())
    candidates = await _assessment_candidates(db, course.id, sources)
    if not sources and not candidates:
        raise ValueError("请先导入并完成处理的词表或阅读材料")
    probes = _vocab_probes(candidates, assessment.id)
    selected = _choose_assessment_passage(sources)
    if selected is None:
        sample_words = "、".join(candidate.word for candidate in candidates[:30]) or "无"
        synthetic: SyntheticAssessmentOut | None = None
        last_error = ""
        for _ in range(3):
            try:
                synthetic = await runtime.run_structured(
                    agent="planner",
                    operation="build_language_assessment",
                    tier=Tier.low,
                    response_model=SyntheticAssessmentOut,
                    system_prompt=ASSESSMENT_BUILD_SYSTEM_V2,
                    stable_blocks=[_track_context(course, profile)],
                    volatile=(
                        "没有上传阅读材料。请原创一篇 350-450 个英文单词的短文，"
                        "必须实际输出至少 300 个英文单词，自然使用部分目标词，"
                        "再基于短文生成阅读题和写作题。不得模仿或复制真题。\n"
                        f"目标词示例：{sample_words}\n"
                        f"上次失败信息：{last_error or '无'}"
                    ),
                    course_id=course.id,
                    job_id=job.id,
                    max_tokens=8192,
                    temperature=0.2,
                    thinking=False,
                )
                break
            except Exception as exc:
                last_error = str(exc)[-1500:]
        if synthetic is None:
            raise ValueError(f"无阅读来源的测评短文连续生成失败：{last_error}")
        title = synthetic.title
        passage = synthetic.passage_md
        estimated_level = synthetic.estimated_level
        generated = AssessmentGeneratedOut(
            questions=synthetic.questions,
            writing=synthetic.writing,
        )
    else:
        title, passage, estimated_level = selected
        generated = await runtime.run_structured(
            agent="planner",
            operation="build_language_assessment",
            tier=Tier.low,
            response_model=AssessmentGeneratedOut,
            system_prompt=ASSESSMENT_BUILD_SYSTEM_V2,
            stable_blocks=[_track_context(course, profile)],
            volatile=f"# 阅读原文\n标题：{title}\n\n{passage}\n\n请生成阅读题和写作题。",
            course_id=course.id,
            job_id=job.id,
            max_tokens=4096,
            temperature=0.1,
            thinking=False,
        )
    expected = {"main_idea": 1, "detail": 3, "inference": 2, "context_vocab": 2}
    actual = {key: 0 for key in expected}
    questions: list[ReadingQuestionPrivate] = []
    for index, question in enumerate(generated.questions, start=1):
        actual[question.dimension] += 1
        questions.append(question.model_copy(update={"id": f"r{index}"}))
    if actual != expected:
        raise ValueError(f"阅读测评题型分布不合规: {actual}")
    bundle = LanguageAssessmentBundleV1(
        vocab_items=probes,
        vocab_population=len(candidates),
        reading=AssessmentReadingPrivate(
            title=title,
            passage_md=passage,
            estimated_level=estimated_level,
            questions=questions,
        ),
        writing=generated.writing,
    )
    assessment.item_bundle = bundle.model_dump(mode="json")
    assessment.status = "ready"
    assessment.error = None
    profile.assessment_status = assessment_profile_status_after_build(profile, assessment)
    await db.commit()
    return {
        "assessment_id": str(assessment.id),
        "vocab_items": len(probes),
        "reading_questions": len(questions),
    }


@job_handler("build_language_assessment")
async def handle_build_language_assessment(db: AsyncSession, job: Job) -> dict:
    return await _build_assessment(db, job)


def _score_choices(private_items: list, answers: list[dict]) -> tuple[int, dict[str, int]]:
    answer_map = {str(answer["item_id"]): int(answer["choice_index"]) for answer in answers}
    if len(answer_map) != len(answers):
        raise ValueError("作答项不能重复")
    expected_ids = {item.id for item in private_items}
    if set(answer_map) != expected_ids:
        raise ValueError("作答项与题目不一致")
    by_dimension: dict[str, list[int]] = {}
    correct = 0
    for item in private_items:
        value = int(answer_map[item.id] == item.correct_index)
        correct += value
        dimension = getattr(item, "dimension", "vocab")
        by_dimension.setdefault(dimension, []).append(value)
    scores = {
        dimension: round(sum(values) / len(values) * 100)
        for dimension, values in by_dimension.items()
    }
    return correct, scores


async def _grade_writing(
    *,
    course_id: uuid.UUID,
    job_id: uuid.UUID,
    track_context: str,
    prompt_md: str,
    requirements_md: str,
    submission_md: str,
    previous_feedback: str = "",
) -> WritingGradeOut:
    return await runtime.run_structured(
        agent="companion",
        operation="grade_language_writing",
        tier=Tier.medium,
        response_model=WritingGradeOut,
        system_prompt=WRITING_GRADE_SYSTEM_V1,
        stable_blocks=[track_context],
        volatile=(
            f"# 题目\n{prompt_md}\n\n# 要求\n{requirements_md}\n\n"
            f"# 上一版反馈\n{previous_feedback or '无'}\n\n# 学生文本\n{submission_md}"
        ),
        course_id=course_id,
        job_id=job_id,
        max_tokens=4096,
        temperature=0.1,
        thinking=False,
    )


async def _grade_assessment(db: AsyncSession, job: Job) -> dict:
    assessment_id = uuid.UUID(job.payload["assessment_id"])
    assessment = (
        await db.execute(
            select(LanguageAssessment)
            .where(LanguageAssessment.id == assessment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if assessment is None:
        return {"skipped": "测评不存在"}
    if assessment.status == "graded":
        return {"skipped": "测评已批改"}
    if assessment.status != "submitted":
        raise ValueError("测评尚未提交")
    course = await db.get(Course, assessment.course_id)
    profile = await db.get(LanguageProfile, assessment.course_id)
    if course is None or profile is None:
        raise ValueError("英语课程画像不存在")
    bundle = LanguageAssessmentBundleV1.model_validate(assessment.item_bundle)
    responses = assessment.response_bundle
    vocab_correct, _ = _score_choices(bundle.vocab_items, responses.get("vocab_answers", []))
    reading_correct, reading_dimensions = _score_choices(
        bundle.reading.questions, responses.get("reading_answers", [])
    )
    reading_score = round(reading_correct / len(bundle.reading.questions) * 100)
    writing_grade = await _grade_writing(
        course_id=course.id,
        job_id=job.id,
        track_context=_track_context(course, profile),
        prompt_md=bundle.writing.prompt_md,
        requirements_md=bundle.writing.requirements_md,
        submission_md=str(responses["writing_md"]),
    )
    writing_score = writing_grade.score
    reading_level = adjusted_reading_level(bundle.reading.estimated_level, reading_score)
    writing_level = level_from_score(writing_score)
    overall_level = lower_level(reading_level, writing_level)
    estimate, ci_low, ci_high = wilson_estimate(
        bundle.vocab_population, vocab_correct, len(bundle.vocab_items)
    )
    recent = await recent_language_performance(db, course.id)
    daily_words, intensive, extensive, writing_words = recommendation(
        overall_level, course.daily_minutes, float(recent["factor"])
    )
    profile.overall_level = overall_level
    profile.reading_level = reading_level
    profile.writing_level = writing_level
    profile.vocab_total = bundle.vocab_population
    profile.vocab_known_estimate = estimate
    profile.vocab_ci_low = ci_low
    profile.vocab_ci_high = ci_high
    profile.reading_score = reading_score
    profile.writing_score = writing_score
    profile.recommended_daily_words = daily_words
    profile.intensive_target_words = intensive
    profile.extensive_target_words = extensive
    profile.writing_target_words = writing_words
    profile.assessment_status = "active"
    profile.latest_assessment_at = utcnow()
    if profile.intensity_mode == "adaptive":
        course.settings = {**(course.settings or {}), "daily_new_words": daily_words}
    assessment.result = {
        "version": "LANGUAGE_ASSESSMENT_RESULT_V1",
        "vocab": {
            "sample_size": len(bundle.vocab_items),
            "correct": vocab_correct,
            "population": bundle.vocab_population,
            "known_estimate": estimate,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "confidence": "low" if len(bundle.vocab_items) < 20 else "normal",
        },
        "reading": {
            "score": reading_score,
            "level": reading_level,
            "dimensions": reading_dimensions,
        },
        "writing": {
            "score": writing_score,
            "level": writing_level,
            "task_fulfillment": writing_grade.task_fulfillment,
            "organization": writing_grade.organization,
            "grammar": writing_grade.grammar,
            "vocabulary": writing_grade.vocabulary,
            "feedback_md": writing_grade.feedback_md,
        },
        "overall_level": overall_level,
        "recommended_daily_words": daily_words,
        "disclaimer": "课程学习分级，不是官方 CEFR 认证。",
    }
    assessment.status = "graded"
    assessment.graded_at = profile.latest_assessment_at
    await db.commit()
    return {"assessment_id": str(assessment.id), "overall_level": overall_level}


@job_handler("grade_language_assessment")
async def handle_grade_language_assessment(db: AsyncSession, job: Job) -> dict:
    return await _grade_assessment(db, job)


def _truncate_to_words(content: str, maximum: int) -> str:
    matches = list(re.finditer(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b", content))
    if len(matches) <= maximum:
        return content
    return content[: matches[maximum - 1].end()].rstrip()


def _passage_for_mode(
    analysis: ReadingSourceAnalysisV1, mode: str, target: int
) -> ReadingPassageAnalysis:
    minimum, maximum = (300, 800) if mode == "intensive" else (600, 3000)
    candidates: list[ReadingPassageAnalysis] = []
    for passage in analysis.passages:
        if passage.word_count < minimum:
            continue
        content = _truncate_to_words(passage.content_md, maximum)
        candidates.append(
            passage.model_copy(
                update={"content_md": content, "word_count": english_word_count(content)}
            )
        )
    if not candidates:
        label = "精读" if mode == "intensive" else "泛读"
        raise ValueError(f"当前材料没有适合{label}的连续原文（至少 {minimum} 词）")
    return min(candidates, key=lambda passage: abs(passage.word_count - target))


async def _generate_reading(db: AsyncSession, job: Job) -> dict:
    existing = (
        await db.execute(
            select(ReadingExercise).where(ReadingExercise.created_by_job_id == job.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"exercise_id": str(existing.id), "mode": existing.mode}
    course_id = uuid.UUID(job.payload["course_id"])
    source_id = uuid.UUID(job.payload["source_id"])
    course = await db.get(Course, course_id)
    source = await db.get(LanguageSource, source_id)
    profile = await db.get(LanguageProfile, course_id)
    if course is None or source is None or profile is None or source.course_id != course_id:
        return {"skipped": "英语课程或阅读来源不存在"}
    if source.kind != "reading" or source.status != "ready":
        raise ValueError("阅读来源尚未处理完成")
    mode = str(job.payload["mode"])
    target = profile.intensive_target_words if mode == "intensive" else profile.extensive_target_words
    analysis = ReadingSourceAnalysisV1.model_validate(source.analysis)
    passage = _passage_for_mode(analysis, mode, target)
    response_model = (
        IntensiveReadingExerciseGenerated
        if mode == "intensive"
        else ExtensiveReadingExerciseGenerated
    )
    generated: ReadingExerciseGenerated = await runtime.run_structured(
        agent="planner",
        operation=f"generate_{mode}_reading",
        tier=Tier.low,
        response_model=response_model,
        system_prompt=READING_EXERCISE_SYSTEM_V2,
        stable_blocks=[_track_context(course, profile)],
        volatile=f"模式：{mode}\n# 原文\n{passage.content_md}",
        course_id=course_id,
        job_id=job.id,
        max_tokens=4096,
        temperature=0.1,
        thinking=False,
    )
    expected_count = 8 if mode == "intensive" else 4
    if len(generated.questions) != expected_count:
        raise ValueError(f"{mode} 阅读题必须为 {expected_count} 道")
    expected_dimensions = (
        {"main_idea": 1, "detail": 3, "inference": 2, "context_vocab": 2}
        if mode == "intensive"
        else {"main_idea": 1, "detail": 1, "inference": 1, "context_vocab": 1}
    )
    if Counter(question.dimension for question in generated.questions) != expected_dimensions:
        raise ValueError(f"{mode} 阅读题维度分布不符合要求")
    question_rows = [
        {
            "id": f"q{index}",
            **question.model_dump(mode="json"),
        }
        for index, question in enumerate(generated.questions, start=1)
    ]
    exercise = ReadingExercise(
        created_by_job_id=job.id,
        course_id=course_id,
        source_id=source_id,
        plan_item_id=(
            uuid.UUID(job.payload["plan_item_id"]) if job.payload.get("plan_item_id") else None
        ),
        mode=mode,
        title=generated.title,
        passage_md=passage.content_md,
        target_level=profile.overall_level or source.estimated_level,
        questions=question_rows,
        focus_vocab=[word.model_dump(mode="json") for word in generated.focus_vocab],
    )
    db.add(exercise)
    await db.commit()
    return {"exercise_id": str(exercise.id), "mode": mode}


@job_handler("generate_language_reading")
async def handle_generate_language_reading(db: AsyncSession, job: Job) -> dict:
    return await _generate_reading(db, job)


async def _complete_plan_item(db: AsyncSession, item_id: uuid.UUID | None, now: datetime) -> None:
    if item_id is None:
        return
    item = await db.get(PlanItem, item_id)
    if item is not None and item.status in ("pending", "in_progress"):
        item.status = "done"
        item.completed_at = now
        item.actual_minutes = max(1, min(item.est_minutes, 240))


async def _grade_reading(db: AsyncSession, job: Job) -> dict:
    attempt_id = uuid.UUID(job.payload["attempt_id"])
    attempt = (
        await db.execute(
            select(ReadingAttempt).where(ReadingAttempt.id == attempt_id).with_for_update()
        )
    ).scalar_one_or_none()
    if attempt is None:
        return {"skipped": "阅读作答不存在"}
    exercise = await db.get(ReadingExercise, attempt.exercise_id)
    if exercise is None:
        return {"skipped": "阅读练习不存在"}
    if exercise.status == "graded" and attempt.feedback_md:
        return {"skipped": "阅读练习已批改"}
    course = await db.get(Course, exercise.course_id)
    profile = await db.get(LanguageProfile, exercise.course_id)
    if course is None or profile is None:
        raise ValueError("英语课程画像不存在")
    private_questions = {
        str(question["id"]): question for question in exercise.questions
    }
    answer_map = {str(answer["item_id"]): int(answer["choice_index"]) for answer in attempt.answers}
    if set(answer_map) != set(private_questions):
        raise ValueError("阅读作答项与题目不一致")
    dimension_values: dict[str, list[int]] = {
        "main_idea": [], "detail": [], "inference": [], "context_vocab": []
    }
    for item_id, question in private_questions.items():
        dimension_values[str(question["dimension"])].append(
            int(answer_map[item_id] == int(question["correct_index"]))
        )
    response_grade: ReadingResponseGradeOut = await runtime.run_structured(
        agent="companion",
        operation="grade_language_reading",
        tier=Tier.medium,
        response_model=ReadingResponseGradeOut,
        system_prompt=READING_RESPONSE_GRADE_SYSTEM_V1,
        stable_blocks=[_track_context(course, profile)],
        volatile=(
            f"# 原文\n{exercise.passage_md}\n\n# 模式\n{exercise.mode}\n\n"
            f"# 学生摘要或回应\n{attempt.response_md}"
        ),
        course_id=course.id,
        job_id=job.id,
        max_tokens=2048,
        temperature=0.1,
        thinking=False,
    )
    scores = {
        key: round(sum(values) / len(values) * 20) if values else 0
        for key, values in dimension_values.items()
    }
    attempt.main_idea = scores["main_idea"]
    attempt.detail = scores["detail"]
    attempt.inference = scores["inference"]
    attempt.context_vocab = scores["context_vocab"]
    attempt.summary = response_grade.summary_score
    attempt.score = sum(scores.values()) + response_grade.summary_score
    attempt.feedback_md = response_grade.feedback_md
    exercise.status = "graded"
    now = utcnow()
    await refresh_language_recommendation(db, course, profile)
    await _complete_plan_item(db, exercise.plan_item_id, now)
    await award_points(
        db,
        user_id=course.user_id,
        course_id=course.id,
        kind="reading_done",
        base_amount=10,
        note=f"reading:{exercise.id}",
        point_date=shanghai_date(now),
        ref_id=exercise.id,
    )
    await db.commit()
    return {"attempt_id": str(attempt.id), "score": attempt.score}


@job_handler("grade_language_reading")
async def handle_grade_language_reading(db: AsyncSession, job: Job) -> dict:
    return await _grade_reading(db, job)


async def _generate_writing(db: AsyncSession, job: Job) -> dict:
    existing = (
        await db.execute(
            select(WritingExercise).where(WritingExercise.created_by_job_id == job.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"exercise_id": str(existing.id)}
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    profile = await db.get(LanguageProfile, course_id)
    if course is None or profile is None:
        return {"skipped": "英语课程画像不存在"}
    source_reading_id = (
        uuid.UUID(job.payload["source_reading_id"]) if job.payload.get("source_reading_id") else None
    )
    reading = await db.get(ReadingExercise, source_reading_id) if source_reading_id else None
    if reading is not None and reading.course_id != course_id:
        raise ValueError("阅读练习不属于当前课程")
    context = reading.passage_md[:12_000] if reading else "（无关联阅读材料）"
    generated: WritingPromptGenerated = await runtime.run_structured(
        agent="planner",
        operation="generate_language_writing",
        tier=Tier.low,
        response_model=WritingPromptGenerated,
        system_prompt=WRITING_PROMPT_SYSTEM_V1,
        stable_blocks=[_track_context(course, profile)],
        volatile=(
            f"建议字数：{profile.writing_target_words}\n课程目标：{course.goal_text}\n\n"
            f"# 关联阅读\n{context}"
        ),
        course_id=course_id,
        job_id=job.id,
        max_tokens=2048,
        temperature=0.2,
        thinking=False,
    )
    exercise = WritingExercise(
        created_by_job_id=job.id,
        course_id=course_id,
        source_reading_id=source_reading_id,
        plan_item_id=(
            uuid.UUID(job.payload["plan_item_id"]) if job.payload.get("plan_item_id") else None
        ),
        prompt_md=generated.prompt_md,
        requirements_md=generated.requirements_md,
        target_level=profile.overall_level,
    )
    db.add(exercise)
    await db.commit()
    return {"exercise_id": str(exercise.id)}


@job_handler("generate_language_writing")
async def handle_generate_language_writing(db: AsyncSession, job: Job) -> dict:
    return await _generate_writing(db, job)


async def _grade_writing_submission(db: AsyncSession, job: Job) -> dict:
    submission_id = uuid.UUID(job.payload["submission_id"])
    submission = (
        await db.execute(
            select(WritingSubmission)
            .where(WritingSubmission.id == submission_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if submission is None:
        return {"skipped": "写作提交不存在"}
    if submission.graded_at is not None:
        return {"skipped": "写作提交已批改"}
    exercise = await db.get(WritingExercise, submission.exercise_id)
    if exercise is None:
        return {"skipped": "写作练习不存在"}
    course = await db.get(Course, exercise.course_id)
    profile = await db.get(LanguageProfile, exercise.course_id)
    if course is None or profile is None:
        raise ValueError("英语课程画像不存在")
    previous = ""
    if submission.version > 1:
        previous_row = (
            await db.execute(
                select(WritingSubmission).where(
                    WritingSubmission.exercise_id == exercise.id,
                    WritingSubmission.version == submission.version - 1,
                )
            )
        ).scalar_one_or_none()
        previous = previous_row.feedback_md if previous_row else ""
    grade = await _grade_writing(
        course_id=course.id,
        job_id=job.id,
        track_context=_track_context(course, profile),
        prompt_md=exercise.prompt_md,
        requirements_md=exercise.requirements_md,
        submission_md=submission.submission_md,
        previous_feedback=previous,
    )
    submission.task_fulfillment = grade.task_fulfillment
    submission.organization = grade.organization
    submission.grammar = grade.grammar
    submission.vocabulary = grade.vocabulary
    submission.score = grade.score
    submission.feedback_md = grade.feedback_md
    submission.graded_at = utcnow()
    await refresh_language_recommendation(db, course, profile)
    if submission.version >= 2:
        exercise.status = "completed"
        exercise.completed_at = submission.graded_at
        await _complete_plan_item(db, exercise.plan_item_id, submission.graded_at)
        await award_points(
            db,
            user_id=course.user_id,
            course_id=course.id,
            kind="writing_done",
            base_amount=15,
            note=f"writing:{exercise.id}",
            point_date=shanghai_date(submission.graded_at),
            ref_id=exercise.id,
        )
    else:
        exercise.status = "revision_due"
    await db.commit()
    return {
        "submission_id": str(submission.id),
        "version": submission.version,
        "score": submission.score,
        "status": exercise.status,
    }


@job_handler("grade_language_writing")
async def handle_grade_language_writing(db: AsyncSession, job: Job) -> dict:
    return await _grade_writing_submission(db, job)


async def _fail_assessment(db: AsyncSession, job: Job, error: str) -> None:
    assessment_id = uuid.UUID(job.payload["assessment_id"])
    assessment = await db.get(LanguageAssessment, assessment_id)
    if assessment is None or assessment.status == "graded":
        return
    assessment.status = "failed"
    assessment.error = error[:2000]
    profile = await db.get(LanguageProfile, assessment.course_id)
    if profile is not None:
        if not (assessment.kind == "periodic" and profile.assessment_status == "active"):
            profile.assessment_status = "stale" if profile.latest_assessment_at else "required"


for _kind in ("build_language_assessment", "grade_language_assessment"):
    job_failure_handler(_kind)(_fail_assessment)


@job_failure_handler("grade_language_reading")
async def fail_language_reading(db: AsyncSession, job: Job, error: str) -> None:
    attempt = await db.get(ReadingAttempt, uuid.UUID(job.payload["attempt_id"]))
    if attempt is None:
        return
    exercise = await db.get(ReadingExercise, attempt.exercise_id)
    if exercise is not None and exercise.status == "submitted":
        exercise.status = "open"
    await db.delete(attempt)


@job_failure_handler("grade_language_writing")
async def fail_language_writing(db: AsyncSession, job: Job, error: str) -> None:
    submission = await db.get(WritingSubmission, uuid.UUID(job.payload["submission_id"]))
    if submission is None:
        return
    exercise = await db.get(WritingExercise, submission.exercise_id)
    if exercise is not None and exercise.status == "grading":
        exercise.status = "revision_due" if submission.version > 1 else "open"
    await db.delete(submission)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
