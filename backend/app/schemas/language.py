import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LanguageTrack = Literal["ielts", "cet6", "postgraduate", "new_concept", "custom"]
LanguageLevel = Literal["A1", "A2", "B1", "B2", "C1"]
AssessmentKind = Literal["baseline", "source_check", "periodic"]
ReadingMode = Literal["intensive", "extensive"]


class LanguageProfileUpdate(BaseModel):
    track: LanguageTrack | None = None
    intensity_mode: Literal["adaptive", "manual"] | None = None
    daily_new_words: int | None = Field(default=None, ge=5, le=100)

    @model_validator(mode="after")
    def manual_words_match_mode(self):
        if self.daily_new_words is not None and self.intensity_mode not in (None, "manual"):
            raise ValueError("自动强度模式不能手动设置每日词量")
        return self


class LanguageProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: uuid.UUID
    target_language: Literal["en"]
    track: LanguageTrack = "custom"
    assessment_status: Literal["required", "building", "ready", "active", "stale"]
    overall_level: LanguageLevel | None
    reading_level: LanguageLevel | None
    writing_level: LanguageLevel | None
    vocab_total: int
    vocab_known_estimate: int
    vocab_ci_low: int
    vocab_ci_high: int
    reading_score: int | None
    writing_score: int | None
    intensity_mode: Literal["adaptive", "manual"]
    daily_new_words: int = Field(default=20, ge=5, le=100)
    recommended_daily_words: int
    intensive_target_words: int
    extensive_target_words: int
    writing_target_words: int
    latest_assessment_at: datetime | None
    updated_at: datetime


class CandidateWord(BaseModel):
    word: str = Field(min_length=1, max_length=64)
    meaning: str = Field(min_length=1, max_length=500)
    context: str = Field(default="", max_length=500)
    source_rank: int | None = Field(default=None, ge=1)
    level_hint: str = Field(default="", max_length=32)


