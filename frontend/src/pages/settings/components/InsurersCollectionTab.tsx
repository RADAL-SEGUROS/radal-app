import * as React from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Building2, Loader2, Lock, Mail, Pencil, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import { LevelBadge } from "@/components/collections/LevelBadge";
import { INSTRUCTION_KEYS, type InstructionKey, type PaymentChannel, type PaymentInstructions } from "@/api/platform";
import { useCan } from "@/lib/permissions";
import { EmptyState, Field, GuardedButton, type Guard } from "./shared";
import type { TFn } from "./cadence";
import {
  useInsurerCapabilities,
  useUpdateInsurerOverride,
  type InsurerCapability,
  type InsurerScope,
} from "../api";
import { safeHttpUrl } from "@/lib/url";
import { formatRut, isValidRut, normalizeRut } from "@/pages/clients/rut";

/**
 * Configuración → Compañías y cobro (v12, 03 §4, decision 4).
 *
 * Per insurer the broker sees what it can DO — the level available (native
 * "En línea" or assisted), the live channels, the merged payment instructions
 * (RADAL profile ⊕ this broker's override) and the collections email — and
 * may override contact, instructions field by field, and opt out of native.
 * It NEVER sees the onboarding status (the server does not send it).
 */

// Explicit maps — no dynamic i18n keys.
export const INSTRUCTION_LABEL: Record<InstructionKey, string> = {
  portal_url: "settings:insurersTab.field.portal_url",
  bank_name: "settings:insurersTab.field.bank_name",
  account_type: "settings:insurersTab.field.account_type",
  account_number: "settings:insurersTab.field.account_number",
  account_holder: "settings:insurersTab.field.account_holder",
  account_rut: "settings:insurersTab.field.account_rut",
  transfer_email: "settings:insurersTab.field.transfer_email",
  receipt_email: "settings:insurersTab.field.receipt_email",
  notes: "settings:insurersTab.field.notes",
};

const CHANNEL_LABEL: Record<PaymentChannel, string> = {
  webpay: "settings:insurersTab.channel.webpay",
  oneclick: "settings:insurersTab.channel.oneclick",
  bank_transfer: "settings:insurersTab.channel.bank_transfer",
  direct_debit: "settings:insurersTab.channel.direct_debit",
};

export function InsurersCollectionTab() {
  const { t: rawT } = useTranslation(["settings", "common"]);
  const t = rawT as unknown as TFn;
  const canView = useCan("Collections", "View");
  const canManage = useCan("Settings", "Manage");
  const [scope, setScope] = React.useState<InsurerScope>("mine");
  const [q, setQ] = React.useState("");
  const [editing, setEditing] = React.useState<InsurerCapability | null>(null);
  const list = useInsurerCapabilities(scope, canView.allowed);

  if (!canView.isLoading && !canView.allowed) {
    return (
      <Card className="p-6">
        <EmptyState>{t("settings:insurersTab.noView")}</EmptyState>
      </Card>
    );
  }

  const guard: Guard = { allowed: canManage.allowed, reason: t("settings:insurersTab.editReason") };
  const needle = q.trim().toLowerCase();
  const rows = (list.data ?? []).filter((row) => {
    if (!needle) return true;
    return [row.insurer.name, row.insurer.legal_name, row.insurer.rut ?? ""]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });

  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-3xl text-body text-ink-3">{t("settings:insurersTab.description")}</p>
      {!canManage.isLoading && !canManage.allowed ? (
        <p
          role="note"
          className="flex max-w-3xl items-start gap-2 rounded-sm bg-paper-2 px-3 py-2.5 text-caption text-ink-2"
        >
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("settings:readOnlyBanner")}
        </p>
      ) : null}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Tabs value={scope} onValueChange={(v) => setScope(v as InsurerScope)}>
          <TabsList>
            <TabsTrigger value="mine">{t("settings:insurersTab.scopeMine")}</TabsTrigger>
            <TabsTrigger value="all">{t("settings:insurersTab.scopeAll")}</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("settings:insurersTab.search")}
            className="pl-9"
            aria-label={t("settings:insurersTab.search")}
          />
        </div>
      </div>

      <ErrorBanner error={list.error} />

      {list.isLoading ? (
        <Card className="p-5">
          <LoadingRows rows={4} />
        </Card>
      ) : rows.length === 0 ? (
        <Card className="p-6">
          <EmptyState>
            {needle
              ? t("settings:insurersTab.emptySearch")
              : scope === "mine"
                ? t("settings:insurersTab.empty")
                : t("settings:insurersTab.emptyAll")}
          </EmptyState>
        </Card>
      ) : (
        <ul className="grid gap-4 lg:grid-cols-2">
          {rows.map((row) => (
            <li key={row.insurer.id}>
              <InsurerCard row={row} guard={guard} t={t} onEdit={() => setEditing(row)} />
            </li>
          ))}
        </ul>
      )}

      <OverrideSheet row={editing} onClose={() => setEditing(null)} />
    </div>
  );
}

