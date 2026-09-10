/**
 * The **empresa picker** shared by "Crear un grupo" and the group's Empresas
 * tab, so the two can never drift the way a copied list does.
 *
 * Two rules shape it, and both come from the domain:
 *
 *  1. A company belongs to **at most one group**. A row already inside another
 *     group is therefore rendered disabled with its reason — offering it would
 *     be a dead button whose only possible answer is 422 `client_in_other_group`.
 *     Rows already inside *this* group are hidden: they are listed right below
 *     the picker, and re-offering them is noise.
 *  2. **A broker arrives with a RUT, not with a client row.** So when the search
 *     comes up empty the picker offers to create the empresa right here, seeded
 *     with whatever was typed (a valid RUT lands in the RUT field, anything else
 *     in the razón social). Sending the broker to Clientes and back was the dead
 *     end this component exists to remove.
 *
 * The picker is presentation + selection only: it never attaches anything. The
 * caller decides what selection means — `client_ids` at creation, or a
 * `POST /account-groups/{id}/clients` per pick from the Empresas tab.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useClients } from "@/api/clients";
import { useDebounced } from "@/pages/groups/shared";
import { CreateClientDialog } from "@/pages/clients/ClientForm";
import { formatRut, normalizeRut } from "@/pages/clients/rut";
import type { Client, ClientListItem } from "@/api/types";

export function ClientPicker({
  selectedIds,
  onToggle,
  groupId,
  disabled,
  /** Rendered under the list; the caller's hint about what a pick means. */
  hint,
  /** Wires an outer <Label htmlFor> to the search box. */
  inputId,
}: {
  selectedIds: number[];
  onToggle: (client: ClientListItem) => void;
  /**
   * The group being filled, when there is one. Its own members are hidden
   * (they are already in); every OTHER group's members are shown disabled.
   * Omitted at creation time — the group does not exist yet, so any company
   * with a group at all is unavailable.
   */
  groupId?: number;
  disabled?: boolean;
  hint?: React.ReactNode;
  inputId?: string;
}) {
  const { t } = useTranslation("accounts");
  const [term, setTerm] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const debounced = useDebounced(term);
  const clients = useClients({ q: debounced || undefined, page_size: 25 });

  // A valid RUT seeds the RUT field; anything else seeds the razón social.
  const typedRut = normalizeRut(term);
  const seed = typedRut
    ? { rut: typedRut }
    : term.trim()
      ? { legal_name: term.trim() }
      : undefined;

  const rows = (clients.data?.items ?? []).filter(
    (client) => client.account_group_id == null || client.account_group_id !== groupId,
  );

  /** A freshly created empresa is not in the cached page yet — select it now. */
  const onCreated = (client: Client) => {
    setTerm("");
    onToggle(client);
  };

  return (
    <div className="flex flex-col gap-2">
      <Input
        id={inputId}
        value={term}
        disabled={disabled}
        placeholder={t("group.searchClients")}
        onChange={(e) => setTerm(e.target.value)}
      />

      <div className="max-h-52 overflow-y-auto rounded-lg border border-line">
        {clients.isLoading ? (
          <div className="flex flex-col gap-1.5 p-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-start gap-2 px-3 py-4">
            <p className="text-caption text-ink-3">{t("group.noClients")}</p>
            <Button
              type="button"
              variant="accent-soft"
              size="sm"
              disabled={disabled}
              onClick={() => setCreating(true)}
            >
              <Plus className="h-3.5 w-3.5" />
              {typedRut
                ? t("group.createClient.withRut", { rut: formatRut(typedRut) })
                : t("group.createClient.action")}
            </Button>
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((client) => {
              const selected = selectedIds.includes(client.id);
              const taken = client.account_group_id != null;
              return (
                <li key={client.id}>
                  <button
                    type="button"
                    disabled={disabled || taken}
                    title={taken ? t("errors.client_in_other_group") : undefined}
                    onClick={() => onToggle(client)}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors duration-150 hover:bg-paper-2/60 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent",
                      selected && "bg-paper-2",
                    )}
                  >
                    <span
                      className={cn(
                        "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                        selected ? "border-brand bg-brand text-cta-foreground" : "border-line",
                      )}
                    >
                      {selected ? <Check className="h-3 w-3" /> : null}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-body text-ink">
                      {client.insured.legal_name}
                    </span>
                    {taken ? (
                      <Badge variant="muted">{t("group.inOtherGroup")}</Badge>
                    ) : null}
                    <span className="text-caption tabular-nums text-ink-3">
                      {formatRut(client.insured.rut)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        {hint ? <p className="text-caption text-ink-3">{hint}</p> : <span />}
        {rows.length > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => setCreating(true)}
          >
            <Plus className="h-3.5 w-3.5" />
            {t("group.createClient.action")}
          </Button>
        ) : null}
      </div>

      <CreateClientDialog
        open={creating}
        onOpenChange={setCreating}
        initial={seed}
        onCreated={onCreated}
      />
    </div>
  );
}
