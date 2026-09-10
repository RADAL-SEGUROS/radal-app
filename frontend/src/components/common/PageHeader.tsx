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
}

export function PageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
  className,
}: PageHeaderProps) {
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
