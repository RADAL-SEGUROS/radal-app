import * as React from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  BellRing,
  CalendarClock,
  Loader2,
  Lock,
  MessageSquareText,
  Plus,
  Scale,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { DisabledHint, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import { useMailAccounts } from "@/api/mail";
import { useCan } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import { parseAmount } from "@/components/collections/amount";
import { cn } from "@/lib/utils";
import { EmptyState, Field, GuardedButton, SectionCard, type Guard } from "./shared";
import { NoticeTemplatesEditor } from "./NoticeTemplatesEditor";
import { cadenceLabel, type TFn } from "./cadence";
import {
  useCollectionSettings,
  useUpdateCollectionSettings,
  type CollectionSettings,
  type CollectionSettingsUpdate,
} from "../api";

/**
 * Configuración → Cobranza (v12, team spec C3.1 / C5.1, 03 §7).
 *
 * The settings form writes `PUT /collections/settings` (Settings.Manage);
 * anyone with Collections.View reads it. Below it, the notice templates
 * editor with its approval gate — the master switch cannot be turned on
 * before the messages are approved (the server says so too, in Spanish).
 */


const NONE = "__none__";
const INT_RE = /^-?\d+$/;

/**
 * Tolerances are typed and shown the Chilean way (`0,009`, `1,0`, `1.500`):
 * the same `parseAmount` the payment form uses, never `Number("1.500")`.
 */
function decimalIn(value: string): string {
  const parsed = parseAmount(value, "UF");
  return parsed.error === null ? String(parsed.value) : value.trim();
}

function clpIn(value: string): number {
  const parsed = parseAmount(value, "CLP");
  return parsed.error === null ? parsed.value : NaN;
}

/** A server decimal string ("0.0090", "1.000") in es-CL ("0,009", "1,0"). */
function decimalOut(value: string | number, minFraction = 0): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("es-CL", {
    minimumFractionDigits: minFraction,
    maximumFractionDigits: 6,
    useGrouping: false,
  });
}

function intField(t: TFn, min: number, max: number) {
  return z
    .string()
    .trim()
    .refine((v) => INT_RE.test(v), { message: t("settings:collectionsTab.validation.integer") })
    .refine((v) => Number(v) >= min && Number(v) <= max, {
      message: t("settings:collectionsTab.validation.range", { min, max }),
    });
}

/** Whole pesos, with or without the thousands dot (`1.500` = 1500). */
function clpField(t: TFn, min: number, max: number) {
  return z
    .string()
    .trim()
    .refine((v) => Number.isInteger(clpIn(v)), {
      message: t("settings:collectionsTab.validation.integer"),
    })
    .refine((v) => clpIn(v) >= min && clpIn(v) <= max, {
      message: t("settings:collectionsTab.validation.range", { min, max: max.toLocaleString("es-CL") }),
    });
}

function decimalField(message: string, max: number) {
  return z
    .string()
    .trim()
    .refine(
      (v) => {
        const parsed = parseAmount(v, "UF");
        return parsed.error === null && parsed.value >= 0 && parsed.value <= max;
      },
      { message },
    );
}

