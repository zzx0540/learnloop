import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { PrintLayout } from "../../components/print/PrintLayout";
import { PrintMarkdown } from "../../components/print/PrintMarkdown";
import { api } from "../../lib/api";
import type { CourseOut, PlanDetailOut, PlanItemStudyOut, WikiPageOut } from "../../lib/types";

function todayKey(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${today.getFullYear()}-${month}-${day}`;
}

export default function DailyNotesPrintPage() {
  const { courseId } = useParams();
  const [searchParams] = useSearchParams();
  const requestedDate = searchParams.get("date");
  const date = requestedDate && /^\d{4}-\d{2}-\d{2}$/.test(requestedDate) ? requestedDate : todayKey();
  const courseQuery = useQuery<CourseOut>({
    queryKey: ["course", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}`),
    enabled: Boolean(courseId),
  });
  const planQuery = useQuery<PlanDetailOut | null>({
    queryKey: ["plan", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/plan`),
    enabled: Boolean(courseId),
  });
  const dailyItems = useMemo(
    () => planQuery.data?.days.find((day) => day.date === date)?.items.filter((item) => item.type === "learn") ?? [],
    [date, planQuery.data],
  );
  const detailQueries = useQueries({
    queries: dailyItems.map((item) => ({
      queryKey: ["plan-item", item.id],
      queryFn: () => api.get<PlanItemStudyOut>(`/api/v1/plan-items/${item.id}`),
    })),
  });
  const details = detailQueries.map((query) => query.data).filter((item): item is PlanItemStudyOut => Boolean(item));
  const wikiSlugs = useMemo(
    () => Array.from(new Set(details.flatMap((item) => item.kps.map((kp) => kp.primary_wiki_slug).filter((slug): slug is string => Boolean(slug))))),
    [details],
  );
  const wikiQueries = useQueries({
    queries: wikiSlugs.map((slug) => ({
      queryKey: ["print-wiki", courseId, slug],
      queryFn: () => api.get<WikiPageOut>(`/api/v1/courses/${courseId}/wiki/pages/${slug}`),
      retry: false,
    })),
  });
  const wikiBySlug = new Map(
    wikiSlugs.map((slug, index) => [slug, wikiQueries[index]?.data] as const),
  );
  const loading = courseQuery.isPending || planQuery.isPending || detailQueries.some((query) => query.isPending) || wikiQueries.some((query) => query.isPending);
  const failed = courseQuery.isError || planQuery.isError || detailQueries.some((query) => query.isError);
  const canPrint = !loading && !failed && details.length > 0;

  return (
    <PrintLayout
      courseName={courseQuery.data?.name ?? "加载中…"}
      templateName="每日学习笔记"
      date={date}
      backTo={`/courses/${courseId}/plan`}
      canPrint={canPrint}
    >
      {loading ? (
        <div className="print-empty-state">正在整理今日学习内容…</div>
      ) : failed ? (
        <div className="print-empty-state">学习内容加载失败，请返回后重试。</div>
      ) : details.length === 0 ? (
        <div className="print-empty-state">该日没有学习条目，无需打印学习笔记。</div>
      ) : (
        details.map((item) => (
          <section key={item.id} className="print-daily-section">
            <h2 className="print-section-title">{item.title}</h2>
            <ul className="print-kp-list">
              {item.kps.map((kp) => (
                <li key={kp.id}>
                  <strong>{kp.title}</strong>
                  <em>{kp.recall_prompt || "尝试不看材料，用自己的话回忆这个知识点。"}</em>
                </li>
              ))}
            </ul>
            {item.kps.map((kp) => {
              const page = kp.primary_wiki_slug ? wikiBySlug.get(kp.primary_wiki_slug) : undefined;
              return (
                <div key={kp.id} className="print-wiki-section">
                  <h3 className="print-wiki-title">{page?.title ?? kp.title}</h3>
                  {page ? <PrintMarkdown content={page.content_md} /> : <p>{kp.summary || "该知识点暂无正文内容。"}</p>}
                </div>
              );
            })}
            <div className="print-notes-label">笔记</div>
            <div className="print-answer-lines print-note-lines" />
          </section>
        ))
      )}
    </PrintLayout>
  );
}
