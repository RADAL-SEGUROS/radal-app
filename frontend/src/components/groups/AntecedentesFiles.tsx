/**
 * Antecedentes — the files desk of an account (v9 rework).
 *
 * The ramo's RECOMMENDED FILES drive the view: each recommended document is a
 * slot (label · required · expected format · description). A slot that already
 * has a matching uploaded document shows it with a working preview + download;
 * an empty slot shows an upload button. Below the slots sits a free "Otros
 * archivos" area for anything the ramo did not name.
 *
 * Two subtabs only:
 *   - **Archivos** — the slots + free files, every download authenticated.
 *   - **Extracción** — the per-document AI reads (`document_extractions`),
 *     each with its confidence and a "requiere visión" chip when the text pass
 *     could not fully read a scanned document.
 *
 * Uploads accept PDF / Word / images only; the server rejects Excel with a 415
 * whose message is surfaced inline (never a silent failure).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, FileText, Loader2, Paperclip, ScanEye, Upload } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ConfidenceBadge,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
} from "@/components/common/kit";
import { DownloadDocButton, PreviewDocButton } from "@/components/common/DownloadDocButton";
import { useCaseFileDocuments } from "@/api/caseFiles";
import { useExpediente, useRamoSchema } from "@/api/antecedentes";
import { useUploadDocument } from "@/api/documents";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  AntecedentesDocumentExtraction,
  CaseDocument,
  DocumentCategory,
  RamoRecommendedFile,
} from "@/api/types";

const ACCEPT = ".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp,.gif,image/*,application/pdf";

/**
 * The antecedentes files desk shows ONLY what the insured communicated — never
 * the cotización / insurer-quote artefacts, which live in the Comparación step.
 *
 * This mirrors the backend antecedentes candidate set
 * (`ai._antecedentes_documents`): keep the intake sections (`root_prospect`,
 * `submission`) and drop the cotización categories. The backend additionally
 * requires a non-null section, but the free "Otros archivos" uploads land with
 * category `other` and a NULL section (legacy categories carry no section), so
 * we keep null-section docs here — they are genuine antecedentes uploads, not
 * comparison artefacts.
 */
const ANTECEDENTES_SECTIONS = new Set<string>(["root_prospect", "submission"]);

const COTIZACION_CATEGORIES = new Set<string>([
  "insurer_quotation",
  "proposal", // deprecated alias of insurer_quotation
  "declination",
  "conditional_pronouncement",
  "quote_comparison",
  "budget_proposal",
  "issuance_proposal",
  "technical_recommendation",
  "comparison_pack",
  "proposal_pack",
]);

function isAntecedentesDoc(doc: CaseDocument): boolean {
  if (COTIZACION_CATEGORIES.has(doc.category)) return false;
  return doc.section === null || ANTECEDENTES_SECTIONS.has(doc.section);
}

