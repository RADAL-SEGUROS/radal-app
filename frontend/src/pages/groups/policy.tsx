/**
 * `/groups/:groupId/policies/:policyId` — a policy, seen from inside its group.
 *
 * The policy itself is `components/policies/PolicyDetailBody`, unchanged: one
 * definition of identidad / espejo / garantías / post-venta, whether the broker
 * arrives from `/policies` or from the group tree. What this page adds is the
 * thing the standalone page cannot know — WHERE this policy sits: the group's
 * breadcrumb, and the group sub-funnel, i.e. the sibling policies of the same
 * vigencia and the versioned children (endoso · cobranza · siniestro) hanging
 * off each, read from `GET /account-groups/{id}/tree`.
 *
 * Batched endorsements render ONCE with N policy chips (spec §5.3): a prórroga
 * over four policies is one act, and drawing it four times would make the
 * broker think four things happened.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Layers, ShieldCheck, TimerReset } from "lucide-react";

import { PolicyDetailBody } from "@/components/policies/PolicyDetailBody";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DisabledHint, EmptyState, ErrorBanner, MonoChip, Section } from "@/pages/proposals/shared";
import {
  GroupCrumbs,
  periodDates,
  useGroupId,
  usePolicyId,
} from "@/pages/groups/shared";
import { useAccountGroup, useGroupTree } from "@/api/accountGroups";
import { useCan } from "@/lib/permissions";
import { formatDate, formatDateTime } from "@/lib/format";
import type {
  GroupTree,
  TreeLineNode,
  TreePeriodNode,
  TreePolicyNode,
  TreePostSaleNode,
} from "@/api/types";

type Located = {
  period: TreePeriodNode;
  line: TreeLineNode;
  policy: TreePolicyNode;
};

function locate(tree: GroupTree | undefined, policyId: number): Located | null {
  for (const period of tree?.periods ?? []) {
    for (const line of period.lines) {
      for (const policy of line.policies) {
        if (policy.id === policyId) return { period, line, policy };
      }
    }
  }
  return null;
}

export default function GroupPolicyPage() {
  const { t } = useTranslation("accounts");
  const groupId = useGroupId();
  const policyId = usePolicyId();

  const group = useAccountGroup(groupId);
  const tree = useGroupTree(groupId);
  const canEndorse = useCan("Endorsements", "Create");

  const at = locate(tree.data, policyId);

  return (
    <div className="flex flex-col gap-5">
      <GroupCrumbs
        items={[
          { label: t("group.title"), to: "/groups" },
          { label: group.data?.name ?? t("group.one"), to: `/groups/${groupId}` },
          ...(at
            ? [
                {
                  label: at.period.label,
                  to: `/groups/${groupId}/periods/${encodeURIComponent(at.period.label)}`,
                },
                {
                  label: at.line.name,
                  to: `/groups/${groupId}/accounts/${at.line.account.case_file_id}`,
                },
              ]
            : []),
          { label: at?.policy.policy_number ?? t("tree.policies") },
        ]}
      />

      <PolicyDetailBody policyId={policyId} />

      {tree.isError ? <ErrorBanner error={tree.error} /> : null}

      <Section
        title={t("policy.subFunnel")}
        description={t("policy.subFunnelHint")}
        actions={
          canEndorse.allowed ? (
            <Button variant="secondary" size="sm" asChild>
              <Link to={`/groups/${groupId}/policies/extend`}>
                <TimerReset className="h-4 w-4" />
                {t("extend.title")}
              </Link>
            </Button>
          ) : (
            <DisabledHint hint={t("extend.noPermission")}>
              <Button variant="secondary" size="sm" disabled>
                <TimerReset className="h-4 w-4" />
                {t("extend.title")}
              </Button>
            </DisabledHint>
          )
        }
      >
        {tree.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : !at ? (
          <EmptyState
            icon={<Layers className="h-6 w-6" />}
            title={t("policy.notInTree")}
            hint={t("policy.notInTreeHint")}
          />
        ) : (
          <div className="flex flex-col gap-4">
            {at.line.policies.map((policy) => (
              <PolicyCard
                key={policy.id}
                groupId={groupId}
                policy={policy}
                isCurrent={policy.id === policyId}
              />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

function PolicyCard({
  groupId,
  policy,
  isCurrent,
}: {
  groupId: number;
  policy: TreePolicyNode;
  isCurrent: boolean;
}) {
  const { t } = useTranslation("accounts");

  return (
    <Card
      className={
        isCurrent
          ? "overflow-hidden border-brand-line"
          : "overflow-hidden"
      }
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
        <ShieldCheck className="h-4 w-4 shrink-0 text-brand" />
        {isCurrent ? (
          <span className="text-label tabular-nums text-text-primary">{policy.policy_number}</span>
        ) : (
          <Link
            to={`/groups/${groupId}/policies/${policy.id}`}
            className="text-label tabular-nums text-text-primary no-underline hover:text-brand-deep"
          >
            {policy.policy_number}
          </Link>
        )}
        <span className="text-caption text-text-muted">{policy.insurer_name ?? "—"}</span>
        {isCurrent ? <Badge variant="brand">{t("policy.current")}</Badge> : null}
        <span className="ml-auto text-caption text-text-muted">
          {policy.extended_end_date
            ? t("tree.extendedUntil", { date: formatDate(policy.extended_end_date) })
            : periodDates(policy.start_date, policy.end_date)}
        </span>
      </div>

      <div className="grid gap-4 px-5 py-4 md:grid-cols-3">
        <ChildColumn
          title={t("tree.endorsement")}
          nodes={policy.children.endorsement}
          module="Endorsements"
        />
        <ChildColumn
          title={t("tree.collection")}
          nodes={policy.children.collection}
          module="Collections"
        />
        <ChildColumn
          title={t("tree.claims")}
          nodes={policy.children.claim}
          module="Claims"
        />
      </div>
    </Card>
  );
}

/**
 * One post-sale column. A shared `batch_key` means one prórroga fanned over N
 * policies: it is drawn once, with the number of members as a chip.
 */
