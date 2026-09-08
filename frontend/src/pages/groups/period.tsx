/**
 * `/groups/:groupId/periods/:periodLabel` — one vigencia, one card per ramo.
 *
 * The label groups; the DATES are the contract. Coccolino carries Vehículos
 * ago–ago and Incendio abr–abr both under "2026-2027", so every card prints its
 * account's own full `period_start`–`period_end` and never the period's
 * extremes (spec §1).
 *
 * The cards are the group hub's `GroupLineCard` (name + compact Journey + stat
 * chips) with this page's extra depth appended: the ANTECEDENTES folder chips,
 * the policies and the renewal footer.
 *
 * A historic vigencia (`is_latest === false`) is read-only: no "nueva cuenta",
 * no renewal CTA, an explicit "histórico" chip. Nothing is hidden — the cards,
 * counts and policies stay fully navigable.
 */
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CalendarRange, FileText, Plus, ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
} from "@/components/common/kit";
import { GroupCrumbs, periodDates, useGroupId } from "@/pages/groups/shared";
import { GroupLineCard } from "@/pages/groups/overview";
import { useAccountGroup, useGroupTree } from "@/api/accountGroups";
import { useNavigator } from "@/api/navigator";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { RECORD_FOLDER_KEYS, type TreeLineNode, type TreePeriodNode } from "@/api/types";

export default function GroupPeriodPage() {
  const { t } = useTranslation("accounts");
  const groupId = useGroupId();
  const { periodLabel = "" } = useParams();
  const label = decodeURIComponent(periodLabel);

  const group = useAccountGroup(groupId);
  const tree = useGroupTree(groupId);
  const canCreateCase = useCan("CaseFiles", "Create");

  const period: TreePeriodNode | undefined = tree.data?.periods.find(
    (item) => item.label === label,
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              { label: group.data?.name ?? t("group.one"), to: `/groups/${groupId}` },
              { label },
            ]}
          />
        }
        title={t("period.title", { label })}
        subtitle={
          period ? (
            <span className="flex flex-wrap items-center gap-2">
              <span>{t("period.subtitle")}</span>
              <MonoChip>{periodDates(period.start, period.end)}</MonoChip>
              <Badge variant={period.is_latest ? "brand" : "muted"}>
                {period.is_latest ? t("period.current") : t("period.historic")}
              </Badge>
            </span>
          ) : (
            t("period.subtitle")
          )
        }
        actions={
          period?.is_latest ? (
            canCreateCase.allowed ? (
              <Button size="sm" asChild>
                <Link to={`/groups/${groupId}/accounts/new?period=${encodeURIComponent(label)}`}>
                  <Plus className="h-4 w-4" />
                  {t("tree.newAccount")}
                </Link>
              </Button>
            ) : (
              <DisabledHint hint={t("account.noCreatePermission")}>
                <Button size="sm" disabled>
                  <Plus className="h-4 w-4" />
                  {t("tree.newAccount")}
                </Button>
              </DisabledHint>
            )
          ) : (
            <Badge variant="muted">{t("tree.readOnly")}</Badge>
          )
        }
      />

      {tree.isError ? <ErrorBanner error={tree.error} /> : null}

      {tree.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : !period ? (
        <Card>
          <EmptyState
            icon={<CalendarRange className="h-6 w-6" />}
            title={t("empty.periods")}
            hint={t("period.notFound")}
            action={
              <Button size="sm" variant="secondary" asChild>
                <Link to={`/groups/${groupId}`}>{t("group.actions.open")}</Link>
              </Button>
            }
          />
        </Card>
      ) : period.lines.length === 0 ? (
        <Card>
          <EmptyState
            icon={<CalendarRange className="h-6 w-6" />}
            title={t("empty.accounts")}
            hint={t("empty.accountsHint")}
          />
        </Card>
      ) : (
        <FadeUp>
          <div className="grid gap-4 xl:grid-cols-2">
            {period.lines.map((line) => (
              <LineCard
                key={line.account.case_file_id}
                groupId={groupId}
                line={line}
                isHistoric={!period.is_latest}
              />
            ))}
          </div>
        </FadeUp>
      )}
    </div>
  );
}

function LineCard({
  groupId,
  line,
  isHistoric,
}: {
  groupId: number;
  line: TreeLineNode;
  isHistoric: boolean;
}) {
  const { t } = useTranslation("accounts");
  const navigator = useNavigator();
  const canCreateCase = useCan("CaseFiles", "Create");

  const account = line.account;
  const accountPath = `/groups/${groupId}/accounts/${account.case_file_id}`;
  const folders = navigator.data?.record_folders ?? [];
  const folderKeys = folders.length
    ? folders.map((folder) => folder.key)
    : [...RECORD_FOLDER_KEYS];

  return (
    <GroupLineCard groupId={groupId} line={line}>
      <div className="flex flex-wrap gap-1.5 border-t border-line px-5 py-3">
        {folderKeys.map((key) => {
          const count = account.record_counts[key] ?? 0;
          return (
            <Link
              key={key}
              to={`${accountPath}?tab=records&folder=${key}`}
              className="no-underline"
            >
              <Badge variant={count > 0 ? "neutral" : "outline"}>
                <FileText className="mr-1 h-3 w-3" />
                {t(`folders.${key}`, { defaultValue: key })}
                <span className="ml-1 tabular-nums">{count}</span>
              </Badge>
            </Link>
          );
        })}
      </div>

      {line.policies.length > 0 ? (
        <ul className="divide-y divide-line border-t border-line">
          {line.policies.map((policy, index) => (
            <li key={policy.id}>
              <Link
                to={`/groups/${groupId}/policies/${policy.id}`}
                className="flex flex-wrap items-center gap-2 px-5 py-2.5 no-underline transition-colors duration-150 hover:bg-paper-2/60"
              >
                <ShieldCheck className="h-4 w-4 shrink-0 text-brand" />
                <span className="text-label tabular-nums text-ink">
                  {t("tree.policyLabel", { n: index + 1, number: policy.policy_number })}
                </span>
                <span className="text-caption text-ink-3">
                  {policy.insurer_name ?? "—"}
                </span>
                <span className="ml-auto text-caption tabular-nums text-ink-3">
                  {policy.extended_end_date
                    ? t("tree.extendedUntil", {
                        date: formatDate(policy.extended_end_date),
                      })
                    : periodDates(policy.start_date, policy.end_date)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="border-t border-line px-5 py-3 text-caption text-ink-3">
          {t("empty.policies")}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3">
        <span className="text-caption text-ink-3">{t("renewal.title")}</span>
        {line.renewal.case_file_id ? (
          <Button variant="secondary" size="sm" asChild>
            <Link to={`/groups/${groupId}/accounts/${line.renewal.case_file_id}`}>
              {t("renewal.open")}
            </Link>
          </Button>
        ) : isHistoric || !line.renewal.allowed || !canCreateCase.allowed ? (
          <DisabledHint
            hint={
              !canCreateCase.allowed
                ? t("account.noCreatePermission")
                : line.renewal.reason
                  ? t(`renewal.reason.${line.renewal.reason}`, {
                      defaultValue: t("tree.readOnly"),
                    })
                  : t("tree.readOnly")
            }
          >
            <Button variant="secondary" size="sm" disabled>
              {t("renewal.start")}
            </Button>
          </DisabledHint>
        ) : (
          <Button variant="secondary" size="sm" asChild>
            <Link to={`${accountPath}/renew`}>{t("renewal.start")}</Link>
          </Button>
        )}
      </div>
    </GroupLineCard>
  );
}
