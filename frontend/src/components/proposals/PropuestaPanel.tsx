/**
 * Propuesta panel — the OUTBOUND broker artifact, mounted on the account page.
 *
 * TERMINOLOGY TRAP: this is NOT a `Proposal` (the insurer's inbound offer). The
 * broker mints a `BrokerProposal` SOLELY from an aligned comparison plus its
 * promoted winning column, ratifies it (`Proposals.Approve`), and sends it.
 *
 * DISCOVERY: the panel lists the account's propuestas via
 * `GET /broker-proposals?case_file_id={id}` (newest-first) and shows the newest,
 * so an existing propuesta surfaces on any device / reload. If the account has
 * none the panel offers "Generar propuesta", minting from the current
 * comparison's recommended (or first promoted) column.
 *
 * Every control is wired or visibly disabled with the server's reason. The PDF
 * download is a `SoonButton` while `pdf_document_id` is null (a later phase).
 *
 * Copy: the `propuesta` namespace (`es` authoritative, `en` mirrors).
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowUpRight,
  CheckCircle2,
  FileSignature,
  Hash,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCan } from "@/lib/permissions";
import { num, type CaseStage } from "@/api/types";
import {
  useBrokerProposalsByCase,
  useMintBrokerProposal,
  useRatifyBrokerProposal,
} from "@/api/brokerProposals";
import { useComparison, useCreateComparison } from "@/api/comparisons";
import { useProposal } from "@/api/proposals";
import {
  CopyButton,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  Section,
  SoonButton,
  StatusBadge,
  apiError,
  permille,
  pct,
  uf,
} from "@/components/common/kit";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  BrokerProposal,
  BrokerProposalAdditionalItem,
  BrokerProposalCore,
} from "@/api/types";

export function PropuestaPanel({
  caseId,
  stage: _stage,
}: {
  caseId: number;
  /** The account stage — reserved for future stage-aware copy. */
  stage?: CaseStage;
}) {
  const { t } = useTranslation("propuesta");

  const canView = useCan("Proposals", "View");
  const canApprove = useCan("Proposals", "Approve");

  // Discover the account's propuesta through the list (newest-first). No local
  // memory: the newest row is authoritative on any device / reload.
  const list = useBrokerProposalsByCase(caseId, canView.allowed);
  const newest = list.data?.[0] ?? null;

  if (!canView.isLoading && !canView.allowed) {
    return (
      <Card>
        <EmptyState title={t("noPermission")} />
      </Card>
    );
  }

  if (list.isLoading) {
    return <Skeleton className="h-56 w-full" />;
  }

  if (list.isError) {
    return (
      <Card>
        <div className="p-5">
          <ErrorBanner error={list.error} />
        </div>
      </Card>
    );
  }

  if (newest) {
    return (
      <BrokerProposalCard
        bp={newest}
        canApprove={canApprove.allowed}
        approveLoading={canApprove.isLoading}
      />
    );
  }

  return (
    <MintPanel
      caseId={caseId}
      canApprove={canApprove.allowed}
      approveLoading={canApprove.isLoading}
    />
  );
}

// =============================================================================
// The existing propuesta
// =============================================================================

