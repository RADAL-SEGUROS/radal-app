/**
 * The "create a group" form, in the two shapes the spec asks for: a dialog on
 * the list page (§5.4 "DataTable + search + NewGroupDialog") and the full page
 * at `/groups/new`. One set of fields, one mutation, two frames — so the two
 * can never drift the way a copied form does.
 *
 * A group is a broker-private LABEL: it carries no money, no stage — and no RUT
 * of its own (the companies inside it hold those). The form is a name, optional
 * notes, and the companies (empresas) that move into it. A company belongs to
 * at most one group, which is why the picker only offers ones not in a group
 * already — attaching one that is returns 422 `client_in_other_group`, and the
 * honest fix is to not offer it.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Building2, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { ErrorBanner } from "@/components/common/kit";
import { IconPicker } from "@/components/groups/IconPicker";
import { accountErrorMessage, useDebounced } from "@/pages/groups/shared";
import { useClients } from "@/api/clients";
import { useCreateGroup, useUpdateGroup } from "@/api/accountGroups";
import type {
  AccountGroupDetail,
  AccountGroupIcon,
  AccountGroupIconInput,
} from "@/api/types";

export interface GroupFormState {
  name: string;
  notes: string;
  clientIds: number[];
  /** `null` = untouched (edit keeps the stored icon; create keeps none). */
  icon: AccountGroupIconInput | null;
}

export const EMPTY_GROUP_FORM: GroupFormState = {
  name: "",
  notes: "",
  clientIds: [],
  icon: null,
};

/**
 * Name + notes + the company picker.
 *
 * The picker lists the broker's clients and marks the ones already in a group
 * as unavailable, using `client.account_group_id` when the server sends it. It
 * is not sent today (`ClientRead` has no such field yet), so until it is, every
 * client is offered and a wrong pick surfaces as the translated 422 rather than
 * as a silent no-op.
 */
