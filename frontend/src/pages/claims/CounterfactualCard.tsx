/**
 * The prior-program counterfactual — the broker's renewal argument.
 *
 * The final adjuster report answers the question the insured always asks after
 * a loss: "what would this have cost me under the previous program?". That
 * comparison (`prior_program_counterfactual[]`, plus the alternatives the
 * broker evaluated) lives in the report, not in a column — so the card renders
 * it from the AI reading of the final report, clearly flagged as an unconfirmed
 * suggestion.
 *
 * With no reading available it does NOT invent one. It shows only what the
 * claim itself proves: damage determined, deductible borne, indemnity paid and
 * what the insured retained.
 */
import { useTranslation } from "react-i18next";
import { Scale } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useClaimItems } from "@/api/claims";
import { num, type Claim } from "@/api/types";
import { EmptyState, KeyValue, Section, uf } from "@/pages/proposals/shared";
import { asRows, humanizePath, pick, renderValue } from "@/pages/policies/shared";

/** Column keys the corpus uses for a counterfactual row, in preference order. */
const CONCEPT_KEYS = ["concept", "item", "scenario", "dimension", "concepto", "escenario"];
const CURRENT_KEYS = ["current", "current_program", "actual", "with_program", "today"];
const PRIOR_KEYS = ["prior", "prior_program", "previous", "anterior", "without_program"];
const DIFF_KEYS = ["difference", "delta", "gap", "diferencia", "benefit"];

function CounterfactualTable({ rows, title }: { rows: unknown; title: string }) {
  const { t } = useTranslation("postsale");
  const parsed = asRows(rows);
  if (parsed.length === 0) return null;

  return (
    <div className="mt-4">
      <div className="mb-1.5 text-caption font-medium text-ink-3">
        {title}
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("counterfactual.columns.concept")}</TableHead>
              <TableHead>{t("counterfactual.columns.current")}</TableHead>
              <TableHead>{t("counterfactual.columns.prior")}</TableHead>
              <TableHead>{t("counterfactual.columns.difference")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {parsed.map((row, index) => {
              const concept = pick(row, ...CONCEPT_KEYS);
              const current = pick(row, ...CURRENT_KEYS);
              const prior = pick(row, ...PRIOR_KEYS);
              const difference = pick(row, ...DIFF_KEYS);
              // A row the schema shaped differently is still shown, verbatim.
              const aligned = concept !== undefined || current !== undefined || prior !== undefined;
              if (!aligned) {
                return (
                  <TableRow key={index}>
                    <TableCell colSpan={4} className="text-text-secondary">
                      {renderValue(row)}
                    </TableCell>
                  </TableRow>
                );
              }
              return (
                <TableRow key={index}>
                  <TableCell className="align-top font-medium text-text-primary">
                    {concept === undefined
                      ? humanizePath(String(index + 1))
                      : renderValue(concept)}
                  </TableCell>
                  <TableCell className="align-top">{renderValue(current)}</TableCell>
                  <TableCell className="align-top text-text-secondary">
                    {renderValue(prior)}
                  </TableCell>
                  <TableCell className="align-top">{renderValue(difference)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

export function CounterfactualCard({
  claim,
  payload,
}: {
  claim: Claim;
  /** The final report's unconfirmed AI reading, when the user asked for one. */
  payload: Record<string, unknown> | null;
}) {
  const { t } = useTranslation("postsale");
  const items = useClaimItems(claim.id);
  const totals = items.data?.totals;

  const determined = num(totals?.determined_uf ?? null) ?? 0;
  const indemnity = num(totals?.indemnity_uf ?? null) ?? 0;
  const deductible = num(totals?.deductible_uf ?? null) ?? num(claim.deductible_uf) ?? 0;
  const retained = determined - indemnity;

  const prior = pick(payload ?? undefined, "prior_program_counterfactual");
  const alternatives = pick(payload ?? undefined, "alternatives_counterfactual");
  const hasReading = asRows(prior).length > 0 || asRows(alternatives).length > 0;

  return (
    <Section title={t("counterfactual.title")} description={t("counterfactual.description")}>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KeyValue label={t("claimItem.columns.determined")} value={uf(determined)} />
        <KeyValue label={t("claimItem.columns.deductible")} value={uf(deductible)} />
        <KeyValue label={t("claimItem.columns.indemnity")} value={uf(indemnity)} />
        <KeyValue
          label={t("claimItem.retention")}
          value={uf(retained)}
          tone={retained > 0 ? "warn" : "success"}
        />
      </div>
      <p className="mt-1 text-caption text-text-muted">{t("counterfactual.actual")}</p>

      {hasReading ? (
        <>
          <p className="mt-3 text-caption text-warn-text">{t("adjuster.suggestionOnly")}</p>
          <CounterfactualTable rows={prior} title={t("counterfactual.title")} />
          <CounterfactualTable rows={alternatives} title={t("counterfactual.alternatives")} />
          <p className="mt-4 text-caption text-text-muted">{t("counterfactual.renewalStory")}</p>
        </>
      ) : (
        <div className="mt-4">
          <EmptyState
            title={t("counterfactual.empty")}
            hint={t("counterfactual.emptyHint")}
            icon={<Scale className="h-6 w-6" />}
          />
        </div>
      )}
    </Section>
  );
}
