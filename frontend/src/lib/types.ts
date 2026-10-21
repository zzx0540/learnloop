export interface CourseOut {
  id: string;
  name: string;
  goal_text: string;
  status: string;
  kind: CourseKind;
  language_track: LanguageTrack | null;
  daily_minutes: number;
  target_date: string | null;
  created_at: string;
}

export type CourseKind = "knowledge" | "language" | "programming";
export type LanguageTrack = "ielts" | "cet6" | "postgraduate" | "new_concept" | "custom";
export type LanguageLevel = "A1" | "A2" | "B1" | "B2" | "C1";

export interface DocumentOut {
  id: string;
  original_name: string;
  size_bytes: number;
  status: "uploaded" | "parsing" | "parsed" | "ingesting" | "ingested" | "failed";
  page_count: number | null;
  error: string | null;
  created_at: string;
}

export interface WikiPageBrief {
  slug: string;
  title: string;
  kind: "concept" | "chapter_summary" | "index" | "schema";
  version: number;
  updated_at: string;
}

export interface WikiPageOut extends WikiPageBrief {
  id: string;
  content_md: string;
  out_links: string[];
  source_refs: { document_id: string; locator: string }[];
  updated_by: string;
}

export interface WikiRevisionOut {
  id: string;
  version: number;
  content_md: string;
  change_summary: string;
  created_at: string;
}

export interface WikiLogOut {
  id: string;
  op: string;
  entry_md: string;
  created_at: string;
}

