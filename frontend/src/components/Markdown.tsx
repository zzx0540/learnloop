import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

export function Markdown({
  content,
  onWikiLink,
}: {
  content: string;
  onWikiLink?: (slug: string) => void;
}) {
  return (
    <div className="prose-learnloop">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={(url) => (url.startsWith("wiki:") ? url : defaultUrlTransform(url))}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("wiki:")) {
              const slug = href.slice(5);
              return (
                <a
                  href={`#${slug}`}
                  className="rounded bg-indigo-50 px-1 py-0.5 text-indigo-700 no-underline hover:bg-indigo-100"
                  onClick={(e) => {
                    e.preventDefault();
                    onWikiLink?.(slug);
                  }}
                >
                  {children}
                </a>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                {children}
              </a>
            );
          },
          img: ({ src, alt }) => (
            <img src={src} alt={alt ?? ""} className="my-2 max-h-96 rounded-lg border border-slate-200" />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
