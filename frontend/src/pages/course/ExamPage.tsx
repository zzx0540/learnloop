import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Expand,
  GraduationCap,
  Loader2,
  Printer,
  RefreshCw,
  ShieldAlert,
  WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

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
  ExamEventOut,
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

function formatRemaining(milliseconds: number): string {
  if (!Number.isFinite(milliseconds)) return "--:--";
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

const eventLabels: Record<ExamEventOut["kind"], string> = {
  fullscreen_exit: "离开全屏",
  tab_blur: "切换到其他标签页",
  resume: "返回考试页面",
  forced_submit: "服务端强制交卷",
};

export default function ExamPage() {
  const { courseId, quizId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [startedAttemptId, setStartedAttemptId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({});
  const [pageError, setPageError] = useState("");
  const [fullscreenWarning, setFullscreenWarning] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const [remainingMs, setRemainingMs] = useState(Number.POSITIVE_INFINITY);
  const [gradingTimedOut, setGradingTimedOut] = useState(false);
  const hydratedAttempt = useRef<string | null>(null);
  const draftsRef = useRef<Record<string, AnswerDraft>>({});
  const savedSignatures = useRef<Record<string, string>>({});
  const versions = useRef<Record<string, number>>({});
  const timers = useRef<Record<string, number>>({});
  const autoSubmitStarted = useRef(false);
  const submittingRef = useRef(false);

  const quizQuery = useQuery<QuizOut>({
    queryKey: ["quiz", quizId],
    queryFn: () => api.get(`/api/v1/quizzes/${quizId}`),
    enabled: Boolean(quizId),
    refetchInterval: (query) => query.state.data?.status === "generating" ? 2000 : false,
  });
  const quiz = quizQuery.data;
  const attemptId = startedAttemptId ?? quiz?.my_attempt?.id ?? null;
  const attemptDetailQuery = useQuery<AttemptDetailOut>({
    queryKey: ["attempt-detail", attemptId],
    queryFn: () => api.get(`/api/v1/attempts/${attemptId}`),
    enabled: Boolean(attemptId),
    refetchInterval: (query) => {
      const attempt = query.state.data;
      if (attempt?.status === "submitted") return 3000;
      if (
        attempt?.status === "in_progress" &&
        attempt.deadline_at &&
        new Date(attempt.deadline_at).getTime() <= new Date(attempt.server_now).getTime()
      ) return 2000;
      return false;
    },
  });
  const attempt = attemptDetailQuery.data;
  const attemptStatus = attempt?.status ?? quiz?.my_attempt?.status;
  const resultMode = attemptStatus === "submitted" || attemptStatus === "graded";
  const inputsLocked = remainingMs <= 0 || resultMode;
  const eventsQuery = useQuery<ExamEventOut[]>({
    queryKey: ["exam-events", attemptId],
    queryFn: () => api.get(`/api/v1/attempts/${attemptId}/events`),
    enabled: Boolean(attemptId && resultMode),
  });

  useHeartbeat(courseId, "learn", attemptStatus === "in_progress");

  useEffect(() => {
    return () => Object.values(timers.current).forEach((timer) => window.clearTimeout(timer));
  }, []);

  useEffect(() => {
    if (!attempt) return;
    setServerOffsetMs(new Date(attempt.server_now).getTime() - Date.now());
  }, [attempt?.server_now]);

  useEffect(() => {
    if (attempt?.status !== "in_progress" || !attempt.deadline_at) return;
    const deadline = new Date(attempt.deadline_at).getTime();
    const update = () => setRemainingMs(Math.max(0, deadline - (Date.now() + serverOffsetMs)));
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [attempt?.deadline_at, attempt?.status, serverOffsetMs]);

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
    setFullscreenWarning(!document.fullscreenElement);
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
    const timer = window.setTimeout(() => setGradingTimedOut(true), remaining);
    return () => window.clearTimeout(timer);
  }, [attempt?.status, attempt?.submitted_at]);

  useEffect(() => {
    if (attempt?.status !== "submitted" && attempt?.status !== "graded") return;
    void quizQuery.refetch();
    if (!document.fullscreenElement) return;
    submittingRef.current = true;
    void document.exitFullscreen().catch(() => undefined).finally(() => {
      submittingRef.current = false;
    });
  }, [attempt?.status]);

  async function startExam() {
    if (!quizId || starting) return;
    setStarting(true);
    setPageError("");
    try {
      let fullscreenUnavailable = false;
      if (document.documentElement.requestFullscreen) {
        try {
          await document.documentElement.requestFullscreen();
        } catch {
          fullscreenUnavailable = true;
        }
      } else {
        fullscreenUnavailable = true;
      }
      const created = await api.post<AttemptOut>(`/api/v1/quizzes/${quizId}/attempts`);
      setStartedAttemptId(created.id);
      setFullscreenWarning(fullscreenUnavailable);
      queryClient.setQueryData<AttemptDetailOut>(["attempt-detail", created.id], {
        ...created,
        answers: [],
      });
      if (fullscreenUnavailable) {
        await api.post<void>(`/api/v1/attempts/${created.id}/events`, { kind: "fullscreen_exit" });
      }
      await quizQuery.refetch();
    } catch (error) {
      setPageError(error instanceof ApiError ? error.message : error instanceof Error ? error.message : "开考失败");
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
    } finally {
      setStarting(false);
    }
  }

  async function persistAnswer(
    questionId: string,
    draft: AnswerDraft,
    version: number,
    retryOnce = true,
  ): Promise<boolean> {
    if (!attemptId || inputsLocked) return false;
    setSaveStates((current) => ({ ...current, [questionId]: { status: "saving" } }));
    try {
      await api.put(`/api/v1/attempts/${attemptId}/answers/${questionId}`, draft);
      if (versions.current[questionId] === version) {
        savedSignatures.current[questionId] = JSON.stringify(draft);
        setSaveStates((current) => ({ ...current, [questionId]: { status: "saved" } }));
      }
      setReconnecting(false);
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
        setReconnecting(true);
        if (retryOnce) {
          window.setTimeout(() => {
            void persistAnswer(questionId, draftsRef.current[questionId] ?? draft, version, false);
          }, 1500);
        }
      }
      return false;
    }
  }

  function changeAnswer(questionId: string, draft: AnswerDraft) {
    if (inputsLocked) return;
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
    if (!draft || inputsLocked) return;
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
      submittingRef.current = true;
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
      await Promise.all([
        quizQuery.refetch(),
        attemptDetailQuery.refetch(),
        queryClient.invalidateQueries({ queryKey: ["formal-exams", courseId] }),
        queryClient.invalidateQueries({ queryKey: ["overview", courseId] }),
      ]);
      submittingRef.current = false;
    },
    onError: async (error) => {
      if (error instanceof ApiError && (error.status === 400 || error.status === 403)) {
        await attemptDetailQuery.refetch();
        return;
      }
      setPageError(error instanceof ApiError ? error.message : "交卷失败");
    },
  });

  useEffect(() => {
    if (attempt?.status !== "in_progress" || remainingMs > 0 || autoSubmitStarted.current) return;
    autoSubmitStarted.current = true;
    submit.mutate();
  }, [attempt?.status, remainingMs]);

  useEffect(() => {
    const onOnline = () => {
      void flushPendingAnswers().then((saved) => {
        if (saved) setReconnecting(false);
      });
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  });

  async function sendEvent(kind: "fullscreen_exit" | "tab_blur" | "resume", retry = true) {
    if (!attemptId || attemptStatus !== "in_progress") return;
    try {
      await api.post<void>(`/api/v1/attempts/${attemptId}/events`, { kind });
    } catch {
      if (retry) window.setTimeout(() => void sendEvent(kind, false), 1000);
    }
  }

  async function returnFullscreen() {
    try {
      await document.documentElement.requestFullscreen();
      setFullscreenWarning(false);
    } catch {
      setFullscreenWarning(true);
      await sendEvent("fullscreen_exit");
    }
  }

  useEffect(() => {
    const onFullscreenChange = () => {
      if (attemptStatus !== "in_progress" || submittingRef.current) return;
      if (!document.fullscreenElement) {
        setFullscreenWarning(true);
        void sendEvent("fullscreen_exit");
      } else {
        setFullscreenWarning(false);
      }
    };
    const onVisibilityChange = () => {
      if (attemptStatus !== "in_progress") return;
      void sendEvent(document.visibilityState === "hidden" ? "tab_blur" : "resume");
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [attemptId, attemptStatus]);

  const answeredCount = useMemo(
    () => quiz?.questions.filter((question) => isAnswered(drafts[question.id])).length ?? 0,
    [drafts, quiz?.questions],
  );
  const answerByQuestion = useMemo(
    () => new Map((attempt?.answers ?? []).map((answer) => [answer.question_id, answer])),
    [attempt?.answers],
  );

  async function submitNow() {
    if (!quiz || submit.isPending) return;
    const unanswered = quiz.questions.length - answeredCount;
    const message = `剩余 ${formatRemaining(remainingMs)}，还有 ${unanswered} 题未作答，确定交卷吗？`;
    if (!window.confirm(message)) return;
    const saved = await flushPendingAnswers();
    if (!saved) {
      setPageError("部分答案保存失败，请恢复网络或重试后再交卷。");
      return;
    }
    submit.mutate();
  }

  const loadError = quizQuery.error ?? attemptDetailQuery.error;
  if (quizQuery.isPending || (attemptId && attemptDetailQuery.isPending)) {
    return <div className="min-h-screen bg-slate-100 py-20 text-center text-sm text-slate-400">加载考试中…</div>;
  }
  if (!quiz || loadError) {
    return <div className="min-h-screen bg-slate-100 p-4"><Alert>考试加载失败</Alert></div>;
  }
  if (quiz.status !== "ready") {
    return (
      <div className="min-h-screen bg-slate-100 p-4 sm:p-8">
        <div className="mx-auto max-w-3xl">
          <Link to={`/courses/${courseId}/overview`} className="inline-flex items-center gap-1 text-sm text-slate-500">
            <ArrowLeft className="h-4 w-4" />返回课程
          </Link>
          <Card className="mt-5 text-center">
            {quiz.status === "failed" ? (
              <>
                <ShieldAlert className="mx-auto h-10 w-10 text-red-400" />
                <h1 className="mt-4 text-lg font-bold">正式考试生成失败</h1>
              </>
            ) : (
              <>
                <Loader2 className="mx-auto h-10 w-10 animate-spin text-rose-400" />
                <h1 className="mt-4 text-lg font-bold">正式考试生成中</h1>
              </>
            )}
          </Card>
        </div>
      </div>
    );
  }

  if (!attemptId) {
    return (
      <div className="min-h-screen bg-slate-100 px-4 py-8 sm:py-12">
        <div className="mx-auto max-w-3xl">
          <Link to={`/courses/${courseId}/overview`} className="inline-flex items-center gap-1 text-sm text-slate-500">
            <ArrowLeft className="h-4 w-4" />返回课程
          </Link>
          <Card className="mt-5 overflow-hidden p-0">
            <div className="bg-gradient-to-br from-rose-600 to-rose-700 px-5 py-8 text-white sm:px-8">
              <GraduationCap className="h-10 w-10 text-rose-100" />
              <div className="mt-4 text-sm text-rose-100">正式考试</div>
              <h1 className="mt-1 break-words text-2xl font-bold">{quiz.title}</h1>
              <div className="mt-3 flex flex-wrap gap-4 text-sm text-rose-100">
                <span>{quiz.questions.length} 题</span>
                <span>{Math.ceil((quiz.time_limit_seconds ?? 0) / 60)} 分钟</span>
              </div>
            </div>
            <div className="p-5 sm:p-8">
              <h2 className="font-semibold text-slate-900">考试规则</h2>
              <ul className="mt-4 space-y-3 text-sm leading-6 text-slate-600">
                <li>点击开始后立即进入全屏并由服务端开始计时，刷新或重开不会重置时限。</li>
                <li>退出全屏、切换标签页与返回页面都会由服务端记录。</li>
                <li>考试期间全部答疑与聊天功能关闭，时间到后答案立即锁定。</li>
                <li>请在稳定网络环境作答；短暂断线不影响倒计时，恢复后会自动补存答案。</li>
              </ul>
              {pageError && <div className="mt-5"><Alert>{pageError}</Alert></div>}
              <Button className="mt-6 w-full justify-center" disabled={starting} onClick={() => void startExam()}>
                {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Expand className="h-4 w-4" />}
                开始考试
              </Button>
              <div className="mt-4 flex flex-col items-center gap-2 text-center">
                <Button variant="outline" disabled>
                  <Printer className="h-4 w-4" />打印空白卷
                </Button>
                <p className="text-xs text-slate-400">正式考试不提供空白卷打印</p>
              </div>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  if (attemptStatus === "in_progress" && remainingMs <= 0) {
    return (
      <div className="min-h-screen bg-slate-100 px-4 py-16">
        <Card className="mx-auto max-w-xl text-center">
          <Clock3 className="mx-auto h-12 w-12 text-rose-500" />
          <h1 className="mt-4 text-xl font-bold text-slate-900">考试已结束</h1>
          <p className="mt-2 text-sm text-slate-500">答案已锁定，正在等待服务端完成交卷。</p>
          <Loader2 className="mx-auto mt-5 h-5 w-5 animate-spin text-slate-400" />
        </Card>
      </div>
    );
  }

  const wikiLink = (slug: string) => navigate(`/courses/${courseId}/wiki/${slug}`);
  const fullscreenExitCount = eventsQuery.data?.filter((event) => event.kind === "fullscreen_exit").length ?? 0;

  return (
    <div className="min-h-screen min-w-0 bg-slate-100 pb-10">
      {!resultMode && (
        <div className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-3 py-3 shadow-sm backdrop-blur sm:px-6">
          <div className="mx-auto flex max-w-5xl items-center gap-3">
            <div className={`shrink-0 font-mono text-xl font-bold ${remainingMs < 300_000 ? "text-red-600" : "text-slate-900"}`}>
              {formatRemaining(remainingMs)}
            </div>
            <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-rose-500 transition-all"
                style={{ width: `${quiz.questions.length ? (answeredCount / quiz.questions.length) * 100 : 0}%` }}
              />
            </div>
            <div className="shrink-0 text-sm font-medium text-slate-600">已答 {answeredCount}/{quiz.questions.length}</div>
          </div>
        </div>
      )}

      <main className="mx-auto min-w-0 max-w-5xl px-4 pt-5 sm:px-6">
        {fullscreenWarning && !resultMode && (
          <div className="mb-4 flex flex-col gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 sm:flex-row sm:items-center">
            <span className="inline-flex min-w-0 items-center gap-2">
              <ShieldAlert className="h-5 w-5 shrink-0" />已离开全屏，本次已记录
            </span>
            <Button
              className="sm:ml-auto"
              variant="outline"
              onClick={() => void returnFullscreen()}
            >
              返回全屏
            </Button>
          </div>
        )}
        {reconnecting && !resultMode && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <WifiOff className="h-5 w-5 shrink-0" />网络连接异常，倒计时仍在继续；恢复后将自动重试保存。
          </div>
        )}
        {pageError && <div className="mb-4"><Alert>{pageError}</Alert></div>}

        {resultMode ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Link to={`/courses/${courseId}/overview`} className="inline-flex items-center gap-1 text-sm text-slate-500">
                <ArrowLeft className="h-4 w-4" />返回课程
              </Link>
              {attemptStatus === "graded" && (
                <Button
                  variant="outline"
                  onClick={() => navigate(`/courses/${courseId}/print/quiz/${quiz.id}?mode=graded`)}
                >
                  <Printer className="h-4 w-4" />打印已批阅卷
                </Button>
              )}
            </div>
            <Card className="overflow-hidden bg-gradient-to-br from-rose-600 to-rose-700 text-white">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <div className="text-sm text-rose-100">正式考试得分</div>
                  <div className="mt-1 text-4xl font-bold">
                    {formatScore(attempt?.total_score ?? quiz.my_attempt?.total_score)}
                    <span className="text-xl font-medium text-rose-200">
                      /{formatScore(attempt?.max_score ?? quiz.my_attempt?.max_score)}
                    </span>
                  </div>
                </div>
                {attemptStatus === "submitted" && !gradingTimedOut ? (
                  <span className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-2 text-sm">
                    <Loader2 className="h-4 w-4 animate-spin" />AI 批改中
                  </span>
                ) : attemptStatus === "graded" ? (
                  <span className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-2 text-sm">
                    <CheckCircle2 className="h-4 w-4" />批改完成
                  </span>
                ) : null}
              </div>
              {attempt?.auto_submitted && (
                <div className="mt-4 rounded-lg bg-white/15 px-3 py-2 text-sm">超时自动交卷</div>
              )}
            </Card>

            {attemptStatus === "submitted" && gradingTimedOut && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />AI 批改仍在进行，已停止自动刷新。
                <button className="ml-auto inline-flex items-center gap-1 underline" onClick={() => void attemptDetailQuery.refetch()}>
                  <RefreshCw className="h-3.5 w-3.5" />刷新
                </button>
              </div>
            )}

            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-semibold text-slate-900">考试过程记录</div>
                <div className={`text-sm font-medium ${fullscreenExitCount ? "text-red-600" : "text-emerald-600"}`}>
                  离开全屏 {fullscreenExitCount} 次
                </div>
              </div>
              <div className="mt-4 space-y-3">
                {(eventsQuery.data ?? []).map((event) => (
                  <div key={event.id} className="flex min-w-0 items-start gap-3 text-sm">
                    <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${event.kind === "forced_submit" || event.kind === "fullscreen_exit" ? "bg-red-500" : "bg-slate-300"}`} />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-slate-700">{eventLabels[event.kind]}</div>
                      <div className="mt-0.5 text-xs text-slate-400">{new Date(event.created_at).toLocaleString("zh-CN")}</div>
                    </div>
                  </div>
                ))}
                {eventsQuery.isPending && <div className="text-sm text-slate-400">记录加载中…</div>}
                {!eventsQuery.isPending && !eventsQuery.data?.length && <div className="text-sm text-slate-400">没有离屏记录</div>}
              </div>
            </Card>

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
          <div className="space-y-4">
            <div className="min-w-0">
              <div className="text-sm text-rose-600">正式考试</div>
              <h1 className="mt-1 break-words text-2xl font-bold text-slate-900">{quiz.title}</h1>
            </div>
            {quiz.questions.map((question) => (
              <AnswerQuestion
                key={question.id}
                question={question}
                draft={drafts[question.id]}
                saveState={saveStates[question.id]}
                disabled={inputsLocked}
                onChange={(draft) => changeAnswer(question.id, draft)}
                onRetry={() => retrySave(question.id)}
              />
            ))}
            <Card className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-slate-500">
                已答 {answeredCount} 题，未答 {quiz.questions.length - answeredCount} 题
              </div>
              <Button disabled={submit.isPending || inputsLocked} onClick={() => void submitNow()}>
                {submit.isPending && <Loader2 className="h-4 w-4 animate-spin" />}交卷
              </Button>
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}
