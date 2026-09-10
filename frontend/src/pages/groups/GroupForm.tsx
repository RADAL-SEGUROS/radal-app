/**
 * The "create a group" form, in the two shapes the spec asks for: a dialog on
 * the list page (§5.4 "DataTable + search + NewGroupDialog") and the full page
 * at `/groups/new`. One set of fields, one mutation, two frames — so the two
 * can never drift the way a copied form does.
 *
 * A group is a broker-private LABEL: it carries no money, no stage — and no RUT
 * of its own (the companies inside it hold those). The form is a name, optional
 * notes, and the companies (empresas) that move into it, picked with the shared
 * `ClientPicker` — which disables the ones already in a group (attaching one
 * returns 422 `client_in_other_group`) and can create an empresa by RUT inline,
 * so arriving with a RUT and no client row is no longer a dead end.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Building2 } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import { ErrorBanner } from "@/components/common/kit";
import { IconPicker } from "@/components/groups/IconPicker";
import { ClientPicker } from "@/components/groups/ClientPicker";
import { accountErrorMessage } from "@/pages/groups/shared";
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

/** Name + notes + the shared company picker. */
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
        <div className="mt-1.5">
          <ClientPicker
            inputId="group-clients"
            selectedIds={value.clientIds}
            onToggle={(client) => toggle(client.id)}
            disabled={disabled}
            hint={t("group.create.clientsHint")}
          />
        </div>
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
