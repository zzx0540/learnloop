import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { BarChart3, Gift, KeyRound, Settings, ShieldCheck, Store, Users } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "../components/AppShell";
import { Toast } from "../components/Toast";
import { Alert, Button, Card, Input, Label } from "../components/ui";
import { api, ApiError } from "../lib/api";
import type {
  AdminSettingsOut,
  AdminUserOut,
  InviteOut,
  LlmCostItem,
  RewardOut,
} from "../lib/types";
import { AdminMarketPanel } from "./AdminMarketPanel";

type AdminTab = "costs" | "market" | "rewards" | "users" | "invites" | "settings";

const tabs: { id: AdminTab; label: string; icon: typeof BarChart3 }[] = [
  { id: "costs", label: "成本", icon: BarChart3 },
  { id: "market", label: "课程市场", icon: Store },
  { id: "rewards", label: "奖励", icon: Gift },
  { id: "users", label: "用户", icon: Users },
  { id: "invites", label: "邀请码", icon: KeyRound },
  { id: "settings", label: "设置", icon: Settings },
];

function shanghaiDateKey(date: Date): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function CostPanel() {
  const { data } = useQuery<{ items: LlmCostItem[]; total_cost_usd: number | null; known_cost_usd: number; unpriced_calls: number }>({
    queryKey: ["llm-costs"],
    queryFn: () => api.get("/api/v1/admin/llm-costs?days=14"),
  });
  const daily = useMemo(() => {
    const sums = new Map<string, number>();
    for (const item of data?.items ?? []) sums.set(item.day, (sums.get(item.day) ?? 0) + item.known_cost_usd);
    return Array.from({ length: 14 }, (_, index) => {
      const date = new Date();
      date.setDate(date.getDate() - (13 - index));
      const day = shanghaiDateKey(date);
      return { day, cost: sums.get(day) ?? 0 };
    });
  }, [data]);
  const [activeDay, setActiveDay] = useState<{ day: string; cost: number } | null>(null);
  const maxCost = Math.max(...daily.map((item) => item.cost), 0.0001);

  return (
    <div>
      <h2 className="text-lg font-bold text-slate-900">LLM 成本看板</h2>
      <p className="mt-1 text-sm text-slate-500">近 14 天，已知价格部分估算 ${data?.known_cost_usd?.toFixed(4) ?? "—"}。{data?.unpriced_calls ? `另有 ${data.unpriced_calls} 次调用未配置价格，总费用未知。` : "最终费用以上游账单为准。"}</p>

      <Card className="mt-5 min-w-0 p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div className="font-semibold text-slate-800">每日已知价格部分</div>
          <div className="min-h-5 text-sm text-indigo-600" aria-live="polite">
            {activeDay ? `${activeDay.day} · $${activeDay.cost.toFixed(4)}` : "悬停柱形查看数值"}
          </div>
        </div>
        <div className="mt-3 max-w-full overflow-x-auto">
          <svg viewBox="0 0 700 220" role="img" aria-label="近 14 天每日 LLM 成本柱状图" className="h-auto w-full min-w-[560px]">
            <line x1="12" y1="170" x2="688" y2="170" stroke="#cbd5e1" strokeWidth="1" />
            {daily.map((item, index) => {
              const height = item.cost === 0 ? 2 : Math.max(5, (item.cost / maxCost) * 130);
              const x = 19 + index * 48;
              return (
                <g key={item.day} onMouseEnter={() => setActiveDay(item)} onFocus={() => setActiveDay(item)} onClick={() => setActiveDay(item)} tabIndex={0}>
                  <title>{item.day}：${item.cost.toFixed(4)}</title>
                  <rect x={x} y={170 - height} width="30" height={height} rx="5" fill="#6366f1" className="cursor-pointer opacity-80 transition-opacity hover:opacity-100" />
                  <text x={x + 15} y="191" textAnchor="middle" fontSize="10" fill="#64748b">{item.day.slice(5)}</text>
                </g>
              );
            })}
          </svg>
        </div>
      </Card>

      <Card className="mt-5 overflow-x-auto p-0">
        <table className="w-full min-w-[820px] text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500">
              <th className="px-3 py-2">日期</th><th className="px-3 py-2">模型</th><th className="px-3 py-2">Agent</th>
              <th className="px-3 py-2 text-right">调用</th><th className="px-3 py-2 text-right">输入 tokens</th>
              <th className="px-3 py-2 text-right">缓存命中</th><th className="px-3 py-2 text-right">输出 tokens</th>
              <th className="px-3 py-2 text-right">成本 $</th><th className="px-3 py-2 text-right">错误</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((item, index) => (
              <tr key={`${item.day}-${item.model}-${item.agent}-${index}`} className="border-b border-slate-100">
                <td className="px-3 py-2">{item.day}</td><td className="px-3 py-2">{item.model}</td><td className="px-3 py-2">{item.agent}</td>
                <td className="px-3 py-2 text-right">{item.calls}</td><td className="px-3 py-2 text-right">{item.prompt_tokens.toLocaleString()}</td>
                <td className="px-3 py-2 text-right">{(item.cache_hit_ratio * 100).toFixed(0)}%</td>
                <td className="px-3 py-2 text-right">{item.completion_tokens.toLocaleString()}</td><td className="px-3 py-2 text-right">{item.cost_usd === null ? "未知（未定价）" : item.cost_usd.toFixed(4)}</td>
                <td className="px-3 py-2 text-right">{item.errors}</td>
              </tr>
            ))}
            {(data?.items ?? []).length === 0 && <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-400">暂无调用记录</td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function RewardsPanel({ notify }: { notify: (message: string) => void }) {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [cost, setCost] = useState("10");
  const [isActive, setIsActive] = useState(true);
  const { data } = useQuery<RewardOut[]>({ queryKey: ["admin-rewards"], queryFn: () => api.get("/api/v1/admin/rewards") });
  const save = useMutation({
    mutationFn: () => {
      const body = { title: title.trim(), description: description.trim(), cost: Number(cost), is_active: isActive };
      return editingId
        ? api.patch<RewardOut>(`/api/v1/admin/rewards/${editingId}`, body)
        : api.post<RewardOut>("/api/v1/admin/rewards", body);
    },
    onSuccess: async () => {
      notify(editingId ? "奖励已更新" : "奖励已上架");
      setEditingId(null); setTitle(""); setDescription(""); setCost("10"); setIsActive(true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin-rewards"] }),
        queryClient.invalidateQueries({ queryKey: ["rewards"] }),
      ]);
    },
    onError: (error) => notify(error instanceof ApiError ? error.message : "保存失败，请重试"),
  });

  function edit(reward: RewardOut) {
    setEditingId(reward.id); setTitle(reward.title); setDescription(reward.description); setCost(String(reward.cost)); setIsActive(reward.is_active);
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[0.9fr_1.4fr]">
      <Card>
        <h2 className="font-bold text-slate-900">{editingId ? "编辑奖励" : "新建奖励"}</h2>
        <div className="mt-4 space-y-3">
          <div><Label htmlFor="reward-title">标题</Label><Input id="reward-title" value={title} maxLength={128} onChange={(event) => setTitle(event.target.value)} /></div>
          <div><Label htmlFor="reward-description">描述</Label><textarea id="reward-description" value={description} maxLength={500} onChange={(event) => setDescription(event.target.value)} className="min-h-24 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" /></div>
          <div><Label htmlFor="reward-cost">所需积分</Label><Input id="reward-cost" type="number" min={1} value={cost} onChange={(event) => setCost(event.target.value)} /></div>
          <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} className="h-4 w-4 accent-indigo-600" />上架中</label>
          <div className="flex flex-wrap gap-2">
            <Button disabled={save.isPending || !title.trim() || Number(cost) <= 0} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : editingId ? "保存修改" : "上架奖励"}</Button>
            {editingId && <Button variant="outline" onClick={() => { setEditingId(null); setTitle(""); setDescription(""); setCost("10"); setIsActive(true); }}>取消编辑</Button>}
          </div>
        </div>
      </Card>
      <div className="space-y-3">
        {(data ?? []).map((reward) => (
          <Card key={reward.id} className="p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-slate-900">{reward.title}</h3><span className={clsx("rounded-full px-2 py-0.5 text-xs", reward.is_active ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-500")}>{reward.is_active ? "已上架" : "已下架"}</span></div><p className="mt-1 text-sm text-slate-500">{reward.description || "暂无描述"}</p><div className="mt-2 text-sm font-semibold text-indigo-600">{reward.cost} 积分</div></div>
              <Button variant="outline" className="shrink-0" onClick={() => edit(reward)}>编辑</Button>
            </div>
          </Card>
        ))}
        {(data ?? []).length === 0 && <Card className="py-12 text-center text-sm text-slate-400">还没有奖励</Card>}
      </div>
    </div>
  );
}

