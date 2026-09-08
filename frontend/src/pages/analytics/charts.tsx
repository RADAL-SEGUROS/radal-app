/**
 * Analytics dashboard charts — recharts, styled to the Signal chart doctrine:
 * theme tokens only (the CSS vars resolve per theme, so dark mode needs no
 * second palette here), hairline grid, 12px ink-3 axis text with no axis
 * lines, one brand hue for a single series, restrained categorical steps of
 * the SAME brand hue for the donut (identity lives in the direct-label list,
 * not in a rainbow), no gradients, no floating legend boxes, tabular numbers.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card } from "@/components/ui/card";
import { FadeUp } from "@/components/common/motion";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProposalInsurerCount } from "@/api/types";

const AXIS_TICK = { fontSize: 12, fill: "var(--ink-3)" } as const;

// =============================================================================
// Shared chrome
// =============================================================================

function ChartCard({
  title,
  subtitle,
  isLoading,
  isEmpty,
  children,
}: {
  title: string;
  subtitle?: string;
  isLoading?: boolean;
  isEmpty?: boolean;
  children: React.ReactNode;
}) {
  const { t } = useTranslation("analytics");
  return (
    <FadeUp className="h-full">
      <Card className="flex h-full flex-col gap-4 p-5">
        <div className="min-w-0">
          <h2 className="text-h3 tracking-tight text-ink">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-caption text-ink-3">{subtitle}</p> : null}
        </div>
        {isLoading ? (
          <Skeleton className="h-[240px] w-full rounded-lg" />
        ) : isEmpty ? (
          <div className="flex h-[240px] items-center justify-center">
            <p className="text-caption text-ink-3">{t("charts.empty")}</p>
          </div>
        ) : (
          children
        )}
      </Card>
    </FadeUp>
  );
}

interface TooltipEntry {
  name?: string | number;
  value?: string | number;
  payload?: Record<string, unknown>;
}

/** Signal tooltip: bordered bone panel, caption type, tabular value. */
function ChartTip({
  active,
  payload,
  label,
  valueLabel,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  /** What the number IS ("expedientes", "propuestas"). */
  valueLabel: string;
}) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  const title = label ?? entry.name ?? "";
  return (
    <div className="rounded-lg border border-line bg-bone px-3 py-2 text-caption shadow-overlay">
      <p className="font-medium text-ink">{String(title)}</p>
      <p className="mt-0.5 tabular-nums text-ink-2">
        {entry.value} {valueLabel}
      </p>
    </div>
  );
}

// =============================================================================
// Case files by stage — horizontal bars, single brand hue
// =============================================================================

export interface StageDatum {
  stage: string;
  label: string;
  count: number;
}

const MAX_STAGES = 8;

export function StageBarChart({
  byStage,
  labelFor,
  isLoading,
}: {
  byStage: Record<string, number> | undefined;
  labelFor: (stage: string) => string;
  isLoading: boolean;
}) {
  const { t } = useTranslation("analytics");

  const data = React.useMemo<StageDatum[]>(
    () =>
      Object.entries(byStage ?? {})
        .filter(([, count]) => count > 0)
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .slice(0, MAX_STAGES)
        .map(([stage, count]) => ({ stage, label: labelFor(stage), count })),
    [byStage, labelFor],
  );

  return (
    <ChartCard
      title={t("charts.byStage.title")}
      subtitle={t("charts.byStage.subtitle")}
      isLoading={isLoading}
      isEmpty={data.length === 0}
    >
      <div className="h-[240px] w-full" dir="ltr">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 8, bottom: 0, left: 0 }}
            barCategoryGap={8}
          >
            <CartesianGrid horizontal={false} stroke="var(--line)" />
            <XAxis
              type="number"
              allowDecimals={false}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--paper-2)" }}
              content={<ChartTip valueLabel={t("charts.byStage.unit")} />}
            />
            <Bar
              dataKey="count"
              fill="var(--brand)"
              radius={[0, 4, 4, 0]}
              barSize={14}
              background={{ fill: "var(--paper-2)", radius: 4 }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

// =============================================================================
// Proposals by insurer — donut, brand hue stepped by opacity + direct labels
// =============================================================================

/** Identity is carried by the labeled list beside the donut, so the slices
 *  stay on ONE brand hue at stepped opacities (restraint doctrine) instead of
 *  a categorical rainbow; the muted last step is the "others" bucket. */
const SLICE_OPACITY = [1, 0.75, 0.55, 0.38, 0.24] as const;
const MAX_INSURERS = SLICE_OPACITY.length;

interface InsurerDatum {
  name: string;
  count: number;
  fill: string;
  fillOpacity: number;
  isOther: boolean;
}

export function InsurerDonutChart({
  byInsurer,
  total,
  isLoading,
}: {
  byInsurer: ProposalInsurerCount[] | undefined;
  /** Total proposals (all insurers), for the center figure + others bucket. */
  total: number | undefined;
  isLoading: boolean;
}) {
  const { t } = useTranslation("analytics");

  const data = React.useMemo<InsurerDatum[]>(() => {
    const rows = byInsurer ?? [];
    const top: InsurerDatum[] = rows.slice(0, MAX_INSURERS).map((row, i) => ({
      name: row.insurer_name,
      count: row.count,
      fill: "var(--brand)",
      fillOpacity: SLICE_OPACITY[i],
      isOther: false,
    }));
    // `by_insurer` is only the top 10 — the bucket is everything the list does
    // not carry, measured against the true total, not the listed remainder.
    const topSum = top.reduce((acc, row) => acc + row.count, 0);
    const listedTotal = rows.reduce((acc, row) => acc + row.count, 0);
    const rest = Math.max(total ?? listedTotal, listedTotal) - topSum;
    if (rest > 0) {
      top.push({
        name: t("charts.byInsurer.others"),
        count: rest,
        fill: "var(--ink-3)",
        fillOpacity: 0.35,
        isOther: true,
      });
    }
    return top.filter((row) => row.count > 0);
  }, [byInsurer, t]);

  const shown = data.reduce((acc, row) => acc + row.count, 0);

  return (
    <ChartCard
      title={t("charts.byInsurer.title")}
      subtitle={t("charts.byInsurer.subtitle")}
      isLoading={isLoading}
      isEmpty={data.length === 0}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-5">
        <div className="relative h-[240px] w-[240px] shrink-0" dir="ltr">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
              <Tooltip content={<ChartTip valueLabel={t("charts.byInsurer.unit")} />} />
              <Pie
                data={data}
                dataKey="count"
                nameKey="name"
                innerRadius={72}
                outerRadius={100}
                paddingAngle={2}
                stroke="var(--bone)"
                strokeWidth={2}
                isAnimationActive={false}
              >
                {data.map((row) => (
                  <Cell key={row.name} fill={row.fill} fillOpacity={row.fillOpacity} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          {/* Center figure — the headline the donut orbits. */}
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-kpi font-semibold tabular-nums tracking-tight text-ink">
              {total ?? shown}
            </span>
            <span className="text-caption text-ink-3">{t("charts.byInsurer.centerLabel")}</span>
          </div>
        </div>
        {/* Direct labels: the legend IS the data list, so no floating box. */}
        <ul className="min-w-0 flex-1 space-y-1.5">
          {data.map((row) => (
            <li key={row.name} className="flex min-w-0 items-center gap-2 text-caption">
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: row.fill, opacity: row.fillOpacity }}
              />
              <span className="min-w-0 flex-1 truncate text-ink-2">{row.name}</span>
              <span className="tabular-nums text-ink-3">{row.count}</span>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  );
}