export interface JobOut {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface KnowledgePointOut {
  id: string;
  slug: string;
  title: string;
  summary: string;
  primary_wiki_page_id: string | null;
  importance: number;
  difficulty: number;
  status: string;
  mastery_score: number;
  projected_basic_at: string | null;
  projected_full_at: string | null;
  created_at: string;
}

export interface GraphNodeOut {
  id: string;
  slug: string;
  title: string;
  summary: string;
  status: string;
  mastery_score: number;
  importance: number;
  difficulty: number;
  projected_basic_at: string | null;
  projected_full_at: string | null;
  primary_wiki_page_id: string | null;
}

export interface GraphEdgeOut {
  id: string;
  source: string;
  target: string;
  relation: "prerequisite" | "related" | "part_of" | "example_of";
}

export interface GraphOut {
  nodes: GraphNodeOut[];
  edges: GraphEdgeOut[];
}

export interface PlanItemKpOut {
  id: string;
  slug: string;
  title: string;
}

export interface PlanItemOut {
  id: string;
  seq: number;
  scheduled_date: string;
  type: "learn" | "review_block" | "quiz" | "stage_exam" | "buffer" | "reading" | "writing";
  title: string;
  description_md: string;
  est_minutes: number;
  status: "pending" | "in_progress" | "done" | "skipped";
  completed_at: string | null;
  actual_minutes: number | null;
  knowledge_points: PlanItemKpOut[];
}

export interface PlanItemStudyKpOut {
  id: string;
  slug: string;
  title: string;
  summary: string;
  recall_prompt: string;
  primary_wiki_slug: string | null;
  status: string;
}

export interface PlanItemStudyOut {
  id: string;
  plan_id: string;
  course_id: string;
  seq: number;
  scheduled_date: string;
  type: PlanItemOut["type"];
  title: string;
  description_md: string;
  est_minutes: number;
  status: PlanItemOut["status"];
  completed_at: string | null;
  actual_minutes: number | null;
  kps: PlanItemStudyKpOut[];
}

export interface PlanDayOut {
  date: string;
  items: PlanItemOut[];
}

export interface PlanDetailOut {
  id: string;
  version: number;
  status: string;
  goal_summary: string;
  daily_minutes: number;
  start_date: string;
  end_date_estimate: string | null;
  projected_basic_date: string | null;
  projected_full_date: string | null;
  rationale_md: string;
  days: PlanDayOut[];
}

export interface CourseOverviewOut {
  course: CourseOut;
  active_plan: {
    id: string;
    version: number;
    start_date: string;
    end_date_estimate: string | null;
    daily_minutes: number;
    rationale_md: string;
  } | null;
  kp_stats: {
    total: number;
    by_status: Record<string, number>;
  };
  projected_basic_date: string | null;
  projected_full_date: string | null;
  assumption_text: string;
  latest_progress_summary_md: string | null;
  open_weakness_count: number;
  today_quiz: {
    quiz_id: string;
    status: QuizStatus;
    my_attempt_status: AttemptStatus | null;
    total_score: number | null;
    max_score: number | null;
  } | null;
}

export interface ChatOut {
  id: string;
  course_id: string;
  agent: string;
  title: string;
  context: ChatContext;
  created_at: string;
  last_message_at: string;
}

export interface ChatContext {
  mode: "learning" | "review" | "general";
  plan_item_id?: string | null;
}

export interface ChatMessageOut {
  id: string;
  role: "user" | "assistant";
  content_md: string;
  images: unknown[];
  scope_class: "in_syllabus" | "adjacent" | "off_topic" | null;
  created_at: string;
}

export interface ReviewKpOut {
  id: string;
  slug: string;
  title: string;
  summary: string;
  recall_prompt: string;
  primary_wiki_slug: string | null;
  status: string;
}

export interface ReviewVocabOut {
  id: string;
  word: string;
  phonetic: string;
  meaning_md: string;
  example_md: string;
}

export interface VocabCardSummaryOut {
  stability: number;
  due: string;
  state: number;
}

export interface VocabEntryOut extends ReviewVocabOut {
  course_id: string;
  status: "pending" | "active" | "archived";
  activated_at: string | null;
  source_note: string;
  created_at: string;
  card: VocabCardSummaryOut | null;
}

export interface VocabStatsOut {
  total: number;
  pending: number;
  active: number;
  archived: number;
  mastered: number;
  daily_new_words: number;
  projected_days: number;
}

export interface VocabImportJobOut {
  job_id: string;
  source_id: string;
}

export interface LanguageProfileOut {
  course_id: string;
  target_language: "en";
  track: LanguageTrack;
  assessment_status: "required" | "building" | "ready" | "active" | "stale";
  overall_level: LanguageLevel | null;
  reading_level: LanguageLevel | null;
  writing_level: LanguageLevel | null;
  vocab_total: number;
  vocab_known_estimate: number;
  vocab_ci_low: number;
  vocab_ci_high: number;
  reading_score: number | null;
  writing_score: number | null;
  intensity_mode: "adaptive" | "manual";
  daily_new_words: number;
  recommended_daily_words: number;
  intensive_target_words: number;
  extensive_target_words: number;
  writing_target_words: number;
  latest_assessment_at: string | null;
  updated_at: string;
}

export interface LanguageCandidateWord {
  word: string;
  meaning: string;
  context: string;
  source_rank: number | null;
  level_hint: string;
}

export interface LanguagePassageAnalysis {
  title: string;
  content_md: string;
  word_count: number;
}

export interface LanguageSourceOut {
  id: string;
  course_id: string;
  kind: "vocab" | "reading";
  original_name: string;
  size_bytes: number;
  status: "uploaded" | "processing" | "ready" | "failed";
  word_count: number;
  estimated_level: LanguageLevel | null;
  analysis: {
    title?: string;
    estimated_level?: LanguageLevel;
    word_count?: number;
    candidate_words?: LanguageCandidateWord[];
    passages?: LanguagePassageAnalysis[];
    provenance?: { url?: string; title?: string; query?: string; retrieved_at?: string };
  };
  vocab_coverage_percent: number | null;
  recommended_mode: "extensive" | "assisted_extensive" | "intensive_challenge" | null;
  error: string | null;
  created_at: string;
}

export interface LanguageAssessmentChoice {
  id: string;
  word?: string;
  dimension?: "main_idea" | "detail" | "inference" | "context_vocab";
  prompt?: string;
  options: string[];
}

export interface LanguageAssessmentItems {
  version: "LANGUAGE_ASSESSMENT_V1";
  vocab_items: LanguageAssessmentChoice[];
  vocab_population: number;
  reading: {
    title: string;
    passage_md: string;
    estimated_level: LanguageLevel;
    questions: LanguageAssessmentChoice[];
  };
  writing: {
    prompt_md: string;
    requirements_md: string;
    min_words: number;
    max_words: number;
  };
}

export interface ChoiceReviewOut {
  item_id: string;
  selected_index: number;
  correct_index: number;
  is_correct: boolean;
  explanation_md: string;
}

export interface LanguageAssessmentOut {
  id: string;
  course_id: string;
  kind: "baseline" | "source_check" | "periodic";
  status: "building" | "ready" | "submitted" | "graded" | "failed";
  source_ids: string[];
  items: LanguageAssessmentItems | null;
  result: Record<string, unknown>;
  review: {
    vocab: ChoiceReviewOut[];
    reading: ChoiceReviewOut[];
    writing_md: string;
  } | null;
  created_by_job_id: string | null;
  created_at: string;
  submitted_at: string | null;
  graded_at: string | null;
  error: string | null;
}

export interface LanguageAssessmentJobOut {
  assessment_id: string;
  job_id: string;
}

export interface ReadingQuestionOut {
  id: string;
  dimension: "main_idea" | "detail" | "inference" | "context_vocab";
  prompt: string;
  options: string[];
}

export interface ReadingAttemptOut {
  id: string;
  attempt_no: number;
  response_md: string;
  duration_seconds: number;
  main_idea: number;
  detail: number;
  inference: number;
  context_vocab: number;
  summary: number;
  score: number;
  feedback_md: string;
  reviews: ChoiceReviewOut[];
  created_at: string;
}

export interface ReadingExerciseOut {
  id: string;
  course_id: string;
  source_id: string;
  plan_item_id: string | null;
  mode: "intensive" | "extensive";
  title: string;
  passage_md: string;
  target_level: LanguageLevel | null;
  questions: ReadingQuestionOut[];
  focus_vocab: LanguageCandidateWord[];
  status: "open" | "submitted" | "graded";
  created_at: string;
  attempts: ReadingAttemptOut[];
}

export interface WritingSubmissionOut {
  id: string;
  version: number;
  submission_md: string;
  task_fulfillment: number;
  organization: number;
  grammar: number;
  vocabulary: number;
  score: number;
  feedback_md: string;
  graded_at: string | null;
  created_at: string;
}

export interface WritingExerciseOut {
  id: string;
  course_id: string;
  source_reading_id: string | null;
  plan_item_id: string | null;
  prompt_md: string;
  requirements_md: string;
  target_level: LanguageLevel | null;
  status: "open" | "grading" | "revision_due" | "completed";
  created_at: string;
  completed_at: string | null;
  submissions: WritingSubmissionOut[];
}

export type QuizStatus = "generating" | "ready" | "failed";
export type AttemptStatus = "in_progress" | "submitted" | "graded" | "expired";
export type QuestionType =
  | "single_choice"
  | "multi_choice"
  | "true_false"
  | "cloze"
  | "short_answer";

export interface AttemptSummaryOut {
  id: string;
  status: AttemptStatus;
  started_at: string;
  deadline_at: string | null;
  auto_submitted: boolean;
  submitted_at: string | null;
  graded_at: string | null;
  total_score: number | null;
  max_score: number | null;
}

export interface QuizListItemOut {
  id: string;
  kind: "daily" | "stage" | "formal_exam";
  title: string;
  scheduled_date: string;
  status: QuizStatus;
  question_count: number;
  time_limit_seconds: number | null;
  my_attempt: AttemptSummaryOut | null;
}

export interface QuestionOptionOut {
  key: string;
  text: string;
}

export interface AttemptAnswerOut {
  id: string;
  question_id: string;
  answer: Record<string, unknown>;
  is_correct: boolean | null;
  score: number | null;
  feedback_md: string;
  graded_by: "auto" | "llm" | "pending";
  graded_at: string | null;
  updated_at: string;
}

export interface QuestionOut {
  id: string;
  seq: number;
  qtype: QuestionType;
  stem_md: string;
  options: QuestionOptionOut[];
  answer?: Record<string, unknown>;
  explanation_md?: string;
  source_refs?: { wiki_slug: string }[];
  my_answer?: AttemptAnswerOut | null;
}

export interface QuizOut {
  id: string;
  course_id: string;
  kind: "daily" | "stage" | "formal_exam";
  title: string;
  scheduled_date: string;
  status: QuizStatus;
  plan_item_id: string | null;
  time_limit_seconds: number | null;
  my_attempt: AttemptSummaryOut | null;
  questions: QuestionOut[];
}

export interface AttemptOut extends AttemptSummaryOut {
  quiz_id: string | null;
  kind: "daily" | "stage" | "formal_exam";
  server_now: string;
}

export interface AttemptDetailOut extends AttemptOut {
  answers: AttemptAnswerOut[];
}

export interface ExamEventOut {
  id: string;
  kind: "fullscreen_exit" | "tab_blur" | "resume" | "forced_submit";
  created_at: string;
}

export interface SubmitAttemptOut extends AttemptSummaryOut {
  grading_job_id: string | null;
}

export interface WeaknessOut {
  id: string;
  kp_id: string;
  kp_slug: string;
  kp_title: string;
  kind:
    | "concept_confusion"
    | "memory_lapse"
    | "application_gap"
    | "calculation_error"
    | "expression_vague";
  severity: 1 | 2 | 3;
  status: "open" | "improving" | "resolved";
  seen_count: number;
  correct_streak: number;
  description: string;
  last_seen_at: string;
  created_at: string;
}

export interface ReviewCardOut {
  card_id: string;
  due: string;
  state: number;
  reps: number;
  kp: ReviewKpOut | null;
  vocab: ReviewVocabOut | null;
  intervals: {
    again_days: number;
    hard_days: number;
    good_days: number;
    easy_days: number;
  };
}

export interface ReviewQueueOut {
  cards: ReviewCardOut[];
  due_count: number;
  reviewed_today: number;
  activated_today: number;
}

export interface ReviewAnswerOut {
  card: {
    id: string;
    stability: number;
    difficulty: number;
    due: string;
    state: number;
    reps: number;
    lapses: number;
    last_review: string | null;
  };
  kp_status: string | null;
  next_due: string;
}

export interface TangentItemOut {
  id: string;
  course_id: string;
  chat_message_id: string | null;
  question: string;
  brief_answer: string;
  status: "parked" | "planned" | "answered" | "dismissed";
  created_at: string;
}

export interface StatsDayOut {
  date: string;
  study_seconds: number;
  review_count: number;
  learn_items_done: number;
}

export interface StatsMeOut {
  days: StatsDayOut[];
  streak: number;
}

export interface PointsSummaryOut {
  balance: number;
  streak: number;
  today_multiplier: number;
  today_earned: number;
}

export type PointsLedgerKind =
  | "study_minutes"
  | "item_done"
  | "review_done"
  | "quiz_score"
  | "reading_done"
  | "writing_done"
  | "redeem"
  | "market_course";

export interface PointsLedgerOut {
  id: string;
  course_id: string | null;
  kind: PointsLedgerKind;
  amount: number;
  multiplier: number;
  note: string;
  ref_id: string | null;
  created_at: string;
}

export interface RewardOut {
  id: string;
  title: string;
  description: string;
  cost: number;
  is_active: boolean;
  created_at: string;
}

export interface RedemptionOut {
  id: string;
  reward_id: string | null;
  title_snapshot: string;
  cost_snapshot: number;
  created_at: string;
}

export interface CompletedItemOut {
  title: string;
  type: string;
  course_name: string;
  completed_at: string;
  actual_minutes: number | null;
}

export interface AdminUserOut {
  id: string;
  username: string;
  nickname: string;
  role: string;
  created_at: string;
  last_login_at: string | null;
}

export interface InviteOut {
  code: string;
  used: number;
  max_uses: number;
  is_active: boolean;
  expires_at: string | null;
}

export interface AdminSettingsOut {
  llm_daily_token_budget_per_course: number;
  upload_max_mb: number;
  upload_quota_mb_per_user: number;
  llm_pricing: Record<string, { miss: number; hit: number; out: number }>;
}

export type WikiLintKind = "broken_link" | "orphan" | "contradiction" | "outdated";

export interface WikiLintFindingOut {
  id: string;
  page_id: string | null;
  page_slug: string | null;
  page_title: string | null;
  kind: WikiLintKind;
  detail_md: string;
  status: "open" | "resolved" | "dismissed";
  created_at: string;
}

export interface LlmCostItem {
  day: string;
  model: string;
  agent: string;
  calls: number;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  errors: number;
  cache_hit_ratio: number;
}

export interface MarketCourseOut {
  id: string;
  title: string;
  description: string;
  kind: CourseKind;
  price_points: number;
  current_version: number;
  document_count: number;
  knowledge_point_count: number;
  vocab_count: number;
  acquisition_id: string | null;
  acquisition_status: "pending" | "completed" | "failed" | null;
  owned_course_id: string | null;
  acquisition_error: string | null;
  published_at: string | null;
}

export interface MarketCourseListOut {
  items: MarketCourseOut[];
  total: number;
}

export interface MarketAcquisitionOut {
  id: string;
  market_course_id: string;
  target_course_id: string | null;
  title_snapshot: string;
  price_snapshot: number;
  version_snapshot: number;
  status: "pending" | "completed" | "failed";
  error: string | null;
  job_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MarketSourceCourseOut {
  id: string;
  name: string;
  owner_username: string;
  kind: CourseKind;
  status: string;
  document_count: number;
  is_owned_by_admin: boolean;
}

export interface MarketCourseAdminOut {
  id: string;
  source_course_id: string | null;
  source_course_name: string | null;
  source_owner_username: string | null;
  source_owned_by_admin: boolean;
  title: string;
  description: string;
  price_points: number;
  status: "draft" | "published" | "archived";
  current_version: number;
  version_status: "building" | "ready" | "failed" | null;
  document_count: number;
  knowledge_point_count: number;
  vocab_count: number;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}
