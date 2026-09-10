/**
 * Bases Técnicas — the completed antecedentes, rendered as the document the
 * broker sends (spec v7 §D). This IS the bases-técnicas builder: the account's
 * assigned line drives what is requested, the AI fills it (suggest → confirm →
 * commit, on the Antecedentes tab), and once the line's mandatory fields are
 * satisfied the broker downloads "Bases Técnicas".
 *
 * The view carries, at every status:
 *   - the assigned LINE name + a "Cambiar línea" control (pick a broker line for
 *     the ramo, or adopt a recommended template — both via `POST …/line`);
 *   - a REQUISITOS checklist (the line's `required_fields`, ticking off what is
 *     still `missing_required`), visible before and after processing;
 *   - the "Descargar Bases Técnicas" button, gated on `complete` (the missing
 *     mandatory labels shown on the tooltip when not). No dead button.
 * The registered payload is grouped by the line's sections, each empty value an
 * explicit marker — the same convention the PDF uses.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Check, Copy, Download, FileStack, FileText, Layers, Loader2, Minus, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  Section,
  StatusBadge,
  apiError,
  pct,
  uf,
} from "@/components/common/kit";
import { AntecedentesReview } from "@/components/common/AntecedentesReview";
import {
  useAssignLine,
  useCreateRamoSchema,
  useExpediente,
  useExpedientePdf,
  useProcessAntecedentes,
  useRamoSchema,
  useRamoSchemas,
} from "@/api/antecedentes";
import { useCan } from "@/lib/permissions";
import { downloadDocument } from "@/lib/download";
import { formatDate } from "@/lib/format";
import type {
  AntecedentesExpediente,
  AntecedentesSuggestion,
  LineRecordSchema,
  RamoField,
  RamoRequiredField,
  RamoSchema,
} from "@/api/types";

// =============================================================================
// Value helpers
// =============================================================================

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/** Format one scalar for read-only display, by its declared field type. */
function formatScalar(field: RamoField, value: unknown, yes: string, no: string): string {
  if (field.type === "boolean") return value === true || value === "true" ? yes : no;
  if (isEmpty(value)) return "—";
  if (field.type === "money_uf") return uf(value as number);
  if (field.type === "percent") return pct(value as number);
  if (field.type === "date") return formatDate(String(value));
  return String(value);
}

/** The line's display name, falling back to the ramo id. */
function lineName(data: AntecedentesExpediente, t: (k: string) => string): string {
  if (data.line_name) return data.line_name;
  if (data.insurance_line_id) return `#${data.insurance_line_id}`;
  return t("basesTecnicas.lineUnassigned");
}

// =============================================================================
// The view
// =============================================================================

export interface ExpedienteViewProps {
  caseId: number;
}

/**
 * Descargar Bases Técnicas — resolves the generated PDF Document via
 * `GET …/antecedentes/pdf`, then fetches its bytes AUTHENTICATED and saves them
 * (the content route 401s for a plain link). Not a route navigation.
 */
