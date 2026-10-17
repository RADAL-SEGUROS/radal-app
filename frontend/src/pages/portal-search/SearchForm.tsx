/**
 * The search form for ONE portal, rendered from the connector's own
 * `input_fields` (VEH-01 today: patente, marca, modelo, año, uso, comuna, RUT,
 * inicio) — a new field server-side renders without a code change, labelled
 * through the explicit `FIELD_LABEL_KEY` map (humanised name as fallback).
 *
 * The ramo → subtipo cascade reads the closed catalogue (`useCatalogLines`)
 * narrowed to the portal's `supported_subtypes`. Validation mirrors the
 * server's (`validate_vehicle_input`) so most mistakes never leave the page;
 * a server `invalid_input` 422 still maps its `errors[]` back onto the fields.
 */
import * as React from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { CircleAlert, KeyRound, Link2, Loader2, X } from "lucide-react";

import {
  portalErrorDetail,
  useCreatePortalRun,
  type Portal,
  type PortalInputField,
  type PortalRun,
} from "@/api/rpa";
import { useCatalogLines } from "@/api/catalog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DisabledHint, apiError } from "@/components/common/kit";
import { isValidRut, normalizeRut } from "@/pages/clients/rut";
import { cn } from "@/lib/utils";
import {
  FIELD_LABEL_KEY,
  FIELD_PLACEHOLDER_KEY,
  OPTION_LABEL_KEY,
  humanizeField,
  own,
} from "@/pages/portal-search/labels";

type Values = Record<string, string>;
type TFn = (key: string, opts?: Record<string, unknown>) => string;

const PLATE_RE = /^(?:[A-Z]{2}\d{4}|[A-Z]{4}\d{2})$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function normalizePlate(raw: string): string {
  return raw.replace(/[\s.\-·]/g, "").toUpperCase();
}

function buildSchema(fields: PortalInputField[], t: TFn) {
  const maxYear = new Date().getFullYear() + 1;
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const field of fields) {
    shape[field.name] = z.string().superRefine((raw, ctx) => {
      const value = raw.trim();
      const fail = (message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, message });
      if (!value) {
        if (field.required) fail(t("validation.required"));
        return;
      }
      if (field.name === "plate" && !PLATE_RE.test(normalizePlate(value))) {
        fail(t("validation.plate"));
        return;
      }
      switch (field.type) {
        case "integer": {
          if (!/^\d+$/.test(value)) {
            fail(t("validation.integer"));
            return;
          }
          const n = Number(value);
          if (field.name === "year" && (n < 1950 || n > maxYear)) {
            fail(t("validation.year", { max: maxYear }));
          }
          return;
        }
        case "rut":
          if (!isValidRut(value)) fail(t("validation.rut"));
          return;
        case "date":
          if (!DATE_RE.test(value) || Number.isNaN(Date.parse(value))) fail(t("validation.date"));
          return;
        case "select":
          if (field.options && !field.options.includes(value)) fail(t("validation.option"));
          return;
        default:
          return;
      }
    });
  }
  return z.object(shape);
}

function blankValues(fields: PortalInputField[], seed?: Record<string, unknown> | null): Values {
  const out: Values = {};
  for (const field of fields) {
    const raw = seed?.[field.name];
    out[field.name] = raw === null || raw === undefined ? "" : String(raw);
  }
  return out;
}

function toPayload(fields: PortalInputField[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    const value = (values[field.name] ?? "").trim();
    if (!value) continue;
    if (field.name === "plate") out[field.name] = normalizePlate(value);
    else if (field.type === "integer") out[field.name] = Number(value);
    else if (field.type === "rut") out[field.name] = normalizeRut(value) ?? value;
    else out[field.name] = value;
  }
  return out;
}

export interface SearchPrefill {
  runId: number;
  input: Record<string, unknown>;
  subtypeId: number | null;
}

