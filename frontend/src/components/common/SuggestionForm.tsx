/**
 * The generic extraction review form — suggest → human edit → confirm.
 *
 * This is the generalisation of `pages/proposals/upload.tsx`'s `SuggestionForm`.
 * It is driven ENTIRELY by `GET /ai/categories`: the registry sends the field
 * list of the category's Pydantic schema, and this component renders one input
 * per field. Adding a 27th document category therefore needs a backend schema
 * and **no new React component**.
 *
 * Three things carry over from the proposal form and must not be lost:
 *   1. the **provenance card** — model, prompt version, confidence, tokens,
 *      source document and the extractor's warnings, always visible while the
 *      human reviews;
 *   2. **override highlighting** — a field the human changed is marked, so at
 *      confirm time it is obvious what is the machine's and what is the
 *      broker's;
 *   3. **nothing auto-commits.** The button posts to
 *      `POST /ai/documents/{extraction_id}/confirm` and is disabled — with the
 *      reason on the tooltip — when the user lacks the grant.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, FileText, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ConfidenceBadge,
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
} from "@/pages/proposals/shared";
import { useConfirmDocumentExtraction } from "@/api/ai";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CategoryField,
  CategorySpec,
  DocumentConfirmResponse,
  DocumentExtractionResponse,
} from "@/api/types";

type Primitive = "string" | "number" | "boolean" | "json";

interface DraftEntry {
  kind: Primitive;
  /** Always a string in the DOM; coerced back on submit. */
  value: string;
  original: string;
}

function classify(value: unknown, declared: string | null): Primitive {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  if (value !== null && typeof value === "object") return "json";
  if (!declared) return "string";
  const d = declared.toLowerCase();
  if (d.includes("bool")) return "boolean";
  if (d.includes("int") || d.includes("float") || d.includes("decimal") || d.includes("number")) {
    return "number";
  }
  if (d.includes("list") || d.includes("array") || d.includes("dict") || d.includes("object")) {
    return "json";
  }
  return "string";
}

function toText(value: unknown, kind: Primitive): string {
  if (value === null || value === undefined) return "";
  if (kind === "json") return JSON.stringify(value, null, 2);
  if (kind === "boolean") return value ? "true" : "false";
  return String(value);
}

function fromText(text: string, kind: Primitive): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (kind === "number") {
    const parsed = Number(trimmed);
    return Number.isNaN(parsed) ? trimmed : parsed;
  }
  if (kind === "boolean") return trimmed === "true";
  if (kind === "json") {
    try {
      return JSON.parse(trimmed);
    } catch {
      // Keep the human's text rather than silently dropping it; the server
      // validates and answers 422 with a message the reviewer can act on.
      return trimmed;
    }
  }
  return trimmed;
}

function buildDraft(
  fields: CategoryField[],
  payload: Record<string, unknown>,
): Record<string, DraftEntry> {
  const draft: Record<string, DraftEntry> = {};
  const seen = new Set<string>();
  for (const field of fields) {
    const raw = payload[field.name];
    const kind = classify(raw, field.type);
    const text = toText(raw, kind);
    draft[field.name] = { kind, value: text, original: text };
    seen.add(field.name);
  }
  // `ExtractionModel` allows extra keys so an unexpected column still reaches
  // the reviewer — surface those too rather than dropping them on confirm.
  for (const [name, raw] of Object.entries(payload)) {
    if (seen.has(name)) continue;
    const kind = classify(raw, null);
    const text = toText(raw, kind);
    draft[name] = { kind, value: text, original: text };
  }
  return draft;
}

export interface SuggestionFormProps {
  result: DocumentExtractionResponse;
  spec?: CategorySpec;
  /** Original file name, for the provenance card's source chip. */
  documentName?: string | null;
  canConfirm?: boolean;
  /** Required only when the category prefills a `proposal`. */
  quoteRequestId?: number | null;
  onConfirmed?: (response: DocumentConfirmResponse) => void;
  className?: string;
}

