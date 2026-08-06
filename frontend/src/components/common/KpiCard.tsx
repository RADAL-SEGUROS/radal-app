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
  /** Tone colors the icon chip. */
  tone?: KpiTone;
  className?: string;
}

const chipTone: Record<KpiTone, string> = {
  default:
    "text-teal-deep bg-[color-mix(in_srgb,var(--teal)_13%,transparent)]",
  brand: "text-teal-deep bg-[color-mix(in_srgb,var(--teal)_13%,transparent)]",
  action: "text-blue-deep bg-[color-mix(in_srgb,var(--blue)_13%,transparent)]",
  danger: "text-red-deep bg-[color-mix(in_srgb,var(--red)_13%,transparent)]",
  warn: "text-amber-deep bg-[color-mix(in_srgb,var(--amber)_13%,transparent)]",
  success:
    "text-lime-deep bg-[color-mix(in_srgb,var(--lime)_13%,transparent)]",
};

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
        {icon ? (
          <span
            className={cn(
              "flex h-[38px] w-[38px] items-center justify-center rounded-[11px] [&_svg]:h-5 [&_svg]:w-5",
              chipTone[tone],
            )}
          >
            {icon}
          </span>
        ) : null}
        <div className="mt-[15px] font-display text-kpi tabular-nums text-text-primary">
          {countTo !== undefined ? (
            <CountUp value={countTo} format={format} />
          ) : (
            value
          )}
        </div>
        <div className="mt-[7px] text-body text-text-muted">{label}</div>
        {hint ? (
          <div className="mt-[3px] text-caption text-text-muted opacity-85">
            {hint}
          </div>
        ) : null}
      </Card>
    </FadeUp>
  );
}
