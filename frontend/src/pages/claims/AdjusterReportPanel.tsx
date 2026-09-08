/**
 * The adjuster's two reports: pre-informe and liquidación final.
 *
 * What the app STORES about them is deliberately small — `coverage_ruling`,
 * the adjuster's name and registry, the per-partida figures — because the
 * reports themselves are long Spanish prose whose wording IS the coverage (the
 * 60-day manifestation window and the 4-hour franchise in the corpus both
 * decided a claim on a sentence).
 *
 * So this panel does two things:
 *   1. shows what is stored, plus the report documents from the case file;
 *   2. offers "leer con IA" — `POST /ai/documents/extract`, which SUGGESTS a
 *      structured reading and writes an `extraction` row but commits nothing.
 *      The payload it returns feeds the warranty-causality list and the
 *      counterfactual card. Nothing here auto-writes: suggest -> confirm.
 *
 * The warranty causality block reads the policy's own warranties too: a code
 * sitting at `met_after_claim` or `breached` is exactly the signal an adjuster
 * turns into an exclusion argument.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ExternalLink, FileSearch, ScrollText, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useCaseFileDocuments } from "@/api/caseFiles";
import { useExtractDocument } from "@/api/ai";
import { usePolicyWarranties } from "@/api/policies";
import type { CaseDocument, Claim, DocumentCategory } from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  KeyValue,
  MonoChip,
  Section,
  apiError,
  resolveFileUrl,
} from "@/pages/proposals/shared";
import { PostsaleBadge, asRows, pick, renderValue } from "@/pages/policies/shared";

const SIGNAL_STATUSES = ["met_after_claim", "met_late", "breached"];

export type ReportSlot = "preliminary" | "final";

const CATEGORY: Record<ReportSlot, DocumentCategory> = {
  preliminary: "claim_preliminary_report",
  final: "claim_final_report",
};

function findDocument(
  sections: { documents: CaseDocument[] }[] | undefined,
  category: DocumentCategory,
): CaseDocument | undefined {
  for (const section of sections ?? []) {
    const hit = section.documents.find((doc) => doc.category === category);
    if (hit) return hit;
  }
  return undefined;
}

/** A list rendered from a loose extraction payload — prose survives verbatim. */
function PayloadList({ title, rows }: { title: string; rows: unknown }) {
  const items = Array.isArray(rows) ? rows : [];
  if (items.length === 0) return null;
  return (
    <div className="mt-4">
      <div className="text-caption font-medium text-ink-3">{title}</div>
      <ul className="mt-1 flex flex-col gap-1.5">
        {items.map((row, index) => (
          <li key={index} className="text-body text-text-secondary">
            {renderValue(row)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReportCard({
  slot,
  document: doc,
  payload,
  onPayload,
  canAnalyze,
}: {
  slot: ReportSlot;
  document: CaseDocument | undefined;
  payload: Record<string, unknown> | null;
  onPayload: (slot: ReportSlot, payload: Record<string, unknown> | null) => void;
  canAnalyze: boolean;
}) {
  const { t } = useTranslation("postsale");
  const extract = useExtractDocument();
  const [busy, setBusy] = React.useState(false);

  const run = async () => {
    if (!doc) return;
    setBusy(true);
    try {
      const result = await extract.mutateAsync({
        document_id: doc.id,
        category: CATEGORY[slot],
      });
      onPayload(slot, result.payload ?? result.parsed ?? null);
      if (result.warnings.length > 0) toast.warning(result.warnings[0]);
    } catch (error) {
      toast.error(apiError(error, t("adjuster.aiUnavailable")));
    } finally {
      setBusy(false);
    }
  };

  const ruling =
    pick<string>(payload ?? undefined, "preliminary_ruling", "coverage_ruling", "final_ruling") ??
    null;

  return (
    <div className="rounded-card border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ScrollText className="h-4 w-4 text-brand" />
          <span className="font-medium text-text-primary">
            {slot === "preliminary" ? t("adjuster.preliminary") : t("adjuster.final")}
          </span>
          {doc?.document_code ? <MonoChip>{doc.document_code}</MonoChip> : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {doc ? (
            <Button asChild variant="ghost" size="sm">
              <a href={resolveFileUrl(doc.url) ?? "#"} target="_blank" rel="noreferrer">
                <ExternalLink className="h-3.5 w-3.5" />
                {t("adjuster.openDocument")}
              </a>
            </Button>
          ) : null}
          <DisabledHint
            hint={
              !doc
                ? t("adjuster.noReports")
                : canAnalyze
                  ? null
                  : t("adjuster.analyzeNoPermission")
            }
          >
            <Button
              variant="secondary"
              size="sm"
              disabled={!doc || !canAnalyze || busy}
              onClick={() => void run()}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {busy ? t("adjuster.analyzing") : t("adjuster.analyze")}
            </Button>
          </DisabledHint>
        </div>
      </div>

      {doc ? (
        <p className="mt-1 text-caption text-text-muted">
          {doc.original_name} · {formatDate(doc.created_at)}
        </p>
      ) : (
        <p className="mt-1 text-caption text-text-muted">{t("adjuster.noReportsHint")}</p>
      )}

      {payload ? (
        <>
          <p className="mt-3 text-caption text-warn-text">{t("adjuster.suggestionOnly")}</p>
          {ruling ? (
            <div className="mt-2">
              <KeyValue
                label={
                  slot === "preliminary"
                    ? t("adjuster.rulingPreliminary")
                    : t("adjuster.rulingFinal")
                }
                value={renderValue(ruling)}
              />
            </div>
          ) : null}

          <PayloadList
            title={t("adjuster.warrantyCausality")}
            rows={pick(payload, "warranty_analysis", "warranty_ruling")}
          />
          <PayloadList
            title={t("adjuster.conclusions")}
            rows={pick(payload, "conclusions", "final_remarks")}
          />
          <PayloadList
            title={t("adjuster.pendingDiligences")}
            rows={pick(payload, "pending_diligences")}
          />

          {pick(payload, "liquidation_duration_days") !== undefined ? (
            <div className="mt-3">
              <KeyValue
                label={t("adjuster.duration")}
                value={renderValue(pick(payload, "liquidation_duration_days"))}
              />
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function AdjusterReportPanel({
  claim,
  onFinalPayload,
}: {
  claim: Claim;
  /** The final report's payload, lifted so the counterfactual card can use it. */
  onFinalPayload: (payload: Record<string, unknown> | null) => void;
}) {
  const { t } = useTranslation("postsale");
  const documents = useCaseFileDocuments(claim.case_file_id ?? undefined, "claim");
  const warranties = usePolicyWarranties(claim.policy_id ?? undefined);
  const canAnalyze = useCan("Documents", "Upload");

  const [payloads, setPayloads] = React.useState<
    Record<ReportSlot, Record<string, unknown> | null>
  >({ preliminary: null, final: null });

  const handlePayload = (slot: ReportSlot, payload: Record<string, unknown> | null) => {
    setPayloads((prev) => ({ ...prev, [slot]: payload }));
    if (slot === "final") onFinalPayload(payload);
  };

  const preliminaryDoc = findDocument(documents.data?.sections, CATEGORY.preliminary);
  const finalDoc = findDocument(documents.data?.sections, CATEGORY.final);

  const signals = (warranties.data ?? []).filter((w) => SIGNAL_STATUSES.includes(w.status));

  // Warranty findings the AI read out of either report, if it read one.
  const aiFindings = [
    ...asRows(pick(payloads.final ?? undefined, "warranty_analysis") ?? []),
    ...asRows(pick(payloads.preliminary ?? undefined, "warranty_analysis") ?? []),
  ];

  return (
    <Section title={t("adjuster.title")} description={t("adjuster.description")}>
      {!preliminaryDoc && !finalDoc && !claim.adjuster_name ? (
        <EmptyState
          title={t("adjuster.noReports")}
          hint={t("adjuster.noReportsHint")}
          icon={<FileSearch className="h-6 w-6" />}
        />
      ) : null}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KeyValue label={t("claim.fields.adjusterName")} value={claim.adjuster_name ?? "—"} />
        <KeyValue
          label={t("claim.fields.adjusterRegistry")}
          value={claim.adjuster_registry ?? "—"}
          mono
        />
        <KeyValue
          label={t("adjuster.ruling")}
          value={
            <PostsaleBadge
              value={claim.coverage_ruling}
              label={t(`claim.ruling.${claim.coverage_ruling}`)}
            />
          }
        />
        <KeyValue
          label={t("claim.fields.lossRatio")}
          value={claim.loss_ratio_pct ? `${claim.loss_ratio_pct} %` : "—"}
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ReportCard
          slot="preliminary"
          document={preliminaryDoc}
          payload={payloads.preliminary}
          onPayload={handlePayload}
          canAnalyze={canAnalyze.allowed}
        />
        <ReportCard
          slot="final"
          document={finalDoc}
          payload={payloads.final}
          onPayload={handlePayload}
          canAnalyze={canAnalyze.allowed}
        />
      </div>

      <div className="mt-5">
        <div className="text-caption font-medium text-ink-3">
          {t("adjuster.warrantySignals")}
        </div>
        <p className="mt-1 text-caption text-text-muted">
          {t("adjuster.warrantyCausalityHint")}
        </p>

        {signals.length === 0 ? (
          <p className="mt-2 text-body text-text-secondary">
            {t("adjuster.noWarrantySignals")}
          </p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {signals.map((w) => (
              <li
                key={w.id}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2"
              >
                <MonoChip>{w.code ?? `#${w.id}`}</MonoChip>
                <span className="min-w-0 flex-1 truncate text-body text-text-secondary">
                  {w.title ?? w.requirement ?? "—"}
                </span>
                {w.is_suspensive ? (
                  <Badge variant="warn">{t("warranty.flags.suspensive")}</Badge>
                ) : null}
                <PostsaleBadge value={w.status} label={t(`warranty.status.${w.status}`)} />
              </li>
            ))}
          </ul>
        )}

        {aiFindings.length > 0 ? (
          <ul className="mt-3 flex flex-col gap-1.5">
            {aiFindings.map((row, index) => (
              <li key={index} className="text-body text-text-secondary">
                {renderValue(row)}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Section>
  );
}