function InsurerCard({
  row,
  guard,
  t,
  onEdit,
}: {
  row: InsurerCapability;
  guard: Guard;
  t: TFn;
  onEdit: () => void;
}) {
  const instructions = INSTRUCTION_KEYS.filter((key) => row.instructions[key]);
  return (
    <Card className="flex h-full flex-col gap-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-soft text-brand-deep">
            <Building2 className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 text-body font-medium text-ink">
              {row.insurer.name}
              {row.broker_override ? (
                <Badge variant="brand" className="px-1.5 py-0">
                  {t("settings:insurersTab.customized")}
                </Badge>
              ) : null}
            </p>
            {row.insurer.rut ? (
              <p className="text-caption tabular-nums text-ink-3">{formatRut(row.insurer.rut)}</p>
            ) : null}
          </div>
        </div>
        <GuardedButton guard={guard} variant="secondary" size="sm" className="min-h-9" onClick={onEdit}>
          <Pencil />
          {t("settings:insurersTab.edit")}
        </GuardedButton>
      </div>

      <div className="flex flex-col gap-1.5">
        <p className="text-caption font-medium text-ink-3">{t("settings:insurersTab.level")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <LevelBadge
            level={row.level_available}
            reason={
              row.level_available === "native"
                ? t("settings:insurersTab.levelNative")
                : t("settings:insurersTab.levelAssisted")
            }
          />
          {row.channels.map((channel) => (
            <Badge key={channel} variant="outline">
              {t(CHANNEL_LABEL[channel])}
            </Badge>
          ))}
        </div>
        <p className="text-caption text-ink-3">
          {row.level_available === "native"
            ? t("settings:insurersTab.levelNative")
            : t("settings:insurersTab.levelAssisted")}
        </p>
      </div>

      <div className="flex flex-col gap-1.5">
        <p className="text-caption font-medium text-ink-3">{t("settings:insurersTab.collectionsEmail")}</p>
        {row.collections_email ? (
          <p className="flex items-center gap-1.5 break-all text-body text-ink">
            <Mail className="h-3.5 w-3.5 shrink-0 text-ink-3" />
            {row.collections_email}
          </p>
        ) : (
          <p className="text-caption text-warn-text">{t("settings:insurersTab.noEmail")}</p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <p className="text-caption font-medium text-ink-3">{t("settings:insurersTab.instructions")}</p>
        {instructions.length === 0 ? (
          <p className="text-caption text-ink-3">{t("settings:insurersTab.noInstructions")}</p>
        ) : (
          <dl className="grid gap-x-4 gap-y-1.5 text-caption sm:grid-cols-[auto_minmax(0,1fr)]">
            {instructions.map((key) => (
              <React.Fragment key={key}>
                <dt className="text-ink-3">{t(INSTRUCTION_LABEL[key])}</dt>
                <dd className="break-words text-ink">
                  {key === "portal_url" && safeHttpUrl(row.instructions[key]) ? (
                    <a
                      href={safeHttpUrl(row.instructions[key]) ?? undefined}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-brand underline-offset-4 hover:underline"
                    >
                      {row.instructions[key]}
                    </a>
                  ) : (
                    row.instructions[key]
                  )}
                </dd>
              </React.Fragment>
            ))}
          </dl>
        )}
      </div>
    </Card>
  );
}

// --- Override sheet ---------------------------------------------------------------------

function buildSchema(t: TFn) {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const key of INSTRUCTION_KEYS) {
    shape[key] =
      key === "portal_url"
        ? z
            .string()
            .trim()
            .refine((v) => v === "" || /^https?:\/\//i.test(v), {
              message: t("settings:insurersTab.sheet.invalidUrl"),
            })
        : key === "account_rut"
          ? z
              .string()
              .trim()
              .refine((v) => v === "" || isValidRut(v), {
                message: t("settings:insurersTab.sheet.invalidRut"),
              })
          : z.string();
  }
  return z.object({
    collections_email: z
      .string()
      .trim()
      .refine((v) => v === "" || z.string().email().safeParse(v).success, {
        message: t("settings:insurersTab.sheet.invalidEmail"),
      }),
    contact_name: z.string(),
    native_enabled: z.boolean(),
    notes: z.string(),
    instructions: z.object(shape),
  });
}

interface OverrideValues {
  collections_email: string;
  contact_name: string;
  native_enabled: boolean;
  notes: string;
  instructions: Record<InstructionKey, string>;
}

function toValues(row: InsurerCapability | null): OverrideValues {
  const o = row?.broker_override;
  const instructions = Object.fromEntries(
    INSTRUCTION_KEYS.map((key) => [key, o?.payment_instructions?.[key] ?? ""]),
  ) as Record<InstructionKey, string>;
  return {
    collections_email: o?.collections_email ?? "",
    contact_name: o?.contact_name ?? "",
    native_enabled: o?.native_enabled ?? true,
    notes: o?.notes ?? "",
    instructions,
  };
}

function OverrideSheet({ row, onClose }: { row: InsurerCapability | null; onClose: () => void }) {
  const { t: rawT } = useTranslation(["settings", "common"]);
  const t = rawT as unknown as TFn;
  const save = useUpdateInsurerOverride(row?.insurer.id ?? 0);
  const schema = React.useMemo(() => buildSchema(t), [t]);
  const form = useForm<OverrideValues>({
    resolver: zodResolver(schema),
    defaultValues: toValues(row),
  });

  React.useEffect(() => {
    form.reset(toValues(row));
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row]);

  const errors = form.formState.errors;
  const own = row?.broker_override?.payment_instructions ?? {};

  /** What Radal would show for a field the broker left blank. */
  const radalValue = (key: InstructionKey): string | undefined => {
    if (!row) return undefined;
    // The merged value equals the profile's whenever the broker has not overridden it.
    return own[key] ? undefined : row.instructions[key];
  };

  const onSubmit = form.handleSubmit((values) => {
    if (!row) return;
    const instructions: PaymentInstructions = {};
    for (const key of INSTRUCTION_KEYS) {
      const v = values.instructions[key]?.trim();
      // The RUT travels BODY-DV (non-negotiable 7), as the server stores it.
      if (v) instructions[key] = key === "account_rut" ? normalizeRut(v) ?? v : v;
    }
    save.mutate(
      {
        collections_email: values.collections_email.trim() || null,
        contact_name: values.contact_name.trim() || null,
        payment_instructions: instructions,
        native_enabled: values.native_enabled,
        notes: values.notes.trim() || null,
      },
      {
        onSuccess: () => {
          toast.success(t("settings:insurersTab.sheet.saved"));
          onClose();
        },
        onError: (error) => toast.error(apiError(error, t("settings:error.generic"))),
      },
    );
  });

  return (
    <Sheet open={row !== null} onOpenChange={(open) => (!open && !save.isPending ? onClose() : undefined)}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-lg">
        <SheetHeader className="border-b border-line px-5 py-4 pr-12">
          <SheetTitle>{t("settings:insurersTab.sheet.title", { name: row?.insurer.name ?? "" })}</SheetTitle>
          <SheetDescription>{t("settings:insurersTab.sheet.description")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={onSubmit} noValidate className="flex flex-1 flex-col">
          <div className="flex flex-1 flex-col gap-6 px-5 py-5">
            <section className="flex flex-col gap-3">
              <h3 className="text-body font-medium text-ink">{t("settings:insurersTab.sheet.contactTitle")}</h3>
              <Field
                label={t("settings:insurersTab.sheet.collectionsEmail")}
                htmlFor="ov-email"
                hint={
                  row && !row.broker_override?.collections_email && row.collections_email
                    ? t("settings:insurersTab.sheet.radalValue", { value: row.collections_email })
                    : t("settings:insurersTab.sheet.collectionsEmailHint")
                }
                error={errors.collections_email?.message}
              >
                <Input id="ov-email" type="email" {...form.register("collections_email")} />
              </Field>
              <Field label={t("settings:insurersTab.sheet.contactName")} htmlFor="ov-contact">
                <Input id="ov-contact" {...form.register("contact_name")} />
              </Field>
            </section>

            <section className="flex flex-col gap-3">
              <div>
                <h3 className="text-body font-medium text-ink">
                  {t("settings:insurersTab.sheet.instructionsTitle")}
                </h3>
                <p className="text-caption text-ink-3">{t("settings:insurersTab.sheet.instructionsHint")}</p>
              </div>
              {INSTRUCTION_KEYS.map((key) => {
                const fallback = radalValue(key);
                const fieldError = (errors.instructions as Record<string, { message?: string }> | undefined)?.[key]
                  ?.message;
                return (
                  <Field
                    key={key}
                    label={t(INSTRUCTION_LABEL[key])}
                    htmlFor={`ov-instr-${key}`}
                    hint={fallback ? t("settings:insurersTab.sheet.radalValue", { value: fallback }) : undefined}
                    error={fieldError}
                  >
                    {key === "notes" ? (
                      <Textarea id={`ov-instr-${key}`} rows={3} {...form.register(`instructions.${key}`)} />
                    ) : (
                      <Input id={`ov-instr-${key}`} {...form.register(`instructions.${key}`)} />
                    )}
                  </Field>
                );
              })}
            </section>

            <section className="flex flex-col gap-3">
              <h3 className="text-body font-medium text-ink">{t("settings:insurersTab.sheet.nativeTitle")}</h3>
              <label className="flex min-h-9 items-center justify-between gap-4 text-body text-ink-2">
                {t("settings:insurersTab.sheet.nativeEnabled")}
                <Controller
                  control={form.control}
                  name="native_enabled"
                  render={({ field }) => <Switch checked={field.value} onCheckedChange={field.onChange} />}
                />
              </label>
            </section>

            <Field label={t("settings:insurersTab.sheet.notes")} htmlFor="ov-notes">
              <Textarea id="ov-notes" rows={3} {...form.register("notes")} />
            </Field>

            <ErrorBanner error={save.error} />
          </div>
          <SheetFooter className="sticky bottom-0 gap-2 border-t border-line bg-bone px-5 py-4 sm:gap-0">
            <Button type="button" variant="ghost" onClick={onClose} disabled={save.isPending}>
              {t("settings:insurersTab.sheet.cancel")}
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : null}
              {save.isPending ? t("settings:insurersTab.sheet.saving") : t("settings:insurersTab.sheet.save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
