import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  Clock3,
  GraduationCap,
  Loader2,
  Network,
  Play,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Markdown } from "../../components/Markdown";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type {
  CourseOverviewOut,
  JobOut,
  QuizListItemOut,
  VocabStatsOut,
  WeaknessOut,
} from "../../lib/types";

const statusItems = [
  { key: "pending", label: "未开始", color: "bg-slate-300" },
  { key: "learning", label: "学习中", color: "bg-blue-500" },
  { key: "basic_mastered", label: "基本掌握", color: "bg-emerald-400" },
  { key: "fully_mastered", label: "完全掌握", color: "bg-emerald-700" },
  { key: "needs_reinforce", label: "需巩固", color: "bg-amber-500" },
];

const weaknessKinds: Record<WeaknessOut["kind"], string> = {
  concept_confusion: "概念混淆",
  memory_lapse: "记忆缺失",
  application_gap: "应用薄弱",
  calculation_error: "计算错误",
  expression_vague: "表达模糊",
};

function formatDate(value: string | null): string {
  if (!value) return "待生成";
  return new Date(`${value}T00:00:00`).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function todayKey(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${today.getFullYear()}-${month}-${day}`;
}

export default function OverviewTab() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [examError, setExamError] = useState("");
  const [examDate, setExamDate] = useState(todayKey);
  const [examJobId, setExamJobId] = useState<string | null>(null);
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [resolvedExpanded, setResolvedExpanded] = useState(false);
  const { data: overview } = useQuery<CourseOverviewOut>({
    queryKey: ["overview", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/overview`),
  });
  const { data: vocabStats } = useQuery<VocabStatsOut>({
    queryKey: ["vocab-stats", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/vocab/stats`),
    enabled: overview?.course.kind === "language",
  });
  const regenerate = useMutation({
    mutationFn: () => api.post<JobOut>(`/api/v1/courses/${courseId}/plan/generate`),
    onSuccess: () => {
      setNotice("重新规划任务已提交，可到计划页查看进度。");
      queryClient.invalidateQueries({ queryKey: ["jobs", courseId] });
    },
  });
  const { data: weaknesses } = useQuery<WeaknessOut[]>({
    queryKey: ["weaknesses", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/weaknesses`),
    enabled: Boolean(courseId),
  });
  const { data: formalExams } = useQuery<QuizListItemOut[]>({
    queryKey: ["formal-exams", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/quizzes?kind=formal_exam`),
    enabled: Boolean(courseId),
    refetchInterval: (query) => query.state.data?.[0]?.status === "generating" ? 2000 : false,
  });
  const { data: examJob } = useQuery<JobOut>({
    queryKey: ["job", examJobId],
    queryFn: () => api.get(`/api/v1/jobs/${examJobId}`),
    enabled: Boolean(examJobId),
    refetchInterval: (query) =>
      query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 2000,
  });
  const bookExam = useMutation({
    mutationFn: () =>
      api.post<JobOut>(`/api/v1/courses/${courseId}/exams/book`, { date: examDate }),
    onSuccess: (job) => {
      setExamError("");
      setExamJobId(job.id);
    },
    onError: (error) => setExamError(error instanceof ApiError ? error.message : "预约正式考试失败"),
  });

  useEffect(() => {
    if (!overview || window.location.hash !== "#weaknesses") return;
    window.requestAnimationFrame(() => document.getElementById("weaknesses")?.scrollIntoView({ behavior: "smooth" }));
  }, [overview, weaknesses]);

  useEffect(() => {
    if (examJob?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["formal-exams", courseId] });
      const skipped = examJob.result?.skipped;
      setExamError(typeof skipped === "string" ? skipped : "");
      setExamJobId(null);
    }
    if (examJob?.status === "failed" || examJob?.status === "cancelled") {
      setExamError(examJob.error || "正式考试生成失败");
      setExamJobId(null);
    }
  }, [courseId, examJob, queryClient]);

  if (!overview) return <div className="py-12 text-center text-sm text-slate-400">加载中…</div>;

  const trackedTotal = statusItems.reduce(
    (sum, item) => sum + (overview.kp_stats.by_status[item.key] ?? 0),
    0,
  );
  const formalExam = formalExams?.[0];
  const examGenerating =
    bookExam.isPending ||
    examJob?.status === "queued" ||
    examJob?.status === "running" ||
    formalExam?.status === "generating";

  return (
    <div className="space-y-4">
      {notice && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</div>}
      {regenerate.isError && (
        <Alert>{regenerate.error instanceof ApiError ? regenerate.error.message : "重新规划失败"}</Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-500">
            <Sparkles className="h-4 w-4 text-indigo-500" />
            掌握预测
          </div>
          {overview.active_plan ? (
            <div className="mt-5 grid gap-6 sm:grid-cols-2">
              <div>
                <div className="text-xs text-slate-400">预计基本掌握</div>
                <div className="mt-1 text-2xl font-bold text-indigo-700">
                  {formatDate(overview.projected_basic_date)}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-400">预计完全掌握</div>
                <div className="mt-1 text-2xl font-bold text-emerald-700">
                  {formatDate(overview.projected_full_date)}
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-5 rounded-lg bg-indigo-50 p-4">
              <p className="text-sm text-indigo-800">还没有学习计划，先和规划师聊聊你的目标与时间。</p>
              <Button className="mt-3" onClick={() => navigate("../plan")}>
                和规划师聊聊，生成学习计划
              </Button>
            </div>
          )}
          <p className="mt-5 text-xs leading-5 text-slate-400">{overview.assumption_text}</p>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />浅绿 = 基本掌握
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-700" />深绿 = 完全掌握
            </span>
          </div>
          {overview.course.kind === "language" && (
            <button
              className="mt-3 flex w-full items-center justify-between gap-3 rounded-lg bg-violet-50 px-3 py-2 text-left text-sm text-violet-700 hover:bg-violet-100"
              onClick={() => navigate("../vocab")}
            >
              <span>
                词汇进度 {vocabStats?.active ?? 0}/{vocabStats?.total ?? 0}
              </span>
              <span className="text-xs">预计 {vocabStats?.projected_days ?? 0} 天引入完毕 →</span>
            </button>
          )}
        </Card>

        <Card>
          <div className="flex items-center gap-2 text-sm font-medium text-slate-500">
            <Network className="h-4 w-4 text-indigo-500" />
            知识点概况
          </div>
          <div className="mt-4 text-3xl font-bold text-slate-900">{overview.kp_stats.total}</div>
          <div className="text-xs text-slate-400">个知识点</div>
          <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-slate-100">
            {statusItems.map((item) => {
              const count = overview.kp_stats.by_status[item.key] ?? 0;
              return count > 0 ? (
                <div
                  key={item.key}
                  className={item.color}
                  style={{ width: `${(count / Math.max(trackedTotal, 1)) * 100}%` }}
                  title={`${item.label} ${count}`}
                />
              ) : null;
            })}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-x-2 gap-y-2 text-xs">
            {statusItems.map((item) => (
              <div key={item.key} className="flex items-center justify-between gap-2 text-slate-500">
                <span className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${item.color}`} />
                  {item.label}
                </span>
                <span className="font-medium text-slate-700">{overview.kp_stats.by_status[item.key] ?? 0}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="min-w-0">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-semibold text-slate-900">
              <GraduationCap className="h-5 w-5 text-rose-500" />正式考试
            </div>
            <p className="mt-1 text-sm text-slate-400">全屏限时作答，离屏行为会被记录，考试期间答疑关闭</p>
          </div>
          {formalExam?.my_attempt?.status === "graded" && (
            <div className="shrink-0 text-right">
              <div className="text-xs text-slate-400">本次得分</div>
              <div className="text-xl font-bold text-rose-600">
                {formalExam.my_attempt.total_score ?? 0}/{formalExam.my_attempt.max_score ?? 0}
              </div>
            </div>
          )}
        </div>

        {examError && <div className="mt-4"><Alert>{examError}</Alert></div>}

        {examGenerating ? (
          <div className="mt-5 flex items-center gap-3 rounded-xl bg-rose-50 px-4 py-5 text-sm text-rose-700">
            <Loader2 className="h-5 w-5 animate-spin" />正式考试生成中，完成后会自动刷新
          </div>
        ) : formalExam?.status === "failed" ? (
          <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
            正式考试生成失败，请稍后重新预约。
          </div>
        ) : formalExam ? (
          <div className="mt-5 flex flex-col gap-4 rounded-xl border border-slate-200 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="font-medium text-slate-800">{formatDate(formalExam.scheduled_date)}</div>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-500">
                <span>{formalExam.question_count} 题</span>
                <span className="inline-flex items-center gap-1">
                  <Clock3 className="h-4 w-4" />
                  {formalExam.time_limit_seconds ? Math.ceil(formalExam.time_limit_seconds / 60) : "—"} 分钟
                </span>
                {formalExam.my_attempt?.status === "submitted" && <span>AI 批改中</span>}
              </div>
            </div>
            <Button
              className="shrink-0"
              onClick={() => navigate(`/courses/${courseId}/exam/${formalExam.id}`)}
            >
              <Play className="h-4 w-4" />
              {formalExam.my_attempt
                ? formalExam.my_attempt.status === "in_progress"
                  ? "继续考试"
                  : "查看结果"
                : "进入考试"}
            </Button>
          </div>
        ) : (
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="min-w-0 flex-1 text-sm text-slate-600">
              考试日期
              <input
                type="date"
                value={examDate}
                min={todayKey()}
                className="mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm focus:border-rose-500 focus:outline-none focus:ring-2 focus:ring-rose-100"
                onChange={(event) => setExamDate(event.target.value)}
              />
            </label>
            <Button disabled={bookExam.isPending} onClick={() => bookExam.mutate()}>
              预约正式考试
            </Button>
          </div>
        )}
      </Card>

      <Card>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2 font-semibold text-slate-900">
              <CalendarDays className="h-5 w-5 text-indigo-500" />
              当前计划
            </div>
            {overview.active_plan ? (
              <p className="mt-1 text-sm text-slate-500">
                版本 v{overview.active_plan.version} · {formatDate(overview.active_plan.start_date)} 开始 · 每日{" "}
                {overview.active_plan.daily_minutes} 分钟
              </p>
            ) : (
              <p className="mt-1 text-sm text-slate-400">尚未生成计划</p>
            )}
          </div>
          {overview.active_plan && (
            <Button
              variant="outline"
              disabled={regenerate.isPending}
              onClick={() => {
                if (window.confirm("重新规划会生成新版本，当前计划将变为历史版本。确定继续吗？")) {
                  regenerate.mutate();
                }
              }}
            >
              {regenerate.isPending ? "提交中…" : "重新规划"}
            </Button>
          )}
        </div>
        {overview.active_plan && (
          <details className="mt-4 rounded-lg bg-slate-50 px-4 py-3">
            <summary className="cursor-pointer text-sm font-medium text-slate-600">查看规划说明</summary>
            <div className="mt-3">
              <Markdown content={overview.active_plan.rationale_md} />
            </div>
          </details>
        )}
      </Card>

      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <Card id="weaknesses" className="min-w-0 scroll-mt-20">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 font-semibold text-slate-900">
                <BrainCircuit className="h-5 w-5 text-rose-500" />薄弱点
              </div>
              <p className="mt-1 text-sm text-slate-400">错题与主观批改会持续更新</p>
            </div>
            <span className="shrink-0 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700">
              待处理 {weaknesses?.filter((item) => item.status !== "resolved").length ?? overview.open_weakness_count}
            </span>
          </div>

          <div className="mt-4 space-y-3">
            {(weaknesses ?? []).filter((item) => item.status !== "resolved").map((item) => (
              <div key={item.id} className="min-w-0 rounded-xl border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <button
                    className="min-w-0 break-words text-left text-sm font-semibold text-slate-900 hover:text-indigo-600"
                    onClick={() => navigate(`/courses/${courseId}/graph`, { state: { selectedKpSlug: item.kp_slug } })}
                  >
                    {item.kp_title}
                  </button>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    item.status === "open"
                      ? "bg-red-50 text-red-700"
                      : "bg-amber-50 text-amber-700"
                  }`}>
                    {item.status === "open" ? "待攻克" : "好转中"}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-600">{weaknessKinds[item.kind]}</span>
                  <span className="inline-flex items-center gap-1" aria-label={`严重度 ${item.severity}`}>
                    {[1, 2, 3].map((level) => (
                      <span
                        key={level}
                        className={`h-2 w-2 rounded-full ${level <= item.severity ? "bg-rose-500" : "bg-slate-200"}`}
                      />
                    ))}
                  </span>
                </div>
                <p className="mt-2 break-words text-sm leading-6 text-slate-500">{item.description || "暂无补充说明"}</p>
              </div>
            ))}
            {(weaknesses ?? []).filter((item) => item.status !== "resolved").length === 0 && (
              <div className="rounded-xl bg-emerald-50 px-4 py-5 text-center text-sm text-emerald-700">
                当前没有待攻克的薄弱点
              </div>
            )}
          </div>

          {(weaknesses ?? []).some((item) => item.status === "resolved") && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              <button
                className="flex w-full items-center justify-between text-sm font-medium text-slate-500"
                onClick={() => setResolvedExpanded((open) => !open)}
              >
                <span className="inline-flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  已解决 {weaknesses!.filter((item) => item.status === "resolved").length} 条
                </span>
                <ChevronDown className={`h-4 w-4 transition-transform ${resolvedExpanded ? "rotate-180" : ""}`} />
              </button>
              {resolvedExpanded && (
                <div className="mt-3 space-y-2">
                  {weaknesses!.filter((item) => item.status === "resolved").map((item) => (
                    <button
                      key={item.id}
                      className="flex w-full items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-left text-sm text-slate-500 hover:bg-slate-100"
                      onClick={() => navigate(`/courses/${courseId}/graph`, { state: { selectedKpSlug: item.kp_slug } })}
                    >
                      <span className="min-w-0 truncate">{item.kp_title}</span>
                      <span className="shrink-0 text-xs">{weaknessKinds[item.kind]}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </Card>

        <Card className="min-w-0">
          <div className="flex items-center gap-2 font-semibold text-slate-900">
            <CalendarDays className="h-5 w-5 text-indigo-500" />本周总结
          </div>
          {overview.latest_progress_summary_md ? (
            <>
              <div className={`relative mt-4 min-w-0 ${summaryExpanded ? "" : "max-h-[5.25rem] overflow-hidden"}`}>
                <Markdown content={overview.latest_progress_summary_md} />
                {!summaryExpanded && (
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-white to-transparent" />
                )}
              </div>
              <button
                className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-indigo-600"
                onClick={() => setSummaryExpanded((open) => !open)}
              >
                {summaryExpanded ? "收起总结" : "展开完整总结"}
                <ChevronDown className={`h-4 w-4 transition-transform ${summaryExpanded ? "rotate-180" : ""}`} />
              </button>
            </>
          ) : (
            <div className="mt-4 rounded-xl bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
              完成一周学习后，这里会生成进度总结
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
