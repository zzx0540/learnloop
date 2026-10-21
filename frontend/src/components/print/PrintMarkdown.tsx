import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

export function PrintMarkdown({ content }: { content: string }) {
  return (
    <div className="print-markdown prose-learnloop">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={(url) => (url.startsWith("wiki:") ? url : defaultUrlTransform(url))}
        components={{
          a: ({ children }) => <span className="print-static-link">{children}</span>,
          img: ({ src, alt }) => <img src={src} alt={alt ?? ""} className="print-content-image" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
