/**
 * The expediente's document tree, grouped by sub-expediente.
 *
 * One row per filed document: the corpus code chip (`00A`, `07R`, `09B`), the
 * Spanish category label, who filed it and when, a download link, and
 * "analizar con IA" — the last of which is offered ONLY when the category has
 * a schema in the registry (`GET /ai/categories`). A category with no schema
 * renders the button disabled with a "pronto" chip instead of failing at 422.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Download, FileText, Sparkles } from "lucide-react";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DisabledHint, EmptyState, MonoChip, resolveFileUrl } from "@/components/common/kit";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CaseDocument, CaseDocumentGroups, CategorySpec } from "@/api/types";

function sizeLabel(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface SectionAccordionProps {
  groups: CaseDocumentGroups | undefined;
  /** The registry, so a row knows whether AI analysis is even possible. */
  specs?: CategorySpec[];
  canAnalyze?: boolean;
  analyzingDocumentId?: number | null;
  onAnalyze?: (document: CaseDocument) => void;
  className?: string;
}

export function SectionAccordion({
  groups,
  specs,
  canAnalyze = false,
  analyzingDocumentId = null,
  onAnalyze,
  className,
}: SectionAccordionProps) {
  const { t } = useTranslation("cases");
  const { t: td } = useTranslation("documents");
  const { t: tc } = useTranslation("common");

  const extractable = React.useMemo(() => {
    const set = new Set<string>();
    for (const spec of specs ?? []) {
      set.add(spec.category);
      set.add(spec.canonical_category);
    }
    return set;
  }, [specs]);

  const sections = (groups?.sections ?? []).filter((s) => s.documents.length > 0);

  if (!sections.length) {
    return (
      <EmptyState
        title={t("documents.empty")}
        hint={t("documents.emptyHint")}
        icon={<FileText className="h-6 w-6" />}
      />
    );
  }

  return (
    <Accordion
      type="multiple"
      defaultValue={sections.map((s) => s.section ?? "unfiled")}
      className={cn("flex flex-col gap-2", className)}
    >
      {sections.map((group) => {
        const key = group.section ?? "unfiled";
        return (
          <AccordionItem key={key} value={key}>
            <AccordionTrigger>
              <span className="flex items-center gap-2.5">
                <span className="font-medium">
                  {group.section
                    ? td(`sections.${group.section}`, { defaultValue: group.label })
                    : td("sections.unfiled", { defaultValue: group.label })}
                </span>
                <Badge variant="neutral">{group.documents.length}</Badge>
              </span>
            </AccordionTrigger>
            <AccordionContent>
              <ul className="flex flex-col divide-y divide-line">
                {group.documents.map((doc) => {
                  const canExtract = extractable.has(doc.category);
                  const busy = analyzingDocumentId === doc.id;
                  const href = resolveFileUrl(doc.url);
                  return (
                    <li
                      key={doc.id}
                      className="flex flex-wrap items-center gap-3 py-2.5 first:pt-0 last:pb-0"
                    >
                      {doc.document_code ? (
                        <MonoChip>{doc.document_code}</MonoChip>
                      ) : (
                        <MonoChip className="opacity-50">—</MonoChip>
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-body font-medium text-ink">
                          {td(`categories.${doc.category}`, {
                            defaultValue: doc.category_label,
                          })}
                        </p>
                        <p className="truncate text-caption text-ink-3">
                          {doc.original_name} · {formatDate(doc.created_at)} ·{" "}
                          {sizeLabel(doc.size_bytes)}
                        </p>
                      </div>

                      <DisabledHint
                        hint={
                          !canExtract
                            ? td("ai.noSchema")
                            : !canAnalyze
                              ? td("ai.noPermission")
                              : null
                        }
                      >
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={!canExtract || !canAnalyze || busy}
                          onClick={onAnalyze ? () => onAnalyze(doc) : undefined}
                        >
                          <Sparkles className="h-3.5 w-3.5" />
                          {busy ? tc("actions.loading") : td("ai.analyze")}
                        </Button>
                      </DisabledHint>

                      {href ? (
                        <Button size="sm" variant="secondary" asChild>
                          <a href={href} target="_blank" rel="noreferrer">
                            <Download className="h-3.5 w-3.5" />
                            {tc("actions.download")}
                          </a>
                        </Button>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </AccordionContent>
          </AccordionItem>
        );
      })}
    </Accordion>
  );
}
