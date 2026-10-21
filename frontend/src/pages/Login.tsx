import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Alert, Button, Card, Input, Label } from "../components/ui";
import { api, ApiError, type UserOut } from "../lib/api";
import { useSetMe } from "../lib/auth";
import { DemoNotice } from "../components/DemoNotice";
import { BrandMark } from "../components/BrandMark";

export default function Login() {
  const navigate = useNavigate();
  const setMe = useSetMe();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const user = await api.post<UserOut>("/api/v1/auth/login", { username, password });
      setMe(user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "网络异常，请稍后再试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <BrandMark />
        <DemoNotice login />
        <Card>
          <h1 className="mb-1 text-lg font-semibold">欢迎回来</h1>
          <p className="mb-5 text-sm text-slate-500">登录你的学习空间</p>
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div>
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            {error && <Alert>{error}</Alert>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "登录中…" : "登录"}
            </Button>
          </form>
        </Card>
        <p className="mt-4 text-center text-sm text-slate-500">
          还没有账号？
          <Link to="/register" className="font-medium text-indigo-600 hover:underline">
            使用邀请码注册
          </Link>
        </p>
      </div>
    </div>
  );
}
