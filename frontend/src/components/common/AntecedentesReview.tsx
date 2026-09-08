/**
 * Antecedentes review — the "human validates & completes" step (spec v6 §E).
 *
 * This is NOT `SuggestionForm`: that one is a flat scalar renderer that dumps
 * lists and objects into raw JSON textareas. An antecedentes schema is nested —
 * sections of fields, repeatable tables, and grouped sub-fields — so this
 * component walks the ramo schema and renders a real input per field type,
 * backed by `react-hook-form` (`useFieldArray` for the repeatable tables).
 *
 * Three things carry over from `SuggestionForm` and must not be lost:
 *   1. the **provenance card** — model, prompt version, confidence, tokens and
 *      the extractor's warnings, visible while the human reviews;
 *   2. **override highlighting** — a field the human changed is marked, so at
 *      register time it is obvious what is the machine's and what is the broker's;
 *   3. **nothing auto-commits** — the button posts the assembled sectioned
 *      payload to `…/antecedentes/register` and is disabled, with the reason on
 *      the tooltip, when the user lacks the grant.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  useController,
  useFieldArray,
  useForm,
  useWatch,
  type Control,
  type FieldValues,
} from "react-hook-form";
import { AlertTriangle, Plus, Sparkles, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  ConfidenceBadge,
  DisabledHint,
  ErrorBanner,
  KeyValue,
  Section,
  StatusBadge,
} from "@/components/common/kit";
import { useRegisterAntecedentes } from "@/api/antecedentes";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  AntecedentesExpediente,
  AntecedentesSuggestion,
  RamoField,
  RamoSchema,
  RamoSection,
} from "@/api/types";

// =============================================================================
// Schema-driven value shaping
// =============================================================================

/** A `list`, or any field flagged `repeatable`, renders as a table of rows. */
function isRepeatable(field: RamoField): boolean {
  return field.type === "list" || !!field.repeatable;
}

/** The columns of a repeatable table — nested `fields`, or one implicit column. */
function rowFields(field: RamoField): RamoField[] {
  if (field.fields && field.fields.length) return field.fields;
  return [{ key: "value", label: field.label, type: field.type === "list" ? "text" : field.type }];
}

/** A scalar's DOM value: booleans stay boolean, everything else is a string. */
function scalarToInput(field: RamoField, raw: unknown): string | boolean {
  if (field.type === "boolean") return raw === true || raw === "true";
  if (raw === null || raw === undefined) return "";
  return typeof raw === "object" ? JSON.stringify(raw) : String(raw);
}

function buildGroup(fields: RamoField[], raw: unknown): Record<string, unknown> {
  const src = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const out: Record<string, unknown> = {};
  for (const f of fields) out[f.key] = buildField(f, src[f.key]);
  return out;
}

function buildRepeatable(field: RamoField, raw: unknown): Record<string, unknown>[] {
  const cols = rowFields(field);
  const rows = Array.isArray(raw) ? raw : [];
  return rows.map((row) => {
    const src = row && typeof row === "object" ? (row as Record<string, unknown>) : {};
    const out: Record<string, unknown> = {};
    for (const c of cols) out[c.key] = buildField(c, src[c.key]);
    return out;
  });
}

function buildField(field: RamoField, raw: unknown): unknown {
  if (isRepeatable(field)) return buildRepeatable(field, raw);
  if (field.type === "group") return buildGroup(field.fields ?? [], raw);
  return scalarToInput(field, raw);
}

/** Build the RHF default values: `{ [sectionKey]: { [fieldKey]: value } }`. */
function buildDefaults(schema: RamoSchema, payload: Record<string, unknown> | null): FieldValues {
  const out: FieldValues = {};
  for (const section of schema.sections) {
    const src =
      payload?.[section.key] && typeof payload[section.key] === "object"
        ? (payload[section.key] as Record<string, unknown>)
        : {};
    const sec: Record<string, unknown> = {};
    for (const f of section.fields) sec[f.key] = buildField(f, src[f.key]);
    out[section.key] = sec;
  }
  return out;
}

