/**
 * "Equipo" — marks a TEAM INBOX (review r3 F4 / R7, owner decision): pagos sin
 * confirmar, respuestas por confirmar and the pending evidence are worked by
 * the whole corredora, so they stay broker-wide even under "Mías" / "Mis
 * números". Shown to every user, so a number that does not shrink with "Mías"
 * never reads as a filter bug.
 *
 * Often rendered INSIDE a link or a card button: the chip itself is not
 * focusable (no nested interactive content). The hint is a hover tooltip on a
 * pointer device and screen-reader text everywhere; the visible word "Equipo"
 * carries the meaning on a phone.
 */
import { Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function TeamScopeChip({ className }: { className?: string }) {
  const { t } = useTranslation("common");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-md bg-paper-2 px-1.5 py-0.5 text-[11px] font-medium leading-4 text-ink-3",
            className,
          )}
        >
          <Users className="h-3 w-3" strokeWidth={1.75} aria-hidden />
          {t("scope.team")}
          <span className="sr-only">{t("scope.teamHint")}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[260px]">
        {t("scope.teamHint")}
      </TooltipContent>
    </Tooltip>
  );
}
