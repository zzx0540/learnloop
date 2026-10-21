import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenCheck,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  FileQuestion,
  Loader2,
  MessageCircle,
  NotebookPen,
  Play,
  Printer,
  RefreshCw,
  SkipForward,
  TextSearch,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { AgentChat } from "../../components/AgentChat";
import { Toast } from "../../components/Toast";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type {
  CourseOverviewOut,
  JobOut,
  PlanDetailOut,
  PlanItemOut,
  QuizListItemOut,
  ReviewQueueOut,
  LanguageSourceOut,
  ReadingExerciseOut,
  WritingExerciseOut,
} from "../../lib/types";

const typeStyle: Record<string, { label: string; cls: string }> = {
  learn: { label: "学习", cls: "bg-blue-50 text-blue-700" },
  review_block: { label: "复习", cls: "bg-violet-50 text-violet-700" },
  quiz: { label: "测验", cls: "bg-amber-50 text-amber-700" },
  stage_exam: { label: "阶段考", cls: "bg-rose-50 text-rose-700" },
  buffer: { label: "缓冲", cls: "bg-slate-100 text-slate-600" },
  reading: { label: "阅读", cls: "bg-emerald-50 text-emerald-700" },
  writing: { label: "写作", cls: "bg-fuchsia-50 text-fuchsia-700" },
};

