import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Loader2, Save, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";

import { Alert, Button, Card, Input } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { CourseOut, VocabEntryOut, VocabStatsOut } from "../../lib/types";

const statusLabels = {
  pending: "待引入",
  active: "学习中",
  archived: "已归档",
};

function cardStatus(entry: VocabEntryOut): string {
  if (entry.status === "archived") return "已归档";
  if (!entry.card) return "待激活";
  if (entry.card.stability >= 21) return `已掌握 · S ${entry.card.stability.toFixed(1)}`;
  return `学习中 · S ${entry.card.stability.toFixed(1)}`;
}

export default function VocabTab() {
  const { courseId } = useParams();
  const { course } = useOutletContext<{ course?: CourseOut }>();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [quota, setQuota] = useState(20);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const limit = 50;

  const { data: stats } = useQuery<VocabStatsOut>({
    queryKey: ["vocab-stats", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/vocab/stats`),
    enabled: Boolean(courseId && course?.kind === "language"),
  });
  const { data: entries, isPending } = useQuery<VocabEntryOut[]>({
    queryKey: ["vocab", courseId, status, query, offset],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (status) params.set("status", status);
      if (query.trim()) params.set("q", query.trim());
      return api.get(`/api/v1/courses/${courseId}/vocab?${params}`);
    },
    enabled: Boolean(courseId && course?.kind === "language"),
  });

  useEffect(() => {
    if (stats) setQuota(stats.daily_new_words);
  }, [stats]);

  const saveSettings = useMutation({
    mutationFn: () =>
      api.patch(`/api/v1/courses/${courseId}/vocab/settings`, { daily_new_words: quota }),
    onSuccess: () => {
      setError("");
      setNotice("每日词量已更新");
      void queryClient.invalidateQueries({ queryKey: ["vocab-stats", courseId] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "每日词量更新失败"),
  });
  const archive = useMutation({
    mutationFn: (entryId: string) => api.patch(`/api/v1/vocab/${entryId}`, { status: "archived" }),
    onSuccess: () => {
      setError("");
      setNotice("词条已归档，复习卡已移出队列");
      void queryClient.invalidateQueries({ queryKey: ["vocab", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["vocab-stats", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue", courseId] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "归档失败"),
  });

  if (course && course.kind !== "language") {
    return <Alert>仅语言课程支持词汇卡通道。</Alert>;
  }

  return (
    <div className="min-w-0 space-y-4">
      {notice && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</div>}
      {error && <Alert>{error}</Alert>}

      <Card>
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold text-slate-900">词汇进度</h2>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ["总数", stats?.total ?? 0],
                ["待引入", stats?.pending ?? 0],
                ["学习中", stats?.active ?? 0],
                ["已掌握", stats?.mastered ?? 0],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl bg-slate-50 px-4 py-3">
                  <div className="text-xs text-slate-400">{label}</div>
                  <div className="mt-1 text-2xl font-bold text-slate-900">{value}</div>
                </div>
              ))}
            </div>
            <p className="mt-4 text-sm text-slate-500">
              按当前每日词量，预计 {stats?.projected_days ?? 0} 天引入完毕。词汇不生成计划条目，避免大词表撑爆计划。
            </p>
          </div>
          <div className="shrink-0">
            <label className="text-sm font-medium text-slate-700">每日引入词量</label>
            <div className="mt-1 flex gap-2">
              <Input
                type="number"
                min={5}
                max={100}
                value={quota}
                className="w-28"
                onChange={(event) => setQuota(Number(event.target.value))}
              />
              <Button
                variant="outline"
                disabled={saveSettings.isPending || quota < 5 || quota > 100}
                onClick={() => saveSettings.mutate()}
              >
                {saveSettings.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                保存
              </Button>
            </div>
          </div>
        </div>
      </Card>

      <Card className="min-w-0 p-0">
        <div className="flex flex-col gap-3 border-b border-slate-100 p-4 sm:flex-row">
          <label className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
            <Input
              value={query}
              placeholder="按单词前缀搜索"
              className="pl-9"
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
            />
          </label>
          <select
            value={status}
            className="h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-700"
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部状态</option>
            <option value="pending">待引入</option>
            <option value="active">学习中</option>
            <option value="archived">已归档</option>
          </select>
        </div>

        {isPending ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />加载词表中…
          </div>
        ) : stats?.total === 0 ? (
          <div className="py-16 text-center">
            <div className="text-lg font-semibold text-slate-700">先导入词表</div>
            <p className="mt-2 text-sm text-slate-400">前往知识库 Tab 下载模板并上传 CSV、TSV、MD 或 TXT。</p>
          </div>
        ) : (entries ?? []).length === 0 ? (
          <div className="py-16 text-center text-sm text-slate-400">没有符合筛选条件的词条</div>
        ) : (
          <div className="max-w-full overflow-x-auto">
            <table className="min-w-[760px] w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">单词</th>
                  <th className="px-4 py-3 font-medium">音标</th>
                  <th className="px-4 py-3 font-medium">释义</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 font-medium">卡片状态</th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {(entries ?? []).map((entry) => (
                  <tr key={entry.id} className="align-top">
                    <td className="px-4 py-3 font-semibold text-slate-900">{entry.word}</td>
                    <td className="px-4 py-3 text-slate-500">{entry.phonetic || "—"}</td>
                    <td className="max-w-sm px-4 py-3 text-slate-600">{entry.meaning_md}</td>
                    <td className="px-4 py-3 text-slate-500">{statusLabels[entry.status]}</td>
                    <td className="px-4 py-3 text-slate-500">{cardStatus(entry)}</td>
                    <td className="px-4 py-3">
                      {entry.status !== "archived" && (
                        <button
                          className="inline-flex items-center gap-1 text-slate-400 hover:text-red-600"
                          disabled={archive.isPending}
                          onClick={() => {
                            if (window.confirm(`归档「${entry.word}」并移除其复习卡？`)) {
                              archive.mutate(entry.id);
                            }
                          }}
                        >
                          <Archive className="h-4 w-4" />归档
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {(offset > 0 || (entries ?? []).length === limit) && (
          <div className="flex justify-end gap-2 border-t border-slate-100 p-4">
            <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
              上一页
            </Button>
            <Button
              variant="outline"
              disabled={(entries ?? []).length < limit}
              onClick={() => setOffset(offset + limit)}
            >
              下一页
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
