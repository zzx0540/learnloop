import { CheckCircle2, Info, X } from "lucide-react";
import { useEffect } from "react";

export function Toast({
  message,
  onClose,
  tone = "success",
}: {
  message: string;
  onClose: () => void;
  tone?: "success" | "info";
}) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 6000);
    return () => window.clearTimeout(timer);
  }, [message, onClose]);

  const Icon = tone === "success" ? CheckCircle2 : Info;
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 items-center gap-2 rounded-xl border border-slate-200 bg-slate-900 px-4 py-3 text-sm text-white shadow-xl">
      <Icon className="h-4 w-4 shrink-0 text-emerald-400" />
      <span className="min-w-0 flex-1">{message}</span>
      <button className="rounded p-1 text-slate-300 hover:bg-white/10 hover:text-white" onClick={onClose}>
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
