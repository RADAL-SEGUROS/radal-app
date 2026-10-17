/**
 * The policy ficha's header actions (lane F1), responsive.
 *
 * Six controls do not fit a 375 px header, so:
 *  - ≥ md: the full row — Abrir carpeta · PDF original · Compartir ·
 *    Nuevo endoso (the ONE ink-filled primary) · Nuevo siniestro · Renovar.
 *  - < md: the primary, Compartir as an icon, and "Más" — a menu holding the
 *    rest. A disabled entry in the menu prints its reason as text under the
 *    label, so a phone (where tooltips never open) still reads WHY.
 *
 * "Nuevo endoso" follows the server's operability row (`endorsements`): when
 * the policy lacks its number the server says so in Spanish and the button
 * carries that reason, instead of a client-side rule.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ExternalLink,
  FileText,
  FolderOpen,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Share2,
  Siren,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { resolveFileUrl } from "@/components/common/kit";
import { useCan } from "@/lib/permissions";
import { useDocumentDownload } from "@/api/documents";
import type { PolicyDetail } from "@/api/policies";
import { OpenSourceLink } from "@/components/policies/PolicyPayloadPanel";
import { ShareDialog } from "@/components/share/ShareDialog";
import { TouchHint } from "@/components/policies/TouchHint";

/** A menu entry that cannot run: label + the reason, readable on touch. */
function DisabledEntry({
  icon,
  label,
  reason,
  soon,
}: {
  icon: React.ReactNode;
  label: string;
  reason: string;
  soon?: boolean;
}) {
  const { t } = useTranslation("common");
  return (
    <div
      role="menuitem"
      aria-disabled="true"
      className="flex items-start gap-2 rounded-sm px-2 py-1.5 text-body text-ink-3 [&_svg]:size-4 [&_svg]:shrink-0"
    >
      <span className="mt-0.5">{icon}</span>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          {label}
          {soon ? (
            <Badge variant="muted" className="px-1.5 py-0 text-[11px]">
              {t("nav.soon")}
            </Badge>
          ) : null}
        </span>
        <span className="block text-caption text-ink-3">{reason}</span>
      </span>
    </div>
  );
}

function SourceEntry({ documentId }: { documentId: number | null }) {
  const { t } = useTranslation("postsale");
  const download = useDocumentDownload(documentId ?? undefined, documentId != null);
  const url = resolveFileUrl(download.data?.url);
  const label = t("policy.extracted.openSource");
  if (documentId == null) {
    return <DisabledEntry icon={<FileText />} label={label} reason={t("policy.overview.noSource")} />;
  }
  if (!url) {
    return (
      <DisabledEntry
        icon={<FileText />}
        label={label}
        reason={download.isError ? t("policy.overview.noSource") : t("policy.overview.opening")}
      />
    );
  }
  return (
    <DropdownMenuItem asChild className="min-h-10">
      <a href={url} target="_blank" rel="noreferrer">
        <ExternalLink />
        {label}
      </a>
    </DropdownMenuItem>
  );
}