function ChildColumn({
  title,
  nodes,
  module,
}: {
  title: string;
  nodes: TreePostSaleNode[];
  module: "Endorsements" | "Collections" | "Claims";
}) {
  const { t } = useTranslation("accounts");
  const can = useCan(module, "View");

  const rows = React.useMemo(() => {
    const seen = new Set<string>();
    const out: { node: TreePostSaleNode; batched: boolean }[] = [];
    for (const node of nodes) {
      if (node.batch_key) {
        if (seen.has(node.batch_key)) continue;
        seen.add(node.batch_key);
        out.push({ node, batched: true });
      } else {
        out.push({ node, batched: false });
      }
    }
    return out;
  }, [nodes]);

  if (!can.allowed) {
    return (
      <div>
        <h3 className="text-label text-text-muted">{title}</h3>
        <p className="mt-1.5 text-caption text-text-muted">{t("policy.noChildPermission")}</p>
      </div>
    );
  }

  return (
    <div>
      <h3 className="flex items-center gap-2 text-label text-text-muted">
        {title}
        <span className="tabular-nums">{nodes.length}</span>
      </h3>
      {rows.length === 0 ? (
        <p className="mt-1.5 text-caption text-text-muted">{t("policy.noChildren")}</p>
      ) : (
        <ul className="mt-1.5 flex flex-col gap-1.5">
          {rows.map(({ node, batched }) => (
            <li key={node.case_file_id} className="flex flex-wrap items-center gap-1.5">
              <Link
                to={`/cases/${node.case_file_id}`}
                className="text-caption text-text-primary no-underline hover:text-brand-deep"
              >
                {node.reference ?? `#${node.case_file_id}`}
              </Link>
              {node.effective_at ? (
                <span className="text-caption text-text-muted">
                  {formatDateTime(node.effective_at)}
                </span>
              ) : null}
              {batched ? (
                <DisabledHint hint={t("tree.batchHint")}>
                  <MonoChip>{t("tree.batch")}</MonoChip>
                </DisabledHint>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