function BrokerProposalCard({
  bp,
  canApprove,
  approveLoading,
}: {
  bp: BrokerProposal;
  canApprove: boolean;
  approveLoading: boolean;
}) {
  const { t } = useTranslation("propuesta");
  const ratify = useRatifyBrokerProposal(bp.id);

  // v8 payload: {core, additional, comparison_snapshot}. The winning offer moved
  // under comparison_snapshot; fall back to the legacy top-level shape.
  const snapshot = bp.payload?.comparison_snapshot ?? null;
  const winner = snapshot?.winning_proposal ?? bp.payload?.winning_proposal;
  const core = bp.payload?.core ?? null;
  const additional = bp.payload?.additional ?? [];

  const winnerId = bp.winning_proposal_id ?? winner?.id ?? null;
  const proposal = useProposal(winnerId ?? undefined);
  const insurerName =
    proposal.data?.insurer?.legal_name ??
    core?.insurer_name ??
    (winner ? t("winner.insurerRef", { id: winner.insurer_id }) : "—");

  const shortHash = bp.content_hash ? bp.content_hash.slice(0, 12) : null;

  const ratifyHint = !canApprove
    ? t("ratify.noPermission")
    : bp.is_ratified
      ? t("ratify.already")
      : null;

  const doRatify = () =>
    ratify.mutate(
      {},
      {
        onSuccess: () => toast.success(t("ratify.done")),
        onError: (error) => toast.error(apiError(error, t("ratify.error"))),
      },
    );

  return (
    <Section
      title={t("title")}
      description={t("subtitle")}
      actions={
        <span className="flex flex-wrap items-center gap-2">
          <StatusBadge value={bp.status} label={t(`status.${bp.status}`)} dot />
          {bp.is_ratified ? (
            <Badge variant="success" dot className="gap-1">
              <CheckCircle2 className="h-3 w-3" />
              {t("ratified")}
            </Badge>
          ) : null}
        </span>
      }
    >
      {/* The validated minimum core. Falls back to the winning offer's money when
          the payload predates the {core, additional} shape. */}
      {core ? (
        <CoreBlock core={core} insurerName={insurerName} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <KeyValue label={t("fields.winner")} value={insurerName} />
          <KeyValue
            label={t("fields.total")}
            value={uf(winner?.total_premium_uf ?? proposal.data?.total_premium_uf)}
          />
          <KeyValue
            label={t("fields.net")}
            value={uf(winner?.net_premium_uf ?? proposal.data?.net_premium_uf)}
          />
          <KeyValue
            label={t("fields.commission")}
            value={
              winner && num(winner.comprehensive_rate_permille) !== null
                ? `${num(winner.comprehensive_rate_permille)} ‰`
                : "—"
            }
          />
        </div>
      )}

      {/* The free tail — everything else worth carrying, rendered defensively. */}
      {additional.length > 0 ? (
        <AdditionalBlock items={additional} />
      ) : null}

      {/* Content hash — the tamper / version fingerprint. */}
      {shortHash ? (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-caption text-ink-3">
            <Hash className="h-3.5 w-3.5" />
            {t("fields.hash")}
          </span>
          <code className="rounded-md border border-line bg-bone px-1.5 py-0.5 text-[12px] tabular-nums text-ink-2">
            {shortHash}
          </code>
          {bp.content_hash ? (
            <CopyButton value={bp.content_hash} label={t("copyHash")} variant="ghost" />
          ) : null}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        {winnerId ? (
          <Button variant="secondary" size="sm" asChild>
            <Link to={`/proposals/${winnerId}`}>
              <ArrowUpRight className="h-4 w-4" />
              {t("openWinner")}
            </Link>
          </Button>
        ) : null}

        {/* Ratify — the approval act that freezes the artifact. */}
        <DisabledHint hint={approveLoading ? undefined : ratifyHint}>
          <Button
            size="sm"
            disabled={!canApprove || bp.is_ratified || ratify.isPending}
            onClick={doRatify}
          >
            <ShieldCheck className="h-4 w-4" />
            {bp.is_ratified ? t("ratify.done") : t("ratify.action")}
          </Button>
        </DisabledHint>

        {/* PDF is a later phase — visibly disabled with the reason, never hidden. */}
        <SoonButton reason={t("pdf.soon")}>{t("pdf.download")}</SoonButton>
      </div>

      {ratify.isError ? <ErrorBanner error={ratify.error} className="mt-3" /> : null}
    </Section>
  );
}

// =============================================================================
// Core + tail
// =============================================================================

/** Render a heterogeneous value for display, or null when blank. */
function coreText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return String(value);
  const s = String(value).trim();
  return s.length > 0 ? s : null;
}

/** The validated minimum core, rendered as a labeled key/value grid. Only the
 *  fields the winning offer actually stated are shown — shapes vary. */
function CoreBlock({
  core,
  insurerName,
}: {
  core: BrokerProposalCore;
  insurerName: string;
}) {
  const { t } = useTranslation("propuesta");

  const fields: {
    key: keyof BrokerProposalCore;
    render: (v: unknown) => string | null;
  }[] = [
    { key: "insured_name", render: coreText },
    { key: "insured_rut", render: coreText },
    { key: "coverage_start", render: coreText },
    { key: "coverage_end", render: coreText },
    { key: "validity_business_days", render: coreText },
    { key: "total_premium_uf", render: (v) => uf(v as never) },
    { key: "net_premium_uf", render: (v) => uf(v as never) },
    { key: "taxable_premium_uf", render: (v) => uf(v as never) },
    { key: "exempt_premium_uf", render: (v) => uf(v as never) },
    { key: "vat_uf", render: (v) => uf(v as never) },
    { key: "comprehensive_rate_permille", render: (v) => permille(v as never) },
    { key: "commission_pct", render: (v) => pct(v as never) },
  ];

  const rows = fields
    .map((f) => ({ key: f.key, value: f.render(core[f.key]) }))
    .filter((r) => r.value !== null);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <KeyValue label={t("core.fields.insurer_name")} value={insurerName} />
      {rows.map((r) => (
        <KeyValue
          key={String(r.key)}
          label={t(`core.fields.${String(r.key)}`)}
          value={r.value}
        />
      ))}
    </div>
  );
}

