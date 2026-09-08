import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { FadeUp } from "@/components/common/motion";
import { cn } from "@/lib/utils";
import type { InspectionRequestStatus, InspectionStatus } from "@/api/types";

/**
 * Small building blocks shared by the inspections pages.
 *
 * The important one is {@link GuardedButton}: NO DEAD BUTTONS. A control is
 * either enabled and wired, or visibly disabled with a tooltip that says why —
 * missing permission, frozen report, or a capability that is not built yet.
 */

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

export const REQUEST_STATUS_VARIANT: Record<InspectionRequestStatus, BadgeVariant> = {
  pending: "warn",
  scheduled: "action",
  in_progress: "brand",
  completed: "success",
  cancelled: "muted",
};

export const REPORT_STATUS_VARIANT: Record<InspectionStatus, BadgeVariant> = {
  draft: "neutral",
  in_review: "warn",
  issued: "success",
  archived: "muted",
};

export const URGENCY_VARIANT: Record<string, BadgeVariant> = {
  low: "muted",
  normal: "neutral",
  high: "warn",
  urgent: "danger",
};

export const RESULT_VARIANT: Record<string, BadgeVariant> = {
  ok: "success",
  observation: "warn",
  critical: "danger",
  not_applicable: "muted",
  unknown: "neutral",
};

/**
 * Legal report transitions — a mirror of `_STATUS_TRANSITIONS` in
 * `backend/app/api/routers/inspections.py`, so the UI only ever offers a move
 * the server will accept.
 */
export const STATUS_TRANSITIONS: Record<InspectionStatus, InspectionStatus[]> = {
  draft: ["in_review", "archived"],
  in_review: ["draft", "issued", "archived"],
  issued: ["archived"],
  archived: [],
};

/** Issued/archived reports are read-only: amend them with a new version. */
export function isFrozen(status: InspectionStatus): boolean {
  return status === "issued" || status === "archived";
}

export interface Guard {
  allowed: boolean;
  /** Localized explanation shown in the tooltip when `allowed` is false. */
  reason: string;
}

/** Combine guards: the first failing one wins, so the tooltip is specific. */
export function allOf(...guards: Guard[]): Guard {
  const failed = guards.find((g) => !g.allowed);
  return failed ?? { allowed: true, reason: "" };
}

interface GuardedButtonProps extends ButtonProps {
  guard: Guard;
}

/** A button that is either wired, or visibly disabled with a reason tooltip. */
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

/** Section shell: title row + optional actions + body, with entrance motion. */
export function SectionCard({
  title,
  icon,
  actions,
  description,
  children,
  className,
  bodyClassName,
}: {
  title: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  description?: React.ReactNode;
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
                <p className="truncate text-caption text-text-muted">{description}</p>
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

/** Label above value, used across the detail header. */
export function InfoItem({
  label,
  children,
  mono,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-caption font-medium text-ink-3">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 truncate text-body text-text-primary",
          mono && "tabular-nums",
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** Labelled form field wrapper for the dialogs. */
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

/** Empty-state block for a card body. */
export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-paper-2 px-6 py-8 text-center">
      <p className="text-body text-text-muted">{children}</p>
    </div>
  );
}

/** Score tone: higher is better (quality scores). */
export function scoreTone(value: number | null): BadgeVariant {
  if (value === null) return "neutral";
  if (value >= 80) return "success";
  if (value >= 60) return "brand";
  if (value >= 40) return "warn";
  return "danger";
}

/** Loss tone: higher is worse (PML / EML). */
export function lossTone(value: number | null): BadgeVariant {
  if (value === null) return "neutral";
  if (value >= 70) return "danger";
  if (value >= 40) return "warn";
  return "success";
}

const TONE_COLOR: Record<BadgeVariant, string> = {
  success: "var(--lime)",
  brand: "var(--teal)",
  warn: "var(--amber)",
  danger: "var(--red)",
  action: "var(--blue)",
  neutral: "var(--text-muted)",
  muted: "var(--text-muted)",
  outline: "var(--text-muted)",
};

/** CSS color for a tone — used by the gauge and bars. */
export function toneColor(tone: BadgeVariant): string {
  return TONE_COLOR[tone];
}

export { Badge };
