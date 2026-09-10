import * as React from "react";
import { FadeUp } from "@/components/common/motion";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Right-aligned actions (buttons, ExportButton, etc.). */
  actions?: React.ReactNode;
  /** Optional back link / breadcrumb slot rendered above the title. */
  eyebrow?: React.ReactNode;
  className?: string;
  /**
   * The page is rendered INSIDE another page — as a tab of Datos, say — so it
   * must not print a second `<h1>` under the host's own. The actions survive:
   * "Nuevo cliente" is the reason the broker opened the tab, and dropping it
   * with the title would be a feature quietly lost in a layout change.
   *
   * Threaded as a prop rather than sniffed from the route so a page can be
   * embedded twice, in different hosts, without either guessing.
   */
  embedded?: boolean;
}

export function PageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
  className,
  embedded,
}: PageHeaderProps) {
  if (embedded) {
    if (!actions) return null;
    return (
      <div className={cn("flex flex-wrap items-center justify-end gap-2.5", className)}>
        {actions}
      </div>
    );
  }

  return (
    <FadeUp
      delay={0}
      className={cn(
        // Wrap rather than overflow: `sm` is too eager for a header that carries
        // a long title AND actions — at tablet widths the two collide and the
        // buttons get pushed past the viewport edge. `min-w-0` on the row keeps
        // it shrinkable inside a flex parent.
        "flex min-w-0 flex-col gap-3 pb-1 md:flex-row md:flex-wrap md:items-end md:justify-between",
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        {eyebrow ? (
          <div className="mb-1.5 text-caption font-medium text-ink-3">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="text-balance text-h1 tracking-tight text-ink">{title}</h1>
        {subtitle ? (
          <div className="mt-1.5 text-pretty text-body text-ink-3">{subtitle}</div>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2.5">
          {actions}
        </div>
      ) : null}
    </FadeUp>
  );
}

/** Props a list page accepts so it can render standalone or inside Datos. */
export interface EmbeddablePageProps {
  /** Suppresses the page's own title block; see {@link PageHeader}. */
  embedded?: boolean;
}
