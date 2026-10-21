import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, Languages, Loader2, RotateCcw, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";

import { StatusBadge } from "../../components/AppShell";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type {
  CourseOut,
  DocumentOut,
  JobOut,
  KnowledgePointOut,
  VocabImportJobOut,
} from "../../lib/types";

function fmtSize(bytes: number): string {
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export default function KbTab() {
  const { courseId } = useParams();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const vocabFileRef = useRef<HTMLInputElement>(null);
  const { course } = useOutletContext<{ course?: CourseOut }>();
  const [vocabJobId, setVocabJobId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const { data: docs } = useQuery<DocumentOut[]>({
    queryKey: ["documents", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/documents`),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((d) => ["uploaded", "parsing", "parsed", "ingesting"].includes(d.status))
        ? 2500
        : false,
  });

  const { data: activeJobs } = useQuery<JobOut[]>({
    queryKey: ["jobs", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/jobs?active=1`),
    refetchInterval: (q) => ((q.state.data ?? []).length > 0 ? 2500 : 10000),
  });

  const { data: knowledgePoints } = useQuery<KnowledgePointOut[]>({
    queryKey: ["knowledge-points", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/kps`),
    refetchInterval: (query) => ((query.state.data ?? []).length === 0 ? 5000 : false),
  });
  const { data: vocabJob } = useQuery<JobOut>({
    queryKey: ["job", vocabJobId],
    queryFn: () => api.get(`/api/v1/jobs/${vocabJobId}`),
    enabled: Boolean(vocabJobId),
    refetchInterval: (query) =>
      query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 2000,
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.upload<DocumentOut>(`/api/v1/courses/${courseId}/documents`, file),
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["documents", courseId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "上传失败"),
  });
  const vocabUpload = useMutation({
    mutationFn: (file: File) =>
      api.upload<VocabImportJobOut>(`/api/v1/courses/${courseId}/vocab/import`, file),
    onSuccess: (result) => {
      setError("");
      setVocabJobId(result.job_id);
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "词表上传失败"),
  });

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/documents/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
  });

  const retry = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/documents/${id}/retry`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
    onError: (e) => setError(e instanceof ApiError ? e.message : "重试失败"),
  });

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    for (const f of files) {
      await upload.mutateAsync(f).catch(() => undefined);
    }
    if (fileRef.current) fileRef.current.value = "";
  }

  async function onVocabPick(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) await vocabUpload.mutateAsync(file).catch(() => undefined);
    if (vocabFileRef.current) vocabFileRef.current.value = "";
  }

  useEffect(() => {
    if (vocabJob?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["vocab", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["vocab-stats", courseId] });
    }
    if (vocabJob?.status === "failed" || vocabJob?.status === "cancelled") {
      setError(vocabJob.error || "词表导入失败");
    }
  }, [courseId, queryClient, vocabJob]);

  const runningIngest = (activeJobs ?? []).find((j) => j.kind === "ingest_document" && j.result);
  const readyForPlan =
    (docs ?? []).length > 0 &&
    (docs ?? []).every((document) => document.status === "ingested") &&
    (knowledgePoints ?? []).length === 0;

  return (
    <div className="space-y-4">
      {course?.kind === "language" && (
        <Card className="border-violet-200 bg-violet-50/50">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2 font-semibold text-slate-900">
                <Languages className="h-5 w-5 text-violet-600" />词表导入
              </div>
              <p className="mt-1 text-sm text-slate-500">
                CSV / TSV 严格格式零 AI 解析；自由格式 MD / TXT 会自动规范化。导入后按每日词量逐步激活。
              </p>
              <a
                href={`/api/v1/courses/${courseId}/vocab/template.csv`}
                download
                className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-violet-700 hover:underline"
              >
                <Download className="h-4 w-4" />下载 CSV 模板
              </a>
            </div>
            <input
              ref={vocabFileRef}
              type="file"
              accept=".csv,.tsv,.md,.txt"
              className="hidden"
              onChange={onVocabPick}
            />
            <Button
              className="shrink-0"
              disabled={vocabUpload.isPending || vocabJob?.status === "queued" || vocabJob?.status === "running"}
              onClick={() => vocabFileRef.current?.click()}
            >
              {vocabUpload.isPending || vocabJob?.status === "queued" || vocabJob?.status === "running" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {vocabJob?.status === "queued" || vocabJob?.status === "running" ? "导入中" : "上传词表"}
            </Button>
          </div>
          {vocabJob?.status === "succeeded" && vocabJob.result && (
            <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl bg-white p-3 text-sm sm:grid-cols-4">
              <div><span className="text-slate-400">新增</span> {String(vocabJob.result.imported ?? 0)}</div>
              <div><span className="text-slate-400">更新</span> {String(vocabJob.result.updated ?? 0)}</div>
              <div><span className="text-slate-400">重复</span> {String(vocabJob.result.duplicates ?? 0)}</div>
              <div><span className="text-slate-400">坏行跳过</span> {String(vocabJob.result.dropped ?? 0)}</div>
            </div>
          )}
        </Card>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-semibold text-slate-900">学习资料</h2>
          <p className="text-sm text-slate-500">
            支持 PDF / EPUB / Word / Markdown / TXT，上传后自动解析并整合进课程 Wiki
          </p>
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".pdf,.epub,.txt,.md,.markdown,.docx"
          className="hidden"
          onChange={onPick}
        />
        <Button onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
          {upload.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          上传资料
        </Button>
      </div>

      {error && <Alert>{error}</Alert>}

      {readyForPlan && (
        <Link
          to="../plan"
          className="flex items-center justify-between gap-3 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm font-medium text-indigo-700 hover:bg-indigo-100"
        >
          <span>资料已入库，去生成学习计划</span>
          <span aria-hidden>→</span>
        </Link>
      )}

      {runningIngest && (
        <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在入库《{String(runningIngest.result?.document ?? "…")}》 第{" "}
          {String(runningIngest.result?.chunk ?? "-")}/{String(runningIngest.result?.total ?? "-")} 段
        </div>
      )}

      <Card className="p-0">
        {(docs ?? []).length === 0 ? (
          <div className="p-10 text-center text-sm text-slate-400">还没有资料，点击右上角上传第一份学习资料</div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {(docs ?? []).map((d) => (
              <li key={d.id} className="flex items-center gap-3 px-4 py-3">
                <FileText className="h-5 w-5 shrink-0 text-slate-400" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-slate-800">{d.original_name}</div>
                  <div className="text-xs text-slate-400">
                    {fmtSize(d.size_bytes)}
                    {d.page_count ? ` · ${d.page_count} 页` : ""}
                    {d.error ? ` · ${d.error}` : ""}
                  </div>
                </div>
                <StatusBadge status={d.status} />
                {d.status === "failed" && (
                  <button
                    onClick={() => retry.mutate(d.id)}
                    disabled={retry.isPending}
                    className="rounded p-1.5 text-slate-400 hover:bg-indigo-50 hover:text-indigo-600"
                    title="断点重试"
                  >
                    <RotateCcw className="h-4 w-4" />
                  </button>
                )}
                <button
                  onClick={() => del.mutate(d.id)}
                  className="rounded p-1.5 text-slate-300 hover:bg-red-50 hover:text-red-500"
                  title="删除"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