class ReadingPassageAnalysis(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    content_md: str = Field(min_length=100, max_length=20000)
    word_count: int = Field(ge=1, le=5000)


class ReadingSourceAnalysisV1(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    estimated_level: LanguageLevel
    word_count: int = Field(ge=1)
    candidate_words: list[CandidateWord] = Field(max_length=200)
    passages: list[ReadingPassageAnalysis] = Field(min_length=1, max_length=100)


class LanguageSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    kind: Literal["vocab", "reading"]
    original_name: str
    size_bytes: int
    status: Literal["uploaded", "processing", "ready", "failed"]
    word_count: int
    estimated_level: LanguageLevel | None
    analysis: dict
    vocab_coverage_percent: float | None = Field(default=None, ge=0, le=100)
    recommended_mode: Literal[
        "extensive", "assisted_extensive", "intensive_challenge"
    ] | None = None
    error: str | None
    created_at: datetime


class ReadingTextCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    content_md: str = Field(min_length=100, max_length=200_000)


class LanguageSourceSearchCreate(BaseModel):
    query: str = Field(min_length=3, max_length=200)


class LanguageSourceJobOut(BaseModel):
    source_id: uuid.UUID
    job_id: uuid.UUID


class VocabProbeOut(BaseModel):
    id: str = Field(pattern=r"^v\d+$")
    word: str
    options: list[str] = Field(min_length=4, max_length=4)


class SearchCandidate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    url: str = Field(pattern=r"^https://[^\s]+$")
    reason: str = Field(default="", max_length=500)


class SearchCandidates(BaseModel):
    candidates: list[SearchCandidate] = Field(min_length=1, max_length=5)


class VocabProbePrivate(VocabProbeOut):
    correct_index: int = Field(ge=0, le=3)
    explanation_md: str = Field(default="", max_length=2000)


class ReadingQuestionOut(BaseModel):
    id: str = Field(pattern=r"^r\d+$")
    dimension: Literal["main_idea", "detail", "inference", "context_vocab"]
    prompt: str = Field(min_length=5, max_length=1000)
    options: list[str] = Field(min_length=4, max_length=4)


class ReadingQuestionPrivate(ReadingQuestionOut):
    correct_index: int = Field(ge=0, le=3)
    explanation_md: str = Field(min_length=10, max_length=2000)


class AssessmentReadingOut(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    passage_md: str = Field(min_length=100, max_length=20_000)
    estimated_level: LanguageLevel
    questions: list[ReadingQuestionOut] = Field(min_length=8, max_length=8)


class AssessmentReadingPrivate(AssessmentReadingOut):
    questions: list[ReadingQuestionPrivate] = Field(min_length=8, max_length=8)


class AssessmentWritingPrompt(BaseModel):
    prompt_md: str = Field(min_length=10, max_length=2000)
    requirements_md: str = Field(min_length=10, max_length=1000)
    min_words: int = Field(ge=50, le=1000)
    max_words: int = Field(ge=80, le=5000)

    @model_validator(mode="after")
    def bounds_are_ordered(self):
        if self.max_words <= self.min_words:
            raise ValueError("写作字数上限必须大于下限")
        return self


class LanguageAssessmentBundleV1(BaseModel):
    version: Literal["LANGUAGE_ASSESSMENT_V1"] = "LANGUAGE_ASSESSMENT_V1"
    vocab_items: list[VocabProbePrivate] = Field(max_length=40)
    vocab_population: int = Field(ge=0)
    reading: AssessmentReadingPrivate
    writing: AssessmentWritingPrompt

    @model_validator(mode="before")
    @classmethod
    def add_legacy_reading_explanations(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("reading"), dict):
            return value
        reading = dict(value["reading"])
        questions = []
        for raw_question in reading.get("questions", []):
            question = dict(raw_question)
            if not str(question.get("explanation_md", "")).strip():
                options = question.get("options", [])
                correct_index = question.get("correct_index")
                correct = (
                    options[correct_index]
                    if isinstance(correct_index, int) and 0 <= correct_index < len(options)
                    else "标注选项"
                )
                question["explanation_md"] = (
                    f"正确答案是“{correct}”。请回到原文核对题干所指的信息与上下文。"
                )
            questions.append(question)
        reading["questions"] = questions
        return {**value, "reading": reading}


class LanguageAssessmentPublicOut(BaseModel):
    version: Literal["LANGUAGE_ASSESSMENT_V1"]
    vocab_items: list[VocabProbeOut]
    vocab_population: int
    reading: AssessmentReadingOut
    writing: AssessmentWritingPrompt


class LanguageAssessmentCreate(BaseModel):
    kind: AssessmentKind
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class LanguageAssessmentJobOut(BaseModel):
    assessment_id: uuid.UUID
    job_id: uuid.UUID


class ChoiceAnswer(BaseModel):
    item_id: str
    choice_index: int = Field(ge=0, le=3)


class LanguageAssessmentSubmit(BaseModel):
    vocab_answers: list[ChoiceAnswer] = Field(max_length=40)
    reading_answers: list[ChoiceAnswer] = Field(min_length=8, max_length=8)
    writing_md: str = Field(min_length=50, max_length=5000)


class ChoiceReviewOut(BaseModel):
    item_id: str
    selected_index: int = Field(ge=0, le=3)
    correct_index: int = Field(ge=0, le=3)
    is_correct: bool
    explanation_md: str = Field(min_length=1, max_length=2000)


class LanguageAssessmentReviewOut(BaseModel):
    vocab: list[ChoiceReviewOut]
    reading: list[ChoiceReviewOut]
    writing_md: str


class LanguageAssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    kind: AssessmentKind
    status: Literal["building", "ready", "submitted", "graded", "failed"]
    source_ids: list
    items: LanguageAssessmentPublicOut | None = None
    result: dict
    review: LanguageAssessmentReviewOut | None = None
    created_by_job_id: uuid.UUID | None
    created_at: datetime
    submitted_at: datetime | None
    graded_at: datetime | None
    error: str | None


class WritingGradeOut(BaseModel):
    task_fulfillment: int = Field(ge=0, le=25)
    organization: int = Field(ge=0, le=25)
    grammar: int = Field(ge=0, le=25)
    vocabulary: int = Field(ge=0, le=25)
    feedback_md: str = Field(min_length=80, max_length=8000)
    suggested_level: LanguageLevel

    @property
    def score(self) -> int:
        return self.task_fulfillment + self.organization + self.grammar + self.vocabulary


class ReadingQuestionGenerated(BaseModel):
    dimension: Literal["main_idea", "detail", "inference", "context_vocab"]
    prompt: str = Field(min_length=5, max_length=1000)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)
    explanation_md: str = Field(min_length=10, max_length=2000)


class ReadingExerciseGenerated(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    questions: list[ReadingQuestionGenerated] = Field(min_length=3, max_length=8)
    focus_vocab: list[CandidateWord] = Field(max_length=20)


class ReadingGenerateCreate(BaseModel):
    source_id: uuid.UUID
    mode: ReadingMode
    plan_item_id: uuid.UUID | None = None


class ReadingGenerateJobOut(BaseModel):
    job_id: uuid.UUID


class ReadingAnswerCreate(BaseModel):
    item_id: str
    choice_index: int = Field(ge=0, le=3)


class ReadingAttemptCreate(BaseModel):
    answers: list[ReadingAnswerCreate] = Field(min_length=3, max_length=8)
    response_md: str = Field(min_length=20, max_length=3000)
    duration_seconds: int = Field(ge=1, le=14_400)


class ReadingAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_no: int
    response_md: str
    duration_seconds: int
    main_idea: int
    detail: int
    inference: int
    context_vocab: int
    summary: int
    score: int
    feedback_md: str
    reviews: list[ChoiceReviewOut] = Field(default_factory=list)
    created_at: datetime


class ReadingExerciseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    source_id: uuid.UUID
    plan_item_id: uuid.UUID | None
    mode: ReadingMode
    title: str
    passage_md: str
    target_level: LanguageLevel | None
    questions: list
    focus_vocab: list
    status: Literal["open", "submitted", "graded"]
    created_at: datetime
    attempts: list[ReadingAttemptOut] = Field(default_factory=list)


class ReadingVocabAdd(BaseModel):
    words: list[str] = Field(min_length=1, max_length=20)


class WritingPromptGenerated(BaseModel):
    prompt_md: str = Field(min_length=10, max_length=2000)
    requirements_md: str = Field(min_length=10, max_length=1000)


class WritingGenerateCreate(BaseModel):
    source_reading_id: uuid.UUID | None = None
    plan_item_id: uuid.UUID | None = None


class WritingGenerateJobOut(BaseModel):
    job_id: uuid.UUID


class WritingSubmissionCreate(BaseModel):
    submission_md: str = Field(min_length=50, max_length=5000)


class WritingSubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    submission_md: str
    task_fulfillment: int
    organization: int
    grammar: int
    vocabulary: int
    score: int
    feedback_md: str
    graded_at: datetime | None
    created_at: datetime


class WritingExerciseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    source_reading_id: uuid.UUID | None
    plan_item_id: uuid.UUID | None
    prompt_md: str
    requirements_md: str
    target_level: LanguageLevel | None
    status: Literal["open", "grading", "revision_due", "completed"]
    created_at: datetime
    completed_at: datetime | None
    submissions: list[WritingSubmissionOut] = Field(default_factory=list)
