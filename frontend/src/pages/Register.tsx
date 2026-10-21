import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Alert, Button, Card, Input, Label } from "../components/ui";
import { api, ApiError, type UserOut } from "../lib/api";
import { useSetMe } from "../lib/auth";
import { BrandMark } from "../components/BrandMark";

export default function Register() {
  const navigate = useNavigate();
  const setMe = useSetMe();
  const [form, setForm] = useState({ username: "", nickname: "", password: "", invite_code: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function update(key: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (form.password.length < 8) {
      setError("密码至少需要 8 位");
      return;
    }
    setLoading(true);
    try {
      const user = await api.post<UserOut>("/api/v1/auth/register", form);
      setMe(user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "网络异常，请稍后再试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm">
        <BrandMark />
        <Card>
          <h1 className="mb-1 text-lg font-semibold">创建账号</h1>
          <p className="mb-5 text-sm text-slate-500">需要邀请码才能注册</p>
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <Label htmlFor="invite_code">邀请码</Label>
              <Input id="invite_code" value={form.invite_code} onChange={update("invite_code")} required />
            </div>
            <div>
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                value={form.username}
                onChange={update("username")}
                placeholder="3-32 位字母、数字或下划线"
                pattern="[A-Za-z0-9_]{3,32}"
                autoComplete="username"
                required
              />
            </div>
            <div>
              <Label htmlFor="nickname">昵称</Label>
              <Input id="nickname" value={form.nickname} onChange={update("nickname")} required />
            </div>
            <div>
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={form.password}
                onChange={update("password")}
                placeholder="至少 8 位"
                autoComplete="new-password"
                required
              />
            </div>
            {error && <Alert>{error}</Alert>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "注册中…" : "注册"}
            </Button>
          </form>
        </Card>
        <p className="mt-4 text-center text-sm text-slate-500">
          已有账号？
          <Link to="/login" className="font-medium text-indigo-600 hover:underline">
            直接登录
          </Link>
        </p>
      </div>
    </div>
  );
}
