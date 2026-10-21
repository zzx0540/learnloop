import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronUp, MessageCircleQuestion, Send, X } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { AgentChat } from "../../components/AgentChat";
import { Toast } from "../../components/Toast";
import { Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { TangentItemOut } from "../../lib/types";
import { useHeartbeat } from "../../lib/useHeartbeat";

type VisibleStatus = "parked" | "answered" | "dismissed";

const filters: { value: VisibleStatus; label: string }[] = [
  { value: "parked", label: "待处理" },
  { value: "answered", label: "已解决" },
  { value: "dismissed", label: "已忽略" },
];

export default function QaTab() {
  const { courseId } = useParams();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<VisibleStatus>("parked");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [prefill, setPrefill] = useState<string | undefined>();
  const [toast, setToast] = useState("");

  const { data: tangents, isPending } = useQuery<TangentItemOut[]>({
    queryKey: ["tangents", courseId, status],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/tangents?status=${status}`),
    enabled: Boolean(courseId),
  });

  useHeartbeat(courseId, "qa", true);

  const updateTangent = useMutation({
    mutationFn: ({ id, next }: { id: string; next: "answered" | "dismissed" }) =>
      api.patch<TangentItemOut>(`/api/v1/tangents/${id}`, { status: next }),
    onSuccess: (_, variables) => {
      setToast(variables.next === "answered" ? "已标记为解决" : "已忽略该问题");
      void queryClient.invalidateQueries({ queryKey: ["tangents", courseId] });
    },
    onError: (e) => setToast(e instanceof ApiError ? e.message : "更新拓展条目失败"),
  });

  function askAgain(question: string) {
    setPrefill(undefined);
    window.setTimeout(() => setPrefill(question), 0);
  }

  function toggleExpanded(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
      <div className="min-w-0">
        <AgentChat
          courseId={courseId!}
          agent="companion"
          context={{ mode: "general" }}
          header={{ title: "陪伴反馈师", subtitle: "基于本课程资料答疑，超纲问题自动进拓展清单" }}
          emptyHint="可以询问课程内概念、例子与联系；延伸问题会自动整理到拓展清单。"
          prefill={prefill}
        />
      </div>

      <Card className="min-w-0 p-4 lg:sticky lg:top-20">
        <div className="flex items-center gap-2">
          <MessageCircleQuestion className="h-5 w-5 text-amber-500" />
          <div>
            <h2 className="font-semibold text-slate-900">拓展清单</h2>
            <p className="text-xs text-slate-400">整理课程边界之外的问题</p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-1.5">
          {filters.map((filter) => (
            <button
              key={filter.value}
              onClick={() => setStatus(filter.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                status === filter.value ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <div className="mt-4 space-y-3">
          {isPending && <div className="py-8 text-center text-sm text-slate-400">加载中…</div>}
          {!isPending && (tangents ?? []).length === 0 && (
            <div className="rounded-lg bg-slate-50 px-3 py-6 text-center text-sm leading-6 text-slate-400">
              聊天中超出课程范围的问题会自动记录在这里
            </div>
          )}
          {(tangents ?? []).map((item) => (
            <div key={item.id} className="min-w-0 rounded-lg border border-slate-200 p-3">
              <p className="break-words text-sm font-medium leading-6 text-slate-700">{item.question}</p>
              {item.brief_answer && (
                <div className="mt-2">
                  <button
                    className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-indigo-600"
                    onClick={() => toggleExpanded(item.id)}
                  >
                    {expanded.has(item.id) ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    {expanded.has(item.id) ? "收起简答" : "查看简答"}
                  </button>
                  {expanded.has(item.id) && (
                    <p className="mt-2 break-words rounded-md bg-amber-50 p-2 text-xs leading-5 text-amber-900">
                      {item.brief_answer}
                    </p>
                  )}
                </div>
              )}
              <div className="mt-3 flex flex-wrap gap-1.5">
                {item.status === "parked" && (
                  <>
                    <Button
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      disabled={updateTangent.isPending}
                      onClick={() => updateTangent.mutate({ id: item.id, next: "answered" })}
                    >
                      <Check className="h-3 w-3" />已解决
                    </Button>
                    <Button
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      disabled={updateTangent.isPending}
                      onClick={() => updateTangent.mutate({ id: item.id, next: "dismissed" })}
                    >
                      <X className="h-3 w-3" />忽略
                    </Button>
                  </>
                )}
                <Button variant="ghost" className="h-7 px-2 text-xs" onClick={() => askAgain(item.question)}>
                  <Send className="h-3 w-3" />去提问
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Card>
      {toast && <Toast key={toast} message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