export function GroupFormFields({
  value,
  onChange,
  disabled,
  currentIcon,
}: {
  value: GroupFormState;
  onChange: (next: GroupFormState) => void;
  disabled?: boolean;
  /** The stored icon (edit mode) shown while `value.icon` is untouched. */
  currentIcon?: AccountGroupIcon | null;
}) {
  const { t } = useTranslation("accounts");
  const [term, setTerm] = React.useState("");
  const debounced = useDebounced(term);
  const clients = useClients({ q: debounced || undefined, page_size: 25 });

  const toggle = (id: number) => {
    const has = value.clientIds.includes(id);
    onChange({
      ...value,
      clientIds: has
        ? value.clientIds.filter((item) => item !== id)
        : [...value.clientIds, id],
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label>{t("group.icon.label")}</Label>
        <div className="mt-1.5">
          <IconPicker
            name={value.name}
            value={value.icon}
            currentIcon={currentIcon}
            disabled={disabled}
            onChange={(icon) => onChange({ ...value, icon })}
          />
        </div>
      </div>

      <div>
        <Label htmlFor="group-name">{t("group.create.nameLabel")}</Label>
        <Input
          id="group-name"
          value={value.name}
          disabled={disabled}
          autoFocus
          onChange={(e) => onChange({ ...value, name: e.target.value })}
          className="mt-1.5"
        />
        <p className="mt-1.5 text-caption text-ink-3">{t("group.fields.nameHint")}</p>
      </div>

      <div>
        <Label htmlFor="group-notes">{t("group.fields.notes")}</Label>
        <Textarea
          id="group-notes"
          rows={3}
          value={value.notes}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, notes: e.target.value })}
          className="mt-1.5"
        />
      </div>

      <div>
        <Label htmlFor="group-clients">{t("group.create.clients")}</Label>
        <Input
          id="group-clients"
          value={term}
          disabled={disabled}
          placeholder={t("group.searchClients")}
          onChange={(e) => setTerm(e.target.value)}
          className="mt-1.5"
        />

        <div className="mt-2 max-h-52 overflow-y-auto rounded-lg border border-line">
          {clients.isLoading ? (
            <div className="flex flex-col gap-1.5 p-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : (clients.data?.items ?? []).length === 0 ? (
            <p className="px-3 py-4 text-caption text-ink-3">
              {t("group.noClients")}
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {(clients.data?.items ?? []).map((client) => {
                const selected = value.clientIds.includes(client.id);
                return (
                  <li key={client.id}>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => toggle(client.id)}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors duration-150 hover:bg-paper-2/60 disabled:opacity-60",
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
                      <span className="text-caption tabular-nums text-ink-3">
                        {client.insured.rut}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <p className="mt-1.5 text-caption text-ink-3">
          {t("group.create.clientsHint")}
        </p>
      </div>
    </div>
  );
}

/** Shared submit path — the dialog and the page both go through this. */
export function useGroupCreate(onDone: (group: AccountGroupDetail) => void) {
  const { t } = useTranslation("accounts");
  const [state, setState] = React.useState<GroupFormState>(EMPTY_GROUP_FORM);
  const create = useCreateGroup();

  const submit = () => {
    const name = state.name.trim();
    if (!name || create.isPending) return;
    create.mutate(
      {
        name,
        notes: state.notes.trim() || null,
        icon: state.icon,
        client_ids: state.clientIds.length ? state.clientIds : null,
      },
      {
        onSuccess: (group) => {
          setState(EMPTY_GROUP_FORM);
          onDone(group);
        },
      },
    );
  };

  return {
    state,
    setState,
    submit,
    isPending: create.isPending,
    error: create.isError
      ? accountErrorMessage(create.error, (key, options) => t(key, options))
      : null,
    canSubmit: state.name.trim().length > 0,
  };
}

export function NewGroupDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onCreated: (group: AccountGroupDetail) => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const form = useGroupCreate((group) => {
    onOpenChange(false);
    onCreated(group);
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-brand" />
            {t("group.create.title")}
          </DialogTitle>
          <DialogDescription>{t("group.subtitle")}</DialogDescription>
        </DialogHeader>

        {form.error ? <ErrorBanner error={form.error} /> : null}

        <GroupFormFields
          value={form.state}
          onChange={form.setState}
          disabled={form.isPending}
        />

        <DialogFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={form.isPending}
          >
            {tc("actions.cancel")}
          </Button>
          <Button size="sm" onClick={form.submit} disabled={!form.canSubmit || form.isPending}>
            {t("group.create.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Edit a group's identity: its avatar, name and notes. Membership is NOT here —
 * a company is attached/detached through its own endpoint (the Empresas tab),
 * so this dialog never grows a client picker that could not save.
 *
 * The icon starts `null` = untouched: an already-stored image icon comes back
 * on read without its `document_id`, so leaving it alone is the only faithful
 * default; picking a new one overrides it.
 */
export function EditGroupDialog({
  group,
  open,
  onOpenChange,
}: {
  group: AccountGroupDetail;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const update = useUpdateGroup(group.id);

  const [name, setName] = React.useState(group.name);
  const [notes, setNotes] = React.useState(group.notes ?? "");
  const [icon, setIcon] = React.useState<AccountGroupIconInput | null>(null);

  // Re-seed when the dialog opens for a (possibly different) group.
  React.useEffect(() => {
    if (open) {
      setName(group.name);
      setNotes(group.notes ?? "");
      setIcon(null);
    }
  }, [open, group.id, group.name, group.notes]);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed || update.isPending) return;
    update.mutate(
      {
        name: trimmed,
        notes: notes.trim() || null,
        // Omit when untouched so the stored icon survives.
        icon: icon ?? undefined,
      },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-brand" />
            {t("group.edit.title")}
          </DialogTitle>
          <DialogDescription>{t("group.edit.subtitle")}</DialogDescription>
        </DialogHeader>

        {update.isError ? (
          <ErrorBanner
            error={accountErrorMessage(update.error, (key, options) => t(key, options))}
          />
        ) : null}

        <div className="flex flex-col gap-4">
          <div>
            <Label>{t("group.icon.label")}</Label>
            <div className="mt-1.5">
              <IconPicker
                name={name}
                value={icon}
                currentIcon={group.icon}
                disabled={update.isPending}
                onChange={setIcon}
              />
            </div>
          </div>

          <div>
            <Label htmlFor="edit-group-name">{t("group.create.nameLabel")}</Label>
            <Input
              id="edit-group-name"
              value={name}
              disabled={update.isPending}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              className="mt-1.5"
            />
          </div>

          <div>
            <Label htmlFor="edit-group-notes">{t("group.fields.notes")}</Label>
            <Textarea
              id="edit-group-notes"
              rows={3}
              value={notes}
              disabled={update.isPending}
              onChange={(e) => setNotes(e.target.value)}
              className="mt-1.5"
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={update.isPending}
          >
            {tc("actions.cancel")}
          </Button>
          <Button
            size="sm"
            onClick={submit}
            disabled={!name.trim() || update.isPending}
          >
            {tc("actions.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
