/**
 * Collection level badge: native (Forpay, "En línea") · assisted ("Asistida") · record ("Registro").
 * v12 cross-lane contract — lane F2 owns it; F1 (Pólizas) and F2 (Cobranza) render it.
 * Props are frozen: keep the signature. `reason` is the server's Spanish `level_reason`,
 * shown as the tooltip — the broker learns WHY a cuota is at its level without a
 * single client-side rule (no onboarding status is ever exposed).
 *
 * Radix tooltips never open on touch, so a TAP toggles the reason (and does
 * not bubble to a tappable row); mouse and keyboard keep the hover/focus tooltip.
 *
 * Focus opens it ONLY for keyboard focus (`:focus-visible`): a sheet or dialog
 * that programmatically focuses its first tabbable on open must not pop the
 * reason over the panel's title.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { LEVEL_LABEL, labelOf } from "@/components/collections/labels";

export type CollectionLevel = "native" | "assisted" | "record";

export interface LevelBadgeProps {
  level: CollectionLevel | null | undefined;
  reason?: string | null;
  size?: "sm" | "md";
}

const TONE: Record<CollectionLevel, NonNullable<BadgeProps["variant"]>> = {
  native: "brand",
  assisted: "neutral",
  record: "outline",
};

export function LevelBadge({ level, reason, size = "sm" }: LevelBadgeProps) {
  const { t } = useTranslation("collections");
  const [open, setOpen] = React.useState(false);
  const touch = React.useRef<{ touch: boolean; wasOpen: boolean }>({ touch: false, wasOpen: false });
  if (!level) return null;
  const label = labelOf(t, LEVEL_LABEL, level, t("level.unknown"));
  const badge = (
    <Badge
      variant={TONE[level] ?? "outline"}
      dot={level === "native"}
      className={cn(
        size === "sm" ? "px-1.5 py-0 text-[11px] leading-[18px]" : undefined,
        reason ? "cursor-help" : undefined,
      )}
      aria-label={reason ? `${label}: ${reason}` : label}
    >
      {label}
    </Badge>
  );
  if (!reason) return badge;
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip open={open} onOpenChange={setOpen}>
        <TooltipTrigger asChild>
          <span
            className="inline-flex"
            tabIndex={0}
            onFocus={(e) => {
              // Radix composes handlers: preventDefault here skips its open-on-focus.
              let keyboard = true;
              try {
                keyboard = e.currentTarget.matches(":focus-visible");
              } catch {
                // Very old engines without :focus-visible keep the default.
              }
              if (!keyboard) e.preventDefault();
            }}
            onPointerDown={(e) => {
              touch.current = { touch: e.pointerType !== "mouse", wasOpen: open };
            }}
            onClick={(e) => {
              if (!touch.current.touch) return;
              // preventDefault skips Radix's own close-on-click; the tap toggles.
              e.preventDefault();
              e.stopPropagation();
              setOpen(!touch.current.wasOpen);
            }}
          >
            {badge}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[280px] text-pretty">
          {reason}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export default LevelBadge;
