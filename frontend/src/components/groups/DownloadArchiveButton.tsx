/**
 * "Descargar expediente" — the group (or one vigencia) as a ZIP (spec v3 §4.1).
 *
 * The bytes never stream through the app: `POST /account-groups/{id}/archives`
 * generates a `document(entity_type=account_group, category=archive_pack)` row
 * and answers with an ordinary presigned `DocumentDownload`, which we open in a
 * new tab. That is not a preference — the deployed backend runs behind
 * CloudFront OAC in **buffered** invoke mode (`docs/deployment.md`, constraint
 * 4), so a streamed multi-megabyte body would arrive in one chunk at the end,
 * or not at all.
 *
 * Only documents of cases visible to the caller are included, which is why the
 * scope menu says so out loud rather than promising "todo el grupo".
 *
 * Gate: `Documents.View`. Without it the control renders **disabled with its
 * reason**, never hidden and never dead (rule 3).
 *
 * Home: the group hub's header (`pages/groups/overview.tsx`) — it left the old
 * contextual rail when the single-sidebar shell landed.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, Loader2 } from "lucide-react";
import { useCreateArchive } from "@/api/accountGroups";
import { useCan } from "@/lib/permissions";
import { Button, type ButtonProps } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { apiError, DisabledHint, resolveFileUrl } from "@/components/common/kit";

export function DownloadArchiveButton({
  groupId,
  /** When given, the menu offers "sólo esta vigencia" alongside the whole group. */
  periodLabel,
  variant = "secondary",
  size = "sm",
  className,
  /** Icon-only, for the narrow rail header. */
  compact = false,
}: {
  groupId: number;
  periodLabel?: string | null;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  className?: string;
  compact?: boolean;
}) {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const { allowed, isLoading: permsLoading } = useCan("Documents", "View");
  const createArchive = useCreateArchive(groupId);
  const [busy, setBusy] = React.useState(false);

  const run = async (scope: string | null) => {
    setBusy(true);
    const toastId = toast.loading(t("archive.preparing"));
    try {
      const result = await createArchive.mutateAsync(
        scope ? { period_label: scope } : {},
      );
      const url = resolveFileUrl(result.download?.url);
      if (url) window.open(url, "_blank", "noreferrer");
      toast.success(t("archive.ready"), { id: toastId });
    } catch (error) {
      toast.error(apiError(error, t("archive.error")), { id: toastId });
    } finally {
      setBusy(false);
    }
  };

  const label = t("tree.download");
  const disabledReason = permsLoading
    ? tc("state.loading")
    : !allowed
      ? t("archive.forbidden")
      : null;

  const renderTrigger = (onClick?: () => void) => (
    <Button
      variant={variant}
      size={size}
      className={className}
      disabled={!allowed || busy}
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      {busy ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Download className="h-4 w-4" />
      )}
      {compact ? null : <span className="truncate">{label}</span>}
    </Button>
  );

  // No vigencia in context → one action, so no menu to choose from.
  if (!periodLabel) {
    return (
      <DisabledHint hint={disabledReason}>
        {renderTrigger(() => void run(null))}
      </DisabledHint>
    );
  }

  if (!allowed || busy) {
    return <DisabledHint hint={disabledReason}>{renderTrigger()}</DisabledHint>;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{renderTrigger()}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[240px]">
        <DropdownMenuLabel>{t("archive.title")}</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => void run(null)}>
          {t("archive.wholeGroup")}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void run(periodLabel)}>
          {t("archive.onlyPeriod", { label: periodLabel })}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <p className="px-2 py-1.5 text-[11px] leading-snug text-ink-3">
          {t("archive.scope")}
        </p>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
