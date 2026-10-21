import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { ExternalLink, Printer, Star, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { Button, Card } from "../../components/ui";
import { api } from "../../lib/api";
import type { GraphNodeOut, GraphOut, KnowledgePointOut } from "../../lib/types";

const nodeWidth = 190;
const nodeHeight = 76;
const statusStyle: Record<string, { label: string; card: string; dot: string; mini: string }> = {
  pending: { label: "未开始", card: "border-slate-300 bg-white", dot: "bg-slate-400", mini: "#94a3b8" },
  learning: { label: "学习中", card: "border-blue-300 bg-blue-50", dot: "bg-blue-500", mini: "#3b82f6" },
  basic_mastered: {
    label: "基本掌握",
    card: "border-emerald-300 bg-emerald-50",
    dot: "bg-emerald-400",
    mini: "#34d399",
  },
  fully_mastered: {
    label: "完全掌握",
    card: "border-emerald-600 bg-emerald-100",
    dot: "bg-emerald-700",
    mini: "#047857",
  },
  needs_reinforce: {
    label: "需巩固",
    card: "border-amber-300 bg-amber-50",
    dot: "bg-amber-500",
    mini: "#f59e0b",
  },
  archived: { label: "已归档", card: "border-slate-200 bg-slate-100", dot: "bg-slate-300", mini: "#cbd5e1" },
};

function dateLabel(value: string | null): string {
  if (!value) return "待排期";
  return new Date(`${value}T00:00:00`).toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
}

function layoutGraph(graph: GraphOut): { nodes: Node[]; edges: Edge[] } {
  const layout = new dagre.graphlib.Graph();
  layout.setDefaultEdgeLabel(() => ({}));
  layout.setGraph({ rankdir: "TB", ranksep: 70, nodesep: 40, marginx: 20, marginy: 20 });
  graph.nodes.forEach((node) => layout.setNode(node.id, { width: nodeWidth, height: nodeHeight }));
  graph.edges
    .filter((edge) => edge.relation === "prerequisite")
    .forEach((edge) => layout.setEdge(edge.source, edge.target));
  dagre.layout(layout);

  const nodes: Node[] = graph.nodes.map((kp) => {
    const point = layout.node(kp.id);
    const style = statusStyle[kp.status] ?? statusStyle.pending;
    return {
      id: kp.id,
      position: { x: point.x - nodeWidth / 2, y: point.y - nodeHeight / 2 },
      data: {
        kp,
        label: (
          <div className={`h-full rounded-lg border px-3 py-2 shadow-sm ${style.card}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="line-clamp-2 text-left text-sm font-semibold text-slate-800">{kp.title}</div>
              {kp.importance >= 4 && <Star className="h-3.5 w-3.5 shrink-0 fill-amber-400 text-amber-400" />}
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500">
              <span className={`h-2 w-2 rounded-full ${style.dot}`} />
              {style.label}
            </div>
          </div>
        ),
      },
      style: { width: nodeWidth, height: nodeHeight, padding: 0, border: 0, background: "transparent" },
    };
  });
  const edges: Edge[] = graph.edges.map((edge) => {
    const prerequisite = edge.relation === "prerequisite";
    const color = prerequisite ? "#6366f1" : edge.relation === "related" ? "#94a3b8" : "#a78bfa";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: {
        stroke: color,
        strokeWidth: prerequisite ? 2 : 1.4,
        strokeDasharray: edge.relation === "related" ? "7 5" : edge.relation === "part_of" ? "2 4" : undefined,
      },
    };
  });
  return { nodes, edges };
}

export default function GraphTab() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<GraphNodeOut | null>(null);
  const { data: graph } = useQuery<GraphOut>({
    queryKey: ["graph", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/graph`),
  });
  const importance = useMutation({
    mutationFn: (value: number) => api.patch<KnowledgePointOut>(`/api/v1/kps/${selected!.id}`, { importance: value }),
    onSuccess: (updated) => {
      setSelected((current) => (current ? { ...current, importance: updated.importance } : current));
      queryClient.invalidateQueries({ queryKey: ["graph", courseId] });
      queryClient.invalidateQueries({ queryKey: ["overview", courseId] });
    },
  });
  const flow = useMemo(() => (graph ? layoutGraph(graph) : { nodes: [], edges: [] }), [graph]);

  useEffect(() => {
    const slug = (location.state as { selectedKpSlug?: string } | null)?.selectedKpSlug;
    if (!graph || !slug) return;
    const match = graph.nodes.find((node) => node.slug === slug);
    if (match) setSelected(match);
    navigate(location.pathname, { replace: true, state: null });
  }, [graph, location.pathname, location.state, navigate]);

  if (!graph) return <div className="py-12 text-center text-sm text-slate-400">加载中…</div>;
  if (graph.nodes.length === 0) {
    return (
      <Card className="py-12 text-center">
        <h2 className="font-semibold text-slate-900">还没有知识点</h2>
        <p className="mt-1 text-sm text-slate-500">资料入库后会自动抽取知识点并建立关系图。</p>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="outline" onClick={() => navigate(`/courses/${courseId}/print/outline`)}>
          <Printer className="h-4 w-4" />打印知识框架
        </Button>
      </div>
      <div className="relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="h-[34rem] sm:h-[42rem]">
          <ReactFlow
            nodes={flow.nodes}
            edges={flow.edges}
            fitView
            minZoom={0.25}
            maxZoom={1.8}
            onNodeClick={(_, node) => setSelected(node.data.kp as GraphNodeOut)}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} color="#e2e8f0" />
            <Controls />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) => statusStyle[(node.data.kp as GraphNodeOut).status]?.mini ?? "#94a3b8"}
            />
          </ReactFlow>
        </div>

        <div className="absolute bottom-3 left-12 z-10 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[11px] text-slate-500 shadow-sm backdrop-blur">
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <span><i className="mr-1 inline-block h-0.5 w-5 bg-indigo-500 align-middle" />前置</span>
            <span><i className="mr-1 inline-block w-5 border-t border-dashed border-slate-400 align-middle" />相关</span>
            <span><i className="mr-1 inline-block w-5 border-t border-dotted border-violet-400 align-middle" />组成</span>
          </div>
        </div>

        {selected && (
          <div className="fixed inset-0 z-30 flex justify-end bg-slate-950/25" onClick={() => setSelected(null)}>
            <aside
              className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-2xl"
              onClick={(event) => event.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-xs text-slate-400">知识点详情</div>
                <h2 className="mt-1 text-xl font-bold text-slate-900">{selected.title}</h2>
              </div>
              <button className="rounded p-1 text-slate-400 hover:bg-slate-100" onClick={() => setSelected(null)}>
                <X className="h-5 w-5" />
              </button>
            </div>
            <p className="mt-5 text-sm leading-7 text-slate-600">{selected.summary || "暂无摘要"}</p>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-indigo-50 p-3">
                <div className="text-xs text-indigo-500">预计基本掌握</div>
                <div className="mt-1 font-semibold text-indigo-900">{dateLabel(selected.projected_basic_at)}</div>
              </div>
              <div className="rounded-lg bg-emerald-50 p-3">
                <div className="text-xs text-emerald-600">预计完全掌握</div>
                <div className="mt-1 font-semibold text-emerald-900">{dateLabel(selected.projected_full_at)}</div>
              </div>
            </div>
            <label className="mt-6 block text-sm font-medium text-slate-700" htmlFor="kp-importance">
              重要度
            </label>
            <select
              id="kp-importance"
              value={selected.importance}
              disabled={importance.isPending}
              onChange={(event) => importance.mutate(Number(event.target.value))}
              className="mt-1 h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            >
              {[1, 2, 3, 4, 5].map((value) => (
                <option key={value} value={value}>{value} - {value >= 4 ? "重点" : "一般"}</option>
              ))}
            </select>
            {selected.primary_wiki_page_id && (
              <Button className="mt-6 w-full" onClick={() => navigate(`../wiki/${selected.slug}`)}>
                <ExternalLink className="h-4 w-4" />
                打开主 Wiki 页
              </Button>
            )}
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