function buildSchema(t: TFn) {
  return z
    .object({
      cadence_days: z
        .array(z.number().int())
        .min(1, t("settings:collectionsTab.cadence.empty"))
        .max(10, t("settings:collectionsTab.cadence.full")),
      followup_days: intField(t, 1, 60),
      tolerance_uf: decimalField(t("settings:collectionsTab.tolerance.invalidUf"), 1),
      tolerance_pct: decimalField(t("settings:collectionsTab.tolerance.invalidPct"), 10),
      tolerance_clp: clpField(t, 0, 100_000),
      notices_enabled: z.boolean(),
      default_sender: z.enum(["radal", "broker_account"]),
      sender_mail_account_id: z.string(),
      native_collection_enabled: z.boolean(),
      auto_status_inquiries: z.boolean(),
      inquiry_after_days: intField(t, 0, 90),
    })
    .superRefine((values, ctx) => {
      if (values.default_sender === "broker_account" && values.sender_mail_account_id === NONE) {
        ctx.addIssue({
          code: "custom",
          path: ["sender_mail_account_id"],
          message: t("settings:collectionsTab.notices.senderAccountRequired"),
        });
      }
    });
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>;

/** Page order of the validated fields, for "focus the first invalid one". */
const FIELD_ORDER = [
  "followup_days",
  "tolerance_uf",
  "tolerance_pct",
  "tolerance_clp",
  "sender_mail_account_id",
  "inquiry_after_days",
] as const;
const FIELD_DOM_ID: Record<(typeof FIELD_ORDER)[number], string> = {
  followup_days: "cs-followup",
  tolerance_uf: "cs-tol-uf",
  tolerance_pct: "cs-tol-pct",
  tolerance_clp: "cs-tol-clp",
  sender_mail_account_id: "cs-sender-account",
  inquiry_after_days: "cs-inq-days",
};

function toForm(s: CollectionSettings): FormValues {
  return {
    cadence_days: [...s.cadence_days].sort((a, b) => a - b),
    followup_days: String(s.followup_days),
    tolerance_uf: decimalOut(s.tolerance_uf),
    tolerance_pct: decimalOut(s.tolerance_pct, 1),
    tolerance_clp: Number(s.tolerance_clp).toLocaleString("es-CL"),
    notices_enabled: s.notices_enabled,
    default_sender: s.default_sender,
    sender_mail_account_id: s.sender_mail_account_id ? String(s.sender_mail_account_id) : NONE,
    native_collection_enabled: s.native_collection_enabled,
    auto_status_inquiries: s.auto_status_inquiries,
    inquiry_after_days: String(s.inquiry_after_days),
  };
}

function toPayload(v: FormValues): CollectionSettingsUpdate {
  return {
    cadence_days: [...v.cadence_days].sort((a, b) => a - b),
    followup_days: Number(v.followup_days),
    tolerance_uf: decimalIn(v.tolerance_uf),
    tolerance_pct: decimalIn(v.tolerance_pct),
    tolerance_clp: clpIn(v.tolerance_clp),
    notices_enabled: v.notices_enabled,
    default_sender: v.default_sender,
    sender_mail_account_id:
      v.default_sender === "broker_account" && v.sender_mail_account_id !== NONE
        ? Number(v.sender_mail_account_id)
        : null,
    native_collection_enabled: v.native_collection_enabled,
    auto_status_inquiries: v.auto_status_inquiries,
    inquiry_after_days: Number(v.inquiry_after_days),
  };
}

/**
 * Only the fields that differ from the server copy. Re-sending an unchanged
 * `notices_enabled: true` after a template edit withdrew the approval would
 * make the server reject the whole save (422), so nothing unchanged is sent.
 */
function changedFields(values: FormValues, server: CollectionSettings): CollectionSettingsUpdate {
  const next = toPayload(values);
  const base = toPayload(toForm(server));
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(next) as (keyof CollectionSettingsUpdate)[]) {
    if (JSON.stringify(next[key]) !== JSON.stringify(base[key])) out[key] = next[key];
  }
  return out as CollectionSettingsUpdate;
}

export function CollectionsSettingsTab() {
  const { t } = useTranslation(["settings", "common"]);
  const canView = useCan("Collections", "View");
  const canManage = useCan("Settings", "Manage");
  const settings = useCollectionSettings(canView.allowed);

  if (!canView.isLoading && !canView.allowed) {
    return (
      <Card className="p-6">
        <EmptyState>{t("settings:collectionsTab.noView")}</EmptyState>
      </Card>
    );
  }

  const guard: Guard = {
    allowed: canManage.allowed,
    reason: t("settings:collectionsTab.readOnly"),
  };

  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-3xl text-body text-ink-3">{t("settings:collectionsTab.description")}</p>
      {!canManage.isLoading && !canManage.allowed ? (
        <p
          role="note"
          className="flex max-w-3xl items-start gap-2 rounded-sm bg-paper-2 px-3 py-2.5 text-caption text-ink-2"
        >
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("settings:readOnlyBanner")}
        </p>
      ) : null}
      <ErrorBanner error={settings.error} />
      {settings.isLoading || !settings.data ? (
        <Card className="p-5">
          <LoadingRows rows={5} />
        </Card>
      ) : (
        <SettingsForm settings={settings.data} guard={guard} />
      )}
      <NoticeTemplatesEditor
        guard={guard}
        cadenceDays={settings.data?.cadence_days ?? []}
        enabled={canView.allowed}
      />
    </div>
  );
}

// --- The settings form ---------------------------------------------------------------

