import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import {
  BookOpenCheck,
  BookOpenText,
  CalendarDays,
  ChevronDown,
  Clock3,
  Flame,
  Gift,
  ListChecks,
  Plus,
  RefreshCw,
  Trash2,
  WalletCards,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, StatusBadge } from "../components/AppShell";
import { Toast } from "../components/Toast";
import { Alert, Button, Card, Input, Label } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { useMe } from "../lib/auth";
import type {
  CompletedItemOut,
  CourseKind,
  CourseOut,
  CourseOverviewOut,
  LanguageTrack,
  MarketCourseListOut,
  PointsLedgerKind,
  PointsLedgerOut,
  PointsSummaryOut,
  StatsDayOut,
  StatsMeOut,
} from "../lib/types";

const courseKindLabels: Record<CourseKind, string> = {
  knowledge: "知识学习",
  language: "语言学习",
  programming: "编程学习",
};

const languageTrackLabels: Record<LanguageTrack, string> = {
  ielts: "雅思",
  cet6: "大学英语六级",
  postgraduate: "考研英语",
  new_concept: "新概念英语",
  custom: "自定义英语课程",
};

const ledgerKindLabels: Record<PointsLedgerKind, string> = {
  study_minutes: "学习时长",
  item_done: "完成条目",
  review_done: "复习",
  quiz_score: "测验",
  reading_done: "阅读练习",
  writing_done: "写作修订",
  redeem: "兑换",
  market_course: "获取课程",
};

function shanghaiDateKey(date: Date): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function lastDays(rows: StatsDayOut[], count: number): StatsDayOut[] {
  const byDate = new Map(rows.map((row) => [row.date, row]));
  return Array.from({ length: count }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (count - 1 - index));
    const key = shanghaiDateKey(date);
    return byDate.get(key) ?? { date: key, study_seconds: 0, review_count: 0, learn_items_done: 0 };
  });
}

function activityValue(day: StatsDayOut): number {
  return day.study_seconds + day.review_count * 60 + day.learn_items_done * 300;
}

function activityLevel(day: StatsDayOut): number {
  const value = activityValue(day);
  if (value === 0) return 0;
  if (value <= 300) return 1;
  if (value <= 900) return 2;
  if (value <= 1800) return 3;
  return 4;
}

function activityDetail(day: StatsDayOut): string {
  return `${day.date} · 学习 ${Math.round(day.study_seconds / 60)} 分钟 · 复习 ${day.review_count} 张 · 完成 ${day.learn_items_done} 条`;
}

function Heatmap({ days }: { days: StatsDayOut[] }) {
  const [selected, setSelected] = useState(days[days.length - 1]);
  const colors = ["bg-slate-200", "bg-emerald-100", "bg-emerald-300", "bg-emerald-500", "bg-emerald-700"];

  useEffect(() => setSelected(days[days.length - 1]), [days]);

  return (
    <Card className="min-w-0 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-600">近 26 周活动</div>
          <div className="mt-1 text-xs text-slate-400">悬停或轻触格子查看明细</div>
        </div>
        <div className="flex items-center gap-1 text-[10px] text-slate-400" aria-label="活动强度图例">
          <span>少</span>
          {colors.map((color) => <span key={color} className={clsx("h-2.5 w-2.5 rounded-sm", color)} />)}
          <span>多</span>
        </div>
      </div>
      <div className="mt-4 max-w-full overflow-x-auto pb-2">
        <div className="grid w-max grid-flow-col grid-rows-7 gap-1">
          {days.map((day) => (
            <button
              key={day.date}
              type="button"
              title={activityDetail(day)}
              aria-label={activityDetail(day)}
              onMouseEnter={() => setSelected(day)}
              onFocus={() => setSelected(day)}
              onClick={() => setSelected(day)}
              className={clsx(
                "h-3 w-3 rounded-[3px] ring-indigo-500 transition-transform hover:scale-125 focus:outline-none focus:ring-2",
                colors[activityLevel(day)],
              )}
            />
          ))}
        </div>
      </div>
      <div className="mt-2 min-h-5 text-xs text-slate-500" aria-live="polite">
        {selected ? activityDetail(selected) : "选择一天查看明细"}
      </div>
    </Card>
  );
}

