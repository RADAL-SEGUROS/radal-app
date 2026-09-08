/**
 * The context chips rendered above the composer input: entity-kind icon +
 * label (`EXP-2026-0004 · Coccolino`), `×` to remove.
 */
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { AgentChip, RefIcon } from "@/components/agent/refs";

export function ContextChips({
  chips,
  onRemove,
  className,
}: {
  chips: AgentChip[];
  onRemove: (key: string) => void;
  className?: string;
}) {
  const { t } = useTranslation("agent");
  if (chips.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {chips.map((chip) => (
        <span
          key={chip.key}
          className="inline-flex max-w-full items-center gap-1.5 rounded-[5px] bg-brand-soft py-[3px] pe-1 ps-2 text-caption text-brand-deep"
        >
          <RefIcon entityType={chip.ref.entity_type} className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate font-mono text-mono">
            {chip.label}
            {chip.sublabel ? (
              <span className="text-[color-mix(in_srgb,var(--brand-deep)_65%,transparent)]">
                {" · "}
                {chip.sublabel}
              </span>
            ) : null}
          </span>
          <button
            type="button"
            aria-label={t("chips.remove", { label: chip.label })}
            onClick={() => onRemove(chip.key)}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[4px] text-brand-deep transition-[background-color] duration-150 ease-out hover:bg-[color-mix(in_srgb,var(--brand)_18%,transparent)]"
          >
            <X className="h-3 w-3" strokeWidth={2} aria-hidden />
          </button>
        </span>
      ))}
    </div>
  );
}
