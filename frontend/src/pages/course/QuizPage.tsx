import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Loader2,
  Printer,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { AppShell } from "../../components/AppShell";
import {
  AnswerQuestion,
  ResultQuestion,
  type AnswerDraft,
  type SaveState,
} from "../../components/quiz/QuestionComponents";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type {
  AttemptDetailOut,
  AttemptOut,
  QuestionOut,
  QuizOut,
  SubmitAttemptOut,
} from "../../lib/types";
import { useHeartbeat } from "../../lib/useHeartbeat";

function emptyDraft(question: QuestionOut): AnswerDraft | undefined {
  if (question.qtype === "single_choice" || question.qtype === "multi_choice") return { selected: [] };
  if (question.qtype === "cloze" || question.qtype === "short_answer") return { text: "" };
  return undefined;
}

function parseDraft(question: QuestionOut, value: Record<string, unknown>): AnswerDraft | undefined {
  if (question.qtype === "single_choice" || question.qtype === "multi_choice") {
    const selected = value.selected;
    return Array.isArray(selected) && selected.every((item) => typeof item === "string")
      ? { selected }
      : emptyDraft(question);
  }
  if (question.qtype === "true_false") {
    return typeof value.selected_bool === "boolean" ? { selected_bool: value.selected_bool } : undefined;
  }
  return typeof value.text === "string" ? { text: value.text } : emptyDraft(question);
}

