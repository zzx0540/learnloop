import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileUp, Loader2, Search, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";

import { Alert, Button, Card, Input, Label } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type {
  CourseOut,
  JobOut,
  LanguageProfileOut,
  LanguageSourceOut,
  ReadingExerciseOut,
} from "../../lib/types";

const sourceStatus = {
  uploaded: "已上传",
  processing: "分析中",
  ready: "可使用",
  failed: "处理失败",
};

const recommendationLabel = {
  extensive: "泛读",
  assisted_extensive: "辅助泛读",
  intensive_challenge: "精读挑战",
};

export default function ReadingTab() {
  const { courseId } = useParams();
  const { course } = useOutletContext<{ course?: CourseOut }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSource, setSelectedSource] = useState("");
  const [mode, setMode] = useState<"intensive" | "extensive">("intensive");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const profileQuery = useQuery<LanguageProfileOut>({
    queryKey: ["language-profile", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/language/profile`),
    enabled: Boolean(courseId && course?.kind === "language"),
  });
  const sourcesQuery = useQuery<LanguageSourceOut[]>({
    queryKey: ["language-sources", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/language/sources`),
    enabled: Boolean(courseId && course?.kind === "language"),
    refetchInterval: (query) => query.state.data?.some((item) => ["uploaded", "processing"].includes(item.status)) ? 2000 : false,
  });
  const readingsQuery = useQuery<ReadingExerciseOut[]>({
    queryKey: ["readings", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/readings`),
    enabled: Boolean(courseId && course?.kind === "language"),
  });
  const jobQuery = useQuery<JobOut>({
    queryKey: ["job", jobId],
    queryFn: () => api.get(`/api/v1/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status) ? false : 2000,
  });
  const readySources = (sourcesQuery.data ?? []).filter((source) => source.kind === "reading" && source.status === "ready");
  const selectedSourceRow = readySources.find((source) => source.id === selectedSource);

  useEffect(() => {
    if (!selectedSource && readySources[0]) setSelectedSource(readySources[0].id);
  }, [readySources, selectedSource]);

  useEffect(() => {
    if (!selectedSourceRow?.recommended_mode) return;
    setMode(selectedSourceRow.recommended_mode === "intensive_challenge" ? "intensive" : "extensive");
  }, [selectedSource, selectedSourceRow?.recommended_mode]);

  useEffect(() => {
    const job = jobQuery.data;
    if (!job) return;
    if (job.status === "succeeded") {
      const exerciseId = typeof job.result?.exercise_id === "string" ? job.result.exercise_id : null;
      setJobId(null);
      void queryClient.invalidateQueries({ queryKey: ["readings", courseId] });
      if (exerciseId) navigate(`/courses/${courseId}/reading/${exerciseId}`);
    }
    if (job.status === "failed" || job.status === "cancelled") {
      setError(job.error || "阅读练习生成失败");
      setJobId(null);
    }
  }, [courseId, jobQuery.data, navigate, queryClient]);

  const upload = useMutation({
    mutationFn: (file: File) => api.upload<{ source_id: string; job_id: string }>(`/api/v1/courses/${courseId}/language/sources/reading`, file),
    onSuccess: () => {
      setError("");
      setNotice("材料已上传，系统正在分析原文难度与可用段落。");
      void sourcesQuery.refetch();
      void profileQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "阅读材料上传失败"),
  });
  const paste = useMutation({
    mutationFn: () => api.post<{ source_id: string; job_id: string }>(`/api/v1/courses/${courseId}/language/sources/text`, { title, content_md: content }),
    onSuccess: () => {
      setTitle("");
      setContent("");
      setError("");
      setNotice("文章已导入，系统正在分析原文难度与可用段落。");
      void sourcesQuery.refetch();
      void profileQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "文章导入失败"),
  });
  const search = useMutation({
    mutationFn: () => api.post<{ source_id: string; job_id: string }>(`/api/v1/courses/${courseId}/language/sources/search`, { query: searchQuery }),
    onSuccess: () => {
      setSearchQuery("");
      setError("");
      setNotice("已提交检索任务，系统会校验公开来源并分析文章难度。可在下方查看处理状态。");
      void sourcesQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "英语材料检索失败"),
  });
  const generate = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/v1/courses/${courseId}/readings/generate`, { source_id: selectedSource, mode }),
    onSuccess: (result) => {
      setError("");
      setNotice("");
      setJobId(result.job_id);
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "阅读练习生成失败"),
  });

  if (course && course.kind !== "language") return <Alert>仅英语课程支持阅读训练。</Alert>;
  const profile = profileQuery.data;
  const canPractice = profile?.assessment_status === "active";

  return (
    <div className="space-y-4">
      {error && <Alert>{error}</Alert>}
      {notice && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-indigo-50 p-2 text-indigo-600"><FileUp className="h-5 w-5" /></div>
            <div><h2 className="font-semibold text-slate-900">上传阅读材料</h2><p className="mt-1 text-sm text-slate-500">支持 PDF、EPUB、TXT、MD、DOC、DOCX；只用于本课程读写，不进入 Wiki。</p></div>
          </div>
          <input ref={fileRef} type="file" className="hidden" accept=".pdf,.epub,.txt,.md,.markdown,.doc,.docx" onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
            event.target.value = "";
          }} />
          <Button variant="outline" className="mt-5" disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
            {upload.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}选择文件
          </Button>
        </Card>

        <Card>
          <h2 className="font-semibold text-slate-900">粘贴英文文章</h2>
          <div className="mt-4"><Label htmlFor="reading-title">标题</Label><Input id="reading-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：The Future of Urban Transport" /></div>
          <div className="mt-3"><Label htmlFor="reading-content">英文原文</Label><textarea id="reading-content" className="min-h-32 w-full rounded-lg border border-slate-300 p-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" value={content} onChange={(event) => setContent(event.target.value)} placeholder="至少 100 个字符…" /></div>
          <Button className="mt-3" disabled={paste.isPending || !title.trim() || content.trim().length < 100} onClick={() => paste.mutate()}>{paste.isPending && <Loader2 className="h-4 w-4 animate-spin" />}导入文章</Button>
        </Card>

        <Card>
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-emerald-50 p-2 text-emerald-600"><Search className="h-5 w-5" /></div>
            <div><h2 className="font-semibold text-slate-900">AI 检索英语材料</h2><p className="mt-1 text-sm text-slate-500">GPT-5.6 会检索公开英文页面，系统校验来源后用于阅读和写作。</p></div>
          </div>
          <div className="mt-4"><Label htmlFor="reading-search">主题或方向</Label><Input id="reading-search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="例如：climate adaptation for B1 learners" /></div>
          <Button className="mt-3" disabled={search.isPending || searchQuery.trim().length < 3} onClick={() => search.mutate()}>{search.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}检索材料</Button>
        </Card>
      </div>

      <Card>
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold text-slate-900">生成适配练习</h2>
            <p className="mt-1 text-sm text-slate-500">
              精读约 {profile?.intensive_target_words ?? 400} 词，重视主旨、细节、推断、语境词义与摘要；泛读约 {profile?.extensive_target_words ?? 800} 词，减少中断并记录阅读速度。
            </p>
            {selectedSourceRow?.vocab_coverage_percent !== null && selectedSourceRow?.recommended_mode && (
              <p className="mt-2 text-sm font-medium text-indigo-700">按课程测评估算词汇覆盖率约 {selectedSourceRow.vocab_coverage_percent}%，建议{recommendationLabel[selectedSourceRow.recommended_mode]}；你仍可手动切换模式。</p>
            )}
            {!canPractice && <p className="mt-2 text-sm font-medium text-amber-700">请先到“能力”完成当前课程测评，再生成练习。</p>}
          </div>
          <div className="grid shrink-0 gap-2 sm:grid-cols-[13rem_8rem_auto]">
            <select value={selectedSource} onChange={(event) => setSelectedSource(event.target.value)} className="h-10 min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-sm">
              {readySources.length === 0 && <option value="">暂无可用材料</option>}
              {readySources.map((source) => <option key={source.id} value={source.id}>{source.original_name}</option>)}
            </select>
            <select value={mode} onChange={(event) => setMode(event.target.value as "intensive" | "extensive")} className="h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="intensive">精读</option><option value="extensive">泛读</option></select>
            <Button disabled={!canPractice || !selectedSource || generate.isPending || Boolean(jobId)} onClick={() => generate.mutate()}>{generate.isPending || jobId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}生成</Button>
          </div>
        </div>
      </Card>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card className="min-w-0 p-0">
          <div className="border-b border-slate-100 px-5 py-4"><h2 className="font-semibold text-slate-900">阅读练习历史</h2></div>
          {(readingsQuery.data ?? []).length === 0 ? <div className="py-14 text-center text-sm text-slate-400">导入材料并完成能力测评后，生成第一篇精读或泛读。</div> : (
            <div className="divide-y divide-slate-100">
              {(readingsQuery.data ?? []).map((reading) => (
                <Link key={reading.id} to={`/courses/${courseId}/reading/${reading.id}`} className="flex min-w-0 items-center gap-3 px-5 py-4 hover:bg-slate-50">
                  <BookOpen className="h-5 w-5 shrink-0 text-indigo-500" />
                  <div className="min-w-0 flex-1"><div className="truncate font-medium text-slate-800">{reading.title}</div><div className="mt-1 text-xs text-slate-400">{reading.mode === "intensive" ? "精读" : "泛读"} · {reading.target_level ?? "自适应"}</div></div>
                  <span className={`shrink-0 rounded-full px-2 py-1 text-xs ${reading.status === "graded" ? "bg-emerald-50 text-emerald-700" : reading.status === "submitted" ? "bg-amber-50 text-amber-700" : "bg-indigo-50 text-indigo-700"}`}>{reading.status === "graded" ? "已批改" : reading.status === "submitted" ? "批改中" : "待完成"}</span>
                </Link>
              ))}
            </div>
          )}
        </Card>

        <Card className="min-w-0">
          <h2 className="font-semibold text-slate-900">材料来源</h2>
          <div className="mt-4 space-y-3">
            {(sourcesQuery.data ?? []).filter((source) => source.kind === "reading").length === 0 && <p className="text-sm text-slate-400">尚未导入阅读材料。</p>}
            {(sourcesQuery.data ?? []).filter((source) => source.kind === "reading").map((source) => (
              <div key={source.id} className="rounded-lg border border-slate-200 p-3">
                <div className="truncate text-sm font-medium text-slate-800">{source.original_name}</div>
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-400"><span>{sourceStatus[source.status]}</span>{source.word_count > 0 && <span>{source.word_count} 词</span>}{source.estimated_level && <span>{source.estimated_level}</span>}{source.vocab_coverage_percent !== null && <span>覆盖率约 {source.vocab_coverage_percent}%</span>}</div>
                {source.recommended_mode && <div className="mt-2 text-xs font-medium text-indigo-600">建议：{recommendationLabel[source.recommended_mode]}</div>}
                {typeof source.analysis?.provenance?.url === "string" && <a className="mt-2 block truncate text-xs text-indigo-600 hover:underline" href={source.analysis.provenance.url} target="_blank" rel="noreferrer">查看公开来源</a>}
                {source.error && <p className="mt-2 text-xs text-red-600">{source.error}</p>}
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