function coerceScalar(field: RamoField, val: unknown): unknown {
  if (field.type === "boolean") return val === true || val === "true";
  const s = typeof val === "string" ? val.trim() : val;
  if (s === "" || s === null || s === undefined) return null;
  if (field.type === "number" || field.type === "money_uf" || field.type === "percent") {
    const n = Number(s);
    return Number.isNaN(n) ? null : n;
  }
  if (field.type === "integer") {
    const n = parseInt(String(s), 10);
    return Number.isNaN(n) ? null : n;
  }
  return String(s);
}

function coerceGroup(fields: RamoField[], val: unknown): Record<string, unknown> {
  const src = val && typeof val === "object" ? (val as Record<string, unknown>) : {};
  const out: Record<string, unknown> = {};
  for (const f of fields) out[f.key] = coerceField(f, src[f.key]);
  return out;
}

function coerceField(field: RamoField, val: unknown): unknown {
  if (isRepeatable(field)) {
    const cols = rowFields(field);
    const rows = Array.isArray(val) ? val : [];
    return rows.map((row) => coerceGroup(cols, row));
  }
  if (field.type === "group") return coerceGroup(field.fields ?? [], val);
  return coerceScalar(field, val);
}

/** The wire payload: the DOM strings coerced back into typed, sectioned values. */
function assemblePayload(schema: RamoSchema, values: FieldValues): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const section of schema.sections) {
    const src = (values?.[section.key] as Record<string, unknown> | undefined) ?? {};
    const sec: Record<string, unknown> = {};
    for (const f of section.fields) sec[f.key] = coerceField(f, src[f.key]);
    out[section.key] = sec;
  }
  return out;
}

// =============================================================================
// The form
// =============================================================================

export interface AntecedentesReviewProps {
  caseId: number;
  suggestion: AntecedentesSuggestion;
  canConfirm?: boolean;
  onRegistered?: (expediente: AntecedentesExpediente) => void;
  className?: string;
}

export function AntecedentesReview({
  caseId,
  suggestion,
  canConfirm = false,
  onRegistered,
  className,
}: AntecedentesReviewProps) {
  const { t } = useTranslation("antecedentes");
  const { t: tp } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");

  const schema = suggestion.schema;
  const { extraction, warnings } = suggestion;

  const defaults = React.useMemo(
    () => (schema ? buildDefaults(schema, suggestion.payload) : {}),
    [schema, suggestion.payload],
  );

  const form = useForm<FieldValues>({ defaultValues: defaults });
  const { control, handleSubmit, reset } = form;

  // A fresh suggestion (re-process) rebuilds the form from scratch.
  React.useEffect(() => {
    reset(defaults);
  }, [defaults, reset]);

  const register = useRegisterAntecedentes(caseId);

  const submit = handleSubmit((values) => {
    if (!schema) return;
    register.mutate(
      { payload: assemblePayload(schema, values) },
      { onSuccess: (data) => onRegistered?.(data) },
    );
  });

  const totalTokens = (extraction.prompt_tokens ?? 0) + (extraction.completion_tokens ?? 0);

  if (!schema) {
    return (
      <Card className="p-5">
        <p className="text-caption text-ink-3">{t("process.noSchema")}</p>
      </Card>
    );
  }

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {/* ── Provenance ── */}
      <Card className="grid grid-cols-2 gap-5 p-5 md:grid-cols-5">
        <KeyValue label={tp("ai.model")} value={<span className="text-caption">{extraction.model}</span>} />
        <KeyValue label={tp("ai.promptVersion")} value={extraction.prompt_version} />
        <KeyValue
          label={tp("ai.status")}
          value={
            <StatusBadge
              value={extraction.status}
              label={tp(`ai.statuses.${extraction.status}`, { defaultValue: extraction.status })}
            />
          }
        />
        <KeyValue label={t("view.confidence")} value={<ConfidenceBadge value={extraction.confidence} />} />
        <KeyValue label={tp("ai.tokens")} value={formatNumber(totalTokens)} />

        <div className="col-span-2 flex flex-wrap items-center gap-3 md:col-span-5">
          <Badge variant="brand" className="gap-1">
            <Sparkles className="h-3 w-3" />
            {t("view.title")}
          </Badge>
          <StatusBadge
            value={suggestion.status}
            label={t(`status.${suggestion.status}`, { defaultValue: suggestion.status })}
          />
        </div>

        {warnings.length ? (
          <div className="col-span-2 flex flex-col gap-1.5 rounded-lg border border-warn-line bg-warn-soft px-3.5 py-2.5 md:col-span-5">
            <span className="text-caption font-medium text-warn-text">{t("review.warningsTitle")}</span>
            {warnings.map((warning, i) => (
              <p key={i} className="flex items-start gap-2 text-caption text-ink-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn-text" />
                {warning}
              </p>
            ))}
          </div>
        ) : null}
      </Card>

      {/* ── The schema-driven form ── */}
      <ReviewBody schema={schema} control={control} defaults={defaults} />

      {register.isError ? <ErrorBanner error={register.error} /> : null}

      <div className="flex items-center justify-end gap-3">
        <DisabledHint hint={canConfirm ? null : t("review.noPermission")}>
          <Button disabled={!canConfirm || register.isPending} onClick={() => void submit()}>
            {register.isPending ? tc("actions.loading") : t("review.confirm")}
          </Button>
        </DisabledHint>
      </div>
    </div>
  );
}

