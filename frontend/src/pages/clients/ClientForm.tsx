import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { AxiosError } from "axios";
import { useCreateClient, useUpdateClient } from "@/api/clients";
import { useUsers } from "@/api/users";
import {
  CLIENT_STATUSES,
  PERSON_TYPES,
  type Client,
  type ClientCreate,
  type ClientUpdate,
} from "@/api/types";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { isValidRut, normalizeRut } from "@/pages/clients/rut";

/** Sentinel for "no account manager" — Radix Select forbids an empty value. */
const NONE = "__none__";

function serverMessage(error: unknown, fallback: string): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function Field({
  label,
  error,
  children,
  className,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label className="text-caption text-text-muted">{label}</Label>
      {children}
      {error ? <p className="text-caption text-neg-text">{error}</p> : null}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-1 text-caption font-medium text-ink-3">
      {children}
    </p>
  );
}

/** The broker's own users, for the account-manager picker. */
function useAccountManagers() {
  return useUsers({ user_type: "broker", is_active: true, limit: 200 });
}

// --- Create ------------------------------------------------------------------

const createSchema = z.object({
  rut: z.string().min(1).refine(isValidRut, { params: { i18n: "rut" } }),
  legal_name: z.string().min(1),
  person_type: z.enum(PERSON_TYPES),
  trade_name: z.string().optional(),
  tax_activity: z.string().optional(),
  insured_email: z.union([z.string().email(), z.literal("")]).optional(),
  insured_phone: z.string().optional(),
  insured_address: z.string().optional(),
  insured_commune: z.string().optional(),
  insured_region: z.string().optional(),
  status: z.enum(CLIENT_STATUSES),
  account_manager_id: z.string().optional(),
  sector: z.string().optional(),
  source: z.string().optional(),
  since: z.string().optional(),
  contact_name: z.string().optional(),
  contact_email: z.union([z.string().email(), z.literal("")]).optional(),
  contact_phone: z.string().optional(),
  internal_notes: z.string().optional(),
});

type CreateValues = z.infer<typeof createSchema>;