function sizeLabel(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function AntecedentesFiles({
  caseId,
  insuranceLineId,
  canUpload,
  uploadHint,
}: {
  caseId: number;
  insuranceLineId: number | null;
  canUpload: boolean;
  uploadHint: string | null;
}) {
  const { t } = useTranslation("accounts");
  const { t: td } = useTranslation("documents");

  const documents = useCaseFileDocuments(caseId);
  const ramoSchema = useRamoSchema(insuranceLineId);
  const expediente = useExpediente(caseId);
  const upload = useUploadDocument();

  const [subtab, setSubtab] = React.useState<"files" | "extraction">("files");
  const [activeSlot, setActiveSlot] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const pendingCategory = React.useRef<DocumentCategory>("other");

  const recommended: RamoRecommendedFile[] = ramoSchema.data?.recommended_files ?? [];

  const allDocs: CaseDocument[] = React.useMemo(
    () =>
      (documents.data?.sections ?? [])
        .flatMap((section) => section.documents)
        .filter(isAntecedentesDoc),
    [documents.data],
  );

  const docsByCategory = React.useMemo(() => {
    const map = new Map<string, CaseDocument[]>();
    for (const doc of allDocs) {
      const list = map.get(doc.category) ?? [];
      list.push(doc);
      map.set(doc.category, list);
    }
    return map;
  }, [allDocs]);

  // Which docs a recommended slot claims, so the rest fall to "Otros archivos".
  const matchedIds = React.useMemo(() => {
    const set = new Set<number>();
    for (const rf of recommended) {
      if (!rf.category) continue;
      for (const doc of docsByCategory.get(rf.category) ?? []) set.add(doc.id);
    }
    return set;
  }, [recommended, docsByCategory]);

  const otherDocs = allDocs.filter((doc) => !matchedIds.has(doc.id));

  const slotLabel = (rf: RamoRecommendedFile): string =>
    rf.label ??
    (rf.category ? td(`categories.${rf.category}`, { defaultValue: rf.doc_type ?? rf.category }) : rf.doc_type ?? "—");

  const openPicker = (slotKey: string, category: DocumentCategory) => {
    setActiveSlot(slotKey);
    pendingCategory.current = category;
    upload.reset();
    inputRef.current?.click();
  };

  const onFilePicked = (file: File | undefined) => {
    if (!file) return;
    upload.mutate(
      {
        file,
        entity_type: "case_file",
        entity_id: caseId,
        category: pendingCategory.current,
      },
      {
        onSuccess: () => {
          void documents.refetch();
          setActiveSlot(null);
        },
      },
    );
  };

  const extractions: AntecedentesDocumentExtraction[] =
    expediente.data?.document_extractions ?? [];

  if (documents.isLoading || ramoSchema.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  const busySlot = (key: string) => upload.isPending && activeSlot === key;

  return (
    <div className="flex flex-col gap-4">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          onFilePicked(e.target.files?.[0]);
          e.target.value = "";
        }}
        aria-hidden
      />

      {documents.isError ? <ErrorBanner error={documents.error} /> : null}

      <Tabs value={subtab} onValueChange={(v) => setSubtab(v as "files" | "extraction")}>
        <TabsList variant="segmented">
          <TabsTrigger value="files">
            {t("account.antecedentesFiles.tabs.files")}
            <span className="ml-1.5 text-caption tabular-nums text-ink-3">{allDocs.length}</span>
          </TabsTrigger>
          <TabsTrigger value="extraction">
            {t("account.antecedentesFiles.tabs.extraction")}
            <span className="ml-1.5 text-caption tabular-nums text-ink-3">
              {extractions.length}
            </span>
          </TabsTrigger>
        </TabsList>

        {/* --- Archivos ------------------------------------------------------ */}
        <TabsContent value="files" className="mt-4 flex flex-col gap-4">
          {recommended.length === 0 ? (
            <Card>
              <EmptyState
                icon={<FileText className="h-6 w-6" />}
                title={t("account.antecedentesFiles.noRecommended")}
                hint={t("account.antecedentesFiles.noRecommendedHint")}
              />
            </Card>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {recommended.map((rf, index) => {
                const key = rf.key ?? rf.category ?? `slot-${index}`;
                const docs = rf.category ? (docsByCategory.get(rf.category) ?? []) : [];
                const filled = docs.length > 0;
                const category = (rf.category as DocumentCategory) ?? "other";
                return (
                  <Card
                    key={key}
                    className={cn(
                      "flex flex-col gap-2.5 p-4",
                      filled ? "border-pos-line/60" : "",
                    )}
                  >
                    <div className="flex items-start gap-2.5">
                      <span
                        className={cn(
                          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-control",
                          filled ? "bg-pos-soft text-pos-text" : "bg-bone text-ink-3",
                        )}
                      >
                        {filled ? (
                          <CheckCircle2 className="h-4 w-4" />
                        ) : (
                          <Paperclip className="h-4 w-4" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-label font-medium text-ink">{slotLabel(rf)}</span>
                          {rf.required ? (
                            <Badge variant="warn" className="px-1.5 py-0">
                              {t("account.antecedentesFiles.required")}
                            </Badge>
                          ) : (
                            <Badge variant="muted" className="px-1.5 py-0">
                              {t("account.antecedentesFiles.optional")}
                            </Badge>
                          )}
                          {rf.format ? (
                            <Badge variant="outline" className="px-1.5 py-0 uppercase">
                              {rf.format}
                            </Badge>
                          ) : null}
                        </div>
                        {rf.description ? (
                          <p className="mt-0.5 text-pretty text-caption text-ink-3">
                            {rf.description}
                          </p>
                        ) : null}
                      </div>
                    </div>

                    {filled ? (
                      <ul className="flex flex-col gap-1.5 border-t border-line pt-2.5">
                        {docs.map((doc) => (
                          <li key={doc.id} className="flex items-center gap-2">
                            <FileText className="h-3.5 w-3.5 shrink-0 text-ink-3" aria-hidden />
                            <span className="min-w-0 flex-1 truncate text-caption text-ink-2">
                              {doc.original_name}
                            </span>
                            <PreviewDocButton documentId={doc.id} />
                            <DownloadDocButton documentId={doc.id} filename={doc.original_name} />
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="border-t border-line pt-2.5">
                        <DisabledHint hint={canUpload ? null : uploadHint}>
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={!canUpload || busySlot(key)}
                            onClick={() => openPicker(key, category)}
                          >
                            {busySlot(key) ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                            ) : (
                              <Upload className="h-3.5 w-3.5" aria-hidden />
                            )}
                            {t("account.antecedentesFiles.upload")}
                          </Button>
                        </DisabledHint>
                        {upload.isError && activeSlot === key ? (
                          <ErrorBanner error={upload.error} className="mt-2" />
                        ) : null}
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )}

          {/* Free files — anything not claimed by a recommended slot. */}
          <Card className="flex flex-col gap-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-label font-medium text-ink">
                  {t("account.antecedentesFiles.otherTitle")}
                </h3>
                <p className="text-caption text-ink-3">{t("account.antecedentesFiles.otherHint")}</p>
              </div>
              <DisabledHint hint={canUpload ? null : uploadHint}>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!canUpload || busySlot("__other__")}
                  onClick={() => openPicker("__other__", "other")}
                >
                  {busySlot("__other__") ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Upload className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {t("account.antecedentesFiles.uploadOther")}
                </Button>
              </DisabledHint>
            </div>
            {upload.isError && activeSlot === "__other__" ? (
              <ErrorBanner error={upload.error} />
            ) : null}
            {otherDocs.length === 0 ? (
              <p className="text-caption text-ink-3">{t("account.antecedentesFiles.otherEmpty")}</p>
            ) : (
              <ul className="divide-y divide-line">
                {otherDocs.map((doc) => (
                  <li key={doc.id} className="flex items-center gap-2.5 py-2">
                    <FileText className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <span className="block truncate text-body text-ink">{doc.original_name}</span>
                      <span className="text-caption text-ink-3">
                        {td(`categories.${doc.category}`, { defaultValue: doc.category_label })}
                        {sizeLabel(doc.size_bytes) ? ` · ${sizeLabel(doc.size_bytes)}` : ""}
                        {doc.created_at ? ` · ${formatDate(doc.created_at)}` : ""}
                      </span>
                    </div>
                    <PreviewDocButton documentId={doc.id} />
                    <DownloadDocButton documentId={doc.id} filename={doc.original_name} />
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </TabsContent>

        {/* --- Extracción --------------------------------------------------- */}
        <TabsContent value="extraction" className="mt-4">
          {expediente.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : extractions.length === 0 ? (
            <Card>
              <EmptyState
                icon={<ScanEye className="h-6 w-6" />}
                title={t("account.antecedentesFiles.extractionEmpty")}
                hint={t("account.antecedentesFiles.extractionEmptyHint")}
              />
            </Card>
          ) : (
            <div className="flex flex-col gap-3">
              {extractions.map((item) => (
                <ExtractionCard key={item.document_id} item={item} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ExtractionCard({ item }: { item: AntecedentesDocumentExtraction }) {
  const { t } = useTranslation("accounts");
  const { t: td } = useTranslation("documents");
  const entries = Object.entries(item.payload ?? {});

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <FileText className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-label font-medium text-ink">
          {item.document_name}
        </span>
        <Badge variant="muted" className="px-1.5 py-0">
          {td(`categories.${item.category}`, { defaultValue: item.category })}
        </Badge>
        {item.confidence !== null && item.confidence !== undefined ? (
          <ConfidenceBadge value={item.confidence} />
        ) : null}
        {item.needs_vision ? (
          <Badge variant="warn" className="gap-1 px-1.5 py-0">
            <ScanEye className="h-3 w-3" />
            {t("account.antecedentesFiles.needsVision")}
          </Badge>
        ) : null}
      </div>

      {item.warnings.length > 0 ? (
        <ul className="flex flex-col gap-1 rounded-control bg-warn-soft/40 p-2.5">
          {item.warnings.map((warning, i) => (
            <li key={i} className="text-caption text-warn-text">
              {warning}
            </li>
          ))}
        </ul>
      ) : null}

      {entries.length === 0 ? (
        <p className="text-caption text-ink-3">{t("account.antecedentesFiles.extractionNoPayload")}</p>
      ) : (
        <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
          {entries.map(([key, value]) => (
            <KeyValue
              key={key}
              label={key}
              value={
                value === null || value === undefined || value === "" ? (
                  <span className="text-ink-3">—</span>
                ) : typeof value === "object" ? (
                  <span className="whitespace-pre-wrap break-words font-mono text-caption text-ink-2">
                    {JSON.stringify(value, null, 1)}
                  </span>
                ) : (
                  String(value)
                )
              }
            />
          ))}
        </div>
      )}
    </Card>
  );
}