function CourseCard({
  course,
  deleting,
  onDelete,
}: {
  course: CourseOut;
  deleting: boolean;
  onDelete: (course: CourseOut) => void;
}) {
  const { data: overview } = useQuery<CourseOverviewOut>({
    queryKey: ["overview", course.id],
    queryFn: () => api.get(`/api/v1/courses/${course.id}/overview`),
  });
  const projected = overview?.projected_basic_date
    ? new Date(`${overview.projected_basic_date}T00:00:00`).toLocaleDateString("zh-CN", {
        month: "numeric",
        day: "numeric",
      })
    : null;

  return (
    <Card className="relative min-h-40 transition-shadow hover:shadow-md">
      <Link to={`/courses/${course.id}`} className="block pr-10">
        <div className="flex items-start justify-between">
          <BookOpenText className="h-7 w-7 text-indigo-600" />
          <StatusBadge status={course.status} />
        </div>
        <h2 className="mt-3 font-semibold text-slate-900">{course.name}</h2>
        <p className="mt-1 line-clamp-2 text-sm text-slate-500">{course.goal_text || "未设置学习目标"}</p>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
          <span>每日 {course.daily_minutes} 分钟</span>
          {projected && <span className="font-medium text-indigo-500">预计 {projected} 基本掌握</span>}
        </div>
      </Link>
      <Button
        variant="ghost"
        className="absolute bottom-3 right-3 h-8 px-2 text-slate-400 hover:bg-red-50 hover:text-red-600"
        aria-label={`删除课程 ${course.name}`}
        title="删除课程"
        disabled={deleting}
        onClick={() => onDelete(course)}
      >
        {deleting ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
      </Button>
    </Card>
  );
}

function CreateCourseCard({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [kind, setKind] = useState<CourseKind>("knowledge");
  const [languageTrack, setLanguageTrack] = useState<LanguageTrack>("ielts");
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<CourseOut>("/api/v1/courses", {
      name,
      goal_text: goal,
      kind,
      ...(kind === "language" ? { language_track: languageTrack } : {}),
    }),
    onSuccess: () => {
      setOpen(false);
      setName("");
      setGoal("");
      setKind("knowledge");
      setLanguageTrack("ielts");
      setError("");
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "创建失败"),
  });

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex min-h-40 flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-300 text-slate-400 transition-colors hover:border-indigo-400 hover:text-indigo-500"
      >
        <Plus className="h-8 w-8" />
        <span className="text-sm font-medium">新建课程</span>
      </button>
    );
  }
  return (
    <Card>
      <h2 className="mb-4 font-semibold">新建课程</h2>
      <div className="space-y-3">
        <div>
          <Label htmlFor="cname">课程名称</Label>
          <Input id="cname" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：概率论与数理统计" />
        </div>
        <div>
          <Label htmlFor="cgoal">学习目标（可选）</Label>
          <Input id="cgoal" value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="例如：三个月掌握贝叶斯统计基础" />
        </div>
        <div>
          <Label htmlFor="ckind">课程类型</Label>
          <select
            id="ckind"
            value={kind}
            onChange={(event) => setKind(event.target.value as CourseKind)}
            className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100"
          >
            {Object.entries(courseKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
        {kind === "language" && (
          <div>
            <Label htmlFor="language-track">英语课程轨道</Label>
            <select
              id="language-track"
              value={languageTrack}
              onChange={(event) => setLanguageTrack(event.target.value as LanguageTrack)}
              className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            >
              {Object.entries(languageTrackLabels).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate-400">词库、材料和能力分级都只在这门课程内计算。</p>
          </div>
        )}
        {error && <Alert>{error}</Alert>}
        <div className="flex gap-2">
          <Button onClick={() => name.trim() && create.mutate()} disabled={create.isPending || !name.trim()}>
            {create.isPending ? "创建中…" : "创建"}
          </Button>
          <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
        </div>
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [completedOpen, setCompletedOpen] = useState(false);
  const [toast, setToast] = useState("");
  const { data: courses } = useQuery<CourseOut[]>({
    queryKey: ["courses"],
    queryFn: () => api.get("/api/v1/courses"),
  });
  const { data: marketPreview } = useQuery<MarketCourseListOut>({
    queryKey: ["market-courses", me?.id, "dashboard-preview"],
    queryFn: () => api.get("/api/v1/market/courses?limit=3"),
  });
  const { data: stats } = useQuery<StatsMeOut>({
    queryKey: ["stats-me", 182],
    queryFn: () => api.get("/api/v1/stats/me?days=182"),
  });
  const { data: points } = useQuery<PointsSummaryOut>({
    queryKey: ["points-summary"],
    queryFn: () => api.get("/api/v1/me/points"),
  });
  const { data: ledger } = useQuery<PointsLedgerOut[]>({
    queryKey: ["points-ledger"],
    queryFn: () => api.get("/api/v1/me/points/ledger?limit=50"),
    enabled: ledgerOpen,
  });
  const { data: completedItems } = useQuery<CompletedItemOut[]>({
    queryKey: ["completed-items"],
    queryFn: () => api.get("/api/v1/me/completed-items?limit=50"),
  });
  const removeCourse = useMutation({
    mutationFn: (course: CourseOut) => api.delete(`/api/v1/courses/${course.id}`),
    onSuccess: async () => {
      setToast("课程已删除");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
        queryClient.invalidateQueries({ queryKey: ["market-courses"] }),
        queryClient.invalidateQueries({ queryKey: ["stats-me"] }),
        queryClient.invalidateQueries({ queryKey: ["completed-items"] }),
      ]);
    },
    onError: (error) => setToast(error instanceof ApiError ? error.message : "删除失败"),
  });

  function confirmDelete(course: CourseOut) {
    if (window.confirm(`确定删除「${course.name}」吗？课程资料和学习进度将一并删除，且无法撤销。`)) {
      removeCourse.mutate(course);
    }
  }
  const heatmapDays = lastDays(stats?.days ?? [], 182);
  const recentDays = heatmapDays.slice(-7);
  const today = heatmapDays[heatmapDays.length - 1];
  const totalMinutes = Math.round(recentDays.reduce((sum, day) => sum + day.study_seconds, 0) / 60);
  const todayActive = activityValue(today) > 0;

  return (
    <AppShell>
      <h1 className="text-2xl font-bold text-slate-900">你好，{me?.nickname}</h1>
      <p className="mt-1 text-slate-500">选择一门课程继续学习，或新建课程导入你的学习资料。</p>

      <div className="mt-5 grid min-w-0 gap-3 lg:grid-cols-[0.75fr_0.8fr_1.8fr]">
        <Card className="border-orange-100 bg-gradient-to-br from-orange-50 to-white p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-orange-600"><Flame className="h-5 w-5 fill-orange-500" />连续打卡</div>
          <div className="mt-3 text-3xl font-bold text-orange-950">连续 {points?.streak ?? 0} 天</div>
          <div className="mt-2 text-sm text-orange-700">今日积分倍率 ×{(points?.today_multiplier ?? 1).toFixed(1)}</div>
          {!todayActive && <div className="mt-2 inline-flex rounded-full bg-amber-100 px-2 py-1 text-xs font-medium text-amber-700">今天还没打卡</div>}
        </Card>

        <Card className="p-0">
          <button type="button" onClick={() => setLedgerOpen((value) => !value)} className="w-full p-4 text-left">
            <div className="flex items-center justify-between text-sm font-medium text-indigo-600">
              <span className="flex items-center gap-2"><WalletCards className="h-5 w-5" />积分余额</span>
              <ChevronDown className={clsx("h-4 w-4 transition-transform", ledgerOpen && "rotate-180")} />
            </div>
            <div className="mt-3 text-3xl font-bold text-slate-950">{points?.balance ?? 0}</div>
            <div className="mt-2 text-sm text-emerald-600">今日 +{points?.today_earned ?? 0}</div>
          </button>
          <Link to="/rewards" className="flex items-center justify-center gap-1 border-t border-slate-100 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50">
            <Gift className="h-4 w-4" />去商城
          </Link>
        </Card>

        <Heatmap days={heatmapDays} />
      </div>

      {ledgerOpen && (
        <Card className="mt-3 p-0">
          <div className="border-b border-slate-100 px-4 py-3 font-semibold text-slate-800">积分流水</div>
          <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto">
            {(ledger ?? []).map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-4 px-4 py-3 text-sm">
                <div className="min-w-0">
                  <div className="font-medium text-slate-700">{ledgerKindLabels[item.kind] ?? item.kind}</div>
                  <div className="mt-0.5 text-xs text-slate-400">{new Date(item.created_at).toLocaleString("zh-CN")} · 倍率 ×{item.multiplier.toFixed(1)}</div>
                </div>
                <div className={clsx("shrink-0 text-base font-bold", item.amount < 0 ? "text-red-600" : "text-emerald-600")}>
                  {item.amount > 0 ? "+" : ""}{item.amount}
                </div>
              </div>
            ))}
            {(ledger ?? []).length === 0 && <div className="px-4 py-8 text-center text-sm text-slate-400">暂无积分流水</div>}
          </div>
        </Card>
      )}

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-indigo-100 bg-indigo-50/70 p-4">
          <div className="flex items-center gap-2 text-sm text-indigo-600"><Clock3 className="h-4 w-4" />今日学习</div>
          <div className="mt-2 text-2xl font-bold text-indigo-950">{Math.round(today.study_seconds / 60)}<span className="ml-1 text-sm font-medium text-indigo-500">分钟</span></div>
        </div>
        <div className="rounded-xl border border-emerald-100 bg-emerald-50/70 p-4">
          <div className="flex items-center gap-2 text-sm text-emerald-600"><BookOpenCheck className="h-4 w-4" />今日复习</div>
          <div className="mt-2 text-2xl font-bold text-emerald-950">{today.review_count}<span className="ml-1 text-sm font-medium text-emerald-600">张</span></div>
        </div>
        <div className="rounded-xl border border-amber-100 bg-amber-50/70 p-4">
          <div className="flex items-center gap-2 text-sm text-amber-600"><CalendarDays className="h-4 w-4" />近 7 日累计</div>
          <div className="mt-2 text-2xl font-bold text-amber-950">{totalMinutes}<span className="ml-1 text-sm font-medium text-amber-600">分钟</span></div>
        </div>
      </div>

      <Card className="mt-5 p-0">
        <button type="button" onClick={() => setCompletedOpen((value) => !value)} className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
          <span className="flex items-center gap-2 font-semibold text-slate-800"><ListChecks className="h-5 w-5 text-indigo-500" />最近完成 · {completedItems?.length ?? 0} 条</span>
          <ChevronDown className={clsx("h-4 w-4 text-slate-400 transition-transform", completedOpen && "rotate-180")} />
        </button>
        {completedOpen && (
          <div className="divide-y divide-slate-100 border-t border-slate-100">
            {(completedItems ?? []).map((item, index) => (
              <div key={`${item.completed_at}-${index}`} className="flex flex-col gap-2 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <span className="mr-2 inline-flex rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-600">{item.course_name}</span>
                  <span className="font-medium text-slate-700">{item.title}</span>
                </div>
                <div className="shrink-0 text-xs text-slate-400">{new Date(item.completed_at).toLocaleString("zh-CN")} · {item.actual_minutes ?? 0} 分钟</div>
              </div>
            ))}
            {(completedItems ?? []).length === 0 && <div className="px-4 py-8 text-center text-sm text-slate-400">还没有已完成任务</div>}
          </div>
        )}
      </Card>

      <div className="mt-8 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-slate-900">课程市场</h2>
          <p className="mt-0.5 text-sm text-slate-500">所有注册用户都可选择的精选课程</p>
        </div>
        <Link to="/market" className="shrink-0 text-sm font-medium text-indigo-600 hover:underline">查看全部</Link>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(marketPreview?.items ?? []).map((course) => (
          <Link key={course.id} to="/market">
            <Card className="min-h-32 border-emerald-100 bg-emerald-50/40 transition-shadow hover:shadow-md">
              <div className="flex items-start justify-between gap-3">
                <BookOpenText className="h-6 w-6 shrink-0 text-emerald-600" />
                <span className="text-sm font-semibold text-emerald-700">
                  {course.price_points === 0 ? "免费" : `${course.price_points} 积分`}
                </span>
              </div>
              <h3 className="mt-3 break-words font-semibold text-slate-900">{course.title}</h3>
              <p className="mt-1 text-xs text-slate-500">{course.document_count} 份资料 · {course.knowledge_point_count} 个知识点</p>
            </Card>
          </Link>
        ))}
        {(marketPreview?.items.length ?? 0) === 0 && (
          <Link to="/market" className="sm:col-span-2 lg:col-span-3">
            <Card className="border-dashed text-center text-sm text-slate-500">进入课程市场查看可用课程</Card>
          </Link>
        )}
      </div>

      <div className="mt-8">
        <h2 className="text-lg font-bold text-slate-900">我的课程</h2>
        <p className="mt-0.5 text-sm text-slate-500">管理自己创建或从市场添加的课程</p>
      </div>
      <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(courses ?? []).map((course) => (
          <CourseCard
            key={course.id}
            course={course}
            deleting={removeCourse.isPending && removeCourse.variables?.id === course.id}
            onDelete={confirmDelete}
          />
        ))}
        <CreateCourseCard onDone={() => queryClient.invalidateQueries({ queryKey: ["courses"] })} />
      </div>
      {toast && <Toast key={toast} message={toast} tone="info" onClose={() => setToast("")} />}
    </AppShell>
  );
}