export function SuggestionForm({
  result,
  spec,
  documentName,
  canConfirm = false,
  quoteRequestId,
  onConfirmed,
  className,
}: SuggestionFormProps) {
  const { t } = useTranslation("documents");
  const { t: tp } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");

  const { extraction, warnings } = result;
  const payload = React.useMemo(
    () => result.payload ?? result.parsed ?? {},
    [result.payload, result.parsed],
  );
  // Memoised: `spec?.fields ?? []` would be a fresh array on every render and
  // the rebuild effect below would then loop forever.
  const fields = React.useMemo(() => spec?.fields ?? [], [spec]);

  const [draft, setDraft] = React.useState<Record<string, DraftEntry>>(() =>
    buildDraft(fields, payload),
  );

  // A fresh extraction (or the registry arriving late) rebuilds the draft.
  React.useEffect(() => {
    setDraft(buildDraft(fields, payload));
  }, [extraction.id, fields, payload]);

  const confirm = useConfirmDocumentExtraction(extraction.id);

  const overrides = Object.entries(draft).filter(([, e]) => e.value !== e.original).length;

  const set = (name: string, value: string) =>
    setDraft((prev) => ({ ...prev, [name]: { ...prev[name], value } }));

  const submit = () => {
    const next: Record<string, unknown> = { ...payload };
    for (const [name, entry] of Object.entries(draft)) {
      next[name] = fromText(entry.value, entry.kind);
    }
    confirm.mutate(
      {
        payload: next,
        category: result.category,
        quote_request_id: quoteRequestId ?? null,
        is_confirmed: true,
      },
      { onSuccess: (data) => onConfirmed?.(data) },
    );
  };

  const totalTokens =
    (extraction.prompt_tokens ?? 0) + (extraction.completion_tokens ?? 0);

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {/* ── Provenance ── */}
      <Card className="grid grid-cols-2 gap-5 p-5 md:grid-cols-5">
        <KeyValue
          label={tp("ai.model")}
          value={<span className="text-caption tabular-nums">{extraction.model}</span>}
        />
        <KeyValue
          label={tp("ai.promptVersion")}
          value={<MonoChip>{extraction.prompt_version}</MonoChip>}
        />
        <KeyValue
          label={tp("ai.status")}
          value={
            <StatusBadge
              value={extraction.status}
              label={tp(`ai.statuses.${extraction.status}`, {
                defaultValue: extraction.status,
              })}
            />
          }
        />
        <KeyValue
          label={tp("ai.confidenceLabel")}
          value={<ConfidenceBadge value={extraction.confidence} />}
        />
        <KeyValue label={tp("ai.tokens")} value={formatNumber(totalTokens)} />

        <div className="col-span-2 md:col-span-5 flex flex-wrap items-center gap-3">
          <Badge variant="brand" className="gap-1">
            <Sparkles className="h-3 w-3" />
            {t(`categories.${result.canonical_category}`, {
              defaultValue: result.canonical_category,
            })}
          </Badge>
          {spec?.code ? <MonoChip>{spec.code}</MonoChip> : null}
          {spec ? (
            <Badge variant="neutral">
              {t(`directions.${spec.direction}`, { defaultValue: spec.direction })}
            </Badge>
          ) : null}
          {documentName ? (
            <span className="flex items-center gap-2 text-caption text-text-muted">
              <FileText className="h-4 w-4 text-brand" />
              {documentName}
              <MonoChip>DOC-{extraction.document_id}</MonoChip>
            </span>
          ) : null}
          <span className="ml-auto text-caption text-text-muted">
            {t("review.prefillTarget", { target: result.prefill_target })}
          </span>
        </div>

        {warnings.length ? (
          <div className="col-span-2 md:col-span-5 flex flex-col gap-1.5 rounded-card border border-warn-line bg-warn-soft px-3.5 py-2.5">
            {warnings.map((warning, i) => (
              <p key={i} className="flex items-start gap-2 text-caption text-text-secondary">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn-text" />
                {warning}
              </p>
            ))}
          </div>
        ) : null}
      </Card>

      {/* ── The registry-driven form ── */}
      <Section
        title={t("review.title")}
        description={spec?.guidance ?? t("review.description")}
        actions={
          <>
            {overrides ? (
              <Badge variant="action">{t("review.overrides", { count: overrides })}</Badge>
            ) : null}
            <DisabledHint hint={canConfirm ? null : t("review.noPermission")}>
              <Button size="sm" disabled={!canConfirm || confirm.isPending} onClick={submit}>
                {confirm.isPending ? tc("actions.loading") : t("review.confirm")}
              </Button>
            </DisabledHint>
          </>
        }
      >
        {Object.keys(draft).length === 0 ? (
          <p className="text-caption text-text-muted">{t("review.noFields")}</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {Object.entries(draft).map(([name, entry]) => {
              const field = fields.find((f) => f.name === name);
              const changed = entry.value !== entry.original;
              const label = t(`fields.${name}`, {
                defaultValue: name.replace(/_/g, " "),
              });
              const id = `sf-${extraction.id}-${name}`;
              return (
                <div
                  key={name}
                  className={cn(
                    "flex flex-col gap-1.5",
                    entry.kind === "json" && "md:col-span-2",
                  )}
                >
                  <Label htmlFor={id} className="flex items-center gap-2">
                    <span className="truncate">{label}</span>
                    {field?.required ? (
                      <Badge variant="muted" className="px-1.5 py-0 text-[9.5px]">
                        {t("review.required")}
                      </Badge>
                    ) : null}
                    {changed ? (
                      <Badge variant="action" className="px-1.5 py-0 text-[9.5px]">
                        {t("review.edited")}
                      </Badge>
                    ) : null}
                  </Label>

                  {entry.kind === "json" ? (
                    <textarea
                      id={id}
                      rows={6}
                      value={entry.value}
                      onChange={(e) => set(name, e.target.value)}
                      className={cn(
                        "w-full rounded-lg border bg-bone px-3 py-2 font-mono text-[12px] leading-relaxed text-text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        changed ? "border-brand" : "border-line",
                      )}
                    />
                  ) : entry.kind === "boolean" ? (
                    <select
                      id={id}
                      value={entry.value}
                      onChange={(e) => set(name, e.target.value)}
                      className={cn(
                        "h-10 rounded-lg border bg-bone px-3 text-body text-text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        changed ? "border-brand" : "border-line",
                      )}
                    >
                      <option value="">{t("review.unset")}</option>
                      <option value="true">{tc("units.si")}</option>
                      <option value="false">{tc("units.no")}</option>
                    </select>
                  ) : (
                    <Input
                      id={id}
                      value={entry.value}
                      type={entry.kind === "number" ? "number" : "text"}
                      step={entry.kind === "number" ? "0.0001" : undefined}
                      onChange={(e) => set(name, e.target.value)}
                      className={cn(
                        entry.kind === "number" && "text-right tabular-nums",
                        changed && "border-brand",
                      )}
                    />
                  )}

                  {field?.description ? (
                    <p className="text-caption text-text-muted">{field.description}</p>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}

        {confirm.isError ? <ErrorBanner error={confirm.error} className="mt-4" /> : null}
      </Section>
    </div>
  );
}
