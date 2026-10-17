/**
 * Pick one of the broker's clients by name or RUT (`GET /clients?q=`).
 *
 * Inline (no popover) so it behaves inside dialogs and on a 375 px screen: a
 * search box, then up to eight tappable results under it. Once picked, the
 * client shows as a chip with "Cambiar". Gated on `Clients.View` from the
 * server — without it the box is visibly disabled with the reason.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiError } from "@/components/common/kit";
import { TouchHint } from "@/components/policies/TouchHint";
import { useCan } from "@/lib/permissions";
import { useClients } from "@/api/clients";
import { formatRut } from "@/pages/clients/rut";

export interface PickedClient {
  id: number;
  name: string;
  rut: string | null;
}

export function ClientPicker({
  value,
  onChange,
  disabled,
  id,
}: {
  value: PickedClient | null;
  onChange: (client: PickedClient | null) => void;
  disabled?: boolean;
  id?: string;
}) {
  const { t } = useTranslation("policies");
  const canView = useCan("Clients", "View");
  const [term, setTerm] = React.useState("");
  const [query, setQuery] = React.useState("");

  React.useEffect(() => {
    const handle = window.setTimeout(() => setQuery(term.trim()), 250);
    return () => window.clearTimeout(handle);
  }, [term]);

  const search = useClients(
    { q: query, page_size: 8 },
    canView.allowed && !value && query.length >= 2,
  );

  if (value) {
    return (
      <div className="flex min-h-10 items-center justify-between gap-2 rounded-sm border border-line bg-paper-2 px-3 py-2">
        <span className="min-w-0">
          <span className="block truncate text-body font-medium text-ink">{value.name}</span>
          {value.rut ? (
            <span className="block text-caption tabular-nums text-ink-3">{formatRut(value.rut)}</span>
          ) : null}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => {
            onChange(null);
            setTerm("");
          }}
        >
          <X className="h-4 w-4" />
          {t("clientPicker.change")}
        </Button>
      </div>
    );
  }

  const items = search.data?.items ?? [];
  return (
    <div className="flex flex-col gap-1.5">
      <TouchHint hint={canView.allowed ? null : t("noPermission.clients")} className="w-full">
        <div className="relative w-full">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <Input
            id={id}
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder={t("clientPicker.placeholder")}
            disabled={disabled || !canView.allowed}
            className="h-10 pl-9"
            autoComplete="off"
          />
          {search.isFetching ? (
            <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-ink-3" />
          ) : null}
        </div>
      </TouchHint>
      {/* The reason in plain text too — a phone never shows the tooltip. */}
      {!canView.isLoading && !canView.allowed ? (
        <p className="px-1 text-caption text-ink-3">{t("noPermission.clients")}</p>
      ) : null}
      {search.isError ? (
        <p role="alert" className="px-1 text-caption text-neg-text">
          {apiError(search.error, t("clientPicker.error"))}
        </p>
      ) : query.length >= 2 && !search.isLoading ? (
        items.length === 0 ? (
          <p className="px-1 text-caption text-ink-3">{t("clientPicker.none")}</p>
        ) : (
          <ul className="flex max-h-56 flex-col overflow-y-auto rounded-sm border border-line bg-bone">
            {items.map((c) => (
              <li key={c.id} className="border-b border-line last:border-b-0">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    onChange({ id: c.id, name: c.insured.legal_name, rut: c.insured.rut })
                  }
                  className="flex min-h-[44px] w-full items-center gap-2 px-3 py-2 text-left transition-[background-color] duration-150 ease-out hover:bg-paper-2"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-body text-ink">{c.insured.legal_name}</span>
                    <span className="block text-caption tabular-nums text-ink-3">
                      {formatRut(c.insured.rut)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}
