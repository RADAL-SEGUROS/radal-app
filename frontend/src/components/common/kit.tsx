/**
 * The cross-app component kit — shared UI + formatting primitives.
 *
 * Promoted out of `pages/proposals/shared.tsx` (which now re-exports this file
 * verbatim, so its 40+ importers keep compiling unchanged). Everything here is
 * styled to the Radal Signal doctrine (docs/v5-signal-ui-spec.md): Inter
 * everywhere, hairline borders, soft-tint status color, no mono in UI chrome —
 * numbers/RUTs/IDs use `tabular-nums`.
 *
 * The copy these helpers read lives in the `quotes` / `proposals` / `insurers` /
 * `offerings` namespaces, registered in `src/i18n/index.ts`. `es` is
 * authoritative, `en` mirrors the same keys.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Check, Clock, Copy, Inbox } from "lucide-react";
import { motion } from "framer-motion";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { API_URL } from "@/lib/api";
import { formatNumber, formatUF } from "@/lib/format";
import { num, type DecimalString, type DeductibleTerm } from "@/api/types";


// =============================================================================
// Formatting
// =============================================================================

/** Wire decimal -> "UF 1.234,56" (em dash when absent). */
export function uf(value: DecimalString | number | null | undefined, decimals = 2): string {
  return formatUF(num(value), { decimals });
}

/** Wire decimal -> "1,25 ‰" (per-mille rate). */
export function permille(value: DecimalString | number | null | undefined): string {
  const parsed = num(value);
  if (parsed === null) return "—";
  return `${formatNumber(parsed, 3)} ‰`;
}

/** Wire decimal 0-100 -> "12,50 %". */
export function pct(value: DecimalString | number | null | undefined, decimals = 2): string {
  const parsed = num(value);
  if (parsed === null) return "—";
  return `${formatNumber(parsed, decimals)} %`;
}

/** Chilean VAT rate — earthquake cover is exempt, so VAT rides on the taxable part only. */
export const VAT_RATE = 0.19;

export interface DerivedMoney {
  net: number;
  vat: number;
  total: number;
  comprehensiveRate: number | null;
}

/**
 * The Chilean premium arithmetic, client-side, for live feedback only:
 * `net = taxable + exempt`, `vat = 0.19 * taxable` (NOT on net),
 * `total = net + vat`, `comprehensive_rate = taxable_rate + exempt_rate`.
 * The server re-derives and rejects a broken invariant — this is a preview.
 */
export function deriveMoney(
  taxable: number | null,
  exempt: number | null,
  taxableRate?: number | null,
  exemptRate?: number | null,
): DerivedMoney {
  const t = taxable ?? 0;
  const e = exempt ?? 0;
  const net = t + e;
  const vat = t * VAT_RATE;
  const rate =
    taxableRate === null || taxableRate === undefined
      ? exemptRate ?? null
      : (taxableRate ?? 0) + (exemptRate ?? 0);
  return { net, vat, total: net + vat, comprehensiveRate: rate };
}

/** True when two amounts differ by more than a cent — the server's tolerance. */
export function differs(a: number | null, b: number | null, tolerance = 0.01): boolean {
  if (a === null || b === null) return false;
  return Math.abs(a - b) > tolerance;
}

// =============================================================================
// Enum -> badge tone
// =============================================================================

type Variant = NonNullable<BadgeProps["variant"]>;

export const TONE: Record<string, Variant> = {
  // quote_request
  draft: "neutral",
  sent: "action",
  receiving: "warn",
  closed: "success",
  cancelled: "muted",
  // proposal
  submitted: "action",
  accepted: "success",
  rejected: "danger",
  withdrawn: "muted",
  expired: "muted",
  // offering
  viewed: "brand",
  // insurer
  active: "success",
  inactive: "muted",
  deregistered: "danger",
  // priority / urgency
  low: "muted",
  normal: "neutral",
  high: "warn",
  urgent: "danger",
  // proposal origin
  native: "brand",
  external: "neutral",
  // extraction
  pending: "neutral",
  running: "action",
  succeeded: "success",
  failed: "danger",
};

export function toneFor(value: string | null | undefined): Variant {
  return (value && TONE[value]) || "neutral";
}

interface StatusBadgeProps extends Omit<BadgeProps, "variant" | "children"> {
  value: string | null | undefined;
  /** Localized label; falls back to the raw token so nothing renders blank. */
  label?: string;
}

/** Badge whose colour family is derived from the backend enum token. */
export function StatusBadge({ value, label, className, ...props }: StatusBadgeProps) {
  return (
    <Badge variant={toneFor(value)} className={className} {...props}>
      {label ?? value ?? "—"}
    </Badge>
  );
}

/** native = Radal commercial partner · external = tracked from an upload. */
export function OriginBadge({ isNative }: { isNative: boolean }) {
  const { t } = useTranslation("insurers");
  return (
    <Badge variant={isNative ? "brand" : "neutral"}>
      {isNative ? t("origin.native") : t("origin.external")}
    </Badge>
  );
}