function blank(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export function CreateClientDialog({
  open,
  onOpenChange,
  onCreated,
  initial,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (client: Client) => void;
  /**
   * Seeds the identity fields when the dialog is opened from somewhere that
   * already knows them — the group picker hands over whatever the broker had
   * typed into the search box, so a fruitless search flows straight into the
   * creation instead of restarting it.
   */
  initial?: { rut?: string; legal_name?: string };
}) {
  const { t } = useTranslation(["clients", "common"]);
  const create = useCreateClient();
  const managers = useAccountManagers();

  const initialRut = initial?.rut ?? "";
  const initialName = initial?.legal_name ?? "";

  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      rut: initialRut,
      legal_name: initialName,
      person_type: "legal",
      status: "prospect",
      account_manager_id: NONE,
    },
  });

  React.useEffect(() => {
    if (open) form.reset({
      rut: initialRut,
      legal_name: initialName,
      person_type: "legal",
      status: "prospect",
      account_manager_id: NONE,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialRut, initialName]);

  const onSubmit = async (values: CreateValues) => {
    const payload: ClientCreate = {
      rut: normalizeRut(values.rut) ?? values.rut,
      legal_name: values.legal_name.trim(),
      person_type: values.person_type,
      trade_name: blank(values.trade_name),
      tax_activity: blank(values.tax_activity),
      insured_email: blank(values.insured_email),
      insured_phone: blank(values.insured_phone),
      insured_address: blank(values.insured_address),
      insured_commune: blank(values.insured_commune),
      insured_region: blank(values.insured_region),
      status: values.status,
      account_manager_id:
        values.account_manager_id && values.account_manager_id !== NONE
          ? Number(values.account_manager_id)
          : null,
      sector: blank(values.sector),
      source: blank(values.source),
      since: blank(values.since),
      contact_name: blank(values.contact_name),
      contact_email: blank(values.contact_email),
      contact_phone: blank(values.contact_phone),
      internal_notes: blank(values.internal_notes),
    };
    try {
      const client = await create.mutateAsync(payload);
      toast.success(t("clients:toast.created"));
      onOpenChange(false);
      onCreated?.(client);
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  const rutError = form.formState.errors.rut
    ? t("clients:form.errors.rut")
    : undefined;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("clients:form.createTitle")}</DialogTitle>
          <DialogDescription>{t("clients:form.createSubtitle")}</DialogDescription>
        </DialogHeader>

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-3.5"
        >
          <SectionTitle>{t("clients:form.sections.insured")}</SectionTitle>
          <div className="grid gap-3.5 sm:grid-cols-2">
            <Field label={t("clients:fields.rut")} error={rutError}>
              <Input placeholder="76.086.428-5" {...form.register("rut")} />
            </Field>
            <Field
              label={t("clients:fields.personType")}
            >
              <Select
                value={form.watch("person_type")}
                onValueChange={(value) =>
                  form.setValue("person_type", value as CreateValues["person_type"])
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERSON_TYPES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`clients:personType.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field
              label={t("clients:fields.legalName")}
              error={
                form.formState.errors.legal_name
                  ? t("clients:form.errors.required")
                  : undefined
              }
              className="sm:col-span-2"
            >
              <Input {...form.register("legal_name")} />
            </Field>
            <Field label={t("clients:fields.tradeName")}>
              <Input {...form.register("trade_name")} />
            </Field>
            <Field label={t("clients:fields.taxActivity")}>
              <Input {...form.register("tax_activity")} />
            </Field>
            <Field
              label={t("clients:fields.insuredEmail")}
              error={
                form.formState.errors.insured_email
                  ? t("clients:form.errors.email")
                  : undefined
              }
            >
              <Input type="email" {...form.register("insured_email")} />
            </Field>
            <Field label={t("clients:fields.insuredPhone")}>
              <Input {...form.register("insured_phone")} />
            </Field>
            <Field label={t("clients:fields.address")} className="sm:col-span-2">
              <Input {...form.register("insured_address")} />
            </Field>
            <Field label={t("clients:fields.commune")}>
              <Input {...form.register("insured_commune")} />
            </Field>
            <Field label={t("clients:fields.region")}>
              <Input {...form.register("insured_region")} />
            </Field>
          </div>

          <SectionTitle>{t("clients:form.sections.crm")}</SectionTitle>
          <div className="grid gap-3.5 sm:grid-cols-2">
            <Field label={t("clients:fields.status")}>
              <Select
                value={form.watch("status")}
                onValueChange={(value) =>
                  form.setValue("status", value as CreateValues["status"])
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLIENT_STATUSES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`clients:status.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label={t("clients:fields.accountManager")}>
              <Select
                value={form.watch("account_manager_id") ?? NONE}
                onValueChange={(value) => form.setValue("account_manager_id", value)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>
                    {t("clients:fields.unassigned")}
                  </SelectItem>
                  {(managers.data?.items ?? []).map((user) => (
                    <SelectItem key={user.id} value={String(user.id)}>
                      {user.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label={t("clients:fields.sector")}>
              <Input {...form.register("sector")} />
            </Field>
            <Field label={t("clients:fields.source")}>
              <Input {...form.register("source")} />
            </Field>
            <Field label={t("clients:fields.since")}>
              <Input type="date" {...form.register("since")} />
            </Field>
            <Field label={t("clients:fields.contactName")}>
              <Input {...form.register("contact_name")} />
            </Field>
            <Field
              label={t("clients:fields.contactEmail")}
              error={
                form.formState.errors.contact_email
                  ? t("clients:form.errors.email")
                  : undefined
              }
            >
              <Input type="email" {...form.register("contact_email")} />
            </Field>
            <Field label={t("clients:fields.contactPhone")}>
              <Input {...form.register("contact_phone")} />
            </Field>
            <Field label={t("clients:fields.internalNotes")} className="sm:col-span-2">
              <textarea
                rows={3}
                className="w-full rounded-lg border border-line bg-bone px-3.5 py-2 text-body text-ink outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-text-muted focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand-ring"
                {...form.register("internal_notes")}
              />
            </Field>
          </div>

          <DialogFooter className="mt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {t("common:actions.cancel")}
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? <Loader2 className="animate-spin" /> : null}
              {t("common:actions.create")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// --- Edit --------------------------------------------------------------------

const editSchema = z.object({
  status: z.enum(CLIENT_STATUSES),
  account_manager_id: z.string().optional(),
  sector: z.string().optional(),
  source: z.string().optional(),
  since: z.string().optional(),
  contact_name: z.string().optional(),
  contact_email: z.union([z.string().email(), z.literal("")]).optional(),
  contact_phone: z.string().optional(),
  internal_notes: z.string().optional(),
});

type EditValues = z.infer<typeof editSchema>;

export function EditClientDialog({
  client,
  open,
  onOpenChange,
}: {
  client: Client;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["clients", "common"]);
  const update = useUpdateClient(client.id);
  const managers = useAccountManagers();

  const defaults = React.useMemo<EditValues>(
    () => ({
      status: client.status,
      account_manager_id: client.account_manager
        ? String(client.account_manager.id)
        : NONE,
      sector: client.sector ?? "",
      source: client.source ?? "",
      since: client.since ?? "",
      contact_name: client.contact_name ?? "",
      contact_email: client.contact_email ?? "",
      contact_phone: client.contact_phone ?? "",
      internal_notes: client.internal_notes ?? "",
    }),
    [client],
  );

  const form = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    if (open) form.reset(defaults);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaults]);

  const onSubmit = async (values: EditValues) => {
    const payload: ClientUpdate = {
      status: values.status,
      account_manager_id:
        values.account_manager_id && values.account_manager_id !== NONE
          ? Number(values.account_manager_id)
          : null,
      sector: blank(values.sector),
      source: blank(values.source),
      since: blank(values.since),
      contact_name: blank(values.contact_name),
      contact_email: blank(values.contact_email),
      contact_phone: blank(values.contact_phone),
      internal_notes: blank(values.internal_notes),
    };
    try {
      await update.mutateAsync(payload);
      toast.success(t("common:toast.saved"));
      onOpenChange(false);
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("clients:form.editTitle")}</DialogTitle>
          <DialogDescription>{t("clients:form.editSubtitle")}</DialogDescription>
        </DialogHeader>

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="grid gap-3.5 sm:grid-cols-2"
        >
          <Field label={t("clients:fields.status")}>
            <Select
              value={form.watch("status")}
              onValueChange={(value) =>
                form.setValue("status", value as EditValues["status"])
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CLIENT_STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`clients:status.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("clients:fields.accountManager")}>
            <Select
              value={form.watch("account_manager_id") ?? NONE}
              onValueChange={(value) => form.setValue("account_manager_id", value)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>{t("clients:fields.unassigned")}</SelectItem>
                {(managers.data?.items ?? []).map((user) => (
                  <SelectItem key={user.id} value={String(user.id)}>
                    {user.full_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("clients:fields.sector")}>
            <Input {...form.register("sector")} />
          </Field>
          <Field label={t("clients:fields.source")}>
            <Input {...form.register("source")} />
          </Field>
          <Field label={t("clients:fields.since")}>
            <Input type="date" {...form.register("since")} />
          </Field>
          <Field label={t("clients:fields.contactName")}>
            <Input {...form.register("contact_name")} />
          </Field>
          <Field
            label={t("clients:fields.contactEmail")}
            error={
              form.formState.errors.contact_email
                ? t("clients:form.errors.email")
                : undefined
            }
          >
            <Input type="email" {...form.register("contact_email")} />
          </Field>
          <Field label={t("clients:fields.contactPhone")}>
            <Input {...form.register("contact_phone")} />
          </Field>
          <Field label={t("clients:fields.internalNotes")} className="sm:col-span-2">
            <textarea
              rows={3}
              className="w-full rounded-lg border border-line bg-bone px-3.5 py-2 text-body text-ink outline-none transition-[border-color,box-shadow] duration-150 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand-ring"
              {...form.register("internal_notes")}
            />
          </Field>

          <DialogFooter className="sm:col-span-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {t("common:actions.cancel")}
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? <Loader2 className="animate-spin" /> : null}
              {t("common:actions.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