/**
 * The body is split out so the overrides badge can subscribe to the whole form
 * (`useWatch`) without re-rendering the provenance card or the submit button.
 */
/** Required top-level fields of one section that are still empty in `values`. */
function sectionMissing(section: RamoSection, values: unknown): RamoField[] {
  const src = (values as Record<string, unknown> | undefined) ?? {};
  const out: RamoField[] = [];
  for (const f of section.fields) {
    if (!f.required) continue;
    const v = src[f.key];
    if (isRepeatable(f)) {
      if (!Array.isArray(v) || v.length === 0) out.push(f);
    } else if (f.type === "group") {
      const g = (v as Record<string, unknown> | undefined) ?? {};
      const anyFilled = (f.fields ?? []).some((sub) => coerceScalar(sub, g[sub.key]) !== null);
      if (!anyFilled) out.push(f);
    } else if (coerceScalar(f, v) === null) {
      out.push(f);
    }
  }
  return out;
}

function ReviewBody({
  schema,
  control,
  defaults,
}: {
  schema: RamoSchema;
  control: Control<FieldValues>;
  defaults: FieldValues;
}) {
  const { t } = useTranslation("antecedentes");
  const watched = useWatch({ control }) as FieldValues;
  const overrides = countScalarChanges(schema, defaults, watched);

  const missingBySection = schema.sections.map((section) =>
    sectionMissing(section, watched?.[section.key]),
  );
  const missingTotal = missingBySection.reduce((sum, list) => sum + list.length, 0);

  return (
    <>
      {schema.sections.map((section, si) => {
        const missing = missingBySection[si];
        return (
        <Section
          key={section.key}
          title={t(`sections.${section.key}`, { defaultValue: section.label })}
          description={section.description ?? undefined}
          bodyClassName="p-0"
          actions={
            <span className="flex items-center gap-2">
              {missing.length > 0 ? (
                <Badge variant="warn">
                  {t("review.sectionMissing", { count: missing.length })}
                </Badge>
              ) : null}
              {overrides > 0 ? (
                <Badge variant="action">{t("review.overrides", { count: overrides })}</Badge>
              ) : null}
            </span>
          }
        >
          {section.fields.length === 0 ? (
            <p className="px-5 py-4 text-caption text-ink-3">{t("review.noFields")}</p>
          ) : (
            <div className="divide-y divide-line">
              {section.fields.map((field) => (
                <div key={field.key} className="px-5 py-4">
                  <FieldControl
                    control={control}
                    name={`${section.key}.${field.key}`}
                    field={field}
                    original={(defaults?.[section.key] as Record<string, unknown> | undefined)?.[field.key]}
                  />
                </div>
              ))}
            </div>
          )}
        </Section>
        );
      })}

      {missingTotal > 0 ? (
        <div className="flex items-start gap-2 rounded-lg border border-warn-line bg-warn-soft px-4 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn-text" />
          <div className="flex flex-col gap-0.5">
            <span className="text-caption font-medium text-warn-text">
              {t("review.missingSummary", { count: missingTotal })}
            </span>
            <span className="text-caption text-ink-2">
              {missingBySection
                .flat()
                .map((f) => f.label)
                .join(", ")}
            </span>
          </div>
        </div>
      ) : null}
    </>
  );
}

