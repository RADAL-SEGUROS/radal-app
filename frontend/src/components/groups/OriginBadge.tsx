/**
 * How a folder came to exist — `case_file.origin` (spec v3 §2.3, §5.3).
 *
 * Origin is one of the three axes that never mix on a case file: **origin**
 * (this badge: new / renewal / period_change — sibling folders in time),
 * **version** (`supersedes_case_file_id`, a rework) and **post-sale**
 * (`parent_case_file_id` + `policy_id`). A renewal folder is a SIBLING of the
 * prior vigencia, never a child of it, and that is exactly what the operator
 * has to be able to read off the rail at a glance.
 *
 * ⚠️ `pages/proposals/shared.tsx` exports a DIFFERENT `OriginBadge` (an
 * insurer's native/external origin). They never appear in the same file today;
 * import this one by path, or use the `CaseOriginBadge` alias when both are
 * needed in one module.
 */
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { DisabledHint } from "@/pages/proposals/shared";
import type { CaseOrigin } from "@/api/types";

/** Renewal is the affirmative outcome; a period change is a correction. */
const TONE: Record<CaseOrigin, "neutral" | "brand" | "warn"> = {
  new: "neutral",
  renewal: "brand",
  period_change: "warn",
};

export function OriginBadge({
  origin,
  className,
  /** Show the one-line explanation from `accounts:originHint.*` on hover. */
  withHint = true,
}: {
  origin: CaseOrigin;
  className?: string;
  withHint?: boolean;
}) {
  const { t } = useTranslation("accounts");
  const badge = (
    <Badge variant={TONE[origin] ?? "neutral"} className={className}>
      {t(`origin.${origin}`)}
    </Badge>
  );
  if (!withHint) return badge;
  return <DisabledHint hint={t(`originHint.${origin}`)}>{badge}</DisabledHint>;
}

/** Explicit alias for modules that also import the insurer `OriginBadge`. */
export { OriginBadge as CaseOriginBadge };
