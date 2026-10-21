import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, ChevronDown, ExternalLink, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { AgentChat } from "../../components/AgentChat";
import { AppShell } from "../../components/AppShell";
import { Markdown } from "../../components/Markdown";
import { Toast } from "../../components/Toast";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import type { ReviewAnswerOut, ReviewCardOut, ReviewQueueOut, WikiPageOut } from "../../lib/types";
import { useHeartbeat } from "../../lib/useHeartbeat";

const ratings = [
  { value: 1, label: "重来", key: "again_days", cls: "border-red-200 bg-red-50 text-red-700 hover:bg-red-100" },
  { value: 2, label: "困难", key: "hard_days", cls: "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100" },
  { value: 3, label: "良好", key: "good_days", cls: "border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100" },
  { value: 4, label: "简单", key: "easy_days", cls: "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100" },
] as const;

function intervalLabel(days: number): string {
  return days < 1 ? "稍后" : `${Math.round(days)} 天后`;
}

export default function ReviewPage() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [cards, setCards] = useState<ReviewCardOut[]>([]);
  const [sessionTotal, setSessionTotal] = useState(0);
  const [reviewedToday, setReviewedToday] = useState(0);
  const [initialized, setInitialized] = useState(false);
  const [showAnswer, setShowAnswer] = useState(false);
  const [showWikiContent, setShowWikiContent] = useState(false);
  const [companionOpen, setCompanionOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  const { data: queue, isPending } = useQuery<ReviewQueueOut>({
    queryKey: ["review-queue", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/reviews/queue`),
    enabled: Boolean(courseId),
  });

  useHeartbeat(courseId, "review", true);

  useEffect(() => {
    if (!queue || initialized) return;
    setCards(queue.cards);
    setSessionTotal(queue.cards.length);
    setReviewedToday(queue.reviewed_today);
    setInitialized(true);
  }, [initialized, queue]);

  const answer = useMutation({
    mutationFn: ({ cardId, rating }: { cardId: string; rating: number }) =>
      api.post<ReviewAnswerOut>(`/api/v1/reviews/${cardId}/answer`, { rating }),
    onSuccess: (result, variables) => {
      setCards((current) => current.filter((card) => card.card_id !== variables.cardId));
      setReviewedToday((current) => current + 1);
      setShowAnswer(false);
      setShowWikiContent(false);
      setError("");
      if (variables.rating === 1) {
        setToast(result.kp_status ? "已标记重来，该知识点转入需巩固" : "已标记重来，稍后继续巩固");
      }
      void queryClient.invalidateQueries({ queryKey: ["review-queue", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["graph", courseId] });
      void queryClient.invalidateQueries({ queryKey: ["stats-me"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "提交复习评分失败"),
  });

  const current = cards[0];
  const currentKp = current?.kp;
  const answeredCount = sessionTotal - cards.length;
  const position = current ? answeredCount + 1 : sessionTotal;
  const { data: wikiPage, isFetching: wikiLoading, isError: wikiError } = useQuery<WikiPageOut>({
    queryKey: ["review-wiki-page", courseId, currentKp?.primary_wiki_slug],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/wiki/pages/${currentKp!.primary_wiki_slug}`),
    enabled: Boolean(showWikiContent && currentKp?.primary_wiki_slug),
  });

  if (isPending || !initialized) {
    return (
      <AppShell>
        <div className="py-16 text-center text-sm text-slate-400">加载复习队列中…</div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <Link
        to={`/courses/${courseId}/plan`}
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-indigo-600"
      >
        <ArrowLeft className="h-4 w-4" />
        返回计划
      </Link>

      <div className="mt-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-sm text-indigo-600">今日复习</div>
          <h1 className="mt-1 text-2xl font-bold text-slate-900">巩固到期知识点</h1>
        </div>
        <div className="rounded-lg bg-white px-3 py-2 text-sm text-slate-600 shadow-sm">
          本次 {position}/{sessionTotal} · 今日已复习 {reviewedToday} 张
        </div>
      </div>

      {error && <div className="mt-4"><Alert>{error}</Alert></div>}
      {(queue?.activated_today ?? 0) > 0 && (
        <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm font-medium text-violet-700">
          今日引入 {queue?.activated_today ?? 0} 个新词
        </div>
      )}

      <div className="mx-auto mt-6 max-w-3xl space-y-4">
        {!current ? (
          <Card className="py-14 text-center">
            <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full bg-emerald-100">
              <CheckCircle2 className="h-10 w-10 text-emerald-600" />
            </div>
            <h2 className="mt-5 text-xl font-bold text-slate-900">今日复习完成</h2>
            <p className="mt-2 text-sm text-slate-500">今日已复习 {reviewedToday} 张，保持这个节奏。</p>
            <Button className="mt-6" onClick={() => navigate(`/courses/${courseId}`)}>
              返回课程
            </Button>
          </Card>
        ) : (
          <Card className="min-h-[24rem] p-5 sm:p-8">
            {current.vocab ? (
              <div className="text-center">
                <h2 className="text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl">
                  {current.vocab.word}
                </h2>
                {current.vocab.phonetic && (
                  <div className="mt-3 text-lg text-slate-400">{current.vocab.phonetic}</div>
                )}
              </div>
            ) : currentKp?.recall_prompt.trim() ? (
              <>
                <div className="text-xs font-medium text-indigo-500">知识点 · {currentKp.title}</div>
                <h2 className="mt-2 text-2xl font-bold leading-snug text-slate-900">{currentKp.recall_prompt}</h2>
              </>
            ) : (
              <>
                <div className="text-xs font-medium text-indigo-500">知识点回忆</div>
                <h2 className="mt-2 text-2xl font-bold text-slate-900">{currentKp?.title}</h2>
              </>
            )}

            {!showAnswer ? (
              <div className="flex min-h-64 flex-col items-center justify-center text-center">
                <RotateCcw className="h-10 w-10 text-indigo-300" />
                <p className="mt-4 text-slate-500">先回忆要点，再看答案</p>
                <Button className="mt-6" onClick={() => setShowAnswer(true)}>
                  显示答案
                </Button>
              </div>
            ) : (
              <div className="mt-6">
                {current.vocab ? (
                  <div className="space-y-4 rounded-xl bg-slate-50 p-5 text-[15px] leading-7 text-slate-700">
                    <Markdown content={current.vocab.meaning_md} />
                    {current.vocab.example_md && (
                      <div className="border-t border-slate-200 pt-4 italic text-slate-500">
                        <Markdown content={current.vocab.example_md} />
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="rounded-xl bg-slate-50 p-4 text-[15px] leading-7 text-slate-700">
                    {currentKp?.summary || "该知识点暂无摘要。"}
                  </div>
                )}
                {currentKp?.primary_wiki_slug && (
                  <div className="mt-3 space-y-3">
                    <button
                      className="flex w-full items-center justify-between rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-left text-sm font-medium text-indigo-700"
                      onClick={() => setShowWikiContent((open) => !open)}
                    >
                      查看完整内容
                      <ChevronDown className={`h-4 w-4 transition-transform ${showWikiContent ? "rotate-180" : ""}`} />
                    </button>
                    {showWikiContent && (
                      <div className="min-w-0 rounded-xl border border-slate-200 bg-white p-4">
                        {wikiLoading ? (
                          <div className="flex items-center gap-2 text-sm text-slate-400">
                            <Loader2 className="h-4 w-4 animate-spin" />加载 Wiki 内容中…
                          </div>
                        ) : wikiError ? (
                          <Alert>完整内容加载失败，请稍后重试。</Alert>
                        ) : wikiPage ? (
                          <Markdown
                            content={wikiPage.content_md}
                            onWikiLink={(slug) => navigate(`/courses/${courseId}/wiki/${slug}`)}
                          />
                        ) : null}
                      </div>
                    )}
                    <button
                      className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline"
                      onClick={() => navigate(`/courses/${courseId}/wiki/${currentKp.primary_wiki_slug}`)}
                    >
                      <ExternalLink className="h-4 w-4" />
                      查看 Wiki 页
                    </button>
                  </div>
                )}
                <div className="mt-8 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {ratings.map((rating) => (
                    <button
                      key={rating.value}
                      disabled={answer.isPending}
                      onClick={() => answer.mutate({ cardId: current.card_id, rating: rating.value })}
                      className={`rounded-xl border px-2 py-3 text-sm font-semibold transition-colors disabled:opacity-50 ${rating.cls}`}
                    >
                      <span className="flex items-center justify-center gap-1">
                        {answer.isPending && answer.variables?.rating === rating.value && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        )}
                        {rating.label}
                      </span>
                      <span className="mt-1 block text-[11px] font-normal text-slate-400">
                        {intervalLabel(current.intervals[rating.key])}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Card>
        )}

        <button
          className="flex w-full items-center justify-between rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm font-medium text-indigo-700"
          onClick={() => setCompanionOpen((open) => !open)}
        >
          复习时问陪伴反馈师
          <ChevronDown className={`h-4 w-4 transition-transform ${companionOpen ? "rotate-180" : ""}`} />
        </button>
        {companionOpen && (
          <AgentChat
            courseId={courseId!}
            agent="companion"
            context={{ mode: "review" }}
            header={{ title: "陪伴反馈师 · 复习答疑", subtitle: "先给提示，帮助你主动回忆" }}
            emptyHint="卡住时可以提问，我会先给提示，不直接揭晓完整答案。"
          />
        )}
      </div>
      {toast && <Toast key={toast} message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
