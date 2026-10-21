import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, Coins, FileText, Library, RefreshCw, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { Toast } from "../components/Toast";
import { Alert, Button, Card, Input } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { useMe } from "../lib/auth";
import type {
  CourseKind,
  MarketAcquisitionOut,
  MarketCourseListOut,
  MarketCourseOut,
  PointsSummaryOut,
} from "../lib/types";

const kindLabels: Record<CourseKind, string> = {
  knowledge: "知识",
  language: "语言",
  programming: "编程",
};

function MarketItem({
  course,
  balance,
  acquiring,
  deleting,
  onAcquire,
  onDelete,
  pendingForMs,
  onRefresh,
}: {
  course: MarketCourseOut;
  balance: number;
  acquiring: boolean;
  deleting: boolean;
  onAcquire: (course: MarketCourseOut) => void;
  onDelete: (course: MarketCourseOut) => void;
  pendingForMs: number;
  onRefresh: () => void;
}) {
  const difference = course.acquisition_status === "completed"
    ? 0
    : Math.max(0, course.price_points - balance);
  const isPending = course.acquisition_status === "pending" || acquiring;

  return (
    <Card className="flex min-h-72 min-w-0 flex-col p-5">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <BookOpen className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h2 className="break-words text-base font-bold text-slate-900">{course.title}</h2>
            <div className="mt-1 text-xs text-slate-500">{kindLabels[course.kind]}课程 · v{course.current_version}</div>
          </div>
        </div>
        <span className={course.price_points === 0 ? "shrink-0 text-sm font-semibold text-emerald-700" : "shrink-0 text-sm font-semibold text-amber-700"}>
          {course.price_points === 0 ? "免费" : `${course.price_points} 积分`}
        </span>
      </div>

      <p className="mt-4 line-clamp-3 min-h-16 break-words text-sm leading-6 text-slate-600">
        {course.description || "课程资料已整理完成。"}
      </p>
      <div className="mt-4 grid grid-cols-3 gap-2 border-y border-slate-100 py-3 text-center">
        <div><div className="font-semibold text-slate-800">{course.document_count}</div><div className="text-xs text-slate-500">资料</div></div>
        <div><div className="font-semibold text-slate-800">{course.knowledge_point_count}</div><div className="text-xs text-slate-500">知识点</div></div>
        <div><div className="font-semibold text-slate-800">{course.vocab_count}</div><div className="text-xs text-slate-500">词条</div></div>
      </div>

      <div className="mt-auto pt-4">
        {course.acquisition_status === "completed" && course.owned_course_id ? (
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <Link to={`/courses/${course.owned_course_id}`}>
              <Button className="w-full"><Check className="h-4 w-4" />进入课程</Button>
            </Link>
            <Button
              variant="outline"
              className="px-3 text-red-600 hover:bg-red-50"
              aria-label={`删除课程 ${course.title}`}
              title="删除已添加的课程"
              disabled={deleting}
              onClick={() => onDelete(course)}
            >
              {deleting ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
            </Button>
          </div>
        ) : (
          <>
            <Button
              className="w-full"
              variant={course.acquisition_status === "failed" ? "outline" : "primary"}
              disabled={isPending || difference > 0}
              onClick={() => onAcquire(course)}
            >
              {isPending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Library className="h-4 w-4" />}
              {isPending
                ? pendingForMs >= 20_000 ? "课程较大，后台处理中" : "正在加入课程"
                : course.acquisition_status === "failed"
                  ? "重新获取"
                  : course.acquisition_status === "completed"
                    ? "重新添加"
                    : "获取课程"}
            </Button>
            {isPending && pendingForMs >= 20_000 && (
              <div className="mt-2 flex items-center justify-between gap-2 text-xs text-slate-500">
                <span>资料较多时可能需要几分钟，请勿重复点击。</span>
                <button className="shrink-0 font-medium text-indigo-600 hover:underline" onClick={onRefresh}>
                  刷新状态
                </button>
              </div>
            )}
          </>
        )}
        {difference > 0 && <p className="mt-2 text-center text-xs text-amber-700">还差 {difference} 积分</p>}
        {course.acquisition_status === "failed" && course.acquisition_error && (
          <p className="mt-2 break-words text-center text-xs text-red-600">{course.acquisition_error}</p>
        )}
      </div>
    </Card>
  );
}

export default function MarketPage() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"" | CourseKind>("");
  const [toast, setToast] = useState("");
  const [pendingSince, setPendingSince] = useState<Record<string, number>>({});
  const params = useMemo(() => {
    const search = new URLSearchParams();
    if (q.trim()) search.set("q", q.trim());
    if (kind) search.set("kind", kind);
    return search.toString();
  }, [kind, q]);
  const { data, error, isError, isFetching, isLoading, refetch } = useQuery<MarketCourseListOut>({
    queryKey: ["market-courses", me?.id, params],
    queryFn: () => api.get(`/api/v1/market/courses${params ? `?${params}` : ""}`),
    refetchOnMount: "always",
    refetchInterval: (query) => query.state.data?.items.some((item) => item.acquisition_status === "pending") ? 1500 : false,
  });
  const { data: points } = useQuery<PointsSummaryOut>({
    queryKey: ["points-summary"],
    queryFn: () => api.get("/api/v1/me/points"),
  });
  const acquire = useMutation({
    mutationFn: (course: MarketCourseOut) => api.post<MarketAcquisitionOut>(`/api/v1/market/courses/${course.id}/acquire`),
    onSuccess: async () => {
      setToast("课程正在加入你的学习列表");
      await queryClient.invalidateQueries({ queryKey: ["market-courses"] });
    },
    onError: (error) => setToast(error instanceof ApiError ? error.message : "获取失败"),
  });
  const remove = useMutation({
    mutationFn: (course: MarketCourseOut) => api.delete(`/api/v1/courses/${course.owned_course_id}`),
    onSuccess: async () => {
      setToast("已删除课程，需要时可在市场免费恢复");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["market-courses"] }),
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
      ]);
    },
    onError: (error) => setToast(error instanceof ApiError ? error.message : "删除失败"),
  });

  function confirmDelete(course: MarketCourseOut) {
    if (!course.owned_course_id) return;
    if (window.confirm(`确定删除「${course.title}」吗？该课程的学习进度会一并删除，之后可从市场重新添加。`)) {
      remove.mutate(course);
    }
  }
  useEffect(() => {
    if (!data) return;
    const now = Date.now();
    setPendingSince((current) => {
      const next = { ...current };
      for (const item of data.items) {
        if (item.acquisition_status === "pending") next[item.id] ??= now;
        else delete next[item.id];
      }
      return next;
    });
  }, [data]);

  useEffect(() => {
    if (!data?.items.some((item) => item.acquisition_status === "completed")) return;
    void queryClient.invalidateQueries({ queryKey: ["points-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["points-ledger"] });
    void queryClient.invalidateQueries({ queryKey: ["courses"] });
  }, [data, queryClient]);

  return (
    <AppShell>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">课程市场</h1>
          <p className="mt-1 text-sm text-slate-500">选择整理好的课程，加入后拥有独立的学习进度。</p>
        </div>
        <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <Coins className="h-4 w-4 text-amber-600" />可用积分 {points?.balance ?? 0}
        </div>
      </div>

      <div className="mt-6 flex flex-col gap-3 sm:flex-row">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
          <Input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索课程" className="pl-9" />
        </div>
        <select
          aria-label="课程类型"
          value={kind}
          onChange={(event) => setKind(event.target.value as "" | CourseKind)}
          className="h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-700 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100"
        >
          <option value="">全部类型</option>
          <option value="knowledge">知识课程</option>
          <option value="language">语言课程</option>
          <option value="programming">编程课程</option>
        </select>
      </div>

      {isError ? (
        <div className="mt-6">
          <Alert>{error instanceof ApiError ? error.message : "课程市场加载失败"}</Alert>
          <Button className="mt-3" variant="outline" disabled={isFetching} onClick={() => void refetch()}>
            <RefreshCw className={isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} />重新加载
          </Button>
        </div>
      ) : isLoading ? (
        <div className="flex min-h-60 items-center justify-center text-sm text-slate-500">正在加载课程</div>
      ) : (data?.items.length ?? 0) > 0 ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data?.items.map((course) => (
            <MarketItem
              key={course.id}
              course={course}
              balance={points?.balance ?? 0}
              acquiring={acquire.isPending && acquire.variables?.id === course.id}
              deleting={remove.isPending && remove.variables?.id === course.id}
              pendingForMs={course.acquisition_status === "pending" && pendingSince[course.id] ? Date.now() - pendingSince[course.id] : 0}
              onRefresh={() => void refetch()}
              onAcquire={(item) => acquire.mutate(item)}
              onDelete={confirmDelete}
            />
          ))}
        </div>
      ) : (
        <div className="flex min-h-64 flex-col items-center justify-center border-y border-slate-200 text-center">
          <FileText className="h-8 w-8 text-slate-300" />
          <div className="mt-3 font-medium text-slate-700">暂无匹配课程</div>
        </div>
      )}
      {toast && <Toast key={toast} message={toast} tone="info" onClose={() => setToast("")} />}
    </AppShell>
  );
}
