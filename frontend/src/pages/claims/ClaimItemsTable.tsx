/**
 * The adjuster's per-partida table.
 *
 * One row per partida, and the columns are the adjuster's own progression:
 *
 *     notificado -> determinado -> daño -> deducible -> indemnización
 *
 * Every column is summed, and the server sends the totals with the rows so the
 * page never re-adds them differently from the API. The retention line is the
 * one figure the insured actually feels: determined damage minus indemnity.
 */
import { useTranslation } from "react-i18next";
import { ClipboardList } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useClaimItems } from "@/api/claims";
import { num } from "@/api/types";
import {
  EmptyState,
  ErrorBanner,
  KeyValue,
  LoadingRows,
  Section,
  uf,
} from "@/pages/proposals/shared";

export function ClaimItemsTable({ claimId }: { claimId: number }) {
  const { t } = useTranslation("postsale");
  const items = useClaimItems(claimId);

  const rows = [...(items.data?.items ?? [])].sort((a, b) => a.sort_order - b.sort_order);
  const totals = items.data?.totals;
  const retention =
    totals !== undefined
      ? (num(totals.determined_uf) ?? 0) - (num(totals.indemnity_uf) ?? 0)
      : null;

  return (
    <Section title={t("claimItem.title")} description={t("claimItem.description")}>
      <ErrorBanner error={items.error} className="mb-3" />
      {items.isLoading ? <LoadingRows rows={4} /> : null}

      {!items.isLoading && rows.length === 0 ? (
        <EmptyState
          title={t("claimItem.empty")}
          hint={t("claimItem.emptyHint")}
          icon={<ClipboardList className="h-6 w-6" />}
        />
      ) : null}

      {rows.length > 0 ? (
        <>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("claimItem.columns.item")}</TableHead>
                  <TableHead>{t("claimItem.columns.kind")}</TableHead>
                  <TableHead className="text-right">
                    {t("claimItem.columns.notified")}
                  </TableHead>
                  <TableHead className="text-right">
                    {t("claimItem.columns.determined")}
                  </TableHead>
                  <TableHead className="text-right">{t("claimItem.columns.damage")}</TableHead>
                  <TableHead className="text-right">
                    {t("claimItem.columns.deductible")}
                  </TableHead>
                  <TableHead className="text-right">
                    {t("claimItem.columns.indemnity")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="align-top">
                      <div className="max-w-[320px]">
                        <div className="font-medium text-text-primary">
                          {row.item ?? `#${row.id}`}
                        </div>
                        {row.basis ? (
                          <p className="mt-0.5 text-caption text-text-muted">
                            {t("claimItem.basis")}: {row.basis}
                          </p>
                        ) : null}
                        {row.note ? (
                          <p className="mt-0.5 text-caption text-text-muted">{row.note}</p>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="align-top">
                      <Badge variant="neutral">{t(`claimItem.kind.${row.kind}`)}</Badge>
                    </TableCell>
                    <TableCell className="text-right align-top tabular-nums">
                      {uf(row.notified_uf)}
                    </TableCell>
                    <TableCell className="text-right align-top tabular-nums">
                      {uf(row.determined_uf)}
                    </TableCell>
                    <TableCell className="text-right align-top tabular-nums">
                      {uf(row.damage_uf)}
                    </TableCell>
                    <TableCell className="text-right align-top tabular-nums text-warn-text">
                      {uf(row.deductible_uf)}
                    </TableCell>
                    <TableCell className="text-right align-top tabular-nums font-medium">
                      {uf(row.indemnity_uf)}
                    </TableCell>
                  </TableRow>
                ))}

                {totals ? (
                  <TableRow className="border-t-2 border-line">
                    <TableCell colSpan={2} className="font-medium">
                      {t("claimItem.totals")}
                    </TableCell>
                    <TableCell className="text-right tabular-nums font-medium">
                      {uf(totals.notified_uf)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums font-medium">
                      {uf(totals.determined_uf)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums font-medium">
                      {uf(totals.damage_uf)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums font-medium text-warn-text">
                      {uf(totals.deductible_uf)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums font-medium text-pos-text">
                      {uf(totals.indemnity_uf)}
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </div>

          {retention !== null ? (
            <div className="mt-4">
              <KeyValue
                label={t("claimItem.retention")}
                value={uf(retention)}
                tone={retention > 0 ? "warn" : "default"}
              />
              <p className="mt-1 text-caption text-text-muted">{t("claimItem.retentionHint")}</p>
            </div>
          ) : null}
        </>
      ) : null}
    </Section>
  );
}