function todayKey(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${today.getFullYear()}-${month}-${day}`;
}

function dayLabel(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  return date.toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "short" });
}

export default function PlanTab() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [quizJobId, setQuizJobId] = useState<string | null>(null);
  const [practiceJob, setPracticeJob] = useState<{ id: string; type: "reading" | "writing" } | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState(() => {
    const state = location.state as { toast?: string } | null;
    return state?.toast ?? "";
  });
  const { data: plan, isPending } = useQuery<PlanDetailOut | null>({
    queryKey: ["plan", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/plan`),
  });
  const { data: job } = useQuery<JobOut>({
    queryKey: ["job", jobId],
    queryFn: () => api.get(`/api/v1/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 2000,
  });
  const { data: reviewQueue } = useQuery<ReviewQueueOut>({
    queryKey: ["review-queue", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/reviews/queue`),
    enabled: Boolean(courseId),
  });
  const { data: overview } = useQuery<CourseOverviewOut>({
    queryKey: ["overview", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/overview`),
    enabled: Boolean(courseId),
    refetchInterval: (query) => query.state.data?.today_quiz?.status === "generating" ? 2000 : false,
  });
  const { data: quizzes } = useQuery<QuizListItemOut[]>({
    queryKey: ["course-quizzes", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/quizzes`),
    enabled: Boolean(courseId),
  });
  const { data: quizJob } = useQuery<JobOut>({
    queryKey: ["job", quizJobId],
    queryFn: () => api.get(`/api/v1/jobs/${quizJobId}`),
    enabled: Boolean(quizJobId),
    refetchInterval: (query) =>
      query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 2000,
  });
  const { data: sources } = useQuery<LanguageSourceOut[]>({
    queryKey: ["language-sources", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/language/sources`),
    enabled: overview?.course.kind === "language",
  });
  const { data: readings } = useQuery<ReadingExerciseOut[]>({
    queryKey: ["readings", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/readings`),
    enabled: overview?.course.kind === "language",
  });
  const { data: writings } = useQuery<WritingExerciseOut[]>({
    queryKey: ["writings", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/writings`),
    enabled: overview?.course.kind === "language",
  });
  const { data: practiceJobState } = useQuery<JobOut>({
    queryKey: ["job", practiceJob?.id],
    queryFn: () => api.get(`/api/v1/jobs/${practiceJob?.id}`),
    enabled: Boolean(practiceJob),
    refetchInterval: (query) =>
      query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 2000,
  });

  useEffect(() => {
    if (!(location.state as { toast?: string } | null)?.toast) return;
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  useEffect(() => {
    if (job?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["overview", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["course", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["language-sources", courseId] });
      setNotice("学习计划已生成。");
      setJobId(null);
    }
    if (job?.status === "failed") {
      setError(job.error || "计划生成失败");
      setJobId(null);
    }
  }, [courseId, job, queryClient]);

  useEffect(() => {
    if (quizJob?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["overview", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["course-quizzes", courseId] });
      setQuizJobId(null);
    }
    if (quizJob?.status === "failed" || quizJob?.status === "cancelled") {
      void queryClient.invalidateQueries({ queryKey: ["overview", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["course-quizzes", courseId] });
    }
  }, [courseId, queryClient, quizJob]);

  useEffect(() => {
    if (!practiceJob || !practiceJobState) return;
    if (practiceJobState.status === "succeeded") {
      const exerciseId = typeof practiceJobState.result?.exercise_id === "string"
        ? practiceJobState.result.exercise_id
        : null;
      const type = practiceJob.type;
      setPracticeJob(null);
      void queryClient.invalidateQueries({ queryKey: [type === "reading" ? "readings" : "writings", courseId] });
      if (exerciseId) navigate(`/courses/${courseId}/${type}/${exerciseId}`);
    }
    if (practiceJobState.status === "failed" || practiceJobState.status === "cancelled") {
      setError(practiceJobState.error || "读写练习生成失败");
      setPracticeJob(null);
    }
  }, [courseId, navigate, practiceJob, practiceJobState, queryClient]);

  const generate = useMutation({
    mutationFn: () => api.post<JobOut>(`/api/v1/courses/${courseId}/plan/generate`),
    onSuccess: (created) => {
      setError("");
      setNotice("");
      setJobId(created.id);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "计划生成失败"),
  });

  const generateQuiz = useMutation({
    mutationFn: () => api.post<JobOut>(`/api/v1/courses/${courseId}/quizzes/generate-daily`),
    onSuccess: (created) => {
      setError("");
      setQuizJobId(created.id);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "试卷生成任务提交失败"),
  });

  const updateItem = useMutation({
    mutationFn: ({ item, status }: { item: PlanItemOut; status: "in_progress" | "done" | "skipped"; openLearn?: boolean }) =>
      api.patch<PlanItemOut>(`/api/v1/plan-items/${item.id}`, {
        status,
        ...(status === "done" ? { actual_minutes: item.est_minutes } : {}),
      }),
    onSuccess: (_, variables) => {
      setError("");
      void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["graph", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue", courseId] });
      if (variables.openLearn) navigate(`/courses/${courseId}/learn/${variables.item.id}`);
    },
    onError: (e, variables) => {
      if (e instanceof ApiError && e.status === 400) {
        void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
        if (variables.openLearn) navigate(`/courses/${courseId}/learn/${variables.item.id}`);
        return;
      }
      setError(e instanceof ApiError ? e.message : "更新计划条目失败");
    },
  });

  const startLanguagePractice = useMutation({
    mutationFn: async (item: PlanItemOut) => {
      if (item.type === "reading") {
        const existing = readings?.find((exercise) => exercise.plan_item_id === item.id);
        if (existing) return { exerciseId: existing.id, type: "reading" as const };
        const source = sources?.find((entry) => entry.kind === "reading" && entry.status === "ready");
        if (!source) throw new ApiError(400, "计划尚未准备好阅读材料，请稍后重试");
        const created = await api.post<{ job_id: string }>(`/api/v1/courses/${courseId}/readings/generate`, {
          source_id: source.id,
          mode: "intensive",
          plan_item_id: item.id,
        });
        return { jobId: created.job_id, type: "reading" as const };
      }
      const existing = writings?.find((exercise) => exercise.plan_item_id === item.id);
      if (existing) return { exerciseId: existing.id, type: "writing" as const };
      const created = await api.post<{ job_id: string }>(`/api/v1/courses/${courseId}/writings/generate`, {
        plan_item_id: item.id,
      });
      return { jobId: created.job_id, type: "writing" as const };
    },
    onSuccess: (result) => {
      setError("");
      if ("exerciseId" in result) navigate(`/courses/${courseId}/${result.type}/${result.exerciseId}`);
      else setPracticeJob({ id: result.jobId, type: result.type });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "读写练习启动失败"),
  });

  const allItems = useMemo(() => plan?.days.flatMap((day) => day.items) ?? [], [plan]);
  const completed = allItems.filter((item) => item.status === "done").length;
  const today = todayKey();
  const generating = generate.isPending || job?.status === "queued" || job?.status === "running";
  const todayQuiz = overview?.today_quiz;
  const quizGenerating =
    generateQuiz.isPending ||
    quizJob?.status === "queued" ||
    quizJob?.status === "running" ||
    todayQuiz?.status === "generating";
  const quizFailed = quizJob?.status === "failed" || quizJob?.status === "cancelled" || todayQuiz?.status === "failed";

  if (isPending) return <div className="py-12 text-center text-sm text-slate-400">加载中…</div>;

  if (!plan) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        {error && <Alert>{error}</Alert>}
        {notice && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</div>}
        <AgentChat
          courseId={courseId!}
          agent="planner"
          header={{ title: "学习规划师", subtitle: "告诉我你的目标、时间、基础与偏好" }}
          emptyHint="你好，我会通过几个简短问题了解你的学习目标和可用时间。信息足够后，请点击下方「生成学习计划」。"
        />
        <Card className="text-center">
          <h2 className="text-lg font-semibold text-slate-900">准备好后生成你的学习计划</h2>
          <p className="mt-1 text-sm text-slate-500">规划师会综合学习目标、知识点前置关系和每日时间安排。</p>
          <Button className="mt-4" disabled={generating} onClick={() => generate.mutate()}>
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {generating ? "正在生成…" : "生成学习计划"}
          </Button>
          {job?.status === "queued" && <p className="mt-2 text-xs text-slate-400">任务已排队</p>}
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && <Alert>{error}</Alert>}
      {notice && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</div>}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem_18rem]">
        <Card>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-sm text-slate-500">当前计划 v{plan.version}</div>
              <div className="mt-1 text-lg font-semibold text-slate-900">
                已完成 {completed}/{allItems.length}
              </div>
              <div className="mt-2 h-2 w-52 max-w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-indigo-500"
                  style={{ width: `${allItems.length ? (completed / allItems.length) * 100 : 0}%` }}
                />
              </div>
            </div>
            <Button variant="outline" onClick={() => setChatOpen((open) => !open)}>
              <MessageCircle className="h-4 w-4" />
              和规划师对话
              <ChevronDown className={`h-4 w-4 transition-transform ${chatOpen ? "rotate-180" : ""}`} />
            </Button>
          </div>
        </Card>
        <button
          className="rounded-xl border border-indigo-100 bg-gradient-to-br from-indigo-50 to-white p-5 text-left shadow-sm transition-shadow hover:shadow-md"
          onClick={() => navigate(`/courses/${courseId}/review`)}
        >
          <div className="flex items-center gap-2 text-sm font-medium text-indigo-600">
            <BookOpenCheck className="h-5 w-5" />
            今日复习
          </div>
          {(reviewQueue?.due_count ?? 0) > 0 ? (
            <>
              <div className="mt-3 text-lg font-bold text-slate-900">待复习 {reviewQueue!.due_count} 张</div>
              <div className="mt-1 text-sm text-indigo-600">开始复习 →</div>
            </>
          ) : (
            <div className="mt-3 text-sm text-slate-500">今日无到期复习</div>
          )}
        </button>
        <div className="rounded-xl border border-amber-100 bg-gradient-to-br from-amber-50 to-white p-5 text-left shadow-sm">
          <div className="flex items-center gap-2 text-sm font-medium text-amber-700">
            <FileQuestion className="h-5 w-5" />
            今日试卷
          </div>
          {quizFailed ? (
            <div className="mt-3">
              <div className="flex items-start gap-2 text-sm font-medium text-red-700">
                <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />今日生成失败
              </div>
              <div className="mt-1 text-xs leading-5 text-slate-500">明日凌晨将自动重试</div>
            </div>
          ) : quizGenerating ? (
            <div className="mt-3 flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-amber-500" />试卷生成中…
            </div>
          ) : todayQuiz?.status === "ready" ? (
            <div className="mt-3">
              {todayQuiz.my_attempt_status === "graded" ? (
                <div className="text-lg font-bold text-slate-900">
                  {todayQuiz.total_score ?? 0}/{todayQuiz.max_score ?? 0} 分
                </div>
              ) : todayQuiz.my_attempt_status === "submitted" ? (
                <div className="text-sm font-medium text-amber-700">主观题批改中</div>
              ) : (
                <div className="text-sm text-slate-500">
                  {todayQuiz.my_attempt_status === "in_progress" ? "继续完成今日测验" : "检验今日学习效果"}
                </div>
              )}
              <button
                className="mt-2 text-sm font-medium text-amber-700 hover:underline"
                onClick={() => navigate(`/courses/${courseId}/quiz/${todayQuiz.quiz_id}`)}
              >
                {todayQuiz.my_attempt_status === "graded" || todayQuiz.my_attempt_status === "submitted"
                  ? "查看反馈 →"
                  : "去答题 →"}
              </button>
            </div>
          ) : (
            <Button
              variant="outline"
              className="mt-4 border-amber-300 text-amber-700 hover:bg-amber-100"
              disabled={generateQuiz.isPending}
              onClick={() => generateQuiz.mutate()}
            >
              <FileQuestion className="h-4 w-4" />生成今日试卷
            </Button>
          )}
        </div>
      </div>

      {chatOpen && (
        <AgentChat
          courseId={courseId!}
          agent="planner"
          header={{ title: "学习规划师", subtitle: "告诉我你的目标、时间、基础与偏好" }}
          emptyHint="你好，我会通过几个简短问题了解你的学习目标和可用时间。信息足够后，请点击下方「生成学习计划」。"
          action={(
            <Button
              variant="outline"
              className="h-8 shrink-0 px-2 text-xs"
              disabled={generating}
              onClick={() => {
                if (window.confirm("重新规划会生成新版本，当前计划将变为历史版本。确定继续吗？")) {
                  generate.mutate();
                }
              }}
            >
              {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              {generating ? "生成中…" : "重新规划"}
            </Button>
          )}
        />
      )}

      <div className="space-y-6">
        {plan.days.map((day) => {
          const isToday = day.date === today;
          return (
            <section key={day.date}>
              <div className="mb-2 flex items-center gap-2">
                <h2 className={`font-semibold ${isToday ? "text-indigo-700" : "text-slate-800"}`}>
                  {dayLabel(day.date)}
                </h2>
                {isToday && <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-700">今天</span>}
              </div>
              <div className="space-y-2 border-l-2 border-slate-200 pl-4">
                {day.items.map((item) => {
                  const overdue = day.date < today && ["pending", "in_progress"].includes(item.status);
                  const style = typeStyle[item.type] ?? typeStyle.buffer;
                  const isAssessment = item.type === "quiz" || item.type === "stage_exam";
                  const expectedKind = item.type === "stage_exam" ? "stage" : "daily";
                  const itemQuiz = isAssessment
                    ? quizzes?.find((quiz) => quiz.kind === expectedKind && quiz.scheduled_date === item.scheduled_date)
                    : undefined;
                  const quizReady = itemQuiz?.status === "ready";
                  const quizAction = itemQuiz?.my_attempt?.status === "in_progress"
                    ? "继续测验"
                    : itemQuiz?.my_attempt?.status === "submitted" || itemQuiz?.my_attempt?.status === "graded"
                      ? "查看反馈"
                      : "开始测验";
                  const isLanguagePractice = item.type === "reading" || item.type === "writing";
                  return (
                    <Card key={item.id} className={overdue ? "border-red-200 bg-red-50/30" : ""}>
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`rounded px-2 py-0.5 text-xs font-medium ${style.cls}`}>{style.label}</span>
                            {isAssessment && !quizReady && (
                              <span className="text-xs text-slate-400">
                                {itemQuiz?.status === "generating"
                                  ? "试卷生成中，可先手动完成"
                                  : "试卷未生成，可先手动完成"}
                              </span>
                            )}
                            {overdue && <span className="text-xs font-medium text-red-600">已逾期</span>}
                            {item.status === "done" && <span className="text-xs text-emerald-600">已完成</span>}
                            {item.status === "skipped" && <span className="text-xs text-slate-400">已跳过</span>}
                            {item.status === "in_progress" && <span className="text-xs text-blue-600">进行中</span>}
                          </div>
                          <h3 className="mt-2 font-medium text-slate-900">{item.title}</h3>
                          <div className="mt-1 flex items-center gap-1 text-xs text-slate-400">
                            <Clock3 className="h-3.5 w-3.5" />
                            预计 {item.est_minutes} 分钟
                          </div>
                          {item.knowledge_points.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {item.knowledge_points.map((kp) => (
                                <span key={kp.id} className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">
                                  {kp.title}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                        {["pending", "in_progress"].includes(item.status) && isLanguagePractice && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button
                              className="h-8 px-3"
                              disabled={startLanguagePractice.isPending || Boolean(practiceJob)}
                              onClick={() => startLanguagePractice.mutate(item)}
                            >
                              {practiceJob ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : item.type === "reading" ? <TextSearch className="h-3.5 w-3.5" /> : <NotebookPen className="h-3.5 w-3.5" />}
                              {item.type === "reading" ? "开始阅读" : "开始写作"}
                            </Button>
                            <Button variant="ghost" className="h-8 px-3" disabled={updateItem.isPending} onClick={() => updateItem.mutate({ item, status: "skipped" })}>
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                        {item.status === "pending" && item.type === "learn" && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button
                              variant="outline"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "in_progress", openLearn: true })}
                            >
                              <Play className="h-3.5 w-3.5" />开始学习
                            </Button>
                            <Button
                              variant="ghost"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "skipped" })}
                            >
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                        {item.status === "in_progress" && item.type === "learn" && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => navigate(`/courses/${courseId}/learn/${item.id}`)}
                            >
                              <Play className="h-3.5 w-3.5" />继续学习
                            </Button>
                            <Button
                              variant="ghost"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "skipped" })}
                            >
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                        {item.status === "pending" && isAssessment && quizReady && itemQuiz && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            {!itemQuiz.my_attempt && (
                              <Button
                                variant="outline"
                                className="h-8 px-3"
                                onClick={() => navigate(`/courses/${courseId}/print/quiz/${itemQuiz.id}?mode=blank`)}
                              >
                                <Printer className="h-3.5 w-3.5" />打印空白卷
                              </Button>
                            )}
                            <Button
                              className="h-8 px-3"
                              onClick={() => navigate(`/courses/${courseId}/quiz/${itemQuiz.id}`)}
                            >
                              <Play className="h-3.5 w-3.5" />{quizAction}
                            </Button>
                            <Button
                              variant="ghost"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "skipped" })}
                            >
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                        {item.status === "pending" && item.type !== "learn" && !isLanguagePractice && !(isAssessment && quizReady) && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button
                              variant="outline"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "in_progress" })}
                            >
                              <Play className="h-3.5 w-3.5" />开始
                            </Button>
                            {isAssessment && (
                              <Button
                                className="h-8 px-3"
                                disabled={updateItem.isPending}
                                onClick={() => updateItem.mutate({ item, status: "done" })}
                              >
                                <Check className="h-3.5 w-3.5" />完成
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "skipped" })}
                            >
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                        {item.status === "in_progress" && isAssessment && quizReady && itemQuiz && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button
                              className="h-8 px-3"
                              onClick={() => navigate(`/courses/${courseId}/quiz/${itemQuiz.id}`)}
                            >
                              <Play className="h-3.5 w-3.5" />{quizAction}
                            </Button>
                            <Button
                              variant="ghost"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "skipped" })}
                            >
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                        {item.status === "in_progress" && item.type !== "learn" && !isLanguagePractice && !(isAssessment && quizReady) && (
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "done" })}
                            >
                              <Check className="h-3.5 w-3.5" />完成
                            </Button>
                            <Button
                              variant="ghost"
                              className="h-8 px-3"
                              disabled={updateItem.isPending}
                              onClick={() => updateItem.mutate({ item, status: "skipped" })}
                            >
                              <SkipForward className="h-3.5 w-3.5" />跳过
                            </Button>
                          </div>
                        )}
                      </div>
                    </Card>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
      {toast && <Toast key={toast} message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