/** Count scalar leaves whose current value differs from the suggestion. */
function countScalarChanges(schema: RamoSchema, defaults: FieldValues, current: FieldValues): number {
  let n = 0;
  const walk = (fields: RamoField[], base: unknown, now: unknown) => {
    const b = (base as Record<string, unknown> | undefined) ?? {};
    const c = (now as Record<string, unknown> | undefined) ?? {};
    for (const f of fields) {
      if (isRepeatable(f)) {
        if (JSON.stringify(b[f.key] ?? []) !== JSON.stringify(c[f.key] ?? [])) n += 1;
      } else if (f.type === "group") {
        walk(f.fields ?? [], b[f.key], c[f.key]);
      } else if (String(b[f.key] ?? "") !== String(c[f.key] ?? "")) {
        n += 1;
      }
    }
  };
  for (const section of schema.sections) {
    walk(section.fields, defaults?.[section.key], current?.[section.key]);
  }
  return n;
}

// =============================================================================
// Field controls
// =============================================================================

function FieldControl({
  control,
  name,
  field,
  original,
}: {
  control: Control<FieldValues>;
  name: string;
  field: RamoField;
  original: unknown;
}) {
  if (isRepeatable(field)) {
    return <RepeatableTable control={control} name={name} field={field} />;
  }
  if (field.type === "group") {
    return <GroupBlock control={control} name={name} field={field} original={original} />;
  }
  return <ScalarField control={control} name={name} field={field} original={original} />;
}

function FieldLabel({ field, changed }: { field: RamoField; changed?: boolean }) {
  const { t } = useTranslation("antecedentes");
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="text-label font-medium text-ink">{field.label}</span>
      {field.unit ? <span className="text-caption text-ink-3">({field.unit})</span> : null}
      {field.required ? (
        <Badge variant="muted" className="px-1.5 py-0 text-[9.5px]">
          {t("review.required")}
        </Badge>
      ) : null}
      {changed ? (
        <Badge variant="action" className="px-1.5 py-0 text-[9.5px]">
          {t("review.edited")}
        </Badge>
      ) : null}
    </span>
  );
}

