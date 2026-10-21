import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { NavLink, Outlet, useParams } from "react-router-dom";

import { AppShell, StatusBadge } from "../../components/AppShell";
import { api } from "../../lib/api";
import type { CourseKind, CourseOut } from "../../lib/types";

const courseKindLabels: Record<CourseKind, string> = {
  knowledge: "知识学习",
  language: "语言学习",
  programming: "编程学习",
};

const tabs = [
  { to: "overview", label: "总览" },
  { to: "plan", label: "计划" },
  { to: "vocab", label: "词汇", languageOnly: true },
  { to: "ability", label: "能力", languageOnly: true },
  { to: "reading", label: "阅读", languageOnly: true },
  { to: "writing", label: "写作", languageOnly: true },
  { to: "graph", label: "图谱" },
  { to: "qa", label: "答疑" },
  { to: "kb", label: "知识库" },
  { to: "wiki", label: "Wiki" },
];

export default function CoursePage() {
  const { courseId } = useParams();
  const { data: course } = useQuery<CourseOut>({
    queryKey: ["course", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}`),
  });

  return (
    <AppShell>
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold text-slate-900">{course?.name ?? "…"}</h1>
        {course && <StatusBadge status={course.status} />}
        {course && course.kind !== "knowledge" && (
          <span className="rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-600">
            {courseKindLabels[course.kind]}
          </span>
        )}
      </div>
      {course?.goal_text && <p className="mt-1 text-sm text-slate-500">目标：{course.goal_text}</p>}

      <nav className="mt-6 flex gap-1 overflow-x-auto border-b border-slate-200">
        {tabs.filter((tab) => !tab.languageOnly || course?.kind === "language").map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              clsx(
                "shrink-0 border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "border-indigo-600 text-indigo-600"
                  : "border-transparent text-slate-500 hover:text-slate-800",
              )
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>

      <div className="py-6">
        <Outlet context={{ course }} />
      </div>
    </AppShell>
  );
}
