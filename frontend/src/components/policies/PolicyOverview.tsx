/**
 * Policies overview — the scannable board of an account's issued policies.
 *
 * Many policies per account is the norm (one per vigencia × ramo, plus prior
 * renewals), so this is a clean `DataTable`: number, insurer, vigencia, total
 * premium, and the fixed-core verdict as a `validada / revisar` badge. Each row
 * inspects the policy; a quiet action opens the ORIGINAL issued file through the
 * documents download flow (the only place an S3 key lives).
 *
 * Dual-mode: pass a `caseId` for the per-account surface (list filtered + KPIs
 * derived from those rows), or none for the broker-wide board (reuses
 * `GET /policies/summary`). The upload dialog is wired here so the empty state
 * teaches the next step instead of dead-ending.
 *
 * Copy: `postsale` namespace (`policy.overview.*` / `policy.status.*`).
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, ExternalLink, ShieldCheck, Upload } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { KpiCard } from "@/components/common/KpiCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatDate } from "@/lib/format";
import { previewDocument } from "@/lib/download";
import { num } from "@/api/types";
import type { Policy } from "@/api/types";
import { usePolicies, usePoliciesSummary } from "@/api/policies";
import {
  apiError,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  uf,
} from "@/components/common/kit";
import { PolicyUploadDialog } from "@/components/policies/PolicyUploadDialog";

/** Whole days from now to `end`, or null. */
function daysToEnd(end: string | null | undefined): number | null {
  if (!end) return null;
  const ms = new Date(end).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  return Math.round(ms / 86_400_000);
}

/** Opens the original issued file — fetches the bytes AUTHENTICATED (the content
 *  route 401s for a plain link) and previews them in a new tab. */
function OpenSourceButton({ documentId }: { documentId: number | null }) {
  const { t } = useTranslation("postsale");
  const [busy, setBusy] = React.useState(false);

  if (documentId == null) {
    return (
      <DisabledHint hint={t("policy.overview.noSource")}>
        <Button size="sm" variant="ghost" disabled>
          <ExternalLink className="h-3.5 w-3.5" />
          {t("policy.overview.openSource")}
        </Button>
      </DisabledHint>
    );
  }

  return (
    <Button
      size="sm"
      variant="ghost"
      disabled={busy}
      onClick={async (e) => {
        e.stopPropagation();
        setBusy(true);
        try {
          await previewDocument(documentId);
        } catch (error) {
          toast.error(apiError(error, t("policy.overview.openSource")));
        } finally {
          setBusy(false);
        }
      }}
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {busy ? t("policy.overview.opening") : t("policy.overview.openSource")}
    </Button>
  );
}

