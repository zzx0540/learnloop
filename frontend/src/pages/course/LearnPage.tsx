import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, ChevronDown, Clock3, Loader2, Printer, SkipForward } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { AgentChat } from "../../components/AgentChat";
import { AppShell } from "../../components/AppShell";
import { Markdown } from "../../components/Markdown";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { PlanItemStudyOut, WikiPageOut } from "../../lib/types";
import { useHeartbeat } from "../../lib/useHeartbeat";

function elapsedLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

export default function LearnPage() {
  const { courseId, itemId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [seconds, setSeconds] = useState(0);
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const [error, setError] = useState("");
  const [companionOpen, setCompanionOpen] = useState(true);

  const { data: item, isPending } = useQuery<PlanItemStudyOut>({
    queryKey: ["plan-item", itemId],
    queryFn: () => api.get(`/api/v1/plan-items/${itemId}`),
    enabled: Boolean(itemId),
  });
  const wikiKey = item?.kps.map((kp) => kp.primary_wiki_slug ?? "-").join("|") ?? "";
  const { data: wikiPages } = useQuery<(WikiPageOut | null)[]>({
    queryKey: ["learn-wiki-pages", courseId, itemId, wikiKey],
    queryFn: () =>
      Promise.all(
        item!.kps.map(async (kp) => {
          if (!kp.primary_wiki_slug) return null;
          try {
            return await api.get<WikiPageOut>(
              `/api/v1/courses/${courseId}/wiki/pages/${kp.primary_wiki_slug}`,
            );
          } catch {
            return null;
          }
        }),
      ),
    enabled: Boolean(item && courseId),
  });

  useHeartbeat(courseId, "learn", true);

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => setSeconds((current) => current + 1), 1000);
    return () => window.clearInterval(timer);
  }, [visible]);

  const updateItem = useMutation({
    mutationFn: (status: "done" | "skipped") =>
      api.patch<PlanItemStudyOut>(`/api/v1/plan-items/${itemId}`, {
        status,
        ...(status === "done" ? { actual_minutes: Math.ceil(seconds / 60) } : {}),
      }),
    onSuccess: (_, status) => {
      void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["plan-item", itemId] });
      void queryClient.invalidateQueries({ queryKey: ["graph", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["stats-me"] });
      navigate(`/courses/${courseId}/plan`, {
        replace: true,
        state: {
          toast: status === "done" ? "已完成，明日将进入复习队列" : "已跳过该学习条目",
        },
      });
    },
    onError: (e, status) => {
      if (e instanceof ApiError && e.status === 400) {
        void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
        navigate(`/courses/${courseId}/plan`, {
          replace: true,
          state: { toast: status === "done" ? "条目已完成" : "条目状态已更新" },
        });
        return;
      }
      setError(e instanceof ApiError ? e.message : "更新学习状态失败");
    },
  });

  if (isPending || !item) {
    return (
      <AppShell>
        <div className="py-16 text-center text-sm text-slate-400">加载学习内容中…</div>
      </AppShell>
    );
  }

  const readOnly = item.status === "done" || item.status === "skipped";
  const wikiLink = (slug: string) => navigate(`/courses/${courseId}/wiki/${slug}`);

  return (
    <AppShell>
      <div className="pb-24">
        <Link
          to={`/courses/${courseId}/plan`}
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-indigo-600"
        >
          <ArrowLeft className="h-4 w-4" />
          返回计划
        </Link>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="text-sm text-indigo-600">学习任务</div>
            <h1 className="mt-1 text-2xl font-bold text-slate-900">{item.title}</h1>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.kps.map((kp) => (
                <span key={kp.id} className="rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">
                  {kp.title}
                </span>
              ))}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            <Button variant="outline" onClick={() => navigate(`/courses/${courseId}/print/daily-notes`)}>
              <Printer className="h-4 w-4" />打印今日笔记
            </Button>
            <div className="flex items-center gap-2 rounded-lg bg-white px-3 py-2 text-sm text-slate-600 shadow-sm">
              <Clock3 className="h-4 w-4 text-indigo-500" />
              已学习 {elapsedLabel(seconds)}
            </div>
          </div>
        </div>

        {error && <div className="mt-4"><Alert>{error}</Alert></div>}
        {readOnly && (
          <div className="mt-4 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700">
            <CheckCircle2 className="h-4 w-4" />
            {item.status === "done" ? "该学习任务已完成，当前为只读浏览" : "该学习任务已跳过，当前为只读浏览"}
          </div>
        )}

        <div className="mt-6 grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start">
          <div className="min-w-0 space-y-4">
            {item.description_md && (
              <Card>
                <Markdown content={item.description_md} onWikiLink={wikiLink} />
              </Card>
            )}
            {item.kps.map((kp, index) => {
              const page = wikiPages?.[index];
              return (
                <Card key={kp.id} className="min-w-0">
                  <div className="mb-4 border-b border-slate-100 pb-3">
                    <div className="text-xs font-medium text-indigo-500">知识点 {index + 1}</div>
                    <h2 className="mt-1 text-lg font-bold text-slate-900">{page?.title ?? kp.title}</h2>
                  </div>
                  {page ? (
                    <Markdown content={page.content_md} onWikiLink={wikiLink} />
                  ) : (
                    <p className="text-[15px] leading-7 text-slate-700">{kp.summary || "该知识点暂无摘要。"}</p>
                  )}
                </Card>
              );
            })}
          </div>

          <aside className="min-w-0 lg:sticky lg:top-20">
            <button
              className="mb-2 flex w-full items-center justify-between rounded-lg bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 lg:hidden"
              onClick={() => setCompanionOpen((open) => !open)}
            >
              学习答疑
              <ChevronDown className={`h-4 w-4 transition-transform ${companionOpen ? "rotate-180" : ""}`} />
            </button>
            <div className={companionOpen ? "block" : "hidden lg:block"}>
              <AgentChat
                courseId={courseId!}
                agent="companion"
                context={{ mode: "learning", plan_item_id: item.id }}
                header={{ title: "陪伴反馈师 · 学习答疑", subtitle: "围绕本节内容引导你理解与推导" }}
                emptyHint="学习中遇到不理解的概念，可以随时在这里提问。"
              />
            </div>
          </aside>
        </div>
      </div>

      {!readOnly && (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 shadow-[0_-8px_24px_rgba(15,23,42,0.08)] backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="text-sm text-slate-500">
              本次已用时 <span className="font-semibold text-slate-800">{elapsedLabel(seconds)}</span>
            </div>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                disabled={updateItem.isPending}
                onClick={() => {
                  if (window.confirm("确定跳过这项学习任务吗？")) updateItem.mutate("skipped");
                }}
              >
                <SkipForward className="h-4 w-4" />
                跳过
              </Button>
              <Button disabled={updateItem.isPending} onClick={() => updateItem.mutate("done")}>
                {updateItem.isPending && updateItem.variables === "done" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <CheckCircle2 className="h-4 w-4" />
                )}
                完成学习
              </Button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