/** AI extraction confidence, coloured by how much review it deserves. */
export function ConfidenceBadge({
  value,
  className,
}: {
  value: DecimalString | number | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation("proposals");
  const parsed = num(value);
  if (parsed === null) return null;
  const asPct = parsed <= 1 ? parsed * 100 : parsed;
  const variant: Variant = asPct >= 85 ? "success" : asPct >= 60 ? "warn" : "danger";
  return (
    <Badge variant={variant} dot className={className}>
      {t("ai.confidence")} {formatNumber(asPct, 0)}%
    </Badge>
  );
}

// =============================================================================
// Deductibles
// =============================================================================

/**
 * Render one peril's deductible as a sentence: "5 % de la pérdida · mín. UF 25".
 * `basis` travels with the number because it differs per peril — that
 * difference is exactly what the comparator has to surface.
 */
export function formatDeductible(
  term: DeductibleTerm | null | undefined,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string | null {
  if (!term) return null;
  const parts: string[] = [];
  const p = num(term.pct as DecimalString | number | null);
  if (p !== null) {
    const basis = term.basis
      ? t(`deductibleBasis.${term.basis}`, { defaultValue: String(term.basis) })
      : null;
    parts.push(basis ? `${formatNumber(p, 2)} % ${basis}` : `${formatNumber(p, 2)} %`);
  }
  const amount = num(term.amount_uf as DecimalString | number | null);
  if (amount !== null) parts.push(uf(amount));
  const min = num(term.min_uf as DecimalString | number | null);
  if (min !== null) parts.push(`${t("deductible.min")} ${uf(min)}`);
  const max = num(term.max_uf as DecimalString | number | null);
  if (max !== null) parts.push(`${t("deductible.max")} ${uf(max)}`);
  if (typeof term.days === "number") parts.push(`${term.days} ${t("deductible.days")}`);
  if (term.notes) parts.push(String(term.notes));
  if (!parts.length && term.text) parts.push(String(term.text));
  return parts.length ? parts.join(" · ") : null;
}

/**
 * The verbatim wording the extractor kept for a peril ("5% de la pérdida,
 * mínimo UF 25"). Shown under the structured summary, because a deductible
 * clause often carries a caveat no set of numbers can hold.
 */
export function deductibleText(term: DeductibleTerm | null | undefined): string | null {
  const text = term?.text;
  return typeof text === "string" && text.trim() ? text.trim() : null;
}

/**
 * Turn a stored file URL into something a browser can open.
 *
 * The API returns an absolute presigned S3 URL in the cloud, but a path like
 * `/media/offerings/1/offering.pdf` when the backend serves files itself — and
 * that path belongs to the API origin, not to the SPA's.
 */
export function resolveFileUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^https?:\/\//i.test(url)) return url;
  try {
    const apiOrigin = new URL(API_URL, window.location.origin).origin;
    return new URL(url, apiOrigin).toString();
  } catch {
    return url;
  }
}

/** Localized peril label with the raw key as its own fallback. */
export function usePerilLabel() {
  const { t } = useTranslation("proposals");
  return React.useCallback(
    (peril: string) => t(`perils.${peril}`, { defaultValue: peril.replace(/_/g, " ") }),
    [t],
  );
}

// =============================================================================
// Layout atoms
// =============================================================================

interface SectionProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}

