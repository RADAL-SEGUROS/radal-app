/**
 * "¿Qué compañía…?" — the catalogue insurer picker shared by Subir póliza
 * (the PDF named the insurer only by name) and Promover a propuesta (review
 * r2 C3/D3): the server's suggestions for the name read come FIRST, then the
 * whole catalogue (`GET /insurers`, which needs Insurers:View — without it the
 * suggestions stay pickable and the reason is shown, never a silent dead
 * control).
 *
 * Copy lives in the `policies` namespace (`upload.insurer.*`).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TouchHint } from "@/components/policies/TouchHint";
import { apiError } from "@/components/common/kit";
import { useInsurers } from "@/api/insurers";
import { useCan } from "@/lib/permissions";

export interface InsurerChoice {
  id: number;
  name: string;
}

interface InsurerOption extends InsurerChoice {
  suggested: boolean;
}

export interface InsurerCatalog {
  suggested: InsurerOption[];
  rest: InsurerOption[];
  /** Why the select cannot be used at all (no options) — or null. */
  blocked: string | null;
  /** A quieter line under a usable select (only suggestions, loading…). */
  note: string | null;
}

/** The options + the honest reason a caller also gates its submit on. */
export function useInsurerCatalog(suggestions: InsurerChoice[]): InsurerCatalog {
  const { t } = useTranslation("policies");
  const canInsurers = useCan("Insurers", "View");
  const insurers = useInsurers({ limit: 200 }, canInsurers.allowed);

  return React.useMemo(() => {
    const ids = new Set(suggestions.map((s) => s.id));
    const suggested = suggestions.map((s) => ({ id: s.id, name: s.name, suggested: true }));
    const rest = (insurers.data?.items ?? [])
      .filter((ins) => !ids.has(ins.id))
      .map((ins) => ({ id: ins.id, name: ins.trade_name ?? ins.legal_name, suggested: false }));
    const listing = canInsurers.allowed && insurers.isLoading;
    const hasOptions = suggested.length + rest.length > 0;
    const blocked = hasOptions
      ? null
      : !canInsurers.allowed
        ? t("noPermission.insurers")
        : listing
          ? t("upload.insurer.loading")
          : insurers.isError
            ? apiError(insurers.error)
            : t("upload.insurer.empty");
    const note = blocked
      ? null
      : !canInsurers.allowed
        ? t("upload.insurer.onlySuggestions")
        : listing
          ? t("upload.insurer.loading")
          : insurers.isError
            ? apiError(insurers.error)
            : null;
    return { suggested, rest, blocked, note };
  }, [
    canInsurers.allowed,
    insurers.data?.items,
    insurers.error,
    insurers.isError,
    insurers.isLoading,
    suggestions,
    t,
  ]);
}

export function InsurerCatalogSelect({
  id,
  catalog,
  value,
  onChange,
}: {
  id: string;
  catalog: InsurerCatalog;
  /** The selected insurer id as a string ("" = none). */
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation("policies");
  const { suggested, rest, blocked, note } = catalog;

  return (
    <>
      <TouchHint hint={blocked} className="w-full">
        <Select value={value || undefined} onValueChange={onChange} disabled={!!blocked}>
          <SelectTrigger id={id} className="h-10 w-full" aria-label={t("upload.insurer.label")}>
            <SelectValue placeholder={t("upload.insurer.placeholder")} />
          </SelectTrigger>
          <SelectContent>
            {suggested.length > 0 ? (
              <SelectGroup>
                <SelectLabel>{t("upload.insurer.suggestedGroup")}</SelectLabel>
                {suggested.map((o) => (
                  <SelectItem key={o.id} value={String(o.id)}>
                    {o.name}
                    <span className="ml-1.5 text-caption font-medium text-brand-deep">
                      · {t("upload.insurer.suggested")}
                    </span>
                  </SelectItem>
                ))}
              </SelectGroup>
            ) : null}
            {suggested.length > 0 && rest.length > 0 ? <SelectSeparator /> : null}
            {rest.length > 0 ? (
              <SelectGroup>
                <SelectLabel>{t("upload.insurer.allGroup")}</SelectLabel>
                {rest.map((o) => (
                  <SelectItem key={o.id} value={String(o.id)}>
                    {o.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            ) : null}
          </SelectContent>
        </Select>
      </TouchHint>
      {/* Visible, not only a tooltip: on a phone the reason must show. */}
      {blocked || note ? <span className="text-caption text-ink-3">{blocked ?? note}</span> : null}
    </>
  );
}