function isAnswered(draft: AnswerDraft | undefined): boolean {
  if (!draft) return false;
  if ("selected" in draft) return draft.selected.length > 0;
  if ("selected_bool" in draft) return true;
  return draft.text.trim().length > 0;
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export default function QuizPage() {
  const { courseId, quizId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({});
  const [pageError, setPageError] = useState("");
  const [gradingTimedOut, setGradingTimedOut] = useState(false);
  const hydratedAttempt = useRef<string | null>(null);
  const draftsRef = useRef<Record<string, AnswerDraft>>({});
  const savedSignatures = useRef<Record<string, string>>({});
  const versions = useRef<Record<string, number>>({});
  const timers = useRef<Record<string, number>>({});

  const quizQuery = useQuery<QuizOut>({
    queryKey: ["quiz", quizId],
    queryFn: () => api.get(`/api/v1/quizzes/${quizId}`),
    enabled: Boolean(quizId),
    refetchInterval: (query) => query.state.data?.status === "generating" ? 2000 : false,
  });
  const quiz = quizQuery.data;
  const attemptQuery = useQuery<AttemptOut>({
    queryKey: ["quiz-attempt", quizId],
    queryFn: () => api.post(`/api/v1/quizzes/${quizId}/attempts`),
    enabled: quiz?.status === "ready",
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const attemptId = attemptQuery.data?.id ?? quiz?.my_attempt?.id;
  const attemptDetailQuery = useQuery<AttemptDetailOut>({
    queryKey: ["attempt-detail", attemptId],
    queryFn: () => api.get(`/api/v1/attempts/${attemptId}`),
    enabled: Boolean(attemptId),
    refetchInterval: (query) =>
      query.state.data?.status === "submitted" && !gradingTimedOut ? 3000 : false,
  });
  const attempt = attemptDetailQuery.data;
  const attemptStatus = attempt?.status ?? attemptQuery.data?.status ?? quiz?.my_attempt?.status;
  const resultMode = attemptStatus === "submitted" || attemptStatus === "graded";

  useHeartbeat(courseId, "learn", true);

  useEffect(() => {
    return () => Object.values(timers.current).forEach((timer) => window.clearTimeout(timer));
  }, []);

  useEffect(() => {
    if (!attempt || !quiz || attempt.status !== "in_progress" || hydratedAttempt.current === attempt.id) return;
    const byId = new Map(attempt.answers.map((answer) => [answer.question_id, answer]));
    const restored: Record<string, AnswerDraft> = {};
    const signatures: Record<string, string> = {};
    quiz.questions.forEach((question) => {
      const saved = byId.get(question.id);
      const draft = saved ? parseDraft(question, saved.answer) : emptyDraft(question);
      if (draft) {
        restored[question.id] = draft;
        if (saved) signatures[question.id] = JSON.stringify(draft);
      }
    });
    draftsRef.current = restored;
    savedSignatures.current = signatures;
    setDrafts(restored);
    hydratedAttempt.current = attempt.id;
  }, [attempt, quiz]);

  useEffect(() => {
    if (attempt?.status !== "submitted" || !attempt.submitted_at) {
      setGradingTimedOut(false);
      return;
    }
    const remaining = 90_000 - (Date.now() - new Date(attempt.submitted_at).getTime());
    if (remaining <= 0) {
      setGradingTimedOut(true);
      return;
    }
    setGradingTimedOut(false);
    const timer = window.setTimeout(() => setGradingTimedOut(true), remaining);
    return () => window.clearTimeout(timer);
  }, [attempt?.status, attempt?.submitted_at]);

  useEffect(() => {
    if (attempt?.status === "graded") void quizQuery.refetch();
  }, [attempt?.status]);

  async function persistAnswer(questionId: string, draft: AnswerDraft, version: number): Promise<boolean> {
    if (!attemptId) return false;
    setSaveStates((current) => ({ ...current, [questionId]: { status: "saving" } }));
    try {
      await api.put(`/api/v1/attempts/${attemptId}/answers/${questionId}`, draft);
      if (versions.current[questionId] === version) {
        savedSignatures.current[questionId] = JSON.stringify(draft);
        setSaveStates((current) => ({ ...current, [questionId]: { status: "saved" } }));
      }
      return true;
    } catch (error) {
      if (versions.current[questionId] === version) {
        setSaveStates((current) => ({
          ...current,
          [questionId]: {
            status: "error",
            message: error instanceof ApiError ? error.message : "保存失败",
          },
        }));
      }
      return false;
    }
  }

  function changeAnswer(questionId: string, draft: AnswerDraft) {
    const version = (versions.current[questionId] ?? 0) + 1;
    versions.current[questionId] = version;
    draftsRef.current = { ...draftsRef.current, [questionId]: draft };
    setDrafts(draftsRef.current);
    setSaveStates((current) => ({ ...current, [questionId]: { status: "saving" } }));
    if (timers.current[questionId]) window.clearTimeout(timers.current[questionId]);
    timers.current[questionId] = window.setTimeout(() => {
      void persistAnswer(questionId, draft, version);
    }, 800);
  }

  function retrySave(questionId: string) {
    const draft = draftsRef.current[questionId];
    if (!draft) return;
    const version = (versions.current[questionId] ?? 0) + 1;
    versions.current[questionId] = version;
    void persistAnswer(questionId, draft, version);
  }

  async function flushPendingAnswers(): Promise<boolean> {
    const pending = Object.entries(draftsRef.current).filter(
      ([questionId, draft]) => JSON.stringify(draft) !== savedSignatures.current[questionId],
    );
    const results = await Promise.all(
      pending.map(([questionId, draft]) => {
        if (timers.current[questionId]) window.clearTimeout(timers.current[questionId]);
        const version = (versions.current[questionId] ?? 0) + 1;
        versions.current[questionId] = version;
        return persistAnswer(questionId, draft, version);
      }),
    );
    return results.every(Boolean);
  }

  const submit = useMutation({
    mutationFn: () => api.post<SubmitAttemptOut>(`/api/v1/attempts/${attemptId}/submit`),
    onSuccess: async (submitted) => {
      setPageError("");
      queryClient.setQueryData<AttemptDetailOut>(["attempt-detail", attemptId], (current) =>
        current ? { ...current, ...submitted } : current,
      );
      await Promise.all([
        quizQuery.refetch(),
        attemptDetailQuery.refetch(),
        queryClient.invalidateQueries({ queryKey: ["overview", courseId] }),
        queryClient.invalidateQueries({ queryKey: ["plan", courseId] }),
        queryClient.invalidateQueries({ queryKey: ["course-quizzes", courseId] }),
      ]);
    },
    onError: (error) => setPageError(error instanceof ApiError ? error.message : "交卷失败"),
  });

  const answeredCount = useMemo(
    () => quiz?.questions.filter((question) => isAnswered(drafts[question.id])).length ?? 0,
    [drafts, quiz?.questions],
  );
  const answerByQuestion = useMemo(
    () => new Map((attempt?.answers ?? []).map((answer) => [answer.question_id, answer])),
    [attempt?.answers],
  );
  const wrongCount = quiz?.questions.filter((question) => {
    const answer = answerByQuestion.get(question.id) ?? question.my_answer;
    return answer?.score !== null && answer?.score !== undefined && answer.score < 1;
  }).length ?? 0;

  async function submitNow() {
    if (!quiz) return;
    const unanswered = quiz.questions.length - answeredCount;
    if (unanswered > 0 && !window.confirm(`还有 ${unanswered} 题未作答，确定交卷吗？`)) return;
    const saved = await flushPendingAnswers();
    if (!saved) {
      setPageError("部分答案保存失败，请重试后再交卷。");
      return;
    }
    submit.mutate();
  }

  if (quizQuery.isPending || (quiz?.status === "ready" && attemptQuery.isPending)) {
    return (
      <AppShell>
        <div className="py-16 text-center text-sm text-slate-400">加载试卷中…</div>
      </AppShell>
    );
  }

  const loadError = quizQuery.error ?? attemptQuery.error ?? attemptDetailQuery.error;
  if (!quiz || loadError) {
    return (
      <AppShell>
        <Alert>{loadError instanceof ApiError ? loadError.message : "试卷加载失败"}</Alert>
      </AppShell>
    );
  }

  if (quiz.status !== "ready") {
    return (
      <AppShell>
        <Link to={`/courses/${courseId}/plan`} className="inline-flex items-center gap-1 text-sm text-slate-500">
          <ArrowLeft className="h-4 w-4" />返回计划
        </Link>
        <Card className="mt-5 text-center">
          {quiz.status === "failed" ? (
            <>
              <CircleAlert className="mx-auto h-10 w-10 text-red-400" />
              <h1 className="mt-4 text-lg font-bold text-slate-900">今日生成失败</h1>
              <p className="mt-2 text-sm text-slate-500">明日凌晨将自动重试</p>
            </>
          ) : (
            <>
              <Loader2 className="mx-auto h-10 w-10 animate-spin text-indigo-400" />
              <h1 className="mt-4 text-lg font-bold text-slate-900">试卷生成中</h1>
            </>
          )}
        </Card>
      </AppShell>
    );
  }

  const wikiLink = (slug: string) => navigate(`/courses/${courseId}/wiki/${slug}`);

  return (
    <AppShell>
      <div className="min-w-0 pb-8">
        <Link
          to={`/courses/${courseId}/plan`}
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-indigo-600"
        >
          <ArrowLeft className="h-4 w-4" />返回计划
        </Link>

        <div className="mt-5 flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="text-sm text-indigo-600">{quiz.kind === "stage" ? "阶段考" : "今日试卷"}</div>
            <h1 className="mt-1 break-words text-2xl font-bold text-slate-900">{quiz.title}</h1>
            <p className="mt-1 text-sm text-slate-400">共 {quiz.questions.length} 题</p>
          </div>
          {!resultMode && quiz.kind !== "formal_exam" && (
            <Button
              variant="outline"
              className="shrink-0"
              onClick={() => navigate(`/courses/${courseId}/print/quiz/${quiz.id}?mode=blank`)}
            >
              <Printer className="h-4 w-4" />打印空白卷
            </Button>
          )}
        </div>

        {pageError && <div className="mt-4"><Alert>{pageError}</Alert></div>}

        {!resultMode && (
          <div className="sticky top-14 z-[9] -mx-4 mt-5 border-y border-slate-200 bg-white/95 px-4 py-3 shadow-sm backdrop-blur">
            <div className="mx-auto flex max-w-4xl items-center gap-3">
              <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-indigo-500 transition-all"
                  style={{ width: `${quiz.questions.length ? (answeredCount / quiz.questions.length) * 100 : 0}%` }}
                />
              </div>
              <div className="shrink-0 text-sm font-medium text-slate-600">已答 {answeredCount}/{quiz.questions.length}</div>
            </div>
          </div>
        )}

        {resultMode ? (
          <div className="mx-auto mt-6 max-w-4xl space-y-4">
            {attemptStatus === "graded" && (
              <div className="flex justify-end">
                <Button
                  variant="outline"
                  onClick={() => navigate(`/courses/${courseId}/print/quiz/${quiz.id}?mode=graded`)}
                >
                  <Printer className="h-4 w-4" />打印已批阅卷
                </Button>
              </div>
            )}
            <Card className="overflow-hidden bg-gradient-to-br from-indigo-600 to-indigo-700 text-white">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <div className="text-sm text-indigo-100">本次得分</div>
                  <div className="mt-1 text-4xl font-bold">
                    {formatScore(attempt?.total_score ?? quiz.my_attempt?.total_score)}
                    <span className="text-xl font-medium text-indigo-200">
                      /{formatScore(attempt?.max_score ?? quiz.my_attempt?.max_score)}
                    </span>
                  </div>
                </div>
                {attemptStatus === "submitted" && !gradingTimedOut && (
                  <div className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-2 text-sm">
                    <Loader2 className="h-4 w-4 animate-spin" />AI 批改中，当前为客观题得分…
                  </div>
                )}
                {attemptStatus === "graded" && (
                  <div className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-2 text-sm">
                    <CheckCircle2 className="h-4 w-4" />批改完成
                  </div>
                )}
              </div>
            </Card>

            {attemptStatus === "submitted" && gradingTimedOut && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
                AI 批改仍在进行，已停止自动刷新。请稍后回来查看结果。
                <button
                  className="ml-auto inline-flex shrink-0 items-center gap-1 font-medium text-amber-900 underline"
                  onClick={() => {
                    void attemptDetailQuery.refetch();
                  }}
                >
                  <RefreshCw className="h-3.5 w-3.5" />刷新
                </button>
              </div>
            )}

            {wrongCount > 0 && (
              <button
                className="flex w-full items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-left text-sm font-medium text-red-700"
                onClick={() => navigate(`/courses/${courseId}/overview#weaknesses`)}
              >
                <CircleAlert className="h-4 w-4 shrink-0" />错 {wrongCount} 题，已记入薄弱记录
                <span className="ml-auto shrink-0">查看 →</span>
              </button>
            )}

            {quiz.questions.map((question) => (
              <ResultQuestion
                key={question.id}
                question={question}
                answer={answerByQuestion.get(question.id) ?? question.my_answer}
                onWikiLink={wikiLink}
              />
            ))}
          </div>
        ) : (
          <div className="mx-auto mt-6 max-w-4xl space-y-4">
            {quiz.questions.map((question) => (
              <AnswerQuestion
                key={question.id}
                question={question}
                draft={drafts[question.id]}
                saveState={saveStates[question.id]}
                onChange={(draft) => changeAnswer(question.id, draft)}
                onRetry={() => retrySave(question.id)}
              />
            ))}
            <Card className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-slate-500">
                已答 {answeredCount} 题，未答 {quiz.questions.length - answeredCount} 题
              </div>
              <Button disabled={submit.isPending || !attemptId} onClick={() => void submitNow()}>
                {submit.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                交卷
              </Button>
            </Card>
          </div>
        )}
      </div>
    </AppShell>
  );
}
