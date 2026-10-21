import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, FilePenLine, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AppShell } from "../../components/AppShell";
import { Markdown } from "../../components/Markdown";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { WritingExerciseOut } from "../../lib/types";
import { useHeartbeat } from "../../lib/useHeartbeat";

function englishWords(value: string): number {
  return value.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g)?.length ?? 0;
}

export default function WritingPage() {
  const { courseId, writingId } = useParams();
  const queryClient = useQueryClient();
  const storageKey = `learnloop-writing-draft:${writingId}`;
  const [draft, setDraft] = useState(() => localStorage.getItem(storageKey) ?? "");
  const [error, setError] = useState("");
  const [gradingTimedOut, setGradingTimedOut] = useState(false);

  const writingQuery = useQuery<WritingExerciseOut>({
    queryKey: ["writing", writingId],
    queryFn: () => api.get(`/api/v1/writings/${writingId}`),
    enabled: Boolean(writingId),
    refetchInterval: (query) => query.state.data?.status === "grading" && !gradingTimedOut ? 3000 : false,
  });
  const writing = writingQuery.data;
  useHeartbeat(courseId, "learn", writing?.status === "open" || writing?.status === "revision_due");

  useEffect(() => {
    localStorage.setItem(storageKey, draft);
  }, [draft, storageKey]);

  useEffect(() => {
    if (writing?.status !== "grading") {
      setGradingTimedOut(false);
      return;
    }
    const timer = window.setTimeout(() => setGradingTimedOut(true), 90_000);
    return () => window.clearTimeout(timer);
  }, [writing?.status, writing?.submissions.length]);

  useEffect(() => {
    if (!writing || writing.status === "grading") return;
    if (writing.status === "completed") {
      localStorage.removeItem(storageKey);
      void queryClient.invalidateQueries({ queryKey: ["writings", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["points-summary"] });
    }
  }, [courseId, queryClient, storageKey, writing]);

  const submit = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/v1/writings/${writingId}/submissions`, { submission_md: draft }),
    onSuccess: () => {
      setError("");
      setGradingTimedOut(false);
      void writingQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "写作提交失败"),
  });

  const latest = writing?.submissions.at(-1);
  const scores = useMemo(() => latest ? [
    ["任务完成", latest.task_fulfillment],
    ["组织结构", latest.organization],
    ["语法准确", latest.grammar],
    ["词汇运用", latest.vocabulary],
  ] as const : [], [latest]);

  if (writingQuery.isPending) return <AppShell><div className="py-16 text-center text-sm text-slate-400">加载写作练习中…</div></AppShell>;
  if (!writing || writingQuery.error) return <AppShell><Alert>{writingQuery.error instanceof ApiError ? writingQuery.error.message : "写作练习加载失败"}</Alert></AppShell>;

  const editable = writing.status === "open" || writing.status === "revision_due";

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl pb-10">
        <Link to={`/courses/${courseId}/writing`} className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-indigo-600"><ArrowLeft className="h-4 w-4" />返回写作</Link>
        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><div className="text-sm font-medium text-violet-600">英语写作 · {writing.target_level ?? "自适应"}</div><h1 className="mt-1 text-2xl font-bold text-slate-900">第 {writing.submissions.length + (editable ? 1 : 0)} 版</h1></div><span className={`w-fit rounded-full px-3 py-1 text-sm ${writing.status === "completed" ? "bg-emerald-50 text-emerald-700" : writing.status === "revision_due" ? "bg-amber-50 text-amber-700" : "bg-violet-50 text-violet-700"}`}>{writing.status === "completed" ? "修订完成" : writing.status === "revision_due" ? "等待第二版" : writing.status === "grading" ? "批改中" : "撰写中"}</span></div>
        {error && <div className="mt-4"><Alert>{error}</Alert></div>}

        <Card className="mt-5"><h2 className="font-semibold text-slate-900">写作题目</h2><div className="mt-3"><Markdown content={writing.prompt_md} /></div><div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-600"><Markdown content={writing.requirements_md} /></div></Card>

        {editable && (
          <Card className="mt-4">
            {writing.status === "revision_due" && latest && <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">请根据第 {latest.version} 版反馈完整重写。只有本版批改完成后，练习和关联计划才完成。</div>}
            <label htmlFor="writing-draft" className="font-semibold text-slate-900">{writing.status === "revision_due" ? "修订稿" : "首稿"}</label>
            <textarea id="writing-draft" className="mt-3 min-h-[22rem] w-full rounded-xl border border-slate-300 p-4 text-sm leading-7 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Write your response in English…" />
            <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="text-sm text-slate-500">{englishWords(draft)} words · 草稿自动保存在当前浏览器</div><Button disabled={draft.trim().length < 50 || draft.length > 5000 || submit.isPending} onClick={() => { if (window.confirm(writing.status === "revision_due" ? "确认提交修订稿？" : "确认提交首稿进行批改？")) submit.mutate(); }}>{submit.isPending && <Loader2 className="h-4 w-4 animate-spin" />}提交第 {writing.submissions.length + 1} 版</Button></div>
          </Card>
        )}

        {writing.status === "grading" && (
          <Card className="mt-4 flex items-start gap-3">
            <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-violet-500" />
            <div className="min-w-0 flex-1"><div className="font-medium text-slate-800">正在进行四维批改</div><div className="mt-1 text-sm text-slate-500">批改会参考课程轨道、目标等级和上一版反馈。</div>{gradingTimedOut && <Button variant="outline" className="mt-3" onClick={() => { setGradingTimedOut(false); void writingQuery.refetch(); }}><RefreshCw className="h-4 w-4" />刷新结果</Button>}</div>
          </Card>
        )}

        {latest && (
          <div className="mt-4 space-y-4">
            <Card className="overflow-hidden bg-gradient-to-br from-violet-600 to-indigo-700 text-white"><div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><div className="flex items-center gap-2 text-violet-100">{writing.status === "completed" ? <CheckCircle2 className="h-4 w-4" /> : <FilePenLine className="h-4 w-4" />}第 {latest.version} 版反馈</div><div className="mt-2 text-4xl font-bold">{latest.score}<span className="text-xl text-violet-200"> / 100</span></div></div>{writing.status === "revision_due" && <div className="rounded-full bg-white/15 px-4 py-2 text-sm">还需修订一次</div>}</div></Card>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{scores.map(([label, score]) => <Card key={label} className="p-4"><div className="text-xs text-slate-400">{label}</div><div className="mt-1 text-xl font-bold text-slate-900">{score}/25</div></Card>)}</div>
            <Card><Markdown content={latest.feedback_md} /></Card>
          </div>
        )}

        {writing.submissions.length > 1 && (
          <Card className="mt-4"><h2 className="font-semibold text-slate-900">版本历史</h2><div className="mt-4 space-y-3">{writing.submissions.map((submission) => <details key={submission.id} className="rounded-lg border border-slate-200 p-4"><summary className="cursor-pointer font-medium text-slate-700">第 {submission.version} 版 · {submission.score} 分</summary><div className="mt-4 whitespace-pre-wrap text-sm leading-6 text-slate-600">{submission.submission_md}</div></details>)}</div></Card>
        )}
      </div>
    </AppShell>
  );
}
