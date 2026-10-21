import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { api, type UserOut } from "./api";

export function useMe() {
  return useQuery<UserOut>({
    queryKey: ["me"],
    queryFn: () => api.get<UserOut>("/api/v1/auth/me"),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export function useSetMe() {
  const queryClient = useQueryClient();
  return (user: UserOut | null) => {
    queryClient.clear();
    if (user) {
      queryClient.setQueryData(["me"], user);
    }
  };
}

export function AuthGuard({ children }: { children: ReactNode }) {
  const { isPending, isError } = useMe();
  if (isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">加载中…</div>
    );
  }
  if (isError) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