export function PolicyHeaderActions({
  policy,
  onNewEndorsement,
  onNewClaim,
}: {
  policy: PolicyDetail;
  onNewEndorsement: () => void;
  onNewClaim: () => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tp } = useTranslation("policies");
  const canEndorse = useCan("Endorsements", "Create");
  const canClaim = useCan("Claims", "Create");
  const canCases = useCan("CaseFiles", "View");

  const endorsementRow = (policy.operability ?? []).find((row) => row.feature === "endorsements");
  const endorseReason = !canEndorse.allowed
    ? t("endorsement.noPermission")
    : endorsementRow && !endorsementRow.enabled
      ? endorsementRow.reason ?? null
      : null;
  const claimReason = canClaim.allowed ? null : t("claim.noPermission");
  const caseReason = canCases.allowed ? null : tp("noPermission.cases");
  const caseLink = policy.case_file_id ? `/cases/${policy.case_file_id}` : null;
  // Renewal is opened on the ACCOUNT (ramo × vigencia), never on a policy:
  // "Renovar" leads to that account's Renovación tab, where the server says
  // whether the next vigencia can be opened yet.
  const renewGroup = policy.journey?.group_id ?? policy.group_id ?? null;
  const renewCase = policy.journey?.case_file_id ?? policy.case_file_id ?? null;
  const renewLink =
    renewGroup != null && renewCase != null
      ? `/groups/${renewGroup}/accounts/${renewCase}?tab=renewal`
      : null;
  const renewReason = !renewLink ? t("shared.soonRenewal") : caseReason;

  const shareTrigger = (iconOnly: boolean) => (
    <ShareDialog
      entityType="policy"
      entityId={policy.id}
      defaultAudience="insured"
      trigger={
        <Button
          variant="secondary"
          size={iconOnly ? "icon" : "sm"}
          className={iconOnly ? "h-10 w-10 shrink-0" : undefined}
          aria-label={tp("share.action")}
        >
          <Share2 className="h-4 w-4" />
          {iconOnly ? null : tp("share.action")}
        </Button>
      }
    />
  );

  const endorseButton = (className?: string) => (
    <TouchHint hint={endorseReason} className={className}>
      <Button
        size="sm"
        className={className ? "h-10 w-full" : undefined}
        disabled={!!endorseReason}
        onClick={onNewEndorsement}
      >
        <Plus className="h-4 w-4" />
        {t("policy.actions.newEndorsement")}
      </Button>
    </TouchHint>
  );

  return (
    <>
      {/* ≥ md — the full row */}
      <div className="hidden flex-wrap items-center gap-2.5 md:flex">
        {caseLink ? (
          caseReason ? (
            <TouchHint hint={caseReason}>
              <Button variant="secondary" size="sm" disabled>
                <FolderOpen className="h-4 w-4" />
                {t("policy.actions.openCase")}
              </Button>
            </TouchHint>
          ) : (
            <Button asChild variant="secondary" size="sm">
              <Link to={caseLink}>
                <FolderOpen className="h-4 w-4" />
                {t("policy.actions.openCase")}
              </Link>
            </Button>
          )
        ) : null}
        <OpenSourceLink documentId={policy.source_document_id} />
        {shareTrigger(false)}
        {endorseButton()}
        <TouchHint hint={claimReason}>
          <Button variant="secondary" size="sm" disabled={!!claimReason} onClick={onNewClaim}>
            <Siren className="h-4 w-4" />
            {t("policy.actions.newClaim")}
          </Button>
        </TouchHint>
        {renewLink && !renewReason ? (
          <Button asChild variant="secondary" size="sm">
            <Link to={renewLink}>
              <RefreshCw className="h-4 w-4" />
              {t("policy.actions.renew")}
            </Link>
          </Button>
        ) : (
          <TouchHint hint={renewReason}>
            <Button variant="secondary" size="sm" disabled>
              <RefreshCw className="h-4 w-4" />
              {t("policy.actions.renew")}
            </Button>
          </TouchHint>
        )}
      </div>

      {/* < md — primary · share · Más */}
      <div className="flex w-full items-center gap-2 md:hidden">
        {endorseButton("min-w-0 flex-1")}
        {shareTrigger(true)}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary" className="h-10 shrink-0" aria-label={tp("actions.more")}>
              <MoreHorizontal className="h-4 w-4" />
              {tp("actions.moreShort")}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[min(300px,calc(100vw-24px))]">
            {endorseReason ? (
              <>
                <DisabledEntry
                  icon={<Plus />}
                  label={t("policy.actions.newEndorsement")}
                  reason={endorseReason}
                />
                <DropdownMenuSeparator />
              </>
            ) : null}
            {caseLink ? (
              caseReason ? (
                <DisabledEntry
                  icon={<FolderOpen />}
                  label={t("policy.actions.openCase")}
                  reason={caseReason}
                />
              ) : (
                <DropdownMenuItem asChild className="min-h-10">
                  <Link to={caseLink}>
                    <FolderOpen />
                    {t("policy.actions.openCase")}
                  </Link>
                </DropdownMenuItem>
              )
            ) : null}
            <SourceEntry documentId={policy.source_document_id} />
            {claimReason ? (
              <DisabledEntry icon={<Siren />} label={t("policy.actions.newClaim")} reason={claimReason} />
            ) : (
              <DropdownMenuItem className="min-h-10" onSelect={onNewClaim}>
                <Siren />
                {t("policy.actions.newClaim")}
              </DropdownMenuItem>
            )}
            {renewLink && !renewReason ? (
              <DropdownMenuItem asChild className="min-h-10">
                <Link to={renewLink}>
                  <RefreshCw />
                  {t("policy.actions.renew")}
                </Link>
              </DropdownMenuItem>
            ) : (
              <DisabledEntry
                icon={<RefreshCw />}
                label={t("policy.actions.renew")}
                reason={renewReason ?? t("shared.soonRenewal")}
              />
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </>
  );
}
