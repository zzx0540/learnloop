import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronDown, Gift, History, WalletCards } from "lucide-react";
import { useState } from "react";

import { AppShell } from "../components/AppShell";
import { Toast } from "../components/Toast";
import { Button, Card } from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { PointsSummaryOut, RedemptionOut, RewardOut } from "../lib/types";

export default function RewardsPage() {
  const queryClient = useQueryClient();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [pendingReward, setPendingReward] = useState<RewardOut | null>(null);
  const { data: points } = useQuery<PointsSummaryOut>({
    queryKey: ["points-summary"],
    queryFn: () => api.get("/api/v1/me/points"),
  });
  const { data: rewards } = useQuery<RewardOut[]>({
    queryKey: ["rewards"],
    queryFn: () => api.get("/api/v1/rewards"),
  });
  const { data: redemptions } = useQuery<RedemptionOut[]>({
    queryKey: ["redemptions"],
    queryFn: () => api.get("/api/v1/me/redemptions"),
    enabled: historyOpen,
  });
  const redeem = useMutation({
    mutationFn: (reward: RewardOut) => api.post<RedemptionOut>(`/api/v1/rewards/${reward.id}/redeem`),
    onSuccess: async (_, reward) => {
      setToast(`已兑换「${reward.title}」`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["points-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["points-ledger"] }),
        queryClient.invalidateQueries({ queryKey: ["redemptions"] }),
      ]);
    },
    onError: (error) => setToast(error instanceof ApiError ? error.message : "兑换失败，请重试"),
  });

  return (
    <AppShell>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">积分商城</h1>
          <p className="mt-1 text-sm text-slate-500">把坚持学习积累的积分，换成给自己的小奖励。</p>
        </div>
        <div className="flex items-center gap-3 rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3">
          <WalletCards className="h-5 w-5 text-indigo-600" />
          <div>
            <div className="text-xs text-indigo-500">当前余额</div>
            <div className="text-2xl font-bold text-indigo-950">{points?.balance ?? 0} <span className="text-sm font-medium text-indigo-500">积分</span></div>
          </div>
        </div>
      </div>

      {(rewards ?? []).length > 0 ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(rewards ?? []).map((reward) => {
            const balance = points?.balance ?? 0;
            const shortfall = Math.max(0, reward.cost - balance);
            const pending = redeem.isPending && redeem.variables?.id === reward.id;
            return (
              <Card key={reward.id} className="flex min-h-56 flex-col">
                <div className="flex items-start justify-between gap-3">
                  <div className="rounded-xl bg-amber-50 p-2.5 text-amber-600"><Gift className="h-6 w-6" /></div>
                  <div className="rounded-full bg-indigo-50 px-3 py-1 text-sm font-bold text-indigo-600">{reward.cost} 积分</div>
                </div>
                <h2 className="mt-4 text-lg font-semibold text-slate-900">{reward.title}</h2>
                <p className="mt-2 flex-1 text-sm leading-6 text-slate-500">{reward.description || "给认真学习的自己一份奖励。"}</p>
                <Button className="mt-5 w-full" disabled={shortfall > 0 || pending} onClick={() => setPendingReward(reward)}>
                  {pending ? "兑换中…" : shortfall > 0 ? `还差 ${shortfall} 积分` : "兑换"}
                </Button>
              </Card>
            );
          })}
        </div>
      ) : (
        <Card className="mt-6 py-16 text-center">
          <Gift className="mx-auto h-10 w-10 text-slate-300" />
          <div className="mt-3 font-medium text-slate-600">管理员还没有上架奖励</div>
          <div className="mt-1 text-sm text-slate-400">先去完成今天的学习任务吧。</div>
        </Card>
      )}

      <Card className="mt-6 p-0">
        <button type="button" onClick={() => setHistoryOpen((value) => !value)} className="flex w-full items-center justify-between px-4 py-3 text-left">
          <span className="flex items-center gap-2 font-semibold text-slate-800"><History className="h-5 w-5 text-indigo-500" />我的兑换</span>
          <ChevronDown className={clsx("h-4 w-4 text-slate-400 transition-transform", historyOpen && "rotate-180")} />
        </button>
        {historyOpen && (
          <div className="divide-y divide-slate-100 border-t border-slate-100">
            {(redemptions ?? []).map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-4 px-4 py-3 text-sm">
                <div className="min-w-0">
                  <div className="truncate font-medium text-slate-700">{item.title_snapshot}</div>
                  <div className="mt-0.5 text-xs text-slate-400">{new Date(item.created_at).toLocaleString("zh-CN")}</div>
                </div>
                <div className="shrink-0 font-semibold text-red-600">-{item.cost_snapshot}</div>
              </div>
            ))}
            {(redemptions ?? []).length === 0 && <div className="px-4 py-8 text-center text-sm text-slate-400">还没有兑换记录</div>}
          </div>
        )}
      </Card>

      {pendingReward && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-labelledby="redeem-confirm-title">
          <Card className="w-full max-w-md">
            <h2 id="redeem-confirm-title" className="text-lg font-bold text-slate-900">确认兑换</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              确认花费 <strong className="text-indigo-600">{pendingReward.cost} 积分</strong>兑换「{pendingReward.title}」吗？
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setPendingReward(null)}>取消</Button>
              <Button onClick={() => { const reward = pendingReward; setPendingReward(null); redeem.mutate(reward); }}>确认兑换</Button>
            </div>
          </Card>
        </div>
      )}

      {toast && <Toast key={toast} message={toast} tone="info" onClose={() => setToast("")} />}
    </AppShell>
  );
}