/** A titled card section — the workhorse container of every detail page. */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: SectionProps) {
  return (
    <Card className={cn("overflow-hidden", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-h3 tracking-tight text-ink">{title}</h2>
          {description ? (
            <p className="mt-1 text-caption text-ink-3">{description}</p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
        ) : null}
      </div>
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </Card>
  );
}

/** Label above value, the unit every detail grid is built from. */
export function KeyValue({
  label,
  value,
  mono,
  tone,
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  /** Legacy prop — identifiers now render in Inter `tabular-nums` (no mono). */
  mono?: boolean;
  tone?: "default" | "danger" | "warn" | "success";
  className?: string;
}) {
  const toneClass =
    tone === "danger"
      ? "text-neg-text"
      : tone === "warn"
        ? "text-warn-text"
        : tone === "success"
          ? "text-pos-text"
          : "text-ink";
  return (
    <div className={cn("min-w-0", className)}>
      <div className="text-caption font-medium text-ink-3">{label}</div>
      <div
        className={cn(
          "mt-1 truncate text-body font-medium tabular-nums",
          mono && "tabular-nums",
          toneClass,
        )}
      >
        {value}
      </div>
    </div>
  );
}

/**
 * Empty state — teach the next step, never blank (spec §EmptyState):
 * 40px brand-soft icon tile, `text-h3` title, one caption, one primary CTA.
 */
export function EmptyState({
  title,
  hint,
  icon,
  action,
}: {
  title: React.ReactNode;
  hint?: React.ReactNode;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-soft text-brand-deep [&_svg]:h-5 [&_svg]:w-5">
        {icon ?? <Inbox />}
      </div>
      <h3 className="text-h3 tracking-tight text-ink">{title}</h3>
      {hint ? (
        <p className="max-w-sm text-pretty text-caption text-ink-3">{hint}</p>
      ) : null}
      {action}
    </div>
  );
}

/** Inline error banner. `error` is an axios error, a string, or anything else. */
export function ErrorBanner({
  error,
  className,
}: {
  error: unknown;
  className?: string;
}) {
  const { t } = useTranslation("common");
  if (!error) return null;
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2.5 rounded-lg bg-neg-soft px-3.5 py-2.5",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-neg" />
      <p className="text-caption text-ink-2">{apiError(error, t("state.error"))}</p>
    </div>
  );
}

/** Skeleton block for a loading card body. */
export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2.5">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

/**
 * A control that is deliberately NOT wired in this pass. Rendered visibly
 * disabled with a "pronto" tooltip — never a button that silently does nothing.
 */
export function SoonButton({
  children,
  reason,
  className,
  variant = "secondary",
  size = "sm",
}: {
  children: React.ReactNode;
  /** Why it is disabled; defaults to the generic "pronto" copy. */
  reason?: string;
  className?: string;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
}) {
  const { t } = useTranslation("common");
  return (
    <DisabledHint hint={reason ?? t("nav.comingSoon")}>
      <Button variant={variant} size={size} disabled className={className}>
        {children}
        <Badge variant="muted" className="ml-1 px-1.5 py-0">
          {t("nav.soon")}
        </Badge>
      </Button>
    </DisabledHint>
  );
}

/** Alias — some call sites read better with the explicit name. */
export const ComingSoonButton = SoonButton;

/**
 * Wraps a possibly-disabled control so the reason is always discoverable.
 * A disabled button swallows pointer events, hence the wrapping span.
 */
export function DisabledHint({
  hint,
  children,
  className,
}: {
  hint?: string | null;
  children: React.ReactNode;
  className?: string;
}) {
  // No hint: render the child directly, but KEEP the caller's className. It is
  // usually layout (`ml-auto`, `w-full`), so dropping it silently moves the
  // control the moment a permission flips the hint off.
  if (!hint) {
    return className ? (
      <span className={cn("inline-flex", className)}>{children}</span>
    ) : (
      <>{children}</>
    );
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("inline-flex", className)} tabIndex={0}>
            {children}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[260px] text-center">
          {hint}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** Copy-to-clipboard button with a two-second confirmation state. */
export function CopyButton({
  value,
  label,
  className,
  variant = "secondary",
  size = "sm",
  onCopied,
}: {
  value: string;
  label?: string;
  className?: string;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  onCopied?: () => void;
}) {
  const { t } = useTranslation("proposals");
  const [copied, setCopied] = React.useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Clipboard API unavailable (insecure context) — fall back to selection.
      const el = document.createElement("textarea");
      el.value = value;
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      document.body.removeChild(el);
    }
    setCopied(true);
    onCopied?.();
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Button variant={variant} size={size} className={className} onClick={() => void copy()}>
      {copied ? <Check className="h-4 w-4 text-pos-text" /> : <Copy className="h-4 w-4" />}
      {label ?? (copied ? t("shared.copied") : t("shared.copy"))}
    </Button>
  );
}

/**
 * Quiet identifier chip for IDs / RUTs / CMF codes — Inter `tabular-nums` on a
 * hairline border (mono is retired from UI chrome). The `MonoChip` name is
 * kept as the primary export so existing importers compile unchanged; new code
 * may prefer a plain `<span className="tabular-nums text-ink-3">` where a chip
 * adds nothing.
 */
export function MonoChip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border border-line bg-bone px-1.5 py-0.5 text-[12px] leading-4 text-ink-2 tabular-nums",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** The Signal-era name for the same chip. */
export const IdChip = MonoChip;

/** Countdown pill: red at <= 4 days, amber otherwise (quote rule). */
export function DueBadge({ days }: { days: number | null }) {
  const { t } = useTranslation("quotes");
  if (days === null) return <span className="text-ink-3">—</span>;
  const variant: Variant = days < 0 ? "danger" : days <= 4 ? "danger" : days < 7 ? "warn" : "neutral";
  return (
    <Badge variant={variant} className="gap-1 tabular-nums">
      <Clock className="h-3 w-3" />
      {days < 0 ? t("due.overdue") : t("due.inDays", { count: days })}
    </Badge>
  );
}

/** Motion-enabled row for tables that animate in. */
export const MotionTr = motion.tr;

// =============================================================================
// Errors
// =============================================================================

interface ApiErrorShape {
  response?: { data?: { detail?: unknown } };
  message?: string;
}

/**
 * Pull a human message out of whatever the API threw. FastAPI sends either a
 * plain string `detail`, a structured `{code, message}` (our 409s), or a
 * pydantic validation array — all three are handled here.
 */
export function apiError(error: unknown, fallback = "Error"): string {
  if (!error) return fallback;
  if (typeof error === "string") return error;
  const err = error as ApiErrorShape;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    if (first?.msg) return first.msg;
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: string }).message;
    if (message) return message;
  }
  return err.message || fallback;
}
