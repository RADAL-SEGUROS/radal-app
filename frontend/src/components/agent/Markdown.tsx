/**
 * Safe Markdown for assistant answers (bold, headings, lists, GFM tables,
 * links, inline code).
 *
 * Safety: no rehype-raw plugin and `skipHtml` — raw HTML in a model answer
 * (an `<img onerror>`) is dropped, never rendered. Links always open in a new tab with
 * `rel="noopener noreferrer"`, and only http(s)/mailto/in-app URLs survive
 * (`urlTransform`). Tables sit in their own horizontal scroller so a wide
 * table never widens the bubble on mobile.
 *
 * Styling is Signal tokens only (no typography plugin): every element gets
 * its own small class so the bubble keeps the transcript's rhythm.
 */
import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

function safeUrl(url: string): string {
  const trimmed = url.trim();
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) return trimmed;
  if (trimmed.startsWith("#")) return trimmed;
  return "";
}

const components: Components = {
  p: ({ node: _node, ...props }) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  h1: ({ node: _node, ...props }) => (
    <h3 className="mb-1.5 mt-3 text-h3 text-ink first:mt-0" {...props} />
  ),
  h2: ({ node: _node, ...props }) => (
    <h3 className="mb-1.5 mt-3 text-h3 text-ink first:mt-0" {...props} />
  ),
  h3: ({ node: _node, ...props }) => (
    <h4 className="mb-1 mt-3 text-label font-semibold text-ink first:mt-0" {...props} />
  ),
  h4: ({ node: _node, ...props }) => (
    <h4 className="mb-1 mt-2.5 text-label font-semibold text-ink first:mt-0" {...props} />
  ),
  h5: ({ node: _node, ...props }) => (
    <h5 className="mb-1 mt-2 text-label font-semibold text-ink first:mt-0" {...props} />
  ),
  h6: ({ node: _node, ...props }) => (
    <h6 className="mb-1 mt-2 text-label font-semibold text-ink-2 first:mt-0" {...props} />
  ),
  strong: ({ node: _node, ...props }) => <strong className="font-semibold text-ink" {...props} />,
  em: ({ node: _node, ...props }) => <em className="italic" {...props} />,
  ul: ({ node: _node, ...props }) => (
    <ul className="my-2 list-disc space-y-1 ps-5 first:mt-0 last:mb-0 marker:text-ink-3" {...props} />
  ),
  ol: ({ node: _node, ...props }) => (
    <ol className="my-2 list-decimal space-y-1 ps-5 first:mt-0 last:mb-0 marker:text-ink-3" {...props} />
  ),
  li: ({ node: _node, ...props }) => <li className="ps-0.5" {...props} />,
  // An unsafe URL was blanked by `safeUrl`: show the text, not a dead link.
  a: ({ node: _node, href, children, ...props }) =>
    href ? (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-brand underline underline-offset-4 hover:text-brand-deep"
        {...props}
      >
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="my-2 border-s-2 border-line-strong ps-3 text-ink-3" {...props} />
  ),
  hr: () => <hr className="my-3 border-line" />,
  code: ({ node: _node, className, ...props }) => (
    <code
      className={cn("rounded-[4px] bg-bg-recessed px-1 py-0.5 font-mono text-[12.5px]", className)}
      {...props}
    />
  ),
  pre: ({ node: _node, ...props }) => (
    <pre
      className="my-2 overflow-x-auto rounded-lg bg-bg-recessed p-3 font-mono text-[12.5px] [&>code]:bg-transparent [&>code]:p-0"
      {...props}
    />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-2 max-w-full overflow-x-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-left text-caption tabular-nums" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <thead className="bg-bg-recessed" {...props} />,
  th: ({ node: _node, style, ...props }) => (
    <th
      className="whitespace-nowrap border-b border-line px-2.5 py-1.5 font-medium text-ink-2"
      style={style}
      {...props}
    />
  ),
  td: ({ node: _node, style, ...props }) => (
    <td
      className="border-b border-line px-2.5 py-1.5 align-top text-ink-2 [tr:last-child_&]:border-b-0"
      style={style}
      {...props}
    />
  ),
  img: ({ node: _node, alt }) => <span>{alt ?? ""}</span>,
};

const remarkPlugins = [remarkGfm];

export const Markdown = memo(function Markdown({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 break-words text-body text-ink-2", className)}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        components={components}
        urlTransform={safeUrl}
        skipHtml
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