/** The free tail: heterogeneous dimensions appended after the core. Defensive —
 *  any of label/group/value/verbatim may be missing. */
function AdditionalBlock({ items }: { items: BrokerProposalAdditionalItem[] }) {
  const { t } = useTranslation("propuesta");
  return (
    <div className="mt-4 border-t border-line pt-4">
      <div className="text-caption font-medium text-ink-3">
        {t("additional.title")}
      </div>
      <ul className="mt-2 flex flex-col divide-y divide-line rounded-lg border border-line">
        {items.map((item, i) => {
          const label =
            coreText(item.label) ?? coreText(item.group) ?? t("additional.item");
          const value = coreText(item.value);
          const verbatim = coreText(item.verbatim);
          return (
            <li
              key={i}
              className="flex items-start justify-between gap-3 px-3.5 py-2.5"
            >
              <span className="min-w-0 text-body text-ink">{label}</span>
              <span className="flex shrink-0 items-center gap-1.5 text-right text-body font-medium tabular-nums text-ink-2">
                {value ?? "—"}
                {verbatim && verbatim !== value ? (
                  <TooltipProvider delayDuration={150}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="cursor-help text-caption font-normal text-ink-3 underline decoration-dotted underline-offset-2">
                          {t("additional.verbatim")}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="max-w-[320px] text-left">
                        {verbatim}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// =============================================================================
// Minting a new propuesta from the comparison
// =============================================================================

function MintPanel({
  caseId,
  canApprove,
  approveLoading,
}: {
  caseId: number;
  canApprove: boolean;
  approveLoading: boolean;
}) {
  const { t } = useTranslation("propuesta");

  // Create-or-get the account's live comparison (idempotent), then read it to
  // find the winning column. This mirrors the comparison page's own bootstrap.
  const create = useCreateComparison();
  const [comparisonId, setComparisonId] = React.useState<number | null>(null);
  const started = React.useRef(false);

  React.useEffect(() => {
    if (started.current) return;
    started.current = true;
    create.mutate({ case_file_id: caseId }, { onSuccess: (c) => setComparisonId(c.id) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const comparison = useComparison(comparisonId ?? undefined);
  const mint = useMintBrokerProposal();

  const data = comparison.data;
  const entries = data?.entries ?? [];
  // The winner: the recommended promoted column, else the first promoted one.
  const promoted = entries.filter((e) => e.proposal_id != null && !e.is_wrong_file);
  const winner = promoted.find((e) => e.is_recommended) ?? promoted[0] ?? null;

  const aligned = data?.status === "aligned";

  const blockedHint = !canApprove
    ? t("mint.noPermission")
    : !data
      ? undefined
      : !aligned
        ? t("mint.notAligned")
        : !winner
          ? t("mint.noWinner")
          : null;

  const doMint = () => {
    if (!comparisonId || !winner?.proposal_id) return;
    mint.mutate(
      { comparison_id: comparisonId, winning_proposal_id: winner.proposal_id },
      {
        // The mint hook invalidates the broker-proposals list, so the panel
        // re-discovers the freshly minted propuesta and swaps to its card.
        onSuccess: () => toast.success(t("mint.done")),
        onError: (error) => toast.error(apiError(error, t("mint.error"))),
      },
    );
  };

  if (create.isError) {
    return (
      <Card>
        <div className="p-5">
          <ErrorBanner error={create.error} />
        </div>
      </Card>
    );
  }

  if (!data || comparison.isLoading || create.isPending) {
    return <Skeleton className="h-52 w-full" />;
  }

  return (
    <Card>
      <EmptyState
        icon={<FileSignature className="h-6 w-6" />}
        title={t("mint.title")}
        hint={
          !aligned
            ? t("mint.hintAlign")
            : !winner
              ? t("mint.hintWinner")
              : t("mint.hintReady", {
                  insurer:
                    winner.proposal_id != null
                      ? t("winner.column", { n: promoted.indexOf(winner) + 1 })
                      : "",
                })
        }
        action={
          <div className="flex flex-col items-center gap-2">
            <DisabledHint hint={approveLoading ? undefined : blockedHint}>
              <Button
                disabled={!canApprove || !aligned || !winner || mint.isPending}
                onClick={doMint}
              >
                <Sparkles className="h-4 w-4" />
                {t("mint.action")}
              </Button>
            </DisabledHint>
            <Button variant="ghost" size="sm" asChild>
              <Link to={`/comparisons/${caseId}`}>{t("mint.openComparison")}</Link>
            </Button>
          </div>
        }
      />
      {mint.isError ? <ErrorBanner error={mint.error} className="mx-5 mb-5" /> : null}
    </Card>
  );
}
