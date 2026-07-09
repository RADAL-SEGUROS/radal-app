import * as React from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type KpiTone = "default" | "danger" | "warn" | "success";

interface KpiCardProps {
  label: string;
  value: React.ReactNode;
  /** Optional small caption below the value (text-mono-sm). */
  hint?: React.ReactNode;
  icon?: React.ReactNode;
  /** Tone colors the headline number (e.g. rojo for "Por vencer 30D"). */
  tone?: KpiTone;
  className?: string;
}

const toneClass: Record<KpiTone, string> = {
  default: "text-text-primary",
  danger: "text-signal-danger",
  warn: "text-signal-warn",
  success: "text-lime",
};

export function KpiCard({
  label,
  value,
  hint,
  icon,
  tone = "default",
  className,
}: KpiCardProps) {
  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-label text-text-muted">{label}</p>
          <p
            className={cn(
              "mt-1 font-display text-kpi tabular-nums",
              toneClass[tone],
            )}
          >
            {value}
          </p>
          {hint ? (
            <p className="mt-1 font-mono text-mono-sm text-text-muted">
              {hint}
            </p>
          ) : null}
        </div>
        {icon ? (
          <div className="shrink-0 rounded-md bg-bg-recessed p-2 text-teal">
            {icon}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
