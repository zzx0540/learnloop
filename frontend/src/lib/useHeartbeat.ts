import { useEffect, useState } from "react";

import { api } from "./api";

export function useHeartbeat(courseId: string | undefined, kind: "learn" | "review" | "qa", active: boolean) {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (!courseId || !active || !visible) return;
    const beat = () => void api.post("/api/v1/stats/heartbeat", { course_id: courseId, kind });
    beat();
    const timer = window.setInterval(beat, 60_000);
    return () => window.clearInterval(timer);
  }, [active, courseId, kind, visible]);
}
