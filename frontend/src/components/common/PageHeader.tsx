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
        "flex flex-col gap-3 pb-1 sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow ? (
          <div className="mb-1.5 font-mono text-mono-sm uppercase tracking-wide text-text-muted">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="font-display text-h1 text-text-primary">{title}</h1>
        {subtitle ? (
          <p className="mt-1.5 text-body text-text-muted">{subtitle}</p>
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
