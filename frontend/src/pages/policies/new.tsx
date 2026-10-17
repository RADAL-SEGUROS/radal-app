/**
 * `/policies/new` — "Registrar a mano" (vía Manual, team 3.2–3.3; `POST /policies`).
 *
 * Only five data are mandatory — RUT asegurado, contratante (by default the
 * insured), compañía, ramo·subtipo and vigencia. Everything else is optional
 * and, when left out, shows on the ficha as "Falta: X" with Completar. Next to
 * each optional field, what it switches on (n° → Endosos; prima bruta → plan de
 * pago). The server locates or creates the colocación (origin = manual), marks
 * the policy Sin respaldo, 409s a taken number and warns about a possible
 * duplicate; all of its Spanish reasons are shown as-is.
 *
 * Money follows the house rule on the preview: net = afecta + exenta,
 * IVA = 19 % × afecta (never on net), bruta = net + IVA. The server owns the
 * arithmetic and answers 422 on a mismatch.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowLeft, Loader2, Save } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { ErrorBanner, Section, apiError } from "@/components/common/kit";
import { TouchHint } from "@/components/policies/TouchHint";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { formatUF } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useInsurers } from "@/api/insurers";
import { useClient } from "@/api/clients";
import {
  PAYMENT_METHODS,
  useCreateManualPolicy,
  type ManualPolicyCreate,
  type PartyContactInput,
  type PaymentMethod,
} from "@/api/policies";
import { RamoSubtypeSelect } from "@/components/catalog/RamoSubtypeSelect";
import { ClientPicker, type PickedClient } from "@/components/policies/ClientPicker";
import { PAYMENT_METHOD_LABEL } from "@/components/policies/policyLabels";
import { isValidRut, normalizeRut } from "@/pages/clients/rut";
import { parseDecimalInput } from "@/components/policies/money";

// =============================================================================
// Validation — zod messages are CODES, rendered through an explicit map
// =============================================================================

const ERROR_LABEL = {
  required: "policies:form.errors.required",
  rut: "policies:form.errors.rut",
  email: "policies:form.errors.email",
  number: "policies:form.errors.number",
  pct: "policies:form.errors.pct",
  endBeforeStart: "policies:form.errors.endBeforeStart",
  client: "policies:form.errors.client",
  subtype: "policies:form.errors.subtype",
  insurer: "policies:form.errors.insurer",
  ambiguous: "policies:form.errors.ambiguous",
  totalMismatch: "policies:form.errors.totalMismatch",
} as const;
type ErrorCode = keyof typeof ERROR_LABEL;

/** Money/percent fields of the form, parsed by currency (see `policies/money.ts`). */
const MONEY_FIELDS = ["taxable", "exempt", "total", "commission"] as const;

const contactSchema = {
  name: z.string().max(255).optional(),
  rut: z.string().optional(),
  email: z.string().optional(),
  phone: z.string().max(64).optional(),
};

