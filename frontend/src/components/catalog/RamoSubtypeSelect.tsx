/**
 * Ramo → subtipo cascade over the closed catalogue (v12, team rule R4).
 *
 * Ramos and subtipos are ONE global Radal catalogue (`GET /catalog/lines`):
 * no corredora creates, edits or deletes them, so this control only ever
 * SELECTS. Two selects, side by side from `sm` and stacked on a phone: first
 * the ramo, then the subtipo of that ramo. The value is the subtipo id — the
 * server derives the ramo from it (`insurance_subtype_id`).
 *
 * Choosing a ramo with a single subtipo selects it straight away (the team's
 * "genérico del ramo"); changing the ramo clears a subtipo of another ramo.
 * `showSuggestedRoute` prints the subtipo's suggested vía (R37: it suggests,
 * it never obliges) under the selects.
 *
 * Props are frozen by the v12 orchestration contract (F1 and F5 both use it).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Compass } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  findSubtype,
  useCatalogLines,
  type PlacementRoute,
  type SuggestedEntry,
} from "@/api/catalog";

export interface RamoSubtypeSelectProps {
  value: number | null;
  onChange: (subtypeId: number | null) => void;
  disabled?: boolean;
  /** Show the suggested-route chip under the selects. */
  showSuggestedRoute?: boolean;
  id?: string;
}

/** Explicit label keys — never `t(\`route.${x}\`)` (dynamic keys render raw). */
export const ROUTE_LABEL_KEY: Record<PlacementRoute, string> = {
  traditional: "catalog:route.traditional",
  digital: "catalog:route.digital",
  manual: "catalog:route.manual",
};

export const ROUTE_HINT_KEY: Record<PlacementRoute, string> = {
  traditional: "catalog:route.hints.traditional",
  digital: "catalog:route.hints.digital",
  manual: "catalog:route.hints.manual",
};

export const ENTRY_LABEL_KEY: Record<SuggestedEntry, string> = {
  market: "catalog:entry.market",
  portal: "catalog:entry.portal",
};

export const ROUTES: readonly PlacementRoute[] = ["traditional", "digital", "manual"];

export function RamoSubtypeSelect({
  value,
  onChange,
  disabled,
  showSuggestedRoute,
  id,
}: RamoSubtypeSelectProps) {
  const { t } = useTranslation("catalog");
  const catalog = useCatalogLines();
  const lines = catalog.data;

  const selected = findSubtype(lines, value);
  // The ramo is local state only while no subtipo is chosen yet; once a
  // subtipo is selected, ITS ramo is the truth (the parent may set `value`).
  const [pickedLine, setPickedLine] = React.useState<number | null>(
    selected?.line.id ?? null,
  );
  const selectedLineId = selected?.line.id ?? null;
  React.useEffect(() => {
    if (selectedLineId != null) setPickedLine(selectedLineId);
  }, [selectedLineId]);

  const lineId = selectedLineId ?? pickedLine;
  const line = lines?.find((item) => item.id === lineId) ?? null;
  const subtypeId = id ? `${id}-subtype` : undefined;

  if (catalog.isLoading) {
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        <Skeleton className="h-10 w-full sm:h-9" />
        <Skeleton className="h-10 w-full sm:h-9" />
      </div>
    );
  }

  if (catalog.isError) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-well border border-line bg-paper-2/60 px-3 py-2.5 text-caption text-ink-2">
        <span className="min-w-0 flex-1">{t("select.error")}</span>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => void catalog.refetch()}
        >
          {t("select.retry")}
        </Button>
      </div>
    );
  }

  if (!lines || lines.length === 0) {
    return (
      <p className="rounded-well border border-line bg-paper-2/60 px-3 py-2.5 text-caption text-ink-3">
        {t("select.empty")}
      </p>
    );
  }

  const onLine = (next: string) => {
    const nextId = Number(next);
    setPickedLine(nextId);
    const nextLine = lines.find((item) => item.id === nextId);
    if (!nextLine) return;
    // A single subtipo IS the ramo's generic one — select it straight away.
    if (nextLine.subtypes.length === 1) {
      onChange(nextLine.subtypes[0].id);
      return;
    }
    if (selected && selected.line.id !== nextId) onChange(null);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="grid gap-2 sm:grid-cols-2">
        <Select
          value={lineId != null ? String(lineId) : ""}
          onValueChange={onLine}
          disabled={disabled}
        >
          <SelectTrigger
            id={id}
            aria-label={t("select.ramo")}
            className="h-10 sm:h-9"
          >
            <SelectValue placeholder={t("select.ramoPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {lines.map((item) => (
              <SelectItem key={item.id} value={String(item.id)}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={selected ? String(selected.subtype.id) : ""}
          onValueChange={(next) => onChange(next ? Number(next) : null)}
          disabled={disabled || !line}
        >
          <SelectTrigger
            id={subtypeId}
            aria-label={t("select.subtype")}
            className="h-10 sm:h-9"
          >
            <SelectValue
              placeholder={line ? t("select.subtypePlaceholder") : t("select.subtypeFirst")}
            />
          </SelectTrigger>
          <SelectContent>
            {(line?.subtypes ?? []).map((item) => (
              <SelectItem key={item.id} value={String(item.id)}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {showSuggestedRoute && selected ? (
        <p
          className={cn(
            "inline-flex w-fit flex-wrap items-center gap-1.5 rounded-full bg-brand-soft px-2.5 py-1",
            "text-caption text-brand-deep",
          )}
        >
          <Compass className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {t("route.suggested")}:{" "}
            <span className="font-medium">
              {t(ROUTE_LABEL_KEY[selected.subtype.suggested_route])}
            </span>
          </span>
          <span className="text-brand-deep/80">
            · {t(ENTRY_LABEL_KEY[selected.subtype.suggested_entry])}
          </span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * The vía of ONE colocación — Tradicional · Digital · Manual — as a small
 * segmented control. `value=null` means "follow the subtipo's suggestion";
 * the suggested option carries a quiet "sugerida" mark (R37).
 */
export function RoutePicker({
  value,
  suggested,
  onChange,
  disabled,
  id,
}: {
  value: PlacementRoute | null;
  suggested: PlacementRoute | null;
  onChange: (route: PlacementRoute) => void;
  disabled?: boolean;
  id?: string;
}) {
  const { t } = useTranslation("catalog");
  const effective = value ?? suggested;
  return (
    <div
      id={id}
      role="radiogroup"
      aria-label={t("route.label")}
      className="inline-flex w-full flex-wrap gap-1 rounded-[10px] bg-paper-2 p-1 sm:w-auto"
    >
      {ROUTES.map((route) => {
        const active = effective === route;
        return (
          <button
            key={route}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            title={t(ROUTE_HINT_KEY[route])}
            onClick={() => onChange(route)}
            className={cn(
              "inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-sm px-3 text-caption font-medium sm:min-h-8 sm:flex-none",
              "transition-[background-color,color,box-shadow,scale] duration-150 ease-out active:scale-[0.96]",
              "disabled:cursor-not-allowed disabled:opacity-50",
              active ? "bg-bone text-ink shadow-elev" : "text-ink-3 hover:text-ink",
            )}
          >
            {t(ROUTE_LABEL_KEY[route])}
            {suggested === route ? (
              <span className="text-[10px] font-normal uppercase tracking-wide text-brand-deep">
                {t("route.suggestedMark")}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export default RamoSubtypeSelect;
