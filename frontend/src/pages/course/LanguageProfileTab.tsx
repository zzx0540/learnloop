import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";

import { Markdown } from "../../components/Markdown";
import { ChoiceReviewList } from "../../components/language/ChoiceReviewList";
import { Alert, Button, Card, Input, Label } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type {
  CourseOut,
  LanguageAssessmentJobOut,
  LanguageAssessmentOut,
  LanguageProfileOut,
  LanguageSourceOut,
} from "../../lib/types";

function englishWords(value: string): number {
  return value.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g)?.length ?? 0;
}

function resultObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

const dimensionLabels = {
  main_idea: "主旨",
  detail: "细节",
  inference: "推断",
  context_vocab: "语境词义",
};

export default function LanguageProfileTab() {
  const { courseId } = useParams();
  const { course } = useOutletContext<{ course?: CourseOut }>();
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const [vocabAnswers, setVocabAnswers] = useState<Record<string, number>>({});
  const [readingAnswers, setReadingAnswers] = useState<Record<string, number>>({});
  const [writing, setWriting] = useState("");
  const [intensityMode, setIntensityMode] = useState<"adaptive" | "manual">("adaptive");
  const [manualWords, setManualWords] = useState(10);

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
  const assessmentQuery = useQuery<LanguageAssessmentOut | null>({
    queryKey: ["language-assessment-current", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/language/assessments/current`),
    enabled: Boolean(courseId && course?.kind === "language"),
    refetchInterval: (query) => ["building", "submitted"].includes(query.state.data?.status ?? "") ? 2000 : false,
  });
  const profile = profileQuery.data;
  const assessment = assessmentQuery.data;
  const items = assessment?.items;
  const assessmentInProgress = ["building", "ready", "submitted"].includes(assessment?.status ?? "");

  useEffect(() => {
    if (!profile) return;
    setIntensityMode(profile.intensity_mode);
    setManualWords(profile.daily_new_words);
  }, [profile]);

  useEffect(() => {
    if (assessment?.status !== "graded") return;
    void queryClient.invalidateQueries({ queryKey: ["language-profile", courseId] });
    void queryClient.invalidateQueries({ queryKey: ["vocab-stats", courseId] });
    void queryClient.invalidateQueries({ queryKey: ["review-queue", courseId] });
  }, [assessment?.status, courseId, queryClient]);

  const startAssessment = useMutation({
    mutationFn: () => api.post<LanguageAssessmentJobOut>(`/api/v1/courses/${courseId}/language/assessments`, {
      kind: profile?.latest_assessment_at ? (profile.assessment_status === "stale" ? "source_check" : "periodic") : "baseline",
      source_ids: [],
    }),
    onSuccess: () => {
      setError("");
      setVocabAnswers({});
      setReadingAnswers({});
      setWriting("");
      void assessmentQuery.refetch();
      void profileQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "能力测评生成失败"),
  });
  const submitAssessment = useMutation({
    mutationFn: () => api.post<LanguageAssessmentJobOut>(`/api/v1/language/assessments/${assessment?.id}/submit`, {
      vocab_answers: items?.vocab_items.map((item) => ({ item_id: item.id, choice_index: vocabAnswers[item.id] })) ?? [],
      reading_answers: items?.reading.questions.map((item) => ({ item_id: item.id, choice_index: readingAnswers[item.id] })) ?? [],
      writing_md: writing,
    }),
    onSuccess: () => {
      setError("");
      void assessmentQuery.refetch();
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "能力测评提交失败"),
  });
  const saveIntensity = useMutation({
    mutationFn: () => api.patch<LanguageProfileOut>(`/api/v1/courses/${courseId}/language/profile`, intensityMode === "manual"
      ? { intensity_mode: "manual", daily_new_words: manualWords }
      : { intensity_mode: "adaptive" }),
    onSuccess: (updated) => {
      setError("");
      queryClient.setQueryData(["language-profile", courseId], updated);
      void queryClient.invalidateQueries({ queryKey: ["vocab-stats", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue", courseId] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "学习强度保存失败"),
  });

  const allAnswered = useMemo(() => Boolean(items)
    && items!.vocab_items.every((item) => vocabAnswers[item.id] !== undefined)
    && items!.reading.questions.every((item) => readingAnswers[item.id] !== undefined)
    && englishWords(writing) >= (items?.writing.min_words ?? 50), [items, readingAnswers, vocabAnswers, writing]);
  const result = resultObject(assessment?.result);
  const resultWriting = resultObject(result.writing);

  if (course && course.kind !== "language") return <Alert>仅英语课程支持能力分级。</Alert>;

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      {error && <Alert>{error}</Alert>}
      <Card className="overflow-hidden bg-gradient-to-br from-indigo-600 to-violet-600 text-white">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="text-sm text-indigo-100">课程学习分级</div>
            <div className="mt-1 text-4xl font-bold">{profile?.overall_level ?? "待测"}</div>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-indigo-100">
              只衡量这门课程导入内容中的词汇与读写表现，不是官方 CEFR 认证，也不代表完整英语词汇量。
            </p>
          </div>
          {profile && !["building", "ready"].includes(profile.assessment_status) && !assessmentInProgress && (
            <Button variant="outline" className="border-white bg-white hover:bg-indigo-50" disabled={startAssessment.isPending} onClick={() => startAssessment.mutate()}>
              {startAssessment.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {profile.latest_assessment_at ? (profile.assessment_status === "stale" ? "评估新增材料" : "重新测评") : "开始基线测评"}
            </Button>
          )}
        </div>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["阅读等级", profile?.reading_level ?? "待测"],
          ["写作等级", profile?.writing_level ?? "待测"],
          ["课程词汇已知量", profile ? `${profile.vocab_known_estimate}/${profile.vocab_total}` : "—"],
          ["每日推荐新词", profile ? `${profile.recommended_daily_words} 个` : "—"],
        ].map(([label, value]) => <Card key={label} className="p-4"><div className="text-xs text-slate-400">{label}</div><div className="mt-1 text-xl font-bold text-slate-900">{value}</div></Card>)}
      </div>

      {profile && profile.vocab_total > 0 && (
        <Card className="p-4 text-sm text-slate-600">
          本课程目标词汇预计已知 <strong>{profile.vocab_known_estimate}</strong> 个，95% 区间 {profile.vocab_ci_low}–{profile.vocab_ci_high}。
        </Card>
      )}

      {profile && (
        <Card>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="min-w-0 flex-1">
              <h2 className="font-semibold text-slate-900">学习强度</h2>
              <p className="mt-1 text-sm text-slate-500">自适应模式会根据等级、每日时间和近 7 天读写得分调整；当前系统推荐 {profile.recommended_daily_words} 个/天。</p>
            </div>
            <div className="grid min-w-0 gap-3 sm:grid-cols-[10rem_9rem_auto] sm:items-end">
              <div>
                <Label htmlFor="intensity-mode">强度模式</Label>
                <select id="intensity-mode" value={intensityMode} onChange={(event) => setIntensityMode(event.target.value as "adaptive" | "manual")} className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm">
                  <option value="adaptive">自适应</option>
                  <option value="manual">手动</option>
                </select>
              </div>
              <div>
                <Label htmlFor="manual-daily-words">每日新词</Label>
                <Input id="manual-daily-words" type="number" min={5} max={100} disabled={intensityMode !== "manual"} value={manualWords} onChange={(event) => setManualWords(Number(event.target.value))} />
              </div>
              <Button disabled={saveIntensity.isPending || (intensityMode === "manual" && (manualWords < 5 || manualWords > 100))} onClick={() => saveIntensity.mutate()}>
                {saveIntensity.isPending && <Loader2 className="h-4 w-4 animate-spin" />}保存强度
              </Button>
            </div>
          </div>
        </Card>
      )}

      {(assessment?.status === "building" || assessment?.status === "submitted") && (
        <Card className="flex items-center gap-3 text-sm text-slate-600">
          <Loader2 className="h-5 w-5 animate-spin text-indigo-500" />
          {assessment.status === "building" ? "正在根据课程材料生成测评…" : "正在批改写作并计算课程分级…"}
        </Card>
      )}

      {assessment?.status === "failed" && <Alert>{assessment.error || "能力测评失败，请重新开始。"}</Alert>}

      {assessment?.status === "ready" && items && (
        <div className="space-y-4">
          {items.vocab_items.length > 0 && (
            <Card>
              <h2 className="text-lg font-semibold text-slate-900">1. 目标词汇识别</h2>
              <p className="mt-1 text-sm text-slate-500">选择最接近的课程内释义，共 {items.vocab_items.length} 题。</p>
              <div className="mt-5 space-y-5">
                {items.vocab_items.map((item, index) => (
                  <fieldset key={item.id}>
                    <legend className="font-medium text-slate-800">{index + 1}. {item.word}</legend>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      {item.options.map((option, optionIndex) => (
                        <label key={optionIndex} className={`cursor-pointer rounded-lg border px-3 py-2 text-sm ${vocabAnswers[item.id] === optionIndex ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200"}`}>
                          <input className="mr-2" type="radio" name={item.id} checked={vocabAnswers[item.id] === optionIndex} onChange={() => setVocabAnswers((current) => ({ ...current, [item.id]: optionIndex }))} />{option}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ))}
              </div>
            </Card>
          )}

          <Card>
            <h2 className="text-lg font-semibold text-slate-900">2. 阅读理解</h2>
            <div className="mt-4 rounded-xl bg-slate-50 p-4 sm:p-6"><h3 className="mb-3 font-semibold">{items.reading.title}</h3><Markdown content={items.reading.passage_md} /></div>
            <div className="mt-5 space-y-5">
              {items.reading.questions.map((item, index) => (
                <fieldset key={item.id}>
                  <legend className="font-medium text-slate-800">{index + 1}. {item.prompt}</legend>
                  <div className="mt-2 grid gap-2">
                    {item.options.map((option, optionIndex) => (
                      <label key={optionIndex} className={`cursor-pointer rounded-lg border px-3 py-2 text-sm ${readingAnswers[item.id] === optionIndex ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200"}`}>
                        <input className="mr-2" type="radio" name={item.id} checked={readingAnswers[item.id] === optionIndex} onChange={() => setReadingAnswers((current) => ({ ...current, [item.id]: optionIndex }))} />{option}
                      </label>
                    ))}
                  </div>
                </fieldset>
              ))}
            </div>
          </Card>

          <Card>
            <h2 className="text-lg font-semibold text-slate-900">3. 英语写作</h2>
            <div className="mt-3"><Markdown content={items.writing.prompt_md} /></div>
            <div className="mt-2 text-sm text-slate-500"><Markdown content={items.writing.requirements_md} /></div>
            <textarea className="mt-4 min-h-56 w-full rounded-xl border border-slate-300 p-4 text-sm leading-6 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" value={writing} onChange={(event) => setWriting(event.target.value)} placeholder="Write your response in English…" />
            <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <span className="text-sm text-slate-500">{englishWords(writing)} / {items.writing.min_words}–{items.writing.max_words} words</span>
              <Button disabled={!allAnswered || submitAssessment.isPending || englishWords(writing) > items.writing.max_words} onClick={() => submitAssessment.mutate()}>
                {submitAssessment.isPending && <Loader2 className="h-4 w-4 animate-spin" />}提交测评
              </Button>
            </div>
          </Card>
        </div>
      )}

      {assessment?.status === "graded" && (
        <div className="space-y-4">
          <Card>
            <div className="flex items-center gap-2 text-emerald-700"><CheckCircle2 className="h-5 w-5" /><h2 className="font-semibold">最近一次测评已完成</h2></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg bg-slate-50 p-3"><div className="text-xs text-slate-400">阅读分数</div><div className="mt-1 text-xl font-bold">{String(resultObject(result.reading).score ?? "—")}</div></div>
              <div className="rounded-lg bg-slate-50 p-3"><div className="text-xs text-slate-400">写作分数</div><div className="mt-1 text-xl font-bold">{String(resultWriting.score ?? "—")}</div></div>
              <div className="rounded-lg bg-slate-50 p-3"><div className="text-xs text-slate-400">综合等级</div><div className="mt-1 text-xl font-bold">{String(result.overall_level ?? "—")}</div></div>
            </div>
            {typeof resultWriting.feedback_md === "string" && <div className="mt-4 rounded-xl border border-slate-200 p-4"><Markdown content={resultWriting.feedback_md} /></div>}
            {assessment.review?.writing_md && (
              <details className="mt-4 rounded-xl border border-slate-200 px-4 py-3">
                <summary className="cursor-pointer text-sm font-medium text-slate-600">查看我的测评作文</summary>
                <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{assessment.review.writing_md}</div>
              </details>
            )}
            <Button variant="outline" className="mt-4" onClick={() => startAssessment.mutate()} disabled={startAssessment.isPending}><RefreshCw className="h-4 w-4" />重新测评</Button>
          </Card>
          {items && assessment.review && items.vocab_items.length > 0 && (
            <ChoiceReviewList
              title="词汇题复盘"
              questions={items.vocab_items.map((item) => ({ id: item.id, label: item.word ?? "词汇题", options: item.options }))}
              reviews={assessment.review.vocab}
            />
          )}
          {items && assessment.review && (
            <ChoiceReviewList
              title="阅读题复盘"
              questions={items.reading.questions.map((item) => ({
                id: item.id,
                label: item.prompt ?? "阅读题",
                options: item.options,
                meta: item.dimension ? dimensionLabels[item.dimension] : undefined,
              }))}
              reviews={assessment.review.reading}
            />
          )}
        </div>
      )}

      <Card className="p-4 text-sm text-slate-500">已导入来源 {sourcesQuery.data?.length ?? 0} 个；每次新增词表或阅读材料后，系统会提示做增量测评，不会清空原有复习卡。</Card>
    </div>
  );
}