const schema = z
  .object({
    insuredMode: z.enum(["client", "rut"]),
    clientId: z.number().int().positive().nullable(),
    insuredRut: z.string().optional(),
    insuredName: z.string().max(255).optional(),
    insurerId: z.string().optional(),
    subtypeId: z.number().int().positive().nullable(),
    startDate: z.string().min(1, "required"),
    endDate: z.string().min(1, "required"),
    policyNumber: z.string().max(64).optional(),
    currency: z.enum(["UF", "USD"]),
    taxable: z.string().optional(),
    exempt: z.string().optional(),
    total: z.string().optional(),
    commission: z.string().optional(),
    paymentMethod: z.string().optional(),
    isProvisional: z.boolean(),
    holderSame: z.boolean(),
    holder: z.object(contactSchema),
    payerSame: z.boolean(),
    payer: z.object(contactSchema),
    notes: z.string().optional(),
  })
  .superRefine((v, ctx) => {
    const issue = (path: (string | number)[], code: ErrorCode) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path, message: code });

    if (v.insuredMode === "client" && !v.clientId) issue(["clientId"], "client");
    if (v.insuredMode === "rut") {
      if (!v.insuredRut?.trim()) issue(["insuredRut"], "required");
      else if (!isValidRut(v.insuredRut)) issue(["insuredRut"], "rut");
      if (!v.insuredName?.trim()) issue(["insuredName"], "required");
    }
    if (!v.insurerId) issue(["insurerId"], "insurer");
    if (!v.subtypeId) issue(["subtypeId"], "subtype");
    if (v.startDate && v.endDate && v.endDate <= v.startDate) issue(["endDate"], "endBeforeStart");

    // "150.000" is ciento cincuenta mil, never 150: the shared es-CL parser
    // reads it by currency and rejects an ambiguous "12.345" with a reason.
    for (const field of MONEY_FIELDS) {
      // A percentage carries decimals like UF does.
      const parsed = parseDecimalInput(v[field], field === "commission" ? "UF" : v.currency);
      if (parsed.error) issue([field], parsed.error === "ambiguous" ? "ambiguous" : "number");
      else if (field === "commission" && parsed.number !== null && parsed.number > 100) {
        issue([field], "pct");
      }
    }

    const checkContact = (key: "holder" | "payer", needsName: boolean) => {
      const c = v[key];
      if (needsName && !c.name?.trim()) issue([key, "name"], "required");
      if (c.rut?.trim() && !isValidRut(c.rut)) issue([key, "rut"], "rut");
      if (c.email?.trim() && !z.string().email().safeParse(c.email.trim()).success) {
        issue([key, "email"], "email");
      }
    };
    checkContact("holder", !v.holderSame);
    if (!v.payerSame) checkContact("payer", true);
  });

type FormValues = z.infer<typeof schema>;

function blank(value: string | undefined | null): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function isoToday(offsetYears = 0): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() + offsetYears);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// =============================================================================
// Small layout helpers
// =============================================================================

function Field({
  label,
  htmlFor,
  hint,
  error,
  required,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor} className="text-caption font-medium text-ink-2">
        {label}
        {required ? <span className="ml-0.5 text-neg-text">*</span> : null}
      </Label>
      {children}
      {error ? (
        <p className="text-caption text-neg-text">{error}</p>
      ) : hint ? (
        <p className="text-caption text-ink-3">{hint}</p>
      ) : null}
    </div>
  );
}

// =============================================================================
// Page
// =============================================================================

