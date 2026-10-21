import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, BookOpen, FileUp, Pencil, Plus, RefreshCw, Rocket, Save } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { StatusBadge } from "../components/AppShell";
import { Alert, Button, Input, Label } from "../components/ui";
import { api, ApiError } from "../lib/api";
import type {
  DocumentOut,
  JobOut,
  MarketCourseAdminOut,
  MarketSourceCourseOut,
} from "../lib/types";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "操作失败";
}

export function AdminMarketPanel({ notify }: { notify: (message: string) => void }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [sourceId, setSourceId] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newPrice, setNewPrice] = useState("0");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editPrice, setEditPrice] = useState("0");
  const [publishJobId, setPublishJobId] = useState<string | null>(null);

  const { data: sources } = useQuery<MarketSourceCourseOut[]>({
    queryKey: ["admin-market-sources"],
    queryFn: () => api.get("/api/v1/admin/market/source-courses"),
  });
  const { data: markets } = useQuery<MarketCourseAdminOut[]>({
    queryKey: ["admin-market-courses"],
    queryFn: () => api.get("/api/v1/admin/market/courses"),
    refetchInterval: publishJobId ? 1500 : false,
  });
  const selected = useMemo(
    () => markets?.find((market) => market.id === selectedId) ?? null,
    [markets, selectedId],
  );
  const { data: documents } = useQuery<DocumentOut[]>({
    queryKey: ["admin-market-documents", selectedId],
    queryFn: () => api.get(`/api/v1/admin/market/courses/${selectedId}/documents`),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => query.state.data?.some((doc) => ["uploaded", "parsing", "parsed", "ingesting"].includes(doc.status)) ? 2000 : false,
  });
  const { data: publishJob } = useQuery<JobOut>({
    queryKey: ["market-publish-job", publishJobId],
    queryFn: () => api.get(`/api/v1/jobs/${publishJobId}`),
    enabled: Boolean(publishJobId),
    refetchInterval: (query) => ["succeeded", "failed"].includes(query.state.data?.status ?? "") ? false : 1500,
  });

  useEffect(() => {
    if (!selected && markets?.length) setSelectedId(markets[0].id);
  }, [markets, selected]);
  useEffect(() => {
    if (!selected) return;
    setEditTitle(selected.title);
    setEditDescription(selected.description);
    setEditPrice(String(selected.price_points));
  }, [selected]);
  useEffect(() => {
    if (!publishJobId || !publishJob) return;
    if (publishJob.status === "succeeded") {
      notify("市场版本已发布");
      setPublishJobId(null);
      void queryClient.invalidateQueries({ queryKey: ["admin-market-courses"] });
    } else if (publishJob.status === "failed") {
      notify(publishJob.error || "发布失败");
      setPublishJobId(null);
      void queryClient.invalidateQueries({ queryKey: ["admin-market-courses"] });
    }
  }, [notify, publishJob, publishJobId, queryClient]);

  const create = useMutation({
    mutationFn: () => api.post<MarketCourseAdminOut>("/api/v1/admin/market/courses", {
      source_course_id: sourceId,
      title: newTitle.trim(),
      description: newDescription.trim(),
      price_points: Number(newPrice),
    }),
    onSuccess: async (market) => {
      setSourceId("");
      setNewTitle("");
      setNewDescription("");
      setNewPrice("0");
      setSelectedId(market.id);
      notify("市场课程已创建");
      await queryClient.invalidateQueries({ queryKey: ["admin-market-courses"] });
    },
    onError: (error) => notify(errorMessage(error)),
  });
  const update = useMutation({
    mutationFn: () => api.patch<MarketCourseAdminOut>(`/api/v1/admin/market/courses/${selectedId}`, {
      title: editTitle.trim(),
      description: editDescription.trim(),
      price_points: Number(editPrice),
    }),
    onSuccess: async () => {
      notify("市场信息已保存");
      await queryClient.invalidateQueries({ queryKey: ["admin-market-courses"] });
    },
    onError: (error) => notify(errorMessage(error)),
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.upload<DocumentOut>(`/api/v1/admin/market/courses/${selectedId}/documents`, file),
    onSuccess: async () => {
      if (fileRef.current) fileRef.current.value = "";
      notify("资料已上传，正在处理");
      await queryClient.invalidateQueries({ queryKey: ["admin-market-documents", selectedId] });
    },
    onError: (error) => notify(errorMessage(error)),
  });
  const publish = useMutation({
    mutationFn: () => api.post<{ job_id: string; version: number }>(`/api/v1/admin/market/courses/${selectedId}/publish`),
    onSuccess: (result) => {
      setPublishJobId(result.job_id);
      notify(`版本 ${result.version} 正在发布`);
    },
    onError: (error) => notify(errorMessage(error)),
  });
  const archive = useMutation({
    mutationFn: () => api.post<MarketCourseAdminOut>(`/api/v1/admin/market/courses/${selectedId}/archive`),
    onSuccess: async () => {
      notify("市场课程已归档");
      await queryClient.invalidateQueries({ queryKey: ["admin-market-courses"] });
    },
    onError: (error) => notify(errorMessage(error)),
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!sourceId || !newTitle.trim()) return;
    create.mutate();
  }

  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-lg font-bold text-slate-900">新增市场课程</h2>
        <form onSubmit={onCreate} className="mt-4 grid gap-4 border-y border-slate-200 py-5 md:grid-cols-2">
          <div className="md:col-span-2">
            <Label htmlFor="market-source">源课程</Label>
            <select
              id="market-source"
              value={sourceId}
              onChange={(event) => {
                setSourceId(event.target.value);
                const source = sources?.find((item) => item.id === event.target.value);
                if (source) setNewTitle(source.name);
              }}
              className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100"
              required
            >
              <option value="">选择课程</option>
              {sources?.map((source) => (
                <option key={source.id} value={source.id}>{source.name} · {source.owner_username} · {source.document_count} 份资料</option>
              ))}
            </select>
          </div>
          <div><Label htmlFor="market-title">市场标题</Label><Input id="market-title" value={newTitle} onChange={(event) => setNewTitle(event.target.value)} maxLength={128} required /></div>
          <div><Label htmlFor="market-price">积分价格</Label><Input id="market-price" type="number" min={0} max={1000000} value={newPrice} onChange={(event) => setNewPrice(event.target.value)} required /></div>
          <div className="md:col-span-2"><Label htmlFor="market-description">简介</Label><textarea id="market-description" value={newDescription} onChange={(event) => setNewDescription(event.target.value)} maxLength={2000} rows={3} className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" /></div>
          <div className="md:col-span-2"><Button type="submit" disabled={create.isPending || !sourceId || !newTitle.trim()}><Plus className="h-4 w-4" />创建市场课程</Button></div>
        </form>
      </section>

      <section>
        <h2 className="text-lg font-bold text-slate-900">已维护课程</h2>
        {(markets?.length ?? 0) > 0 ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {markets?.map((market) => (
              <button key={market.id} type="button" onClick={() => setSelectedId(market.id)} className={`min-h-28 rounded-lg border p-4 text-left transition-colors ${selectedId === market.id ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white hover:border-slate-300"}`}>
                <div className="flex items-start justify-between gap-2"><span className="break-words font-semibold text-slate-800">{market.title}</span><StatusBadge status={market.status} /></div>
                <div className="mt-2 text-xs text-slate-500">{market.source_owner_username || "源已删除"} · v{market.current_version}</div>
                <div className="mt-2 text-sm font-medium text-amber-700">{market.price_points === 0 ? "免费" : `${market.price_points} 积分`}</div>
              </button>
            ))}
          </div>
        ) : <div className="mt-4 border-y border-slate-200 py-12 text-center text-sm text-slate-500">尚未创建市场课程</div>}
      </section>

      {selected && (
        <section className="border-t border-slate-200 pt-7">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div><h2 className="text-lg font-bold text-slate-900">{selected.title}</h2><p className="mt-1 text-sm text-slate-500">源课程：{selected.source_course_name || "已删除"} · {selected.source_owner_username || "未知用户"}</p></div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => publish.mutate()} disabled={publish.isPending || Boolean(publishJobId)}>{publishJobId ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}发布新版本</Button>
              <Button variant="danger" onClick={() => archive.mutate()} disabled={archive.isPending || selected.status === "archived"}><Archive className="h-4 w-4" />归档</Button>
            </div>
          </div>

          <div className="mt-5 grid gap-6 lg:grid-cols-2">
            <div>
              <div className="flex items-center gap-2 font-semibold text-slate-800"><Pencil className="h-4 w-4" />市场信息</div>
              <div className="mt-3 space-y-3">
                <div><Label htmlFor="edit-market-title">标题</Label><Input id="edit-market-title" value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></div>
                <div><Label htmlFor="edit-market-price">积分价格</Label><Input id="edit-market-price" type="number" min={0} max={1000000} value={editPrice} onChange={(event) => setEditPrice(event.target.value)} /></div>
                <div><Label htmlFor="edit-market-description">简介</Label><textarea id="edit-market-description" value={editDescription} onChange={(event) => setEditDescription(event.target.value)} rows={4} className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" /></div>
                <Button onClick={() => update.mutate()} disabled={update.isPending || !editTitle.trim()}><Save className="h-4 w-4" />保存</Button>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2 font-semibold text-slate-800"><BookOpen className="h-4 w-4" />学习资料</div><span className="text-xs text-slate-500">{documents?.length ?? 0} 份</span></div>
              {selected.source_owned_by_admin ? (
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <input ref={fileRef} type="file" className="min-w-0 flex-1 text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium" />
                  <Button variant="outline" disabled={upload.isPending} onClick={() => { const file = fileRef.current?.files?.[0]; if (file) upload.mutate(file); }}><FileUp className="h-4 w-4" />上传</Button>
                </div>
              ) : <Alert>该源课程属于其他用户，只允许只读发布，不能从市场管理页改写资料。</Alert>}
              <div className="mt-4 divide-y divide-slate-100 border-y border-slate-200">
                {documents?.map((doc) => (
                  <div key={doc.id} className="flex min-w-0 items-center justify-between gap-3 py-3 text-sm"><span className="min-w-0 truncate text-slate-700">{doc.original_name}</span><StatusBadge status={doc.status} /></div>
                ))}
                {(documents?.length ?? 0) === 0 && <div className="py-8 text-center text-sm text-slate-500">暂无资料</div>}
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
