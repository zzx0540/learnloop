import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Clock3, Loader2, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AppShell } from "../../components/AppShell";
import { Markdown } from "../../components/Markdown";
import { ChoiceReviewList } from "../../components/language/ChoiceReviewList";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { ReadingExerciseOut } from "../../lib/types";
import { useHeartbeat } from "../../lib/useHeartbeat";

const dimensionLabels = {
  main_idea: "主旨",
  detail: "细节",
  inference: "推断",
  context_vocab: "语境词义",
};

export default function ReadingPage() {
  const { courseId, readingId } = useParams();
  const queryClient = useQueryClient();
  const startedAt = useRef(Date.now());
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [response, setResponse] = useState("");
  const [selectedWords, setSelectedWords] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const readingQuery = useQuery<ReadingExerciseOut>({
    queryKey: ["reading", readingId],
    queryFn: () => api.get(`/api/v1/readings/${readingId}`),
    enabled: Boolean(readingId),
    refetchInterval: (query) => query.state.data?.status === "submitted" ? 3000 : false,
  });
  const reading = readingQuery.data;
  useHeartbeat(courseId, "learn", reading?.status === "open");

  useEffect(() => {
    if (reading?.status !== "graded") return;
    void queryClient.invalidateQueries({ queryKey: ["readings", courseId] });
    void queryClient.invalidateQueries({ queryKey: ["plan", courseId] });
    void queryClient.invalidateQueries({ queryKey: ["points-summary"] });
  }, [courseId, queryClient, reading?.status]);

  const submit = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/v1/readings/${readingId}/attempts`, {
      answers: reading?.questions.map((question) => ({ item_id: question.id, choice_index: answers[question.id] })) ?? [],
      response_md: response,
      duration_seconds: Math.max(1, Math.min(14_400, Math.round((Date.now() - startedAt.current) / 1000))),
    }),
    onSuccess: () => {
      setError("");
      void readingQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "阅读作答提交失败"),
  });
  const addVocab = useMutation({
    mutationFn: () => api.post<Record<string, number>>(`/api/v1/readings/${readingId}/vocab`, { words: selectedWords }),
    onSuccess: () => {
      setSelectedWords([]);
      setError("");
      setNotice("重点词已加入本课程词库；已有词条不会被覆盖。");
      void queryClient.invalidateQueries({ queryKey: ["vocab", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["vocab-stats", courseId] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "重点词加入失败"),
  });

  const allAnswered = Boolean(reading) && reading!.questions.every((question) => answers[question.id] !== undefined);
  const responseWords = response.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g)?.length ?? 0;
  const minimumResponseWords = reading?.mode === "intensive" ? 30 : 20;
  const latestAttempt = reading?.attempts.at(-1);
  const wpm = latestAttempt && latestAttempt.duration_seconds > 0
    ? Math.round((reading?.passage_md.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g)?.length ?? 0) / latestAttempt.duration_seconds * 60)
    : null;
  const dimensions = useMemo(() => latestAttempt ? [
    ["主旨", latestAttempt.main_idea],
    ["细节", latestAttempt.detail],
    ["推断", latestAttempt.inference],
    ["语境词义", latestAttempt.context_vocab],
    [reading?.mode === "intensive" ? "摘要" : "回应", latestAttempt.summary],
  ] as const : [], [latestAttempt, reading?.mode]);

  if (readingQuery.isPending) return <AppShell><div className="py-16 text-center text-sm text-slate-400">加载阅读练习中…</div></AppShell>;
  if (!reading || readingQuery.error) return <AppShell><Alert>{readingQuery.error instanceof ApiError ? readingQuery.error.message : "阅读练习加载失败"}</Alert></AppShell>;

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl pb-10">
        <Link to={`/courses/${courseId}/reading`} className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-indigo-600"><ArrowLeft className="h-4 w-4" />返回阅读</Link>
        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div><div className="text-sm font-medium text-indigo-600">{reading.mode === "intensive" ? "精读练习" : "泛读练习"} · {reading.target_level ?? "自适应"}</div><h1 className="mt-1 text-2xl font-bold text-slate-900">{reading.title}</h1></div>
          <span className={`w-fit rounded-full px-3 py-1 text-sm ${reading.status === "graded" ? "bg-emerald-50 text-emerald-700" : reading.status === "submitted" ? "bg-amber-50 text-amber-700" : "bg-indigo-50 text-indigo-700"}`}>{reading.status === "graded" ? "已批改" : reading.status === "submitted" ? "批改中" : "作答中"}</span>
        </div>
        {error && <div className="mt-4"><Alert>{error}</Alert></div>}
        {notice && <div className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</div>}

        <Card className="mt-5 p-5 sm:p-8"><Markdown content={reading.passage_md} /></Card>

        {reading.focus_vocab.length > 0 && (
          <Card className="mt-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-semibold text-slate-900">重点词</h2><p className="mt-1 text-sm text-slate-500">勾选需要继续复习的词，加入本课程词库。</p></div><Button variant="outline" disabled={selectedWords.length === 0 || addVocab.isPending} onClick={() => addVocab.mutate()}><Plus className="h-4 w-4" />加入词库 ({selectedWords.length})</Button></div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {reading.focus_vocab.map((item) => (
                <label key={item.word} className={`cursor-pointer rounded-lg border p-3 ${selectedWords.includes(item.word) ? "border-indigo-400 bg-indigo-50" : "border-slate-200"}`}><input className="mr-2" type="checkbox" checked={selectedWords.includes(item.word)} onChange={(event) => setSelectedWords((current) => event.target.checked ? [...current, item.word] : current.filter((word) => word !== item.word))} /><strong>{item.word}</strong><span className="ml-2 text-sm text-slate-500">{item.meaning}</span></label>
              ))}
            </div>
          </Card>
        )}

        {reading.status === "open" && (
          <div className="mt-4 space-y-4">
            <Card>
              <h2 className="text-lg font-semibold text-slate-900">阅读理解</h2>
              <div className="mt-5 space-y-6">
                {reading.questions.map((question, index) => (
                  <fieldset key={question.id}>
                    <legend className="font-medium text-slate-800">{index + 1}. {question.prompt} <span className="ml-1 text-xs font-normal text-slate-400">{dimensionLabels[question.dimension]}</span></legend>
                    <div className="mt-2 grid gap-2">
                      {question.options.map((option, optionIndex) => <label key={optionIndex} className={`cursor-pointer rounded-lg border px-3 py-2 text-sm ${answers[question.id] === optionIndex ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200"}`}><input className="mr-2" type="radio" name={question.id} checked={answers[question.id] === optionIndex} onChange={() => setAnswers((current) => ({ ...current, [question.id]: optionIndex }))} />{option}</label>)}
                    </div>
                  </fieldset>
                ))}
              </div>
            </Card>
            <Card>
              <h2 className="text-lg font-semibold text-slate-900">{reading.mode === "intensive" ? "英文摘要" : "英文阅读回应"}</h2>
              <p className="mt-1 text-sm text-slate-500">{reading.mode === "intensive" ? "用自己的话概括主要观点与关键证据。" : "记录主旨、你的判断或最值得记住的一点，保持泛读流畅。"}</p>
              <textarea className="mt-4 min-h-40 w-full rounded-xl border border-slate-300 p-4 text-sm leading-6 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" value={response} onChange={(event) => setResponse(event.target.value)} placeholder="Write in English…" />
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><span className="inline-flex items-center gap-1 text-sm text-slate-500"><Clock3 className="h-4 w-4" />{responseWords} / {minimumResponseWords}–300 words · 提交时记录实际阅读时长</span><Button disabled={!allAnswered || responseWords < minimumResponseWords || responseWords > 300 || submit.isPending} onClick={() => { if (window.confirm("提交后将进入 AI 批改，确认提交？")) submit.mutate(); }}>{submit.isPending && <Loader2 className="h-4 w-4 animate-spin" />}提交阅读练习</Button></div>
            </Card>
          </div>
        )}

        {reading.status === "submitted" && <Card className="mt-4 flex items-center gap-3"><Loader2 className="h-5 w-5 animate-spin text-indigo-500" /><div><div className="font-medium text-slate-800">正在批改阅读回应</div><div className="mt-1 text-sm text-slate-500">选择题由代码计分，摘要或回应由陪伴师按课程等级批改。</div></div></Card>}

        {reading.status === "graded" && latestAttempt && (
          <div className="mt-4 space-y-4">
            <Card className="overflow-hidden bg-gradient-to-br from-indigo-600 to-indigo-700 text-white"><div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><div className="flex items-center gap-2 text-indigo-100"><CheckCircle2 className="h-4 w-4" />批改完成</div><div className="mt-2 text-4xl font-bold">{latestAttempt.score}<span className="text-xl text-indigo-200"> / 100</span></div></div>{reading.mode === "extensive" && wpm !== null && <div className="rounded-full bg-white/15 px-4 py-2 text-sm">约 {wpm} WPM</div>}</div></Card>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">{dimensions.map(([label, score]) => <Card key={label} className="p-4"><div className="text-xs text-slate-400">{label}</div><div className="mt-1 text-xl font-bold text-slate-900">{score}/20</div></Card>)}</div>
            <ChoiceReviewList
              title="阅读题复盘"
              questions={reading.questions.map((question) => ({
                id: question.id,
                label: question.prompt,
                options: question.options,
                meta: dimensionLabels[question.dimension],
              }))}
              reviews={latestAttempt.reviews}
            />
            <Card><h2 className="font-semibold text-slate-900">批改反馈</h2><div className="mt-4"><Markdown content={latestAttempt.feedback_md} /></div></Card>
          </div>
        )}
      </div>
    </AppShell>
  );
}