function DownloadPdfButton({ caseId, disabledHint }: { caseId: number; disabledHint: string | null }) {
  const { t } = useTranslation("antecedentes");
  // Manual-trigger query: resolve the PDF Document only when the broker asks.
  const { data, refetch } = useExpedientePdf(caseId, false);
  const [busy, setBusy] = React.useState(false);

  const onClick = async () => {
    setBusy(true);
    try {
      const doc = data?.id ? data : (await refetch()).data;
      if (doc?.id) await downloadDocument(doc.id, doc.original_name);
      else toast.error(t("basesTecnicas.download"));
    } catch (error) {
      toast.error(apiError(error, t("basesTecnicas.download")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <DisabledHint hint={disabledHint}>
      <Button size="sm" disabled={!!disabledHint || busy} onClick={onClick}>
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Download className="h-4 w-4" />
        )}
        {busy ? t("basesTecnicas.generating") : t("basesTecnicas.download")}
      </Button>
    </DisabledHint>
  );
}

export function ExpedienteView({ caseId }: ExpedienteViewProps) {
  const { t } = useTranslation("antecedentes");
  const expediente = useExpediente(caseId);
  const canView = useCan("CaseFiles", "View");
  const canEdit = useCan("CaseFiles", "Edit");
  const [changing, setChanging] = React.useState(false);

  // Consolidate the account's antecedentes into this Bases-Técnicas expediente
  // (suggest → the human validates & completes → register — CLAUDE.md rule 6).
  const insuranceLineId = expediente.data?.insurance_line_id ?? null;
  const ramoSchema = useRamoSchema(insuranceLineId);
  const process = useProcessAntecedentes();
  const [suggestion, setSuggestion] = React.useState<AntecedentesSuggestion | null>(null);

  const processHint = !canEdit.allowed
    ? t("process.noPermission")
    : ramoSchema.isLoading
      ? t("process.loadingSchema")
      : !ramoSchema.data
        ? t("process.noSchema")
        : null;

  const onProcess = () => {
    setSuggestion(null);
    process.mutate(
      { case_file_id: caseId },
      { onSuccess: (data) => setSuggestion(data) },
    );
  };

  if (expediente.isLoading) return <Skeleton className="h-64 w-full" />;
  if (expediente.isError) return <ErrorBanner error={expediente.error} />;

  const data = expediente.data;
  if (!data) {
    return (
      <Card>
        <EmptyState
          icon={<FileText className="h-6 w-6" />}
          title={t("basesTecnicas.empty")}
          hint={t("basesTecnicas.emptyHint")}
        />
      </Card>
    );
  }

  const registered = data.status === "registered";
  const hasPayload = data.status === "review" || data.status === "registered";
  const requiredFields = data.required_fields ?? [];
  const missingRequired = data.missing_required ?? [];
  const complete = data.complete ?? missingRequired.length === 0;
  const missingLabels = missingRequired.map((m) => m.label).join(", ");

  const pdfHint = !canView.allowed
    ? t("basesTecnicas.noPermission")
    : !complete
      ? t("basesTecnicas.incomplete", { fields: missingLabels })
      : !registered
        ? t("basesTecnicas.notRegistered")
        : null;

  const changeHint = canEdit.allowed ? null : t("assign.noPermission");

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t("basesTecnicas.title")}
        description={t("basesTecnicas.subtitle")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <DisabledHint hint={changeHint}>
              <Button
                size="sm"
                variant="secondary"
                disabled={!canEdit.allowed}
                onClick={() => setChanging(true)}
              >
                <Layers className="h-4 w-4" />
                {t("assign.change")}
              </Button>
            </DisabledHint>
            <DisabledHint hint={processHint}>
              <Button
                size="sm"
                variant="secondary"
                disabled={!!processHint || process.isPending}
                onClick={onProcess}
              >
                <FileStack className="h-4 w-4" />
                {process.isPending
                  ? t("process.processing")
                  : registered || data.status === "review"
                    ? t("process.reprocess")
                    : t("process.cta")}
              </Button>
            </DisabledHint>
            <DownloadPdfButton caseId={caseId} disabledHint={pdfHint} />
          </div>
        }
        bodyClassName="flex flex-wrap items-center gap-x-5 gap-y-2"
      >
        <span className="inline-flex items-center gap-1.5">
          <span className="text-caption text-ink-3">{t("basesTecnicas.line")}</span>
          <span className="text-label font-medium text-ink">{lineName(data, t)}</span>
        </span>
        <StatusBadge
          value={data.status}
          label={t(`status.${data.status}`, { defaultValue: data.status })}
        />
        {data.registered_at ? (
          <span className="text-caption text-ink-3">
            {t("view.registeredAt", { date: formatDate(data.registered_at) })}
          </span>
        ) : null}
        {data.confidence !== null && data.confidence !== undefined ? (
          <span className="text-caption text-ink-3">
            {t("view.confidence")} {pct(data.confidence)}
          </span>
        ) : null}
      </Section>

      <RequisitosPanel required={requiredFields} missing={missingRequired} />

      {process.isError ? <ErrorBanner error={process.error} /> : null}

      {suggestion ? (
        <AntecedentesReview
          caseId={caseId}
          suggestion={suggestion}
          canConfirm={canEdit.allowed}
          onRegistered={() => {
            setSuggestion(null);
            void expediente.refetch();
          }}
        />
      ) : null}

      {data.status === "review" ? (
        <Card className="flex flex-col gap-1 border-warn-line bg-warn-soft/40 p-4">
          <span className="text-label font-medium text-warn-text">{t("view.inReview")}</span>
          <span className="text-caption text-ink-2">{t("view.inReviewHint")}</span>
        </Card>
      ) : null}

      {hasPayload && data.schema ? (
        <ExpedienteSections schema={data.schema} payload={data.payload} />
      ) : (
        <Card className="p-5">
          <p className="text-caption text-ink-3">{t("basesTecnicas.processHint")}</p>
        </Card>
      )}

      {changing ? (
        <ChangeLineDialog
          caseId={caseId}
          insuranceLineId={data.insurance_line_id}
          currentSchemaId={data.schema_id}
          onClose={() => setChanging(false)}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// Requisitos — the line's mandatory checklist (before + after processing)
// =============================================================================

function RequisitosPanel({
  required,
  missing,
}: {
  required: RamoRequiredField[];
  missing: RamoRequiredField[];
}) {
  const { t } = useTranslation("antecedentes");
  const missingKeys = React.useMemo(
    () => new Set(missing.map((m) => `${m.section}.${m.field}`)),
    [missing],
  );
  const done = required.length - missing.length;

  return (
    <Section
      title={t("requisitos.title")}
      description={t("requisitos.description")}
      actions={
        required.length > 0 ? (
          <Badge variant={missing.length === 0 ? "success" : "warn"}>
            {t("requisitos.count", { done, total: required.length })}
          </Badge>
        ) : null
      }
    >
      {required.length === 0 ? (
        <p className="text-caption text-ink-3">{t("requisitos.empty")}</p>
      ) : (
        <ul className="grid gap-x-6 gap-y-2 md:grid-cols-2">
          {required.map((item) => {
            const isMissing = missingKeys.has(`${item.section}.${item.field}`);
            return (
              <li key={`${item.section}.${item.field}`} className="flex items-center gap-2">
                {isMissing ? (
                  <Minus className="h-4 w-4 shrink-0 text-warn-text" />
                ) : (
                  <Check className="h-4 w-4 shrink-0 text-pos-text" />
                )}
                <span className={isMissing ? "text-body text-ink-2" : "text-body text-ink"}>
                  {item.label}
                </span>
                {isMissing ? (
                  <Badge variant="warn" className="ml-auto px-1.5 py-0">
                    {t("requisitos.missing")}
                  </Badge>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

// =============================================================================
// Cambiar línea — pick a broker line for the ramo, or adopt a template
// =============================================================================

function ChangeLineDialog({
  caseId,
  insuranceLineId,
  currentSchemaId,
  onClose,
}: {
  caseId: number;
  insuranceLineId: number | null;
  currentSchemaId: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation("antecedentes");
  const schemas = useRamoSchemas();
  const assign = useAssignLine(caseId);
  const create = useCreateRamoSchema();

  const forRamo = React.useMemo(
    () =>
      (schemas.data?.items ?? []).filter(
        (s) => insuranceLineId != null && s.insurance_line_id === insuranceLineId,
      ),
    [schemas.data, insuranceLineId],
  );
  const brokerLines = forRamo.filter((s) => !s.is_template);
  const templates = forRamo.filter((s) => s.is_template);

  const busy = assign.isPending || create.isPending;

  const doAssign = (schemaId: number) =>
    assign.mutate(
      { line_record_schema_id: schemaId },
      {
        onSuccess: () => {
          toast.success(t("assign.assigned"));
          onClose();
        },
        onError: (error) => toast.error(apiError(error, t("assign.error"))),
      },
    );

  // Adopt a template = clone it into a broker line (its ramo + definition),
  // then assign that new line to the account.
  const doAdopt = (template: LineRecordSchema) =>
    create.mutate(
      {
        name: template.name,
        insurance_line_id: template.insurance_line_id,
        definition: template.definition,
      },
      {
        onSuccess: (line) => doAssign(line.id),
        onError: (error) => toast.error(apiError(error, t("assign.error"))),
      },
    );

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("assign.title")}</DialogTitle>
          <DialogDescription>{t("assign.description")}</DialogDescription>
        </DialogHeader>

        {schemas.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <span className="text-caption font-medium text-ink-3">{t("assign.brokerLines")}</span>
              {brokerLines.length === 0 ? (
                <p className="text-caption text-ink-3">{t("assign.noOptions")}</p>
              ) : (
                brokerLines.map((line) => {
                  const isCurrent = line.id === currentSchemaId;
                  return (
                    <div
                      key={line.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bone/40 px-4 py-2.5"
                    >
                      <span className="min-w-0 truncate text-label font-medium text-ink">
                        {line.name}
                      </span>
                      {isCurrent ? (
                        <Badge variant="success">{t("assign.current")}</Badge>
                      ) : (
                        <Button size="sm" variant="secondary" disabled={busy} onClick={() => doAssign(line.id)}>
                          {t("assign.assign")}
                        </Button>
                      )}
                    </div>
                  );
                })
              )}
            </div>

            {templates.length > 0 ? (
              <div className="flex flex-col gap-2 border-t border-line pt-3">
                <span className="text-caption font-medium text-ink-3">{t("assign.templates")}</span>
                {templates.map((template) => (
                  <div
                    key={template.id}
                    className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bone/40 px-4 py-2.5"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <Sparkles className="h-3.5 w-3.5 shrink-0 text-brand" />
                      <span className="truncate text-label font-medium text-ink">{template.name}</span>
                    </span>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => doAdopt(template)}>
                      <Copy className="h-3.5 w-3.5" />
                      {t("assign.adopt")}
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// =============================================================================
// Registered payload — sections
// =============================================================================

function ExpedienteSections({
  schema,
  payload,
}: {
  schema: RamoSchema;
  payload: AntecedentesExpediente["payload"];
}) {
  const { t } = useTranslation("antecedentes");
  const yes = t("review.yes");
  const no = t("review.no");

  return (
    <>
      {schema.sections.map((section) => {
        const sectionValues =
          payload?.[section.key] && typeof payload[section.key] === "object"
            ? (payload[section.key] as Record<string, unknown>)
            : {};
        return (
          <Section
            key={section.key}
            title={t(`sections.${section.key}`, { defaultValue: section.label })}
            description={section.description ?? undefined}
          >
            <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
              {section.fields.map((field) => (
                <ReadField
                  key={field.key}
                  field={field}
                  value={sectionValues[field.key]}
                  yes={yes}
                  no={no}
                />
              ))}
            </div>
          </Section>
        );
      })}
    </>
  );
}

function MissingMarker({ required }: { required?: boolean }) {
  const { t } = useTranslation("antecedentes");
  if (required) {
    return (
      <Badge variant="warn" className="px-1.5 py-0">
        {t("view.falta")}
      </Badge>
    );
  }
  return <span className="text-ink-3">—</span>;
}

function isRepeatable(field: RamoField): boolean {
  return field.type === "list" || !!field.repeatable;
}

function rowFields(field: RamoField): RamoField[] {
  if (field.fields && field.fields.length) return field.fields;
  return [{ key: "value", label: field.label, type: field.type === "list" ? "text" : field.type }];
}

function ReadField({
  field,
  value,
  yes,
  no,
  className,
}: {
  field: RamoField;
  value: unknown;
  yes: string;
  no: string;
  className?: string;
}) {
  if (isRepeatable(field)) {
    return (
      <div className="md:col-span-2">
        <RepeatableReadTable field={field} value={value} yes={yes} no={no} />
      </div>
    );
  }

  if (field.type === "group") {
    const group = (value as Record<string, unknown> | undefined) ?? {};
    return (
      <div className="md:col-span-2">
        <div className="text-caption font-medium text-ink-3">{field.label}</div>
        <div className="mt-2 grid gap-x-6 gap-y-3 rounded-lg border border-line bg-bone/40 p-4 md:grid-cols-2">
          {(field.fields ?? []).map((sub) => (
            <ReadField key={sub.key} field={sub} value={group[sub.key]} yes={yes} no={no} />
          ))}
        </div>
      </div>
    );
  }

  const empty = field.type !== "boolean" && isEmpty(value);
  return (
    <KeyValue
      className={className}
      label={field.label}
      value={empty ? <MissingMarker required={field.required} /> : formatScalar(field, value, yes, no)}
    />
  );
}

function RepeatableReadTable({
  field,
  value,
  yes,
  no,
}: {
  field: RamoField;
  value: unknown;
  yes: string;
  no: string;
}) {
  const cols = rowFields(field);
  const rows = Array.isArray(value) ? (value as Record<string, unknown>[]) : [];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-caption font-medium text-ink-3">{field.label}</span>
        {field.required && rows.length === 0 ? <MissingMarker required /> : null}
      </div>
      {rows.length === 0 ? (
        <span className="text-ink-3">—</span>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="border-b border-line bg-paper-2/60 text-left">
                {cols.map((c) => (
                  <th key={c.key} className="px-3 py-2 text-caption font-medium text-ink-3">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row, i) => (
                <tr key={i}>
                  {cols.map((c) => {
                    const cell = row?.[c.key];
                    const empty = c.type !== "boolean" && isEmpty(cell);
                    return (
                      <td key={c.key} className="px-3 py-2 align-top tabular-nums">
                        {empty ? (
                          <MissingMarker required={c.required} />
                        ) : (
                          formatScalar(c, cell, yes, no)
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
