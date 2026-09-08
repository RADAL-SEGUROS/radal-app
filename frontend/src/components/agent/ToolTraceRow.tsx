/**
 * One collapsed line per tool the assistant used ("Consultó
 * `get_quote_comparison`"), expandable to the stored result JSON —
 * progressive disclosure, not a wall of JSON (agent spec §8.4).
 */
import * as React from "react";
import { ChevronRight, Search, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

function prettify(content: string | null): string {
  if (!content) return "";
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

export function ToolTraceRow({
  tool,
  status,
  content,
}: {
  tool: string;
  /** `ok | error` (loop trace statuses; pending_confirmation renders as a card). */
  status: string;
  content: string | null;
}) {
  const { t } = useTranslation("agent");
  const [open, setOpen] = React.useState(false);
  const failed = status === "error";
  const label = t(`tools.${tool}`, { defaultValue: tool });

  return (
    <div className="ps-9">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "inline-flex min-h-8 items-center gap-1.5 rounded-sm px-2 py-1 text-caption transition-[background-color,color] duration-150 ease-out",
          failed ? "text-[var(--neg-text)]" : "text-text-muted",
          "hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] hover:text-text-primary",
        )}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 transition-[rotate] duration-150 ease-out",
            open && "rotate-90",
          )}
          strokeWidth={1.5}
          aria-hidden
        />
        {failed ? (
          <TriangleAlert className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        ) : (
          <Search className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        )}
        <span>
          {failed ? t("trace.failed", { tool: label }) : t("trace.used", { tool: label })}
        </span>
      </button>
      {open ? (
        <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-bg-recessed p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
          {prettify(content)}
        </pre>
      ) : null}
    </div>
  );
}
