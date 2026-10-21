import { Gift, LogOut, Settings, Store } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../lib/api";
import { useMe, useSetMe } from "../lib/auth";
import { Button } from "./ui";

export function AppShell({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const setMe = useSetMe();
  const navigate = useNavigate();

  async function onLogout() {
    await api.post("/api/v1/auth/logout");
    setMe(null);
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between px-4">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span className="font-bold text-slate-900">学习环</span>
          </Link>
          <div className="flex min-w-0 shrink-0 items-center gap-0.5 sm:gap-2">
            <Link to="/market">
              <Button variant="ghost" className="h-8 px-2 text-sm" aria-label="课程市场" title="课程市场">
                <Store className="h-4 w-4" />
                <span className="hidden sm:inline">课程市场</span>
              </Button>
            </Link>
            <Link to="/rewards">
              <Button variant="ghost" className="h-8 px-2 text-sm" aria-label="积分商城" title="积分商城">
                <Gift className="h-4 w-4" />
                <span className="hidden sm:inline">积分商城</span>
              </Button>
            </Link>
            {me?.role === "admin" && (
              <Link to="/admin/costs">
                <Button variant="ghost" className="h-8 px-2 text-sm" aria-label="管理" title="管理">
                  <Settings className="h-4 w-4" />
                  <span className="hidden sm:inline">管理</span>
                </Button>
              </Link>
            )}
            <span className="hidden max-w-24 truncate text-sm text-slate-600 md:inline">{me?.nickname}</span>
            <Button variant="ghost" onClick={onLogout} className="h-8 px-2 text-sm" aria-label="退出" title="退出">
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">退出</span>
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full min-w-0 max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    uploaded: { label: "待解析", cls: "bg-slate-100 text-slate-600" },
    parsing: { label: "解析中", cls: "bg-blue-50 text-blue-600" },
    parsed: { label: "待入库", cls: "bg-amber-50 text-amber-600" },
    ingesting: { label: "入库中", cls: "bg-blue-50 text-blue-600" },
    ingested: { label: "已入库", cls: "bg-emerald-50 text-emerald-600" },
    failed: { label: "失败", cls: "bg-red-50 text-red-600" },
    queued: { label: "排队中", cls: "bg-slate-100 text-slate-600" },
    running: { label: "进行中", cls: "bg-blue-50 text-blue-600" },
    succeeded: { label: "完成", cls: "bg-emerald-50 text-emerald-600" },
    onboarding: { label: "待规划", cls: "bg-amber-50 text-amber-600" },
    active: { label: "进行中", cls: "bg-emerald-50 text-emerald-600" },
    completed: { label: "已完成", cls: "bg-slate-100 text-slate-600" },
    archived: { label: "已归档", cls: "bg-slate-100 text-slate-400" },
    draft: { label: "草稿", cls: "bg-slate-100 text-slate-600" },
    building: { label: "发布中", cls: "bg-blue-50 text-blue-600" },
    ready: { label: "已就绪", cls: "bg-emerald-50 text-emerald-600" },
    published: { label: "已发布", cls: "bg-emerald-50 text-emerald-600" },
  };
  const it = map[status] ?? { label: status, cls: "bg-slate-100 text-slate-600" };
  return <span className={`rounded-full px-2 py-0.5 text-xs ${it.cls}`}>{it.label}</span>;
}
