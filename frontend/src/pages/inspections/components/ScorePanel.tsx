import * as React from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Gauge } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { CountUp } from "@/components/common/motion";
import { formatNumber } from "@/lib/format";
import { num } from "@/api/types";
import type { Inspection } from "@/api/types";
import { EmptyState, SectionCard, lossTone, scoreTone, toneColor } from "./shared";

/**
 * The five promoted score columns rendered as a gauge + bars, plus PML/EML and
 * the risk classification. Values arrive as wire Decimals (strings) so they go
 * through `num()` before any arithmetic.
 */

function Gauge100({ value }: { value: number }) {
  const reduce = useReducedMotion();
  const size = 132;
  const stroke = 11;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.max(0, Math.min(100, value)) / 100;
  const color = toneColor(scoreTone(value));

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="stroke-line"
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          stroke={color}
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: reduce ? circumference * (1 - pct) : circumference }}
          animate={{ strokeDashoffset: circumference * (1 - pct) }}
          transition={{ duration: reduce ? 0 : 1, ease: [0.22, 0.72, 0.24, 1] }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-display text-[30px] leading-none tabular-nums text-text-primary">
          <CountUp value={value} format={(n) => formatNumber(n, 0)} />
        </span>
      </div>
    </div>
  );
}

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const reduce = useReducedMotion();
  const color = toneColor(scoreTone(value));
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-caption text-text-tertiary">{label}</span>
        <span className="text-caption font-medium tabular-nums text-text-primary">
          {value === null ? "—" : formatNumber(value, 0)}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-bg-recessed">
        <motion.div
          className="h-full rounded-full"
          style={{ background: color }}
          initial={{ width: reduce ? `${value ?? 0}%` : 0 }}
          animate={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }}
          transition={{ duration: reduce ? 0 : 0.8, ease: [0.22, 0.72, 0.24, 1] }}
        />
      </div>
    </div>
  );
}

export function ScorePanel({
  inspection,
  actions,
}: {
  inspection: Inspection;
  actions?: React.ReactNode;
}) {
  const { t } = useTranslation("inspections");

  const overall = num(inspection.overall_score);
  const technical = num(inspection.technical_score);
  const commercial = num(inspection.commercial_score);
  const location = num(inspection.location_score);
  const loss = num(inspection.loss_estimate_score);
  const pml = num(inspection.pml_pct);
  const eml = num(inspection.eml_pct);

  const hasAny =
    overall !== null ||
    technical !== null ||
    commercial !== null ||
    location !== null ||
    loss !== null ||
    pml !== null ||
    eml !== null ||
    !!inspection.risk_classification;

  return (
    <SectionCard title={t("scores.title")} icon={<Gauge />} actions={actions}>
      {!hasAny ? (
        <EmptyState>{t("scores.empty")}</EmptyState>
      ) : (
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
          <div className="flex shrink-0 flex-col items-center gap-2">
            {overall === null ? (
              <div className="flex h-[132px] w-[132px] items-center justify-center rounded-full border border-dashed border-line text-body text-text-muted">
                —
              </div>
            ) : (
              <Gauge100 value={overall} />
            )}
            <span className="text-caption text-text-muted">
              {t("scores.overall")} · {t("scores.outOf")}
            </span>
          </div>

          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <ScoreBar label={t("scores.technical")} value={technical} />
            <ScoreBar label={t("scores.commercial")} value={commercial} />
            <ScoreBar label={t("scores.location")} value={location} />
            <ScoreBar label={t("scores.lossEstimate")} value={loss} />

            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Badge variant={lossTone(pml)}>
                {t("scores.pml")} · {pml === null ? "—" : `${formatNumber(pml, 0)}%`}
              </Badge>
              <Badge variant={lossTone(eml)}>
                {t("scores.eml")} · {eml === null ? "—" : `${formatNumber(eml, 0)}%`}
              </Badge>
              {inspection.risk_classification ? (
                <Badge variant="outline">{inspection.risk_classification}</Badge>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </SectionCard>
  );
}
