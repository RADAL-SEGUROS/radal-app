/**
 * One collapsed line per tool the assistant used ("Consultó: Listar pólizas"),
 * expandable to the stored result JSON — progressive disclosure, not a wall of
 * JSON (agent spec §8.4).
 *
 * Labels come from the explicit `TOOL_LABEL_KEY` map; a tool the map does not
 * know yet renders the generic label plus its id (never a raw i18n key).
 */
import * as React from "react";
import { BookOpen, ChevronRight, Search, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { isKnownTool, toolLabelKey } from "@/components/agent/labels";
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
  summary,
}: {
  tool: string;
  /** `ok | error` (loop trace statuses; pending_confirmation renders as a card). */
  status: string;
  content: string | null;
  /** A ready sentence that replaces the generic "Consultó: …" line. */
  summary?: string | null;
}) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = React.useState(false);
  const failed = status === "error";
  const known = isKnownTool(tool);
  const label = known ? t(toolLabelKey(tool)) : `${t(toolLabelKey(tool))} · ${tool}`;
  const Icon = failed ? TriangleAlert : tool === "load_skill" ? BookOpen : Search;

  return (
    <div className="ps-9">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-sm px-2 py-1 text-left text-caption transition-[background-color,color] duration-150 ease-out",
          failed ? "text-neg-text" : "text-ink-3",
          "hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] hover:text-ink",
        )}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-[rotate] duration-150 ease-out",
            open && "rotate-90",
          )}
          strokeWidth={1.5}
          aria-hidden
        />
        <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
        <span className="min-w-0 truncate">
          {summary && !failed
            ? summary
            : failed
              ? t("agent.trace.failed", { tool: label })
              : t("agent.trace.used", { tool: label })}
        </span>
      </button>
      {open ? (
        <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-bg-recessed p-3 font-mono text-[11px] leading-relaxed text-ink-2">
          {prettify(content)}
        </pre>
      ) : null}
    </div>
  );
}
