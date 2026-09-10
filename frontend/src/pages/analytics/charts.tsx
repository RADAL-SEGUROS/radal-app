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
  Area,
  AreaChart,
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
import { ChartPngButton } from "@/components/common/ExportMenu";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProposalInsurerCount } from "@/api/types";

const AXIS_TICK = { fontSize: 12, fill: "var(--ink-3)" } as const;

// =============================================================================
// Shared chrome
// =============================================================================

export function ChartCard({
  title,
  subtitle,
  isLoading,
  isEmpty,
  children,
  /** Filename stem for the PNG capture; omit to hide the capture button. */
  exportAs,
}: {
  title: string;
  subtitle?: string;
  isLoading?: boolean;
  isEmpty?: boolean;
  children: React.ReactNode;
  exportAs?: string;
}) {
  const { t } = useTranslation("analytics");
  // The capture reads the rendered <svg> out of this node — see lib/exports.ts.
  // Every chart in the app gets PNG export from this one place; adding it per
  // chart is how half of them would end up without it.
  const chartRef = React.useRef<HTMLDivElement | null>(null);
  const capturable = Boolean(exportAs) && !isLoading && !isEmpty;

  return (
    <FadeUp className="h-full">
      <Card className="flex h-full flex-col gap-4 p-5">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-h3 tracking-tight text-ink">{title}</h2>
            {subtitle ? <p className="mt-0.5 text-caption text-ink-3">{subtitle}</p> : null}
          </div>
          {capturable ? (
            <ChartPngButton chartRef={chartRef} filename={exportAs as string} />
          ) : null}
        </div>
        {isLoading ? (
          <Skeleton className="h-[240px] w-full rounded-lg" />
        ) : isEmpty ? (
          <div className="flex h-[240px] items-center justify-center">
            <p className="text-caption text-ink-3">{t("charts.empty")}</p>
          </div>
        ) : (
          <div ref={chartRef} className="min-w-0">
            {children}
          </div>
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
      exportAs="expedientes-por-etapa"
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
      exportAs="propuestas-por-aseguradora"
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

// =============================================================================
// Generic group-by charts — whatever dimension the broker picks
// =============================================================================

/**
 * The bucket shape every `/summary?group_by=` answer returns. One component
 * serves every dimension (etapa, aseguradora, ramo, grupo, mes…) so adding a
 * dimension is a server change, not a new chart.
 */
export interface BucketDatum {
  key: string;
  label: string;
  count: number;
  total_uf?: string | number | null;
}

const MAX_BUCKETS = 10;

function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Buckets as horizontal bars. `metric` chooses what the bar LENGTH means —
 * how many, or how much UF. They answer different questions and a broker
 * reading the wrong one draws the wrong conclusion, so it is explicit.
 */
export function BucketBarChart({
  title,
  subtitle,
  buckets,
  isLoading,
  metric = "count",
  unitLabel,
  exportAs,
  /** Resolves a bucket's display text; see {@link useBucketLabel}. */
  labelFor,
  onSelect,
  selectedKey,
}: {
  title: string;
  subtitle?: string;
  buckets: BucketDatum[] | undefined;
  isLoading: boolean;
  metric?: "count" | "uf";
  unitLabel: string;
  exportAs?: string;
  labelFor?: (bucket: BucketDatum) => string;
  /** Clicking a bar narrows the page to that bucket, when the caller can. */
  onSelect?: (key: string) => void;
  selectedKey?: string | null;
}) {
  const data = React.useMemo(
    () =>
      (buckets ?? [])
        .map((bucket) => ({
          ...bucket,
          label: labelFor ? labelFor(bucket) : bucket.label,
          value: metric === "uf" ? toNumber(bucket.total_uf) : bucket.count,
        }))
        .filter((row) => row.value > 0)
        .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
        .slice(0, MAX_BUCKETS),
    [buckets, metric, labelFor],
  );

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      isLoading={isLoading}
      isEmpty={data.length === 0}
      exportAs={exportAs}
    >
      <div className="h-[260px] w-full" dir="ltr">
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
              width={160}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--paper-2)" }}
              content={<ChartTip valueLabel={unitLabel} />}
            />
            <Bar
              dataKey="value"
              radius={[0, 4, 4, 0]}
              barSize={14}
              background={{ fill: "var(--paper-2)", radius: 4 }}
              onClick={
                onSelect
                  ? (entry: unknown) => {
                      const key = (entry as { payload?: BucketDatum })?.payload?.key;
                      if (key) onSelect(key);
                    }
                  : undefined
              }
              cursor={onSelect ? "pointer" : undefined}
            >
              {data.map((row) => (
                <Cell
                  key={row.key}
                  fill="var(--brand)"
                  // The unselected bars recede rather than change hue — one
                  // hue, one meaning (Signal chart doctrine).
                  fillOpacity={
                    selectedKey && selectedKey !== row.key ? 0.3 : 1
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

/**
 * The same buckets as a monthly trend. Only offered when the dimension IS
 * time — a line between unordered categories would be a lie.
 */
export function BucketTrendChart({
  title,
  subtitle,
  buckets,
  isLoading,
  metric = "count",
  unitLabel,
  exportAs,
  /** Resolves a bucket's display text; see {@link useBucketLabel}. */
  labelFor,
}: {
  title: string;
  subtitle?: string;
  buckets: BucketDatum[] | undefined;
  isLoading: boolean;
  metric?: "count" | "uf";
  unitLabel: string;
  exportAs?: string;
  labelFor?: (bucket: BucketDatum) => string;
}) {
  const data = React.useMemo(
    () =>
      (buckets ?? [])
        // Time buckets arrive keyed by ISO month, so a plain key sort IS
        // chronological order.
        .slice()
        .sort((a, b) => a.key.localeCompare(b.key))
        .map((bucket) => ({
          ...bucket,
          label: labelFor ? labelFor(bucket) : bucket.label,
          value: metric === "uf" ? toNumber(bucket.total_uf) : bucket.count,
        })),
    [buckets, metric, labelFor],
  );

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      isLoading={isLoading}
      isEmpty={data.length === 0}
      exportAs={exportAs}
    >
      <div className="h-[260px] w-full" dir="ltr">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="bucketTrendFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--brand)" stopOpacity={0.22} />
                <stop offset="100%" stopColor="var(--brand)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis
              dataKey="label"
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              allowDecimals={false}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
              width={44}
            />
            <Tooltip
              cursor={{ stroke: "var(--line-strong)" }}
              content={<ChartTip valueLabel={unitLabel} />}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke="var(--brand)"
              strokeWidth={2}
              fill="url(#bucketTrendFill)"
              dot={false}
              activeDot={{ r: 4 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

// =============================================================================
// Bucket labels
// =============================================================================

/**
 * Turns a bucket into display text.
 *
 * The server sends `label` DATA-derived on purpose — it resolves identities it
 * alone can resolve (an insurer's name, a group's name) and leaves enums as
 * their raw value, because translating those is the frontend's job (rule 1:
 * identifiers are English, Spanish lives in `locales/`). Without this the
 * group-by chart printed `technical_basis` next to a chart that said "Bases
 * técnicas" — the same fact, spelled two ways, one of them not Spanish.
 *
 * Unknown dimensions and unknown keys fall back to the server's label, so a
 * dimension added server-side degrades to something readable instead of blank.
 */
export function useBucketLabel(dimension: string): (bucket: BucketDatum) => string {
  const { t: tCases } = useTranslation("cases");
  const { i18n } = useTranslation("analytics");

  return React.useCallback(
    (bucket: BucketDatum) => {
      const fallback = bucket.label || bucket.key;
      switch (dimension) {
        case "stage":
          return tCases(`stages.${bucket.key}`, { defaultValue: fallback });
        case "status":
          return tCases(`statuses.${bucket.key}`, { defaultValue: fallback });
        case "kind":
          return tCases(`kinds.${bucket.key}`, { defaultValue: fallback });
        case "month": {
          // "2026-09" -> "sep 2026", in the active locale.
          const match = /^(\d{4})-(\d{2})$/.exec(bucket.key);
          if (!match) return fallback;
          const date = new Date(Number(match[1]), Number(match[2]) - 1, 1);
          return new Intl.DateTimeFormat(i18n.language, {
            month: "short",
            year: "numeric",
          }).format(date);
        }
        default:
          // Identity dimensions (insurer, account_group, insurance_line) are
          // rows, not enums: only the server can name them.
          return fallback;
      }
    },
    [dimension, tCases, i18n.language],
  );
}