export function PolicyOverview({
  caseId,
  canUpload = false,
  uploadHint,
  canView = true,
}: {
  /** Filter to one account. Omit for the broker-wide board. */
  caseId?: number;
  canUpload?: boolean;
  /** Why upload is disabled (historic vigencia, no permission…). */
  uploadHint?: string | null;
  /** `Policies.View` — gates the summary call in global mode. */
  canView?: boolean;
}) {
  const { t } = useTranslation("postsale");
  const navigate = useNavigate();
  const perAccount = caseId != null;

  const policies = usePolicies({ case_file_id: caseId, limit: 200 });
  const summary = usePoliciesSummary(!perAccount && canView);

  const [uploadOpen, setUploadOpen] = React.useState(false);

  const rows = policies.data?.items ?? [];

  // Per-account KPIs from the rows in hand; broker-wide KPIs from the summary.
  const active = rows.filter((p) => p.status === "active");
  const accountGross = active.reduce((sum, p) => sum + (num(p.total_premium_uf) ?? 0), 0);
  const accountExpiring = active.filter((p) => {
    const d = daysToEnd(p.period_end_at ?? p.end_date);
    return d !== null && d >= 0 && d <= 60;
  }).length;

  const columns = React.useMemo<ColumnDef<Policy, unknown>[]>(
    () => [
      {
        id: "number",
        header: t("policy.overview.columns.number"),
        accessorFn: (row) => row.policy_number,
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-1.5 font-medium tabular-nums text-ink">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-brand" />
            {row.original.policy_number}
          </span>
        ),
      },
      {
        id: "insurer",
        header: t("policy.overview.columns.insurer"),
        accessorFn: (row) => row.insurer_name ?? `#${row.insurer_id}`,
      },
      {
        id: "period",
        header: t("policy.overview.columns.period"),
        cell: ({ row }) => {
          const start = row.original.period_start_at ?? row.original.start_date;
          const end = row.original.period_end_at ?? row.original.end_date;
          return (
            <span className="tabular-nums text-ink-2">
              {start ? formatDate(start) : "—"} — {end ? formatDate(end) : "—"}
            </span>
          );
        },
      },
      {
        id: "total",
        header: t("policy.overview.columns.total"),
        accessorFn: (row) => row.total_premium_uf ?? "",
        cell: ({ row }) => (
          <span className="tabular-nums font-medium">{uf(row.original.total_premium_uf)}</span>
        ),
      },
      {
        id: "core",
        header: t("policy.overview.columns.core"),
        cell: ({ row }) => <CoreBadge policy={row.original} />,
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <div
            className="flex justify-end"
            onClick={(e) => e.stopPropagation()}
            role="presentation"
          >
            <OpenSourceButton documentId={row.original.source_document_id} />
          </div>
        ),
      },
    ],
    [t],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* KPI strip */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {perAccount ? (
          <>
            <KpiCard label={t("policy.overview.kpi.total")} value={rows.length} />
            <KpiCard label={t("policy.overview.kpi.active")} value={active.length} />
            <KpiCard
              label={t("policy.overview.kpi.expiring")}
              value={accountExpiring}
              hint={t("policy.overview.kpi.expiringHint")}
            />
            <KpiCard label={t("policy.overview.kpi.gross")} value={uf(accountGross)} />
          </>
        ) : (
          <>
            <KpiCard label={t("policy.overview.kpi.total")} value={summary.data?.total ?? 0} />
            <KpiCard
              label={t("policy.overview.kpi.active")}
              value={summary.data?.active_count ?? 0}
            />
            <KpiCard
              label={t("policy.overview.kpi.expiring")}
              value={summary.data?.expiring_within_60_days ?? 0}
              hint={t("policy.overview.kpi.expiringHint")}
            />
            <KpiCard
              label={t("policy.overview.kpi.gross")}
              value={uf(summary.data?.total_premium_uf)}
            />
          </>
        )}
      </div>

      {policies.isError ? <ErrorBanner error={policies.error} /> : null}

      {/* Upload CTA (per-account only) */}
      {perAccount ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-caption text-ink-3">{t("policy.overview.uploadHint")}</p>
          <DisabledHint hint={canUpload ? null : (uploadHint ?? undefined)}>
            <Button size="sm" disabled={!canUpload} onClick={() => setUploadOpen(true)}>
              <Upload className="h-4 w-4" />
              {t("policy.overview.upload")}
            </Button>
          </DisabledHint>
        </div>
      ) : null}

      {rows.length === 0 && !policies.isLoading ? (
        <Card>
          <EmptyState
            icon={<ShieldCheck className="h-6 w-6" />}
            title={t("policy.overview.empty")}
            hint={perAccount ? t("policy.overview.emptyHint") : undefined}
            action={
              perAccount && canUpload ? (
                <Button size="sm" onClick={() => setUploadOpen(true)}>
                  <Upload className="h-4 w-4" />
                  {t("policy.overview.upload")}
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <DataTable
          columns={columns}
          data={rows}
          isLoading={policies.isLoading}
          emptyMessage={t("policy.overview.empty")}
          onRowClick={(row) => navigate(`/policies/${row.id}`)}
        />
      )}

      {perAccount ? (
        <PolicyUploadDialog
          caseId={caseId}
          open={uploadOpen}
          onOpenChange={setUploadOpen}
          onUploaded={() => void policies.refetch()}
        />
      ) : null}
    </div>
  );
}

/** validada / revisar — the fixed-core verdict as a status-dot badge. */
function CoreBadge({ policy }: { policy: Policy }) {
  const { t } = useTranslation("postsale");
  if (policy.is_core_valid == null) {
    return <span className="text-caption text-ink-3">—</span>;
  }
  return policy.is_core_valid ? (
    <Badge variant="success" dot className="gap-1">
      <CheckCircle2 className="h-3 w-3" />
      {t("policy.overview.core.valid")}
    </Badge>
  ) : (
    <Badge variant="warn" dot>
      {t("policy.overview.core.review")}
    </Badge>
  );
}