export default function PolicyNewPage() {
  const { t } = useTranslation("policies");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const canCreate = useCan("Policies", "Create");
  const canInsurers = useCan("Insurers", "View");
  const insurers = useInsurers({ limit: 200 }, canInsurers.allowed);
  const create = useCreateManualPolicy();
  const [serverError, setServerError] = React.useState<unknown>(null);

  // `?client=<id>` preselects the insured (e.g. from a client's ficha).
  const presetClientId = Number(searchParams.get("client")) || undefined;
  const presetClient = useClient(presetClientId);
  const [picked, setPicked] = React.useState<PickedClient | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      insuredMode: "client",
      clientId: null,
      insuredRut: "",
      insuredName: "",
      insurerId: "",
      subtypeId: null,
      startDate: isoToday(),
      endDate: isoToday(1),
      policyNumber: "",
      currency: "UF",
      taxable: "",
      exempt: "",
      total: "",
      commission: "",
      paymentMethod: "",
      isProvisional: false,
      holderSame: true,
      holder: { name: "", rut: "", email: "", phone: "" },
      payerSame: true,
      payer: { name: "", rut: "", email: "", phone: "" },
      notes: "",
    },
  });
  const { register, control, watch, setValue, formState, handleSubmit } = form;
  const errors = formState.errors;
  const err = (message: string | undefined): string | undefined =>
    message ? t(ERROR_LABEL[message as ErrorCode] ?? ERROR_LABEL.required) : undefined;

  React.useEffect(() => {
    const c = presetClient.data;
    if (c && !picked) {
      const next = { id: c.id, name: c.insured.legal_name, rut: c.insured.rut };
      setPicked(next);
      setValue("clientId", c.id);
    }
  }, [presetClient.data, picked, setValue]);

  const insuredMode = watch("insuredMode");
  const holderSame = watch("holderSame");
  const payerSame = watch("payerSame");
  const currency = watch("currency");

  // ---- Money preview (house rule) -------------------------------------------
  const num = (raw: string | undefined) => parseDecimalInput(raw, currency).number ?? NaN;
  const taxable = num(watch("taxable"));
  const exempt = num(watch("exempt"));
  const statedTotal = num(watch("total"));
  const hasInputs = !Number.isNaN(taxable) || !Number.isNaN(exempt);
  const net = (Number.isNaN(taxable) ? 0 : taxable) + (Number.isNaN(exempt) ? 0 : exempt);
  const vat = (Number.isNaN(taxable) ? 0 : taxable) * 0.19;
  const derivedTotal = net + vat;
  const totalMismatch =
    hasInputs && !Number.isNaN(statedTotal) && Math.abs(statedTotal - derivedTotal) > 0.01;
  const fmt = (n: number) =>
    currency === "USD"
      ? `USD ${new Intl.NumberFormat("es-CL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`
      : formatUF(n);

  const onSubmit = async (v: FormValues) => {
    setServerError(null);
    // The server 422s a stated gross that is not net + IVA — say so on the
    // field instead of letting the request fail.
    {
      const tx = parseDecimalInput(v.taxable, v.currency).number;
      const ex = parseDecimalInput(v.exempt, v.currency).number;
      const tot = parseDecimalInput(v.total, v.currency).number;
      if ((tx != null || ex != null) && tot != null) {
        const expected = (tx ?? 0) * 1.19 + (ex ?? 0);
        if (Math.abs(tot - expected) > 0.01) {
          form.setError("total", { type: "manual", message: "totalMismatch" }, { shouldFocus: true });
          return;
        }
      }
    }
    const insuredName =
      v.insuredMode === "client" ? picked?.name ?? null : blank(v.insuredName);
    const insuredRut =
      v.insuredMode === "client" ? picked?.rut ?? null : normalizeRut(v.insuredRut);

    const contact = (c: FormValues["holder"]): Omit<PartyContactInput, "name"> => ({
      rut: c.rut?.trim() ? normalizeRut(c.rut) : null,
      email: blank(c.email),
      phone: blank(c.phone),
    });

    let policyholder: PartyContactInput | null = null;
    if (!v.holderSame) {
      policyholder = { name: v.holder.name!.trim(), ...contact(v.holder) };
    } else if (blank(v.holder.email) || blank(v.holder.phone)) {
      // Same person as the insured, but with the contact cobranza needs.
      policyholder = {
        name: insuredName ?? "",
        rut: insuredRut,
        email: blank(v.holder.email),
        phone: blank(v.holder.phone),
      };
    }
    const payer: PartyContactInput | null = v.payerSame
      ? null
      : { name: v.payer.name!.trim(), ...contact(v.payer) };

    const decimal = (raw: string | undefined, cur: string = v.currency) =>
      parseDecimalInput(raw, cur).value;
    const payload: ManualPolicyCreate = {
      insurer_id: Number(v.insurerId),
      insurance_subtype_id: v.subtypeId as number,
      client_id: v.insuredMode === "client" ? v.clientId : null,
      insured_rut: v.insuredMode === "rut" ? insuredRut : null,
      insured_name: v.insuredMode === "rut" ? insuredName : null,
      start_date: v.startDate,
      end_date: v.endDate,
      policy_number: blank(v.policyNumber),
      currency: v.currency,
      is_provisional: v.isProvisional,
      taxable_premium_uf: decimal(v.taxable),
      exempt_premium_uf: decimal(v.exempt),
      total_premium_uf: decimal(v.total),
      commission_pct: decimal(v.commission, "UF"),
      payment_method: (v.paymentMethod || null) as PaymentMethod | null,
      policyholder: policyholder && policyholder.name ? policyholder : null,
      payer,
      notes: blank(v.notes),
    };

    try {
      const created = await create.mutateAsync(payload);
      toast.success(t("form.toast.created", { number: created.policy_number }));
      for (const warning of created.warnings ?? []) toast.warning(warning);
      navigate(`/policies/${created.id}`);
    } catch (error) {
      const detail = (error as { response?: { status?: number; data?: { detail?: unknown } } })
        .response?.data?.detail;
      const moneyField = Array.isArray(detail)
        ? (detail[0] as { field?: string } | undefined)?.field
        : undefined;
      if (moneyField === "total_premium_uf") {
        form.setError("total", { type: "manual", message: "totalMismatch" }, { shouldFocus: true });
      }
      setServerError(error);
      toast.error(apiError(error, t("form.toast.error")));
    }
  };

  if (!canCreate.isLoading && !canCreate.allowed) {
    return (
      <>
        <PageHeader title={t("new.title")} subtitle={t("new.description")} />
        <ErrorBanner error={t("noPermission.create")} />
        <Button asChild variant="secondary" className="self-start">
          <Link to="/policies">
            <ArrowLeft className="h-4 w-4" />
            {t("title")}
          </Link>
        </Button>
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/policies" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("title")}
          </Link>
        }
        title={t("new.title")}
        subtitle={t("new.description")}
      />

      <form
        onSubmit={handleSubmit(onSubmit)}
        className="flex flex-col gap-[18px]"
        noValidate
      >
        {/* ---- 1. Asegurado ---- */}
        <FadeUp>
          <Section title={t("form.sections.insured")} description={t("form.sections.insuredHint")}>
            <div className="flex flex-col gap-4">
              <Controller
                control={control}
                name="insuredMode"
                render={({ field }) => (
                  <Tabs value={field.value} onValueChange={(v) => field.onChange(v)}>
                    <TabsList className="w-full sm:w-auto">
                      <TabsTrigger value="client" className="flex-1 sm:flex-none">
                        {t("form.insuredMode.client")}
                      </TabsTrigger>
                      <TabsTrigger value="rut" className="flex-1 sm:flex-none">
                        {t("form.insuredMode.rut")}
                      </TabsTrigger>
                    </TabsList>
                  </Tabs>
                )}
              />
              {insuredMode === "client" ? (
                <Field
                  label={t("form.fields.client")}
                  error={err(errors.clientId?.message)}
                  required
                >
                  <ClientPicker
                    value={picked}
                    onChange={(c) => {
                      setPicked(c);
                      setValue("clientId", c?.id ?? null, { shouldValidate: formState.isSubmitted });
                    }}
                  />
                </Field>
              ) : (
                <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                  <Field
                    label={t("form.fields.insuredRut")}
                    htmlFor="insuredRut"
                    error={err(errors.insuredRut?.message)}
                    hint={t("form.hints.insuredRut")}
                    required
                  >
                    <Input id="insuredRut" placeholder="76.086.428-5" className="h-10" {...register("insuredRut")} />
                  </Field>
                  <Field
                    label={t("form.fields.insuredName")}
                    htmlFor="insuredName"
                    error={err(errors.insuredName?.message)}
                    required
                  >
                    <Input id="insuredName" className="h-10" {...register("insuredName")} />
                  </Field>
                </div>
              )}
            </div>
          </Section>
        </FadeUp>

        {/* ---- 2. Póliza ---- */}
        <FadeUp delay={0.04}>
          <Section title={t("form.sections.policy")}>
            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
              <Field
                label={t("form.fields.insurer")}
                error={err(errors.insurerId?.message)}
                // Visible, not only a tooltip: on a phone the reason must show.
                hint={canInsurers.allowed ? undefined : t("noPermission.insurers")}
                required
              >
                <Controller
                  control={control}
                  name="insurerId"
                  render={({ field }) => (
                    <TouchHint
                      hint={canInsurers.allowed ? null : t("noPermission.insurers")}
                      className="w-full"
                    >
                      <Select
                        value={field.value || undefined}
                        onValueChange={field.onChange}
                        disabled={!canInsurers.allowed}
                      >
                        <SelectTrigger className="h-10 w-full" aria-label={t("form.fields.insurer")}>
                          <SelectValue placeholder={t("form.placeholders.insurer")} />
                        </SelectTrigger>
                        <SelectContent>
                          {(insurers.data?.items ?? []).map((ins) => (
                            <SelectItem key={ins.id} value={String(ins.id)}>
                              {ins.trade_name ?? ins.legal_name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TouchHint>
                  )}
                />
              </Field>

              <Field
                label={t("form.fields.subtype")}
                htmlFor="subtype"
                error={err(errors.subtypeId?.message)}
                required
              >
                <Controller
                  control={control}
                  name="subtypeId"
                  render={({ field }) => (
                    <RamoSubtypeSelect
                      id="subtype"
                      value={field.value}
                      onChange={field.onChange}
                      showSuggestedRoute
                    />
                  )}
                />
              </Field>

              <Field
                label={t("form.fields.startDate")}
                htmlFor="startDate"
                error={err(errors.startDate?.message)}
                required
              >
                <Input id="startDate" type="date" className="h-10" {...register("startDate")} />
              </Field>
              <Field
                label={t("form.fields.endDate")}
                htmlFor="endDate"
                error={err(errors.endDate?.message)}
                required
              >
                <Input id="endDate" type="date" className="h-10" {...register("endDate")} />
              </Field>

              <Field
                label={t("form.fields.policyNumber")}
                htmlFor="policyNumber"
                hint={t("form.hints.policyNumber")}
              >
                <Input id="policyNumber" className="h-10" {...register("policyNumber")} />
              </Field>

              <Field label={t("form.fields.paymentMethod")} hint={t("form.hints.paymentMethod")}>
                <Controller
                  control={control}
                  name="paymentMethod"
                  render={({ field }) => (
                    <Select value={field.value || undefined} onValueChange={field.onChange}>
                      <SelectTrigger className="h-10 w-full" aria-label={t("form.fields.paymentMethod")}>
                        <SelectValue placeholder={t("form.placeholders.paymentMethod")} />
                      </SelectTrigger>
                      <SelectContent>
                        {PAYMENT_METHODS.map((m) => (
                          <SelectItem key={m} value={m}>
                            {t(PAYMENT_METHOD_LABEL[m])}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </Field>

              <label className="flex min-h-10 cursor-pointer items-start gap-2.5 sm:col-span-2">
                <Controller
                  control={control}
                  name="isProvisional"
                  render={({ field }) => (
                    <Checkbox
                      className="mt-0.5"
                      checked={field.value}
                      onCheckedChange={(c) => field.onChange(c === true)}
                    />
                  )}
                />
                <span className="text-body text-ink-2">
                  {t("form.fields.isProvisional")}
                  <span className="block text-caption text-ink-3">{t("form.hints.isProvisional")}</span>
                </span>
              </label>
            </div>
          </Section>
        </FadeUp>

        {/* ---- 3. Primas ---- */}
        <FadeUp delay={0.08}>
          <Section title={t("form.sections.premiums")} description={t("form.sections.premiumsHint")}>
            <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-5">
              <Field label={t("form.fields.currency")}>
                <Controller
                  control={control}
                  name="currency"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger className="h-10 w-full" aria-label={t("form.fields.currency")}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="UF">UF</SelectItem>
                        <SelectItem value="USD">USD</SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                />
              </Field>
              <Field label={t("form.fields.taxable")} htmlFor="taxable" error={err(errors.taxable?.message)}>
                <Input id="taxable" inputMode="decimal" className="h-10 tabular-nums" {...register("taxable")} />
              </Field>
              <Field label={t("form.fields.exempt")} htmlFor="exempt" error={err(errors.exempt?.message)}>
                <Input id="exempt" inputMode="decimal" className="h-10 tabular-nums" {...register("exempt")} />
              </Field>
              <Field
                label={t("form.fields.total")}
                htmlFor="total"
                error={err(errors.total?.message)}
                hint={t("form.hints.total")}
              >
                <Input id="total" inputMode="decimal" className="h-10 tabular-nums" {...register("total")} />
              </Field>
              <Field
                label={t("form.fields.commission")}
                htmlFor="commission"
                error={err(errors.commission?.message)}
                hint={t("form.hints.commission")}
              >
                <Input id="commission" inputMode="decimal" className="h-10 tabular-nums" {...register("commission")} />
              </Field>
            </div>
            {hasInputs ? (
              <div
                className={cn(
                  "mt-4 flex flex-wrap gap-x-6 gap-y-1 rounded-lg px-3 py-2.5 text-caption tabular-nums",
                  totalMismatch ? "bg-warn-soft text-warn-text" : "bg-paper-2 text-ink-2",
                )}
              >
                <span>{t("form.preview.net", { value: fmt(net) })}</span>
                <span>{t("form.preview.vat", { value: fmt(vat) })}</span>
                <span className="font-medium">{t("form.preview.total", { value: fmt(derivedTotal) })}</span>
                {totalMismatch ? <span>{t("form.preview.mismatch")}</span> : null}
              </div>
            ) : null}
          </Section>
        </FadeUp>

        {/* ---- 4. Contratante y pagador ---- */}
        <FadeUp delay={0.12}>
          <Section title={t("form.sections.parties")} description={t("form.sections.partiesHint")}>
            <div className="flex flex-col gap-5">
              <div className="flex flex-col gap-3">
                <label className="flex min-h-10 cursor-pointer items-center gap-2.5">
                  <Controller
                    control={control}
                    name="holderSame"
                    render={({ field }) => (
                      <Checkbox checked={field.value} onCheckedChange={(c) => field.onChange(c === true)} />
                    )}
                  />
                  <span className="text-body text-ink-2">{t("form.fields.holderSame")}</span>
                </label>
                <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                  {!holderSame ? (
                    <>
                      <Field
                        label={t("form.fields.holderName")}
                        htmlFor="holderName"
                        error={err(errors.holder?.name?.message)}
                        required
                      >
                        <Input id="holderName" className="h-10" {...register("holder.name")} />
                      </Field>
                      <Field
                        label={t("form.fields.rut")}
                        htmlFor="holderRut"
                        error={err(errors.holder?.rut?.message)}
                      >
                        <Input id="holderRut" placeholder="76.086.428-5" className="h-10" {...register("holder.rut")} />
                      </Field>
                    </>
                  ) : null}
                  <Field
                    label={t("form.fields.holderEmail")}
                    htmlFor="holderEmail"
                    error={err(errors.holder?.email?.message)}
                    hint={t("form.hints.contact")}
                  >
                    <Input id="holderEmail" type="email" inputMode="email" className="h-10" {...register("holder.email")} />
                  </Field>
                  <Field label={t("form.fields.phone")} htmlFor="holderPhone">
                    <Input id="holderPhone" type="tel" inputMode="tel" className="h-10" {...register("holder.phone")} />
                  </Field>
                </div>
              </div>

              <div className="flex flex-col gap-3 border-t border-line pt-4">
                <label className="flex min-h-10 cursor-pointer items-center gap-2.5">
                  <Controller
                    control={control}
                    name="payerSame"
                    render={({ field }) => (
                      <Checkbox checked={field.value} onCheckedChange={(c) => field.onChange(c === true)} />
                    )}
                  />
                  <span className="text-body text-ink-2">{t("form.fields.payerSame")}</span>
                </label>
                {!payerSame ? (
                  <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                    <Field
                      label={t("form.fields.payerName")}
                      htmlFor="payerName"
                      error={err(errors.payer?.name?.message)}
                      required
                    >
                      <Input id="payerName" className="h-10" {...register("payer.name")} />
                    </Field>
                    <Field label={t("form.fields.rut")} htmlFor="payerRut" error={err(errors.payer?.rut?.message)}>
                      <Input id="payerRut" placeholder="76.086.428-5" className="h-10" {...register("payer.rut")} />
                    </Field>
                    <Field label={t("form.fields.email")} htmlFor="payerEmail" error={err(errors.payer?.email?.message)}>
                      <Input id="payerEmail" type="email" inputMode="email" className="h-10" {...register("payer.email")} />
                    </Field>
                    <Field label={t("form.fields.phone")} htmlFor="payerPhone">
                      <Input id="payerPhone" type="tel" inputMode="tel" className="h-10" {...register("payer.phone")} />
                    </Field>
                  </div>
                ) : null}
              </div>
            </div>
          </Section>
        </FadeUp>

        <FadeUp delay={0.16}>
          <Section title={t("form.sections.notes")}>
            <Textarea rows={3} {...register("notes")} aria-label={t("form.sections.notes")} />
          </Section>
        </FadeUp>

        <ErrorBanner error={serverError} />

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button asChild variant="secondary" className="h-10">
            <Link to="/policies">{t("form.cancel")}</Link>
          </Button>
          <Button type="submit" className="h-10" disabled={create.isPending}>
            {create.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            {t("form.submit")}
          </Button>
        </div>
      </form>
    </>
  );
}