function UsersPanel({ notify }: { notify: (message: string) => void }) {
  const [resettingId, setResettingId] = useState<string | null>(null);
  const [resetCandidate, setResetCandidate] = useState<AdminUserOut | null>(null);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const passwordText = useRef<HTMLElement>(null);
  const { data } = useQuery<AdminUserOut[]>({ queryKey: ["admin-users"], queryFn: () => api.get("/api/v1/admin/users") });

  function clearPassword() {
    if (passwordText.current) passwordText.current.textContent = "";
  }

  function closePassword() {
    clearPassword();
    setPasswordVisible(false);
  }

  useEffect(() => clearPassword, []);

  async function resetPassword(user: AdminUserOut) {
    clearPassword();
    setResettingId(user.id);
    try {
      const response = await api.post<{ password: string }>(`/api/v1/admin/users/${user.id}/reset-password`);
      setPasswordVisible(true);
      window.requestAnimationFrame(() => {
        if (passwordText.current) passwordText.current.textContent = response.password;
      });
    } catch (error) {
      notify(error instanceof ApiError ? error.message : "密码重置失败，请重试");
    } finally {
      setResettingId(null);
    }
  }

  return (
    <>
      <Card className="overflow-x-auto p-0">
        <table className="w-full min-w-[720px] text-sm">
          <thead><tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500"><th className="px-4 py-3">用户名</th><th className="px-4 py-3">昵称</th><th className="px-4 py-3">角色</th><th className="px-4 py-3">注册时间</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
          <tbody>
            {(data ?? []).map((user) => <tr key={user.id} className="border-b border-slate-100"><td className="px-4 py-3 font-medium text-slate-700">{user.username}</td><td className="px-4 py-3">{user.nickname}</td><td className="px-4 py-3"><span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{user.role === "admin" ? "管理员" : "用户"}</span></td><td className="px-4 py-3 text-slate-500">{new Date(user.created_at).toLocaleString("zh-CN")}</td><td className="px-4 py-3 text-right"><Button variant="outline" disabled={resettingId === user.id} onClick={() => setResetCandidate(user)}><KeyRound className="h-4 w-4" />{resettingId === user.id ? "重置中…" : "重置密码"}</Button></td></tr>)}
          </tbody>
        </table>
      </Card>
      {resetCandidate && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-labelledby="reset-confirm-title">
          <Card className="w-full max-w-md">
            <h2 id="reset-confirm-title" className="text-lg font-bold text-slate-900">确认重置密码</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">将为用户「{resetCandidate.username}」生成新密码，并使其全部会话失效。</p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setResetCandidate(null)}>取消</Button>
              <Button variant="danger" onClick={() => { const user = resetCandidate; setResetCandidate(null); void resetPassword(user); }}>确认重置</Button>
            </div>
          </Card>
        </div>
      )}
      {passwordVisible && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-labelledby="password-result-title">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
          <div id="password-result-title" className="flex items-center gap-2 text-lg font-bold text-slate-900"><ShieldCheck className="h-6 w-6 text-emerald-600" />密码已重置</div>
          <Alert><strong>仅显示一次：</strong>关闭弹窗后，新密码会立即从页面清除。请现在安全地交给用户。</Alert>
          <code ref={passwordText} className="mt-4 block select-all rounded-xl bg-slate-950 px-4 py-4 text-center text-xl font-bold tracking-wider text-white" />
          <Button className="mt-5 w-full" onClick={closePassword}>我已保存，关闭并清除</Button>
          </div>
        </div>
      )}
    </>
  );
}

