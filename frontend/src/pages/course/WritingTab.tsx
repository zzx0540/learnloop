import { useMutation, useQuery } from "@tanstack/react-query";
import { FilePenLine, Loader2, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";

import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { CourseOut, JobOut, LanguageProfileOut, WritingExerciseOut } from "../../lib/types";

export default function WritingTab() {
  const { courseId } = useParams();
  const { course } = useOutletContext<{ course?: CourseOut }>();
  const navigate = useNavigate();
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const profileQuery = useQuery<LanguageProfileOut>({
    queryKey: ["language-profile", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/language/profile`),
    enabled: Boolean(courseId && course?.kind === "language"),
  });
  const writingsQuery = useQuery<WritingExerciseOut[]>({
    queryKey: ["writings", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/writings`),
    enabled: Boolean(courseId && course?.kind === "language"),
  });
  const jobQuery = useQuery<JobOut>({
    queryKey: ["job", jobId],
    queryFn: () => api.get(`/api/v1/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status) ? false : 2000,
  });

  useEffect(() => {
    const job = jobQuery.data;
    if (!job) return;
    if (job.status === "succeeded") {
      const exerciseId = typeof job.result?.exercise_id === "string" ? job.result.exercise_id : null;
      setJobId(null);
      void writingsQuery.refetch();
      if (exerciseId) navigate(`/courses/${courseId}/writing/${exerciseId}`);
    }
    if (job.status === "failed" || job.status === "cancelled") {
      setError(job.error || "写作题目生成失败");
      setJobId(null);
    }
  }, [courseId, jobQuery.data, navigate]);

  const generate = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/v1/courses/${courseId}/writings/generate`, {}),
    onSuccess: (result) => {
      setError("");
      setJobId(result.job_id);
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "写作题目生成失败"),
  });

  if (course && course.kind !== "language") return <Alert>仅英语课程支持写作训练。</Alert>;
  const canPractice = profileQuery.data?.assessment_status === "active";

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      {error && <Alert>{error}</Alert>}
      <Card className="overflow-hidden bg-gradient-to-br from-violet-600 to-indigo-600 text-white">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div><div className="text-sm text-violet-100">英语写作修订</div><h2 className="mt-1 text-2xl font-bold">写一版，再根据反馈重写</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-violet-100">首稿只产生四维反馈；至少完成第二版修订后，练习和计划条目才算完成。</p></div>
          <Button variant="outline" className="border-white bg-white hover:bg-indigo-50" disabled={!canPractice || generate.isPending || Boolean(jobId)} onClick={() => generate.mutate()}>{generate.isPending || jobId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}新写一篇</Button>
        </div>
        {!canPractice && <p className="mt-3 text-sm font-medium text-amber-200">请先完成课程能力测评。</p>}
      </Card>

      <Card className="min-w-0 p-0">
        <div className="border-b border-slate-100 px-5 py-4"><h2 className="font-semibold text-slate-900">写作历史</h2></div>
        {(writingsQuery.data ?? []).length === 0 ? <div className="py-16 text-center"><FilePenLine className="mx-auto h-9 w-9 text-slate-300" /><div className="mt-3 text-sm text-slate-400">完成能力测评后，生成第一道贴合课程轨道的写作题。</div></div> : (
          <div className="divide-y divide-slate-100">
            {(writingsQuery.data ?? []).map((writing) => {
              const latest = writing.submissions.at(-1);
              const status = writing.status === "completed" ? "已完成修订" : writing.status === "revision_due" ? "待修订" : writing.status === "grading" ? "批改中" : "待写首稿";
              return <Link key={writing.id} to={`/courses/${courseId}/writing/${writing.id}`} className="flex min-w-0 items-center gap-3 px-5 py-4 hover:bg-slate-50"><FilePenLine className="h-5 w-5 shrink-0 text-violet-500" /><div className="min-w-0 flex-1"><div className="truncate font-medium text-slate-800">{writing.prompt_md}</div><div className="mt-1 text-xs text-slate-400">{latest ? `第 ${latest.version} 版 · ${latest.score} 分` : `目标等级 ${writing.target_level ?? "自适应"}`}</div></div><span className={`shrink-0 rounded-full px-2 py-1 text-xs ${writing.status === "completed" ? "bg-emerald-50 text-emerald-700" : writing.status === "revision_due" ? "bg-amber-50 text-amber-700" : "bg-violet-50 text-violet-700"}`}>{status}</span></Link>;
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
