import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function DemoNotice({ login = false }: { login?: boolean }) {
  const { data } = useQuery<{ demo_mode: boolean }>({ queryKey: ["public-info"], queryFn: () => api.get("/api/info"), staleTime: Infinity });
  if (!data?.demo_mode) return null;
  return <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950" role="note">
    <strong>预置只读演示 · 无 API 费用</strong>
    <p>课程、计划与学习记录均为人工编写的虚构示例。AI、上传和保存已关闭。</p>
    {login && <p>体验账号：<code>demo</code> · 密码：<code>learnloop-demo-only</code></p>}
  </div>;
}