function InvitesPanel({ notify }: { notify: (message: string) => void }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [maxUses, setMaxUses] = useState("10");
  const [expiresDays, setExpiresDays] = useState("");
  const { data } = useQuery<InviteOut[]>({ queryKey: ["admin-invites"], queryFn: () => api.get("/api/v1/admin/invites") });
  const create = useMutation({
    mutationFn: () => api.post<{ code: string; max_uses: number }>("/api/v1/admin/invites", { code: code.trim() || null, max_uses: Number(maxUses), expires_in_days: expiresDays ? Number(expiresDays) : null }),
    onSuccess: async (result) => { notify(`邀请码已创建：${result.code}`); setCode(""); await queryClient.invalidateQueries({ queryKey: ["admin-invites"] }); },
    onError: (error) => notify(error instanceof ApiError ? error.message : "邀请码创建失败，请重试"),
  });
  return (
    <div className="grid gap-5 lg:grid-cols-[0.8fr_1.5fr]">
      <Card><h2 className="font-bold text-slate-900">新建邀请码</h2><div className="mt-4 space-y-3"><div><Label htmlFor="invite-code">指定代码（可选）</Label><Input id="invite-code" value={code} maxLength={64} onChange={(event) => setCode(event.target.value)} placeholder="留空自动生成" /></div><div><Label htmlFor="invite-max">使用上限</Label><Input id="invite-max" type="number" min={1} max={1000} value={maxUses} onChange={(event) => setMaxUses(event.target.value)} /></div><div><Label htmlFor="invite-expire">有效天数（可选）</Label><Input id="invite-expire" type="number" min={1} max={365} value={expiresDays} onChange={(event) => setExpiresDays(event.target.value)} /></div><Button disabled={create.isPending || Number(maxUses) <= 0} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : "创建邀请码"}</Button></div></Card>
      <Card className="overflow-x-auto p-0"><table className="w-full min-w-[620px] text-sm"><thead><tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500"><th className="px-4 py-3">邀请码</th><th className="px-4 py-3">已用</th><th className="px-4 py-3">上限</th><th className="px-4 py-3">过期时间</th><th className="px-4 py-3">状态</th></tr></thead><tbody>{(data ?? []).map((invite) => <tr key={invite.code} className="border-b border-slate-100"><td className="px-4 py-3 font-mono font-medium text-slate-700">{invite.code}</td><td className="px-4 py-3">{invite.used}</td><td className="px-4 py-3">{invite.max_uses}</td><td className="px-4 py-3 text-slate-500">{invite.expires_at ? new Date(invite.expires_at).toLocaleString("zh-CN") : "永不过期"}</td><td className="px-4 py-3"><span className={clsx("rounded-full px-2 py-0.5 text-xs", invite.is_active ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-500")}>{invite.is_active ? "可用" : "停用"}</span></td></tr>)}</tbody></table></Card>
    </div>
  );
}