function SettingsForm({ settings, guard }: { settings: CollectionSettings; guard: Guard }) {
  const { t: rawT } = useTranslation(["settings", "mail", "common"]);
  const t = rawT as unknown as TFn;
  const update = useUpdateCollectionSettings();
  const canViewMail = useCan("Mail", "View");
  const mail = useMailAccounts(canViewMail.allowed);
  const schema = React.useMemo(() => buildSchema(t), [t]);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: toForm(settings),
  });

  // Re-seed when the server copy changes (save here, or another tab saved).
  React.useEffect(() => {
    form.reset(toForm(settings));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.updated_at]);

  const readOnly = !guard.allowed;
  const errors = form.formState.errors;
  const dirty = form.formState.isDirty;
  const sender = form.watch("default_sender");
  const inquiriesOn = form.watch("auto_status_inquiries");
  const approved = settings.notices_approved_at !== null;
  const noticesOn = form.watch("notices_enabled");

  const senderAccounts = (mail.data?.items ?? []).filter(
    (a) => a.id !== null && a.send_enabled,
  );

  // A failed submit must be visible next to the button, not only far up the
  // page: a toast, then focus (and scroll to) the first invalid field.
  const onInvalid = (invalid: Record<string, unknown>) => {
    toast.error(t("settings:collectionsTab.validation.fixFields"));
    const first = FIELD_ORDER.find((name) => name in invalid);
    if (first) {
      const el = document.getElementById(FIELD_DOM_ID[first]);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.focus({ preventScroll: true });
    }
  };

  const onSubmit = form.handleSubmit((values) => {
    const payload = changedFields(values, settings);
    if (Object.keys(payload).length === 0) {
      form.reset(toForm(settings));
      return;
    }
    update.mutate(payload, {
      onSuccess: () => toast.success(t("settings:collectionsTab.saved")),
      onError: (error) => toast.error(apiError(error, t("settings:error.generic"))),
    });
  }, onInvalid);

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
      <div className="grid gap-5 xl:grid-cols-2">
        <SectionCard
          title={t("settings:collectionsTab.cadence.title")}
          icon={<CalendarClock />}
          description={t("settings:collectionsTab.cadence.description")}
        >
          <div className="flex flex-col gap-5">
            <Controller
              control={form.control}
              name="cadence_days"
              render={({ field }) => (
                <CadenceEditor
                  value={field.value}
                  onChange={field.onChange}
                  disabled={readOnly}
                  t={t}
                />
              )}
            />
            {errors.cadence_days?.message ? (
              <p className="text-caption text-neg-text">{errors.cadence_days.message}</p>
            ) : null}
            <Field
              label={t("settings:collectionsTab.followup.label")}
              htmlFor="cs-followup"
              hint={t("settings:collectionsTab.followup.hint")}
              error={errors.followup_days?.message}
            >
              <div className="flex items-center gap-2">
                <Input
                  id="cs-followup"
                  inputMode="numeric"
                  className="w-24 tabular-nums"
                  disabled={readOnly}
                  {...form.register("followup_days")}
                />
                <span className="text-caption text-ink-3">
                  {t("settings:collectionsTab.followup.unit")}
                </span>
              </div>
            </Field>
          </div>
        </SectionCard>

        <SectionCard
          title={t("settings:collectionsTab.tolerance.title")}
          icon={<Scale />}
          description={t("settings:collectionsTab.tolerance.description")}
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <Field
              label={t("settings:collectionsTab.tolerance.uf")}
              htmlFor="cs-tol-uf"
              error={errors.tolerance_uf?.message}
            >
              <Input
                id="cs-tol-uf"
                inputMode="decimal"
                className="tabular-nums"
                disabled={readOnly}
                {...form.register("tolerance_uf")}
              />
            </Field>
            <Field
              label={t("settings:collectionsTab.tolerance.pct")}
              htmlFor="cs-tol-pct"
              error={errors.tolerance_pct?.message}
            >
              <Input
                id="cs-tol-pct"
                inputMode="decimal"
                className="tabular-nums"
                disabled={readOnly}
                {...form.register("tolerance_pct")}
              />
            </Field>
            <Field
              label={t("settings:collectionsTab.tolerance.clp")}
              htmlFor="cs-tol-clp"
              error={errors.tolerance_clp?.message}
            >
              <Input
                id="cs-tol-clp"
                inputMode="numeric"
                className="tabular-nums"
                disabled={readOnly}
                {...form.register("tolerance_clp")}
              />
            </Field>
          </div>
        </SectionCard>
      </div>

      <SectionCard
        title={t("settings:collectionsTab.notices.title")}
        icon={<BellRing />}
        description={t("settings:collectionsTab.notices.description")}
      >
        <div className="flex flex-col gap-5">
          <SwitchRow
            label={t("settings:collectionsTab.notices.enabled")}
            hint={!approved && !noticesOn ? t("settings:collectionsTab.notices.needsApproval") : undefined}
            warning={approved || !noticesOn ? undefined : t("settings:collectionsTab.notices.pausedUntilApproved")}
            disabledReason={
              readOnly
                ? guard.reason
                : !approved && !noticesOn
                  ? t("settings:collectionsTab.notices.needsApproval")
                  : null
            }
          >
            <Controller
              control={form.control}
              name="notices_enabled"
              render={({ field }) => (
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  disabled={readOnly || (!approved && !field.value)}
                />
              )}
            />
          </SwitchRow>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("settings:collectionsTab.notices.sender")}>
              <Controller
                control={form.control}
                name="default_sender"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} disabled={readOnly}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="radal">{t("settings:collectionsTab.notices.senderRadal")}</SelectItem>
                      <SelectItem value="broker_account">
                        {t("settings:collectionsTab.notices.senderBroker")}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </Field>
            {sender === "broker_account" ? (
              <Field
                label={t("settings:collectionsTab.notices.senderAccount")}
                error={errors.sender_mail_account_id?.message}
                hint={
                  !canViewMail.allowed
                    ? t("settings:collectionsTab.notices.senderNoMailView")
                    : senderAccounts.length === 0 && !mail.isLoading
                      ? t("settings:collectionsTab.notices.senderNoAccounts")
                      : undefined
                }
              >
                <Controller
                  control={form.control}
                  name="sender_mail_account_id"
                  render={({ field }) => (
                    <Select
                      value={field.value}
                      onValueChange={field.onChange}
                      disabled={readOnly || !canViewMail.allowed || senderAccounts.length === 0}
                    >
                      <SelectTrigger id="cs-sender-account">
                        <SelectValue placeholder={t("settings:collectionsTab.notices.senderAccountPlaceholder")} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE} disabled>
                          {t("settings:collectionsTab.notices.senderAccountPlaceholder")}
                        </SelectItem>
                        {senderAccounts.map((a) => (
                          <SelectItem key={a.id} value={String(a.id)}>
                            {a.address}
                            {a.status !== "connected"
                              ? ` · ${t("settings:collectionsTab.notices.senderNotConnected")}`
                              : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </Field>
            ) : null}
          </div>
        </div>
      </SectionCard>

      <div className="grid gap-5 xl:grid-cols-2">
        <SectionCard title={t("settings:collectionsTab.inquiries.title")} icon={<MessageSquareText />}>
          <div className="flex flex-col gap-4">
            <SwitchRow
              label={t("settings:collectionsTab.inquiries.enabled")}
              hint={t("settings:collectionsTab.inquiries.hint")}
              disabledReason={readOnly ? guard.reason : null}
            >
              <Controller
                control={form.control}
                name="auto_status_inquiries"
                render={({ field }) => (
                  <Switch checked={field.value} onCheckedChange={field.onChange} disabled={readOnly} />
                )}
              />
            </SwitchRow>
            {inquiriesOn ? (
              <Field
                label={t("settings:collectionsTab.inquiries.afterDays")}
                htmlFor="cs-inq-days"
                error={errors.inquiry_after_days?.message}
              >
                <Input
                  id="cs-inq-days"
                  inputMode="numeric"
                  className="w-24 tabular-nums"
                  disabled={readOnly}
                  {...form.register("inquiry_after_days")}
                />
              </Field>
            ) : null}
          </div>
        </SectionCard>

        <SectionCard title={t("settings:collectionsTab.native.title")} icon={<Scale />}>
          <SwitchRow
            label={t("settings:collectionsTab.native.enabled")}
            hint={t("settings:collectionsTab.native.hint")}
            disabledReason={readOnly ? guard.reason : null}
          >
            <Controller
              control={form.control}
              name="native_collection_enabled"
              render={({ field }) => (
                <Switch checked={field.value} onCheckedChange={field.onChange} disabled={readOnly} />
              )}
            />
          </SwitchRow>
        </SectionCard>
      </div>

      <ErrorBanner error={update.error} />

      <div className="sticky bottom-0 z-10 flex flex-col-reverse gap-2 border-t border-line bg-paper py-3 sm:flex-row sm:items-center sm:justify-end">
        {settings.updated_at ? (
          <span className="text-caption text-ink-3 sm:mr-auto">
            {t("settings:collectionsTab.updatedAt", { date: formatDateTime(settings.updated_at) })}
          </span>
        ) : null}
        {dirty ? (
          <Button
            type="button"
            variant="ghost"
            onClick={() => form.reset(toForm(settings))}
            disabled={update.isPending}
          >
            {t("settings:collectionsTab.discard")}
          </Button>
        ) : null}
        <GuardedButton
          guard={guard}
          type="submit"
          className="min-h-10"
          disabled={!dirty || update.isPending}
        >
          {update.isPending ? <Loader2 className="animate-spin" /> : null}
          {update.isPending ? t("settings:collectionsTab.saving") : t("settings:collectionsTab.save")}
        </GuardedButton>
      </div>
    </form>
  );
}

function SwitchRow({
  label,
  hint,
  warning,
  disabledReason,
  children,
}: {
  label: string;
  hint?: string;
  /** A live state the broker must act on (e.g. avisos detenidos). */
  warning?: string;
  disabledReason?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <p className="text-body font-medium text-ink">{label}</p>
        {hint ? <p className="mt-0.5 text-caption text-ink-3">{hint}</p> : null}
        {warning ? (
          <p role="status" className="mt-0.5 text-caption text-warn-text">
            {warning}
          </p>
        ) : null}
      </div>
      <DisabledHint hint={disabledReason ?? null} className="min-h-9 items-center">
        {children}
      </DisabledHint>
    </div>
  );
}

// --- Cadence chips -----------------------------------------------------------------------

function CadenceEditor({
  value,
  onChange,
  disabled,
  t,
}: {
  value: number[];
  onChange: (next: number[]) => void;
  disabled: boolean;
  t: TFn;
}) {
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const sorted = [...value].sort((a, b) => a - b);

  const add = () => {
    const raw = draft.trim().replace("−", "-").replace(/^\+/, "");
    if (!INT_RE.test(raw) || Number(raw) < -90 || Number(raw) > 180) {
      setError(t("settings:collectionsTab.cadence.invalid"));
      return;
    }
    const day = Number(raw);
    if (value.includes(day)) {
      setError(t("settings:collectionsTab.cadence.duplicate"));
      return;
    }
    if (value.length >= 10) {
      setError(t("settings:collectionsTab.cadence.full"));
      return;
    }
    onChange([...value, day].sort((a, b) => a - b));
    setDraft("");
    setError(null);
  };

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-wrap gap-2" aria-live="polite">
        {sorted.map((day) => {
          const label = cadenceLabel(t, day);
          return (
            <li
              key={day}
              className={cn(
                "inline-flex min-h-9 items-center gap-1.5 rounded-full border py-1 pl-3 pr-1.5 text-caption font-medium tabular-nums",
                day < 0
                  ? "border-line bg-paper-2 text-ink-2"
                  : day === 0
                    ? "border-brand-line bg-brand-soft text-brand-deep"
                    : "border-warn-line bg-warn-soft text-warn-text",
              )}
            >
              <span>{day > 0 ? `+${day}` : day === 0 ? "0" : `−${-day}`}</span>
              <span className="font-normal opacity-80">· {label}</span>
              <button
                type="button"
                disabled={disabled || value.length <= 1}
                onClick={() => onChange(value.filter((d) => d !== day))}
                aria-label={t("settings:collectionsTab.cadence.remove", { label })}
                className="grid h-7 w-7 place-items-center rounded-full transition-[background-color,transform] duration-150 ease-out hover:bg-black/5 active:scale-[0.96] disabled:pointer-events-none disabled:opacity-40"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder={t("settings:collectionsTab.cadence.addPlaceholder")}
          inputMode="text"
          className="w-36 tabular-nums"
          disabled={disabled}
          aria-label={t("settings:collectionsTab.cadence.add")}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="min-h-9"
          onClick={add}
          disabled={disabled || !draft.trim()}
        >
          <Plus />
          {t("settings:collectionsTab.cadence.add")}
        </Button>
      </div>
      {error ? <p className="text-caption text-neg-text">{error}</p> : null}
      <p className="text-caption text-ink-3">{t("settings:collectionsTab.cadence.hint")}</p>
      <p className="text-caption text-ink-3">{t("settings:collectionsTab.cadence.termRisk")}</p>
    </div>
  );
}
