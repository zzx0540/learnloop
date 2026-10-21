import { ArrowLeft, Printer } from "lucide-react";
import { useCallback, useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Button } from "../ui";

export function formatPrintDate(value: string): string {
  return new Date(`${value}T00:00:00`).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function PrintLayout({
  courseName,
  templateName,
  date,
  backTo,
  canPrint = true,
  headerExtra,
  children,
}: {
  courseName: string;
  templateName: string;
  date: string;
  backTo: string;
  canPrint?: boolean;
  headerExtra?: ReactNode;
  children: ReactNode;
}) {
  const fitMath = useCallback(() => {
    document.querySelectorAll<HTMLElement>(".print-root .katex").forEach((formula) => {
      formula.style.zoom = "";
      const container = formula.closest<HTMLElement>(".print-markdown");
      if (!container) return;
      const available = container.clientWidth;
      const required = formula.scrollWidth;
      if (available > 0 && required > available) {
        formula.style.zoom = String(available / required);
      }
    });
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(fitMath);
    const onBeforePrint = () => fitMath();
    const root = document.querySelector(".print-root");
    const observer = new MutationObserver(() => window.requestAnimationFrame(fitMath));
    if (root) observer.observe(root, { childList: true, subtree: true });
    window.addEventListener("resize", fitMath);
    window.addEventListener("beforeprint", onBeforePrint);
    void document.fonts?.ready.then(fitMath);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", fitMath);
      window.removeEventListener("beforeprint", onBeforePrint);
    };
  }, [fitMath]);

  function printPage() {
    fitMath();
    window.requestAnimationFrame(() => window.print());
  }

  return (
    <div className="print-screen min-h-screen min-w-0 bg-slate-100">
      <div className="print-toolbar" data-print-hide>
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 px-4 py-3">
          <Link to={backTo} className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-indigo-600">
            <ArrowLeft className="h-4 w-4" />返回
          </Link>
          {canPrint && (
            <Button onClick={printPage}>
              <Printer className="h-4 w-4" />打印
            </Button>
          )}
        </div>
      </div>
      <div className="print-preview-scroll min-w-0 overflow-x-auto px-3 py-6 sm:px-6">
        <main className="print-root print-sheet">
          <header className="print-page-header">
            <div className="print-page-header-main">
              <div>
                <div className="print-course-name">{courseName || "学习环课程"}</div>
                <h1>{templateName}</h1>
              </div>
              <div className="print-header-date">{formatPrintDate(date)}</div>
            </div>
            <div className="print-header-fields">
              <span>姓名：________________</span>
              <span>日期：{formatPrintDate(date)}</span>
            </div>
            {headerExtra}
          </header>
          {children}
        </main>
      </div>
    </div>
  );
}