function SettingsPanel({ notify }: { notify: (message: string) => void }) {
  const queryClient = useQueryClient();
  const [dailyBudget, setDailyBudget] = useState("");
  const [uploadMax, setUploadMax] = useState("");
  const [uploadQuota, setUploadQuota] = useState("");
  const [pricing, setPricing] = useState("{}");
  const { data } = useQuery<AdminSettingsOut>({ queryKey: ["admin-settings"], queryFn: () => api.get("/api/v1/admin/settings") });
  useEffect(() => { if (!data) return; setDailyBudget(String(data.llm_daily_token_budget_per_course)); setUploadMax(String(data.upload_max_mb)); setUploadQuota(String(data.upload_quota_mb_per_user)); setPricing(JSON.stringify(data.llm_pricing, null, 2)); }, [data]);
  const save = useMutation({
    mutationFn: (body: AdminSettingsOut) => api.patch<AdminSettingsOut>("/api/v1/admin/settings", body),
    onSuccess: async () => { notify("设置已保存并生效"); await queryClient.invalidateQueries({ queryKey: ["admin-settings"] }); },
    onError: (error) => notify(error instanceof ApiError ? error.message : "设置保存失败，请重试"),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    const values = [Number(dailyBudget), Number(uploadMax), Number(uploadQuota)];
    if (values.some((value) => !Number.isInteger(value) || value <= 0)) { notify("预算与上传限额必须是正整数"); return; }
    try {
      const parsed = JSON.parse(pricing) as AdminSettingsOut["llm_pricing"];
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      save.mutate({ llm_daily_token_budget_per_course: values[0], upload_max_mb: values[1], upload_quota_mb_per_user: values[2], llm_pricing: parsed });
    } catch { notify("模型价格必须是有效的 JSON 对象"); }
  }
  return (
    <Card className="max-w-3xl">
      <h2 className="font-bold text-slate-900">应用设置</h2><p className="mt-1 text-sm text-slate-500">这里只提交服务端白名单内的四项设置。</p>
      <form onSubmit={submit} className="mt-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-3"><div><Label htmlFor="daily-budget">每课程日 Token 预算</Label><Input id="daily-budget" type="number" min={1} value={dailyBudget} onChange={(event) => setDailyBudget(event.target.value)} /></div><div><Label htmlFor="upload-max">单文件上限（MB）</Label><Input id="upload-max" type="number" min={1} value={uploadMax} onChange={(event) => setUploadMax(event.target.value)} /></div><div><Label htmlFor="upload-quota">用户总配额（MB）</Label><Input id="upload-quota" type="number" min={1} value={uploadQuota} onChange={(event) => setUploadQuota(event.target.value)} /></div></div>
        <div><Label htmlFor="llm-pricing">模型价格（JSON）</Label><textarea id="llm-pricing" value={pricing} onChange={(event) => setPricing(event.target.value)} spellCheck={false} className="min-h-64 w-full rounded-lg border border-slate-300 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" /></div>
        <Button type="submit" disabled={save.isPending}>{save.isPending ? "保存中…" : "保存设置"}</Button>
      </form>
    </Card>
  );
}

export default function AdminCosts() {
  const [tab, setTab] = useState<AdminTab>("costs");
  const [toast, setToast] = useState("");
  return (
    <AppShell>
      <h1 className="text-2xl font-bold text-slate-900">管理端</h1>
      <p className="mt-1 text-sm text-slate-500">管理课程市场、成本、奖励、用户、邀请码与应用设置。</p>
      <div className="mt-6 max-w-full overflow-x-auto border-b border-slate-200">
        <div className="flex w-max min-w-full gap-1">
          {tabs.map((item) => { const Icon = item.icon; return <button key={item.id} type="button" onClick={() => setTab(item.id)} className={clsx("flex shrink-0 items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium", tab === item.id ? "border-indigo-600 text-indigo-600" : "border-transparent text-slate-500 hover:text-slate-800")}><Icon className="h-4 w-4" />{item.label}</button>; })}
        </div>
      </div>
      <div className="mt-6">{tab === "costs" && <CostPanel />}{tab === "market" && <AdminMarketPanel notify={setToast} />}{tab === "rewards" && <RewardsPanel notify={setToast} />}{tab === "users" && <UsersPanel notify={setToast} />}{tab === "invites" && <InvitesPanel notify={setToast} />}{tab === "settings" && <SettingsPanel notify={setToast} />}</div>
      {toast && <Toast key={toast} message={toast} tone="info" onClose={() => setToast("")} />}
    </AppShell>
  );
}