function ScalarField({
  control,
  name,
  field,
  original,
  compact,
}: {
  control: Control<FieldValues>;
  name: string;
  field: RamoField;
  original?: unknown;
  /** Table cells drop the label and description. */
  compact?: boolean;
}) {
  const { t } = useTranslation("antecedentes");
  const value = useWatch({ control, name });
  const { field: rhf } = useControllerLoose(control, name);

  const changed =
    field.type === "boolean"
      ? Boolean(value) !== Boolean(original)
      : String(value ?? "") !== String(original ?? "");

  const control_ = (() => {
    if (field.type === "boolean") {
      return (
        <div className="flex h-9 items-center">
          <Checkbox
            checked={value === true || value === "true"}
            onCheckedChange={(v) => rhf.onChange(v === true)}
          />
          <span className="ml-2 text-caption text-ink-2">
            {value === true || value === "true" ? t("review.yes") : t("review.no")}
          </span>
        </div>
      );
    }
    if (field.type === "select") {
      return (
        <select
          value={String(value ?? "")}
          onChange={(e) => rhf.onChange(e.target.value)}
          className={cn(
            "h-9 w-full rounded-lg border bg-bone px-3 text-body text-ink outline-none focus-visible:ring-2 focus-visible:ring-ring",
            changed ? "border-brand" : "border-line",
          )}
        >
          <option value="">{t("review.unset")}</option>
          {(field.options ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      );
    }
    const numeric =
      field.type === "number" ||
      field.type === "integer" ||
      field.type === "money_uf" ||
      field.type === "percent";
    return (
      <Input
        value={String(value ?? "")}
        type={field.type === "date" ? "date" : numeric ? "number" : "text"}
        step={field.type === "integer" ? "1" : numeric ? "0.0001" : undefined}
        onChange={(e) => rhf.onChange(e.target.value)}
        className={cn(numeric && "text-right tabular-nums", changed && "border-brand")}
      />
    );
  })();

  if (compact) return control_;

  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel field={field} changed={changed} />
      {control_}
      {field.description ? <p className="text-caption text-ink-3">{field.description}</p> : null}
    </div>
  );
}

function GroupBlock({
  control,
  name,
  field,
  original,
}: {
  control: Control<FieldValues>;
  name: string;
  field: RamoField;
  original: unknown;
}) {
  const subFields = field.fields ?? [];
  const originalGroup = (original as Record<string, unknown> | undefined) ?? {};
  return (
    <div className="flex flex-col gap-3">
      <FieldLabel field={field} />
      {field.description ? <p className="text-caption text-ink-3">{field.description}</p> : null}
      <div className="grid gap-4 rounded-lg border border-line bg-bone/50 p-4 md:grid-cols-2">
        {subFields.map((sub) => (
          <FieldControl
            key={sub.key}
            control={control}
            name={`${name}.${sub.key}`}
            field={sub}
            original={originalGroup[sub.key]}
          />
        ))}
      </div>
    </div>
  );
}

function RepeatableTable({
  control,
  name,
  field,
}: {
  control: Control<FieldValues>;
  name: string;
  field: RamoField;
}) {
  const { t } = useTranslation("antecedentes");
  const cols = rowFields(field);
  const { fields, append, remove } = useFieldArray({ control, name });

  const emptyRow = React.useMemo(() => {
    const row: Record<string, unknown> = {};
    for (const c of cols) row[c.key] = buildField(c, undefined);
    return row;
  }, [cols]);

  return (
    <div className="flex flex-col gap-2.5">
      <FieldLabel field={field} />
      {field.description ? <p className="text-caption text-ink-3">{field.description}</p> : null}

      {fields.length === 0 ? (
        <p className="text-caption text-ink-3">{t("review.emptyTable")}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="border-b border-line bg-paper-2/60 text-left">
                {cols.map((c) => (
                  <th key={c.key} className="px-3 py-2 text-caption font-medium text-ink-3">
                    {c.label}
                    {c.required ? <span className="ml-1 text-warn-text">*</span> : null}
                  </th>
                ))}
                <th className="w-10 px-3 py-2" aria-hidden />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {fields.map((row, index) => (
                <tr key={row.id}>
                  {cols.map((c) => (
                    <td key={c.key} className="px-3 py-2 align-top">
                      <ScalarField
                        control={control}
                        name={`${name}.${index}.${c.key}`}
                        field={c}
                        compact
                      />
                    </td>
                  ))}
                  <td className="px-3 py-2 text-right align-top">
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-8 w-8"
                      aria-label={t("review.removeRow")}
                      onClick={() => remove(index)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div>
        <Button type="button" size="sm" variant="secondary" onClick={() => append(emptyRow)}>
          <Plus className="h-3.5 w-3.5" />
          {t("review.addRow")}
        </Button>
      </div>
    </div>
  );
}

// A tiny wrapper so the dynamic string field name stays typed loosely.
function useControllerLoose(control: Control<FieldValues>, name: string) {
  return useController({ control, name });
}
