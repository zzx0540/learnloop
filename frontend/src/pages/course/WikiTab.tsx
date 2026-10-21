import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronDown, History, ScrollText, Search, Wrench } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Markdown } from "../../components/Markdown";
import { Toast } from "../../components/Toast";
import { Button, Card } from "../../components/ui";
import { api } from "../../lib/api";
import type { WikiLintFindingOut, WikiLintKind, WikiLogOut, WikiPageBrief, WikiPageOut, WikiRevisionOut } from "../../lib/types";

const kindLabels: Record<string, string> = {
  concept: "概念页",
  chapter_summary: "章节总结",
  index: "索引",
  schema: "规范",
};

const lintKindLabels: Record<WikiLintKind, string> = {
  broken_link: "死链",
  orphan: "孤儿页",
  contradiction: "内容矛盾",
  outdated: "可能过时",
};

export default function WikiTab() {
  const { courseId, slug } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showLog, setShowLog] = useState(false);
  const [showRevisions, setShowRevisions] = useState(false);
  const [showFindings, setShowFindings] = useState(false);
  const [showConcepts, setShowConcepts] = useState(false);
  const [conceptSearch, setConceptSearch] = useState("");
  const [toast, setToast] = useState("");

  const { data: pages } = useQuery<WikiPageBrief[]>({
    queryKey: ["wiki-pages", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/wiki/pages`),
  });

  const currentSlug = slug ?? "wiki-index";
  const { data: page, isError: pageFailed } = useQuery<WikiPageOut>({
    queryKey: ["wiki-page", courseId, currentSlug],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/wiki/pages/${currentSlug}`),
  });

  const { data: log } = useQuery<WikiLogOut[]>({
    queryKey: ["wiki-log", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/wiki/log`),
    enabled: showLog,
  });

  const { data: revisions } = useQuery<WikiRevisionOut[]>({
    queryKey: ["wiki-revisions", courseId, currentSlug],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/wiki/pages/${currentSlug}/revisions`),
    enabled: showRevisions,
  });

  const { data: findings } = useQuery<WikiLintFindingOut[]>({
    queryKey: ["wiki-lint-findings", courseId, "open"],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/wiki/lint-findings?status=open`),
  });

  const decideFinding = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "resolved" | "dismissed" }) =>
      api.patch<WikiLintFindingOut>(`/api/v1/wiki/lint-findings/${id}`, { status }),
    onSuccess: async (_, variables) => {
      setToast(variables.status === "resolved" ? "已标记为已处理" : "已忽略该发现项");
      await queryClient.invalidateQueries({ queryKey: ["wiki-lint-findings", courseId, "open"] });
    },
    onError: () => setToast("操作失败，请重试"),
  });

  function goTo(s: string) {
    setShowRevisions(false);
    navigate(`/courses/${courseId}/wiki/${s}`);
  }

  const groups: [string, WikiPageBrief[]][] = ["index", "concept", "chapter_summary", "schema"].map(
    (k) => [k, (pages ?? []).filter((p) => p.kind === k)],
  );
  const filteredConcepts = (pages ?? []).filter(
    (page) =>
      page.kind === "concept" &&
      `${page.title} ${page.slug}`.toLocaleLowerCase().includes(conceptSearch.trim().toLocaleLowerCase()),
  );

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <aside className="w-full shrink-0 lg:w-60">
        <div className="space-y-4 lg:sticky lg:top-24">
          {groups.map(([kind, items]) => {
            if (items.length === 0) return null;
            if (kind === "concept") {
              return (
                <div key={kind}>
                  <button
                    type="button"
                    onClick={() => setShowConcepts((visible) => !visible)}
                    className="mb-1 flex w-full items-center justify-between px-2 text-left text-xs font-medium uppercase tracking-wide text-slate-400 hover:text-indigo-600"
                    aria-expanded={showConcepts}
                  >
                    <span>{kindLabels[kind]} · {items.length}</span>
                    <ChevronDown className={clsx("h-4 w-4 transition-transform", showConcepts && "rotate-180")} />
                  </button>
                  {showConcepts && (
                    <>
                      <label className="relative mb-2 block">
                        <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-slate-400" />
                        <input
                          value={conceptSearch}
                          onChange={(event) => setConceptSearch(event.target.value)}
                          placeholder="搜索概念页"
                          aria-label="搜索概念页"
                          className="w-full rounded-md border border-slate-200 py-1.5 pl-8 pr-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                        />
                      </label>
                      <ul className="max-h-[32rem] space-y-0.5 overflow-y-auto">
                        {filteredConcepts.map((p) => (
                          <li key={p.slug}>
                            <button
                              onClick={() => goTo(p.slug)}
                              className={clsx(
                                "w-full truncate rounded-md px-2 py-1 text-left text-sm",
                                currentSlug === p.slug
                                  ? "bg-indigo-50 font-medium text-indigo-700"
                                  : "text-slate-600 hover:bg-slate-100",
                              )}
                            >
                              {p.title}
                            </button>
                          </li>
                        ))}
                      </ul>
                      {filteredConcepts.length === 0 && (
                        <div className="px-2 py-3 text-center text-xs text-slate-400">没有匹配的概念页</div>
                      )}
                    </>
                  )}
                </div>
              );
            }
            return (
              <div key={kind}>
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">
                  {kindLabels[kind]}
                </div>
                <ul className="space-y-0.5">
                  {items.map((p) => (
                    <li key={p.slug}>
                      <button
                        onClick={() => goTo(p.slug)}
                        className={clsx(
                          "w-full truncate rounded-md px-2 py-1 text-left text-sm",
                          currentSlug === p.slug
                            ? "bg-indigo-50 font-medium text-indigo-700"
                            : "text-slate-600 hover:bg-slate-100",
                        )}
                      >
                        {p.title}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          <button
            onClick={() => setShowFindings(!showFindings)}
            className={clsx(
              "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-sm",
              showFindings ? "bg-indigo-50 font-medium text-indigo-700" : "text-slate-500 hover:text-indigo-600",
            )}
          >
            <Wrench className="h-4 w-4" />
            维护
            {(findings?.length ?? 0) > 0 && (
              <span className="ml-auto rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] font-bold leading-none text-white">
                {findings?.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setShowLog(!showLog)}
            className="flex items-center gap-1.5 px-2 text-sm text-slate-500 hover:text-indigo-600"
          >
            <ScrollText className="h-4 w-4" />
            维护日志
          </button>
        </div>
      </aside>

      <div className="min-w-0 flex-1 space-y-4">
        {showFindings && (
          <Card>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="font-semibold text-slate-900">Wiki 维护发现</h3>
              <span className="text-xs text-slate-400">待处理 {findings?.length ?? 0} 项</span>
            </div>
            <div className="space-y-3">
              {(findings ?? []).map((finding) => (
                <div key={finding.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                      {lintKindLabels[finding.kind] ?? finding.kind}
                    </span>
                    {finding.page_slug ? (
                      <button type="button" onClick={() => goTo(finding.page_slug!)} className="text-sm font-medium text-indigo-600 hover:underline">
                        {finding.page_title ?? finding.page_slug}
                      </button>
                    ) : (
                      <span className="text-sm text-slate-500">未关联页面</span>
                    )}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600">{finding.detail_md}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button className="h-8 px-3" disabled={decideFinding.isPending} onClick={() => decideFinding.mutate({ id: finding.id, status: "resolved" })}>已处理</Button>
                    <Button variant="outline" className="h-8 px-3" disabled={decideFinding.isPending} onClick={() => decideFinding.mutate({ id: finding.id, status: "dismissed" })}>忽略</Button>
                  </div>
                </div>
              ))}
              {(findings ?? []).length === 0 && <div className="py-8 text-center text-sm text-slate-400">没有待处理发现项</div>}
            </div>
          </Card>
        )}
        {showLog && (
          <Card>
            <h3 className="mb-2 font-semibold">Wiki 维护日志</h3>
            <ul className="space-y-2 text-sm">
              {(log ?? []).map((l) => (
                <li key={l.id} className="flex gap-2">
                  <span className="shrink-0 text-xs text-slate-400">
                    {new Date(l.created_at).toLocaleString("zh-CN")}
                  </span>
                  <span className="rounded bg-slate-100 px-1 text-xs text-slate-500">{l.op}</span>
                  <span className="text-slate-600">{l.entry_md}</span>
                </li>
              ))}
              {(log ?? []).length === 0 && <li className="text-slate-400">暂无日志</li>}
            </ul>
          </Card>
        )}

        <Card>
          {page ? (
            <>
              <div className="mb-4 flex items-start justify-between border-b border-slate-100 pb-3">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">{page.title}</h2>
                  <p className="mt-0.5 text-xs text-slate-400">
                    {kindLabels[page.kind]} · v{page.version} · 更新于{" "}
                    {new Date(page.updated_at).toLocaleString("zh-CN")}
                  </p>
                </div>
                <button
                  onClick={() => setShowRevisions(!showRevisions)}
                  className="flex items-center gap-1 text-sm text-slate-400 hover:text-indigo-600"
                >
                  <History className="h-4 w-4" />
                  历史
                </button>
              </div>
              {showRevisions && (
                <div className="mb-4 rounded-lg bg-slate-50 p-3 text-sm">
                  <div className="mb-1 font-medium text-slate-700">修订历史</div>
                  <ul className="space-y-1">
                    {(revisions ?? []).map((r) => (
                      <li key={r.id} className="text-slate-500">
                        v{r.version} · {new Date(r.created_at).toLocaleString("zh-CN")} ·{" "}
                        {r.change_summary || "（无说明）"}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <Markdown content={page.content_md} onWikiLink={goTo} />
            </>
          ) : (
            <div className="p-8 text-center text-sm text-slate-400">{pageFailed ? "暂时无法加载此页，请从左侧选择已有页面，或检查资料是否已完成入库。" : "加载中…"}</div>
          )}
        </Card>
      </div>
      {toast && <Toast key={toast} message={toast} tone="info" onClose={() => setToast("")} />}
    </div>
  );
}
