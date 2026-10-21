export function LegalNotice() {
  return (
    <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-4 gap-y-2 border-t border-slate-200 px-4 py-5 text-xs text-slate-500 print:hidden">
      <span>© 2026 zzx0540 · 不提供担保</span>
      <a href="/api/legal/license" className="underline">AGPL-3.0-only</a>
      <a href="/api/legal/notices" className="underline">第三方声明</a>
      <a href="/api/legal/source" className="underline">本版本源码（ZIP）</a>
      <a href="https://github.com/zzx0540/learnloop" target="_blank" rel="noreferrer" className="underline">GitHub</a>
    </footer>
  );
}
