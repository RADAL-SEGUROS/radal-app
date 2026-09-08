import * as React from "react";
import { Card } from "@/components/ui/card";
import { CountUp, FadeUp } from "@/components/common/motion";
import { cn } from "@/lib/utils";

export type KpiTone = "default" | "brand" | "danger" | "warn" | "success" | "action";

interface KpiCardProps {
  label: string;
  /** Pre-formatted display value (used when no count-up is wanted). */
  value?: React.ReactNode;
  /** Numeric target: when set, the number counts up from 0 on mount. */
  countTo?: number;
  /** Formatter for the counted value (defaults to de-DE integer grouping). */
  format?: (n: number) => string;
  /** Optional small caption below the value. */
  hint?: React.ReactNode;
  icon?: React.ReactNode;
  /** Tone colors the HINT line (up/down/status), not a chip. */
  tone?: KpiTone;
  className?: string;
}

/** Hint tone: positive/negative/status text on the caption line. */
const hintTone: Record<KpiTone, string> = {
  default: "text-ink-2",
  brand: "text-brand-deep",
  action: "text-brand-deep",
  danger: "text-neg-text",
  warn: "text-warn-text",
  success: "text-pos-text",
};

/**
 * Signal KPI tile: sentence-case caption label FIRST, then the tabular value,
 * then a toned hint. The icon chip is optional and always brand-soft — status
 * meaning lives in the hint text, not in a colored chip.
 */
export function KpiCard({
  label,
  value,
  countTo,
  format,
  hint,
  icon,
  tone = "default",
  className,
}: KpiCardProps) {
  return (
    <FadeUp className="h-full">
      <Card interactive className={cn("h-full p-[18px]", className)}>
        <div className="flex items-start justify-between gap-2">
          <div className="text-caption font-medium text-ink-3">
            {label}
          </div>
          {icon ? (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep [&_svg]:h-4 [&_svg]:w-4">
              {icon}
            </span>
          ) : null}
        </div>
        <div className="mt-2 text-kpi font-semibold tabular-nums tracking-tight text-ink">
          {countTo !== undefined ? (
            <CountUp value={countTo} format={format} />
          ) : (
            value
          )}
        </div>
        {hint ? (
          <div className={cn("mt-[5px] text-caption", hintTone[tone])}>
            {hint}
          </div>
        ) : null}
      </Card>
    </FadeUp>
  );
}
