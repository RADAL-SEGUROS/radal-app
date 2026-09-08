import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { FadeUp } from "@/components/common/motion";
import { cn } from "@/lib/utils";

/**
 * Settings building blocks.
 *
 * {@link GuardedButton} is the NO DEAD BUTTONS primitive: a control is either
 * wired to a live endpoint, or rendered visibly disabled with a tooltip saying
 * why — missing permission, or a capability that has no endpoint yet ("pronto").
 */

export interface Guard {
  allowed: boolean;
  reason: string;
}

interface GuardedButtonProps extends ButtonProps {
  guard: Guard;
}

export const GuardedButton = React.forwardRef<HTMLButtonElement, GuardedButtonProps>(
  ({ guard, children, disabled, ...props }, ref) => {
    if (guard.allowed) {
      return (
        <Button ref={ref} disabled={disabled} {...props}>
          {children}
        </Button>
      );
    }
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex cursor-not-allowed">
            <Button ref={ref} {...props} disabled className="pointer-events-none">
              {children}
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>{guard.reason}</TooltipContent>
      </Tooltip>
    );
  },
);
GuardedButton.displayName = "GuardedButton";

export function SectionCard({
  title,
  icon,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: React.ReactNode;
  icon?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <FadeUp className={cn("h-full", className)}>
      <Card className="flex h-full flex-col overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
          <div className="flex min-w-0 items-center gap-2.5">
            {icon ? (
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep [&_svg]:h-[17px] [&_svg]:w-[17px]">
                {icon}
              </span>
            ) : null}
            <div className="min-w-0">
              <h2 className="truncate font-display text-h3 text-text-primary">{title}</h2>
              {description ? (
                <p className="text-caption text-text-muted">{description}</p>
              ) : null}
            </div>
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
        <div className={cn("flex-1 p-5", bodyClassName)}>{children}</div>
      </Card>
    </FadeUp>
  );
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
  className,
}: {
  label: React.ReactNode;
  htmlFor?: string;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p className="text-caption text-neg-text">{error}</p>
      ) : hint ? (
        <p className="text-caption text-text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

/** Read-only key/value row used by the broker profile. */
export function ReadOnlyField({
  label,
  value,
  mono,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-caption font-medium text-ink-3">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 truncate rounded-lg border border-dashed border-line bg-paper-2 px-3 py-2 text-body text-text-primary",
          mono && "tabular-nums",
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-paper-2 px-6 py-8 text-center">
      <p className="text-body text-text-muted">{children}</p>
    </div>
  );
}

/** Bytes -> "1,4 MB". Sizes are optional on the wire (derived logos have none). */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${new Intl.NumberFormat("es-CL", {
    maximumFractionDigits: unit === 0 ? 0 : 1,
  }).format(value)} ${units[unit]}`;
}
