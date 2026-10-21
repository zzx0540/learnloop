import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams } from "react-router-dom";

import { PrintLayout, formatPrintDate } from "../../components/print/PrintLayout";
import { api } from "../../lib/api";
import type { CourseOut, CourseOverviewOut, GraphNodeOut, GraphOut, KnowledgePointOut } from "../../lib/types";

const statusMeta: Record<string, { symbol: string; label: string }> = {
  pending: { symbol: "□", label: "未学" },
  learning: { symbol: "◐", label: "学习中" },
  basic_mastered: { symbol: "■", label: "基本" },
  fully_mastered: { symbol: "●", label: "完全" },
  needs_reinforce: { symbol: "▲", label: "需巩固" },
  archived: { symbol: "□", label: "未学" },
};

function todayKey(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${today.getFullYear()}-${month}-${day}`;
}

function topologicalLayers(graph: GraphOut): GraphNodeOut[][] {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const indegree = new Map(graph.nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(graph.nodes.map((node) => [node.id, [] as string[]]));
  graph.edges.filter((edge) => edge.relation === "prerequisite").forEach((edge) => {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) return;
    outgoing.get(edge.source)!.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  });
  let current = graph.nodes.filter((node) => indegree.get(node.id) === 0).sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  const visited = new Set<string>();
  const layers: GraphNodeOut[][] = [];
  while (current.length > 0) {
    layers.push(current);
    const next: GraphNodeOut[] = [];
    current.forEach((node) => {
      visited.add(node.id);
      outgoing.get(node.id)?.forEach((target) => {
        const remaining = (indegree.get(target) ?? 0) - 1;
        indegree.set(target, remaining);
        if (remaining === 0) next.push(nodes.get(target)!);
      });
    });
    current = next.sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  }
  const cyclic = graph.nodes.filter((node) => !visited.has(node.id));
  if (cyclic.length > 0) layers.push(cyclic.sort((a, b) => a.title.localeCompare(b.title, "zh-CN")));
  return layers;
}

export default function OutlinePrintPage() {
  const { courseId } = useParams();
  const date = todayKey();
  const courseQuery = useQuery<CourseOut>({
    queryKey: ["course", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}`),
    enabled: Boolean(courseId),
  });
  const graphQuery = useQuery<GraphOut>({
    queryKey: ["graph", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/graph`),
    enabled: Boolean(courseId),
  });
  const kpsQuery = useQuery<KnowledgePointOut[]>({
    queryKey: ["kps", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/kps`),
    enabled: Boolean(courseId),
  });
  const overviewQuery = useQuery<CourseOverviewOut>({
    queryKey: ["overview", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/overview`),
    enabled: Boolean(courseId),
  });
  const layers = useMemo(() => graphQuery.data ? topologicalLayers(graphQuery.data) : [], [graphQuery.data]);
  const kpById = new Map((kpsQuery.data ?? []).map((kp) => [kp.id, kp]));
  const loading = courseQuery.isPending || graphQuery.isPending || kpsQuery.isPending || overviewQuery.isPending;
  const failed = courseQuery.isError || graphQuery.isError || kpsQuery.isError || overviewQuery.isError;
  const canPrint = !loading && !failed && layers.length > 0;
  const stats = overviewQuery.data?.kp_stats.by_status ?? {};

  return (
    <PrintLayout
      courseName={courseQuery.data?.name ?? "加载中…"}
      templateName="知识框架大纲"
      date={date}
      backTo={`/courses/${courseId}/graph`}
      canPrint={canPrint}
    >
      {loading ? (
        <div className="print-empty-state">正在整理知识框架…</div>
      ) : failed ? (
        <div className="print-empty-state">知识框架加载失败，请返回后重试。</div>
      ) : layers.length === 0 ? (
        <div className="print-empty-state">课程尚无知识点，无法生成大纲。</div>
      ) : (
        <>
          {layers.map((layer, layerIndex) => (
            <section key={layerIndex} className="print-outline-layer">
              <h2 className="print-outline-layer-title">
                {layerIndex + 1}. {layerIndex === 0 ? "基础概念层" : `进阶关系层 ${layerIndex}`}
              </h2>
              {layer.map((node, itemIndex) => {
                const kp = kpById.get(node.id);
                const status = statusMeta[kp?.status ?? node.status] ?? statusMeta.pending;
                const importance = kp?.importance ?? node.importance;
                return (
                  <div key={node.id} className="print-outline-item">
                    <div className="print-outline-number">{layerIndex + 1}.{itemIndex + 1}</div>
                    <div>
                      <div className="print-outline-title">{node.title}{importance >= 4 ? " ★" : ""}</div>
                      <div className="print-outline-summary">{node.summary || "暂无摘要"}</div>
                    </div>
                    <div className="print-outline-status">{status.symbol} {status.label}</div>
                  </div>
                );
              })}
            </section>
          ))}
          <footer className="print-outline-footer">
            <div className="print-outline-legend">
              {Object.entries(statusMeta).filter(([key]) => key !== "archived").map(([key, item]) => (
                <span key={key}>{item.symbol} {item.label}</span>
              ))}
              <span>★ 重要度 ≥ 4</span>
            </div>
            <div className="print-outline-stats">
              <span>总计 {overviewQuery.data?.kp_stats.total ?? kpsQuery.data?.length ?? 0}</span>
              <span>未学 {stats.pending ?? 0}</span>
              <span>学习中 {stats.learning ?? 0}</span>
              <span>基本掌握 {stats.basic_mastered ?? 0}</span>
              <span>完全掌握 {stats.fully_mastered ?? 0}</span>
              <span>需巩固 {stats.needs_reinforce ?? 0}</span>
            </div>
            <div className="print-outline-stats">
              <span>预计基本掌握：{overviewQuery.data?.projected_basic_date ? formatPrintDate(overviewQuery.data.projected_basic_date) : "待生成"}</span>
              <span>预计完全掌握：{overviewQuery.data?.projected_full_date ? formatPrintDate(overviewQuery.data.projected_full_date) : "待生成"}</span>
            </div>
          </footer>
        </>
      )}
    </PrintLayout>
  );
}
