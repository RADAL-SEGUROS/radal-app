/**
 * Mirror validation — hito c7.
 *
 * `GET /policies/{id}/mirror-diff` compares the CONFIRMED issuance proposal
 * against the CONFIRMED policy, field by field: coverages align on their
 * number, deductibles on the peril key, money on the five premium fields.
 *
 * Nothing here corrects anything. A difference becomes an
 * `endorsement(status=draft)` through `POST .../mirror-diff/queue`, and a human
 * confirms it later — suggest -> confirm -> commit, applied to a diff.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { CheckCircle2, Clock, GitCompareArrows, MinusCircle, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCan } from "@/lib/permissions";
import { useMirrorDiff, useQueueMirrorDiff } from "@/api/policies";
import type { MirrorDiffRow } from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  LoadingRows,
  Section,
  apiError,
} from "@/pages/proposals/shared";
import { PostsaleBadge, humanizePath, renderValue } from "@/pages/policies/shared";
import { ENDORSEMENT_KIND_LABEL, POSTSALE_SEVERITY_LABEL, enumLabel } from "@/i18n/labels";

/** `MirrorDiff.missing_sources` tokens → literal keys (never printed raw). */
const MIRROR_SOURCE_LABEL = {
  issuance_proposal: "postsale:mirror.sources.issuance_proposal",
  policy: "postsale:mirror.sources.policy",
} as const;

export function MirrorDiffPanel({
  policyId,
  hasProposal,
}: {
  policyId: number;
  /** `policy.proposal_id != null`. Unknown → inferred from the missing sources. */
  hasProposal?: boolean;
}) {
  const { t } = useTranslation("postsale");
  const diff = useMirrorDiff(policyId);
  const queue = useQueueMirrorDiff(policyId);
  const canCreate = useCan("Endorsements", "Create");

  const missing = diff.data?.missing_sources ?? [];
  // A policy registered without a proposal (manual entry, PDF read, portal)
  // has nothing to mirror: "no aplica" — never the green "calza" state, which
  // would certify a comparison that never ran (review v12 #6).
  const noProposal =
    hasProposal === false ||
    (hasProposal === undefined && !!diff.data && missing.includes("issuance_proposal"));
  const rows: MirrorDiffRow[] = noProposal || missing.length > 0 ? [] : (diff.data?.rows ?? []);
  const [busyPath, setBusyPath] = React.useState<string | null>(null);

  const gate = canCreate.isLoading
    ? t("shared.loading")
    : canCreate.allowed
      ? null
      : t("mirror.noPermission");

  const run = async (paths: string[], marker: string) => {
    setBusyPath(marker);
    try {
      const result = await queue.mutateAsync({ paths });
      toast.success(t("mirror.queued", { count: result.queued }));
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    } finally {
      setBusyPath(null);
    }
  };

  const kindLabel = (kind: MirrorDiffRow["suggested_endorsement_kind"]) => {
    if (!kind || typeof kind !== "string") return t("mirror.noSuggestion");
    const label = enumLabel(t, ENDORSEMENT_KIND_LABEL, kind);
    return t("mirror.suggestedKind", { kind: label });
  };

  return (
    <Section
      title={t("mirror.title")}
      description={t("mirror.description")}
      actions={
        rows.length > 0 ? (
          <DisabledHint hint={gate}>
            <Button
              size="sm"
              disabled={!canCreate.allowed || queue.isPending}
              onClick={() => void run([], "__all__")}
            >
              <Plus className="h-4 w-4" />
              {t("mirror.createAll")}
            </Button>
          </DisabledHint>
        ) : null
      }
    >
      <ErrorBanner error={diff.error} className="mb-3" />

      {diff.isLoading ? <LoadingRows rows={4} /> : null}

      {!diff.isLoading && noProposal ? (
        <EmptyState
          title={t("mirror.notApplicable")}
          hint={t("mirror.notApplicableHint")}
          icon={<MinusCircle className="h-6 w-6 text-ink-3" />}
        />
      ) : null}

      {!diff.isLoading && !noProposal && missing.length > 0 ? (
        <EmptyState
          title={t("mirror.pendingTitle")}
          hint={missing
            .map((source) => enumLabel(t, MIRROR_SOURCE_LABEL, source, t("mirror.sources.other")))
            .join(" ")}
          icon={<Clock className="h-6 w-6 text-warn-text" />}
        />
      ) : null}

      {!diff.isLoading && diff.data && missing.length === 0 && rows.length === 0 ? (
        <EmptyState
          title={t("mirror.empty")}
          hint={t("mirror.emptyHint")}
          icon={<CheckCircle2 className="h-6 w-6 text-pos-text" />}
        />
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("mirror.columns.field")}</TableHead>
                <TableHead>{t("mirror.columns.expected")}</TableHead>
                <TableHead>{t("mirror.columns.found")}</TableHead>
                <TableHead>{t("mirror.columns.severity")}</TableHead>
                <TableHead className="text-right">{t("mirror.columns.action")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.path}>
                  <TableCell className="align-top">
                    <div className="font-medium text-text-primary">
                      {humanizePath(row.path)}
                    </div>
                    <div className="mt-0.5 text-caption text-text-muted">
                      {row.path}
                    </div>
                  </TableCell>
                  <TableCell className="align-top text-text-secondary">
                    {renderValue(row.expected)}
                  </TableCell>
                  <TableCell className="align-top text-text-primary">
                    {renderValue(row.found)}
                  </TableCell>
                  <TableCell className="align-top">
                    <PostsaleBadge
                      value={row.severity}
                      label={enumLabel(t, POSTSALE_SEVERITY_LABEL, row.severity)}
                    />
                  </TableCell>
                  <TableCell className="align-top text-right">
                    <div className="flex flex-col items-end gap-1.5">
                      <DisabledHint hint={gate}>
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={!canCreate.allowed || queue.isPending}
                          onClick={() => void run([row.path], row.path)}
                        >
                          <GitCompareArrows className="h-3.5 w-3.5" />
                          {busyPath === row.path ? t("shared.loading") : t("mirror.createEndorsement")}
                        </Button>
                      </DisabledHint>
                      <Badge variant="neutral" className="max-w-[220px] truncate">
                        {kindLabel(row.suggested_endorsement_kind)}
                      </Badge>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </Section>
  );
}