export function SearchForm({
  portal,
  canSubmit,
  permsLoading,
  canManageCredentials,
  caseFileId,
  onUnlinkCase,
  prefill,
  onQueued,
  onConfigure,
}: {
  portal: Portal;
  canSubmit: boolean;
  permsLoading: boolean;
  canManageCredentials: boolean;
  caseFileId: number | null;
  onUnlinkCase: () => void;
  prefill: SearchPrefill | null;
  onQueued: (run: PortalRun) => void;
  onConfigure: () => void;
}) {
  const { t } = useTranslation("rpa");
  const tt = React.useCallback<TFn>((key, opts) => t(key, opts), [t]);
  const fields = portal.input_fields;
  const schema = React.useMemo(() => buildSchema(fields, tt), [fields, tt]);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: blankValues(fields, prefill?.input),
  });
  const create = useCreatePortalRun();

  // --- ramo → subtipo, narrowed to what this portal quotes -----------------------
  const catalog = useCatalogLines();
  const lines = React.useMemo(() => {
    const supported = new Set(portal.supported_subtypes);
    return (catalog.data ?? [])
      .map((line) => ({ ...line, subtypes: line.subtypes.filter((s) => supported.has(s.code)) }))
      .filter((line) => line.subtypes.length > 0);
  }, [catalog.data, portal.supported_subtypes]);
  const allowedIds = React.useMemo(
    () => new Set(lines.flatMap((line) => line.subtypes.map((s) => s.id))),
    [lines],
  );

  const [subtypeId, setSubtypeId] = React.useState<number | null>(null);
  const [lineId, setLineId] = React.useState<number | null>(null);
  const [subtypeError, setSubtypeError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!lines.length) return;
    const fromPrefill = prefill?.subtypeId && allowedIds.has(prefill.subtypeId) ? prefill.subtypeId : null;
    if (fromPrefill) {
      setSubtypeId(fromPrefill);
      setLineId(lines.find((l) => l.subtypes.some((s) => s.id === fromPrefill))?.id ?? null);
      return;
    }
    // One ramo with one subtipo (VEH-01 on HDI): nothing to choose.
    if (lines.length === 1) {
      setLineId(lines[0].id);
      if (lines[0].subtypes.length === 1) setSubtypeId(lines[0].subtypes[0].id);
    }
  }, [lines, allowedIds, prefill]);

  const line = lines.find((l) => l.id === lineId) ?? null;

  const needsCredentials = portal.requires_credentials && !portal.has_credentials;
  const submitHint = !canSubmit && !permsLoading
    ? t("form.noPermission")
    : needsCredentials
      ? t("form.needsCredentials", { portal: portal.name })
      : null;
  const submitDisabled =
    permsLoading || !canSubmit || needsCredentials || create.isPending || lines.length === 0;

  const onSubmit = form.handleSubmit((values) => {
    if (!subtypeId) {
      setSubtypeError(t("validation.required"));
      return;
    }
    setSubtypeError(null);
    create.mutate(
      {
        portal: portal.key,
        insurance_subtype_id: subtypeId,
        case_file_id: caseFileId,
        input: toPayload(fields, values),
      },
      {
        onSuccess: (run) => {
          if (run.will_not_run) toast.warning(run.will_not_run_reason || t("form.willNotRun"));
          else toast.success(t("form.queued"));
          onQueued(run);
        },
        onError: (error) => {
          const detail = portalErrorDetail(error);
          for (const item of detail?.errors ?? []) {
            if (item.field in values) {
              form.setError(item.field, { type: "server", message: item.message });
            }
          }
          toast.error(detail?.message ?? apiError(error, t("form.invalid")));
        },
      },
    );
  });

  const fieldLabel = (name: string) => {
    const key = own(FIELD_LABEL_KEY, name);
    return key ? t(key) : humanizeField(name);
  };
  const fieldPlaceholder = (name: string) => {
    const key = own(FIELD_PLACEHOLDER_KEY, name);
    return key ? t(key) : undefined;
  };
  const optionLabel = (name: string, option: string) => {
    const key = own(own(OPTION_LABEL_KEY, name) ?? {}, option);
    return key ? t(key) : option;
  };

  return (
    <Card className="p-4 sm:p-5">
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <div>
          <h2 className="text-h3 tracking-tight text-ink">
            {t("form.title", { portal: portal.name })}
          </h2>
          <p className="mt-1 text-caption text-ink-3 [text-wrap:pretty]">{t("form.hint")}</p>
        </div>

        {prefill ? (
          <p className="text-caption text-brand-deep">{t("form.prefilled", { id: prefill.runId })}</p>
        ) : null}

        {caseFileId ? (
          <span className="inline-flex w-fit items-center gap-1.5 rounded-[5px] bg-brand-soft py-[3px] pe-1 ps-2 text-caption text-brand-deep">
            <Link2 className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
            {t("form.linkedCase", { id: caseFileId })}
            <button
              type="button"
              onClick={onUnlinkCase}
              aria-label={t("form.unlinkCase")}
              className="flex h-6 w-6 items-center justify-center rounded-[4px] transition-[background-color] duration-150 ease-out hover:bg-[color-mix(in_srgb,var(--brand)_18%,transparent)] sm:h-5 sm:w-5"
            >
              <X className="h-3 w-3" strokeWidth={2} aria-hidden />
            </button>
          </span>
        ) : null}

        {needsCredentials ? (
          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-warn-soft px-3.5 py-2.5 text-caption text-ink-2">
            <CircleAlert className="h-4 w-4 shrink-0 text-warn-text" strokeWidth={1.5} aria-hidden />
            <span className="min-w-0 flex-1 [text-wrap:pretty]">
              {canManageCredentials
                ? t("form.needsCredentials", { portal: portal.name })
                : t("form.needsCredentialsNoPerm", { portal: portal.name })}
            </span>
            {canManageCredentials ? (
              <Button type="button" size="sm" variant="secondary" className="min-h-10 sm:min-h-8" onClick={onConfigure}>
                <KeyRound className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                {t("portal.configure")}
              </Button>
            ) : null}
          </div>
        ) : null}

        {/* Ramo → subtipo */}
        {catalog.isLoading ? null : lines.length === 0 ? (
          <p className="rounded-lg bg-paper-2 px-3 py-2.5 text-caption text-ink-3">{t("form.noSubtypes")}</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rpa-ramo" className="text-caption text-ink-3">
                {t("form.ramo")}
              </Label>
              <Select
                value={lineId ? String(lineId) : ""}
                onValueChange={(value) => {
                  const next = lines.find((l) => l.id === Number(value)) ?? null;
                  setLineId(next?.id ?? null);
                  setSubtypeId(next && next.subtypes.length === 1 ? next.subtypes[0].id : null);
                }}
              >
                <SelectTrigger id="rpa-ramo" className="h-10 sm:h-9">
                  <SelectValue placeholder={t("form.ramoPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {lines.map((item) => (
                    <SelectItem key={item.id} value={String(item.id)}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="rpa-subtype" className="text-caption text-ink-3">
                {t("form.subtype")}
              </Label>
              <Select
                value={subtypeId ? String(subtypeId) : ""}
                disabled={!line}
                onValueChange={(value) => {
                  setSubtypeId(Number(value));
                  setSubtypeError(null);
                }}
              >
                <SelectTrigger id="rpa-subtype" className="h-10 sm:h-9" aria-invalid={!!subtypeError}>
                  <SelectValue placeholder={t("form.subtypePlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {(line?.subtypes ?? []).map((item) => (
                    <SelectItem key={item.id} value={String(item.id)}>
                      {item.code} · {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {subtypeError ? <p className="text-caption text-neg-text">{subtypeError}</p> : null}
            </div>
          </div>
        )}

        {/* The connector's fields */}
        <div className="grid gap-3 sm:grid-cols-2">
          {fields.map((field) => {
            const id = `rpa-field-${field.name}`;
            const error = form.formState.errors[field.name]?.message;
            return (
              <div key={field.name} className="flex flex-col gap-1.5">
                <Label htmlFor={id} className="text-caption text-ink-3">
                  {fieldLabel(field.name)}
                  {field.required ? null : (
                    <span className="ms-1 font-normal text-ink-3">({t("form.optional")})</span>
                  )}
                </Label>
                {field.type === "select" && field.options?.length ? (
                  <Controller
                    control={form.control}
                    name={field.name}
                    render={({ field: control }) => (
                      <Select value={control.value || ""} onValueChange={control.onChange}>
                        <SelectTrigger id={id} className="h-10 sm:h-9" aria-invalid={!!error}>
                          <SelectValue placeholder={fieldPlaceholder(field.name)} />
                        </SelectTrigger>
                        <SelectContent>
                          {(field.options ?? []).map((option) => (
                            <SelectItem key={option} value={option}>
                              {optionLabel(field.name, option)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                ) : (
                  <Input
                    id={id}
                    type={field.type === "date" ? "date" : "text"}
                    inputMode={field.type === "integer" ? "numeric" : undefined}
                    autoCapitalize={field.name === "plate" ? "characters" : undefined}
                    autoComplete="off"
                    placeholder={fieldPlaceholder(field.name)}
                    aria-invalid={!!error}
                    className={cn("h-10 sm:h-9", error && "border-neg")}
                    {...form.register(field.name)}
                  />
                )}
                {error ? <p className="text-caption text-neg-text">{error}</p> : null}
              </div>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
          <DisabledHint hint={submitHint} className="w-full sm:w-auto">
            <Button type="submit" disabled={submitDisabled} className="min-h-10 w-full sm:w-auto">
              {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
              {create.isPending ? t("form.submitting") : t("form.submit")}
            </Button>
          </DisabledHint>
        </div>
      </form>
    </Card>
  );
}
