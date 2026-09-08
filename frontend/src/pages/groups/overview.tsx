/**
 * `/groups/:groupId` — the group HUB, the broker's home for one account.
 *
 * Layout (Signal, v5):
 *   1. PageHeader — name, counts (cuentas · pólizas · empresas), latest
 *      vigencia chip; actions: Descargar expediente (re-homed from the old
 *      rail, `Documents.View`-gated with its scope menu), Prórroga
 *      (`Endorsements.Create`) and the primary Nueva cuenta (`CaseFiles.Create`).
 *   2. HERO — one card per ramo of the selected vigencia (default: latest;
 *      a quiet segmented selector appears when there are several). Each card
 *      shows the line name, the Journey COMPACT rail (render-only — the
 *      transitions live on the account page) and quiet stat chips linking
 *      straight into the account's tabs (`?tab=records|quotes|proposals|policies`).
 *   3. Underline tabs: Bitácora / Vigencias / Empresas.
 *
 * Data notes that survive the restyle:
 *   - The Bitácora is cursor-paged (`next_before`), newest first, GROUPED BY
 *     VIGENCIA — the vigencia of a row is resolved through the tree via its
 *     `case_file_id`, never inferred from `occurred_at`.
 *   - A group has NO RUT of its own: the companies (empresas) inside it do.
 *     The third tab lists them; their RUT renders as the company's identifier.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  CalendarRange,
  ChevronDown,
  Layers,
  Lock,
  Plus,
  TimerReset,
  Users,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  SoonButton,
} from "@/components/common/kit";
import { ClientStatusBadge } from "@/pages/clients/status";
import {
  CaseStatusBadge,
  GroupCrumbs,
  OriginChip,
  periodDates,
  useGroupId,
} from "@/pages/groups/shared";
import { Journey } from "@/components/groups/Journey";
import { DownloadArchiveButton } from "@/components/groups/DownloadArchiveButton";
import {
  useAccountGroup,
  useDetachGroupClient,
  useGroupTimeline,
  useGroupTree,
} from "@/api/accountGroups";
import { useCan } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import { useTimelineCopy } from "@/components/groups/timelineCopy";
import type {
  GroupTimelineEntry,
  GroupTimelineKind,
  GroupTree,
  TreeLineNode,
  TreePeriodNode,
} from "@/api/types";

const ALL = "__all__";
const PAGE_SIZE = 50;

const KIND_TONE: Record<GroupTimelineKind, "neutral" | "brand" | "action" | "warn" | "success"> = {
  stage: "brand",
  activity: "neutral",
  note: "neutral",
  policy: "success",
  endorsement: "action",
  claim: "warn",
  collection: "warn",
};

/** `case_file_id -> period_label`, built from the tree so the rows can be grouped. */
function periodByCase(tree: GroupTree | undefined): Map<number, string> {
  const map = new Map<number, string>();
  for (const period of tree?.periods ?? []) {
    for (const line of period.lines) {
      map.set(line.account.case_file_id, period.label);
      for (const policy of line.policies) {
        for (const kind of ["endorsement", "collection", "claim"] as const) {
          for (const child of policy.children[kind]) {
            map.set(child.case_file_id, period.label);
          }
        }
      }
    }
  }
  return map;
}

/** The account folders of the group, so a row can link to the right page. */
function accountCaseIds(tree: GroupTree | undefined): Set<number> {
  const ids = new Set<number>();
  for (const period of tree?.periods ?? []) {
    for (const line of period.lines) ids.add(line.account.case_file_id);
  }
  return ids;
}

export default function GroupOverviewPage() {
  const { t } = useTranslation("accounts");
  const groupId = useGroupId();
  const [params, setParams] = useSearchParams();

  const group = useAccountGroup(groupId);
  const tree = useGroupTree(groupId);

  const canCreateCase = useCan("CaseFiles", "Create");
  const canEndorse = useCan("Endorsements", "Create");

  const tab = params.get("tab") ?? "timeline";
  const setTab = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    setParams(next, { replace: true });
  };

  if (group.isError) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader title={t("group.notFound")} />
        <ErrorBanner error={group.error} />
        <Button variant="secondary" size="sm" asChild>
          <Link to="/groups">{t("nav.allGroups")}</Link>
        </Button>
      </div>
    );
  }

  const g = group.data;
  const periods = tree.data?.periods ?? [];
  const policiesCount = periods.reduce(
    (total, period) =>
      total + period.lines.reduce((sum, line) => sum + line.policies.length, 0),
    0,
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              { label: g?.name ?? t("group.one") },
            ]}
          />
        }
        title={g ? g.name : <Skeleton className="h-7 w-56" />}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span>
              {t("tree.accounts", { count: g?.accounts_count ?? 0 })} ·{" "}
              {t("overview.policies", { count: policiesCount })} ·{" "}
              {t("overview.companies", { count: g?.clients_count ?? 0 })}
            </span>
            {g?.latest_period_label ? (
              <MonoChip>{g.latest_period_label}</MonoChip>
            ) : null}
            {g && g.status !== "active" ? (
              <Badge variant="muted">{t(`group.status.${g.status}`)}</Badge>
            ) : null}
          </span>
        }
        actions={
          <>
            <DownloadArchiveButton
              groupId={groupId}
              periodLabel={g?.latest_period_label ?? null}
            />

            {canEndorse.allowed ? (
              <Button size="sm" variant="secondary" asChild>
                <Link to={`/groups/${groupId}/policies/extend`}>
                  <TimerReset className="h-4 w-4" />
                  {t("extend.title")}
                </Link>
              </Button>
            ) : (
              <DisabledHint hint={t("extend.noPermission")}>
                <Button size="sm" variant="secondary" disabled>
                  <TimerReset className="h-4 w-4" />
                  {t("extend.title")}
                </Button>
              </DisabledHint>
            )}

            {canCreateCase.allowed ? (
              <Button size="sm" asChild>
                <Link to={`/groups/${groupId}/accounts/new`}>
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
            )}
          </>
        }
      />

      <GroupHero
        groupId={groupId}
        tree={tree.data}
        isLoading={tree.isLoading}
        canCreateCase={canCreateCase.allowed}
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList variant="underline" className="flex-wrap">
          <TabsTrigger value="timeline">{t("timeline.title")}</TabsTrigger>
          <TabsTrigger value="periods">{t("overview.periods")}</TabsTrigger>
          <TabsTrigger value="clients">{t("group.fields.clients")}</TabsTrigger>
        </TabsList>

        <TabsContent value="timeline" className="mt-4">
          <TimelinePane groupId={groupId} tree={tree.data} />
        </TabsContent>

        <TabsContent value="periods" className="mt-4">
          <PeriodsPane groupId={groupId} tree={tree.data} isLoading={tree.isLoading} />
        </TabsContent>

        <TabsContent value="clients" className="mt-4">
          <ClientsPane groupId={groupId} />
        </TabsContent>
      </Tabs>

      {tree.isError ? <ErrorBanner error={tree.error} className="mt-2" /> : null}
    </div>
  );
}

// =============================================================================
// HERO — the selected vigencia, one Journey card per ramo
// =============================================================================

function GroupHero({
  groupId,
  tree,
  isLoading,
  canCreateCase,
}: {
  groupId: number;
  tree: GroupTree | undefined;
  isLoading: boolean;
  canCreateCase: boolean;
}) {
  const { t } = useTranslation("accounts");
  const periods = tree?.periods ?? [];
  const [selected, setSelected] = React.useState<string | null>(null);

  const period: TreePeriodNode | undefined =
    periods.find((item) => item.label === selected) ??
    periods.find((item) => item.is_latest) ??
    periods[0];

  if (isLoading) {
    return (
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  // An empty group is the first thing a broker sees after creating one: teach
  // the next step instead of showing a bare card. The CTA is accent-soft, not a
  // second `primary` competing with the header's.
  const newAccountCta = canCreateCase ? (
    <Button size="sm" variant="accent-soft" asChild>
      <Link to={`/groups/${groupId}/accounts/new`}>
        <Plus className="h-4 w-4" />
        {t("tree.newAccount")}
      </Link>
    </Button>
  ) : (
    <DisabledHint hint={t("account.noCreatePermission")}>
      <Button size="sm" variant="accent-soft" disabled>
        <Plus className="h-4 w-4" />
        {t("tree.newAccount")}
      </Button>
    </DisabledHint>
  );

  if (!period) {
    return (
      <Card>
        <EmptyState
          icon={<CalendarRange className="h-6 w-6" />}
          title={t("empty.accounts")}
          hint={t("empty.accountsHint")}
          action={newAccountCta}
        />
      </Card>
    );
  }

  return (
    <FadeUp>
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          {periods.length > 1 ? (
            <Tabs value={period.label} onValueChange={setSelected}>
              <TabsList variant="segmented" className="h-auto flex-wrap">
                {periods.map((item) => (
                  <TabsTrigger key={item.label} value={item.label}>
                    {item.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          ) : (
            <span className="text-label font-medium text-ink">{period.label}</span>
          )}

          <span className="flex flex-wrap items-center gap-2 text-caption text-ink-3">
            <span className="tabular-nums">{periodDates(period.start, period.end)}</span>
            {!period.is_latest ? (
              <Badge variant="muted">{t("tree.historic")}</Badge>
            ) : null}
            <Link
              to={`/groups/${groupId}/periods/${encodeURIComponent(period.label)}`}
              className="no-underline transition-colors duration-150 hover:text-brand-deep"
            >
              {t("tree.goToPeriod", { label: period.label })}
            </Link>
          </span>
        </div>

        {period.lines.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Layers className="h-6 w-6" />}
              title={t("empty.lines")}
              hint={t("empty.accountsHint")}
              action={newAccountCta}
            />
          </Card>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {period.lines.map((line) => (
              <GroupLineCard
                key={line.account.case_file_id}
                groupId={groupId}
                line={line}
                linkCard
              />
            ))}
          </div>
        )}
      </section>
    </FadeUp>
  );
}

/**
 * One ramo of a vigencia as a Signal card: line name, its own full dates, the
 * compact Journey rail (render-only) and quiet stat chips into the account's
 * tabs. `period.tsx` reuses it and appends the folder chips, the policies and
 * the renewal footer through `children` — one look, two depths.
 */
export function GroupLineCard({
  groupId,
  line,
  linkCard = false,
  children,
}: {
  groupId: number;
  line: TreeLineNode;
  /** Whole-card click → the account page (hero cards only; inner links stop). */
  linkCard?: boolean;
  children?: React.ReactNode;
}) {
  const { t } = useTranslation("accounts");
  const navigate = useNavigate();

  const account = line.account;
  const accountPath = `/groups/${groupId}/accounts/${account.case_file_id}`;

  const stats: { tab: string; label: string; count: number }[] = [
    { tab: "records", label: t("tree.records"), count: account.documents_count },
    { tab: "quotes", label: t("tree.quotes"), count: account.quotes_count },
    { tab: "proposals", label: t("tree.proposals"), count: account.proposals_count },
    { tab: "policies", label: t("tree.policies"), count: line.policies.length },
  ];

  return (
    <Card
      interactive={linkCard}
      className={linkCard ? "flex cursor-pointer flex-col overflow-hidden" : "flex flex-col overflow-hidden"}
      onClick={linkCard ? () => navigate(accountPath) : undefined}
    >
      <div className="flex flex-col gap-3 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <Link
              to={accountPath}
              onClick={(e) => e.stopPropagation()}
              className="text-h3 tracking-tight text-ink no-underline transition-colors duration-150 hover:text-brand-deep"
            >
              {account.line_name || line.name}
            </Link>
            <p className="mt-0.5 text-caption tabular-nums text-ink-3">
              {periodDates(account.period_start, account.period_end)}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-1.5">
            {account.origin !== "new" ? <OriginChip origin={account.origin} t={t} /> : null}
            {account.status !== "open" ? <CaseStatusBadge status={account.status} /> : null}
            {account.period_locked ? (
              <DisabledHint hint={t("tree.locked")}>
                <span className="inline-flex items-center text-ink-3">
                  <Lock className="h-3.5 w-3.5" />
                </span>
              </DisabledHint>
            ) : null}
          </div>
        </div>

        <Journey kind={account.kind} stage={account.stage} variant="compact" />

        <div className="flex flex-wrap gap-1.5">
          {stats.map((stat) => (
            <Link
              key={stat.tab}
              to={`${accountPath}?tab=${stat.tab}`}
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1.5 rounded-md border border-line bg-bone px-2 py-1 text-caption text-ink-2 no-underline transition-[border-color,background-color] duration-150 hover:border-line-strong hover:bg-paper-2/60"
            >
              {stat.label}
              <span className="font-medium tabular-nums text-ink">{stat.count}</span>
            </Link>
          ))}
        </div>
      </div>

      {children}
    </Card>
  );
}

// =============================================================================
// Bitácora — descending, grouped by vigencia
// =============================================================================

function TimelinePane({ groupId, tree }: { groupId: number; tree: GroupTree | undefined }) {
  const { t } = useTranslation("accounts");
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [rows, setRows] = React.useState<GroupTimelineEntry[]>([]);
  const [done, setDone] = React.useState(false);

  const page = useGroupTimeline(groupId, {
    limit: PAGE_SIZE,
    before: cursor ?? undefined,
  });

  // Accumulate cursor pages. The key includes `before`, so each page is cached
  // separately and this effect appends exactly once per page.
  React.useEffect(() => {
    const data = page.data;
    if (!data) return;
    setRows((current) => {
      const seen = new Set(
        current.map((row) => `${row.kind}|${row.occurred_at}|${row.title}`),
      );
      const next = data.entries.filter(
        (row) => !seen.has(`${row.kind}|${row.occurred_at}|${row.title}`),
      );
      return next.length ? [...current, ...next] : current;
    });
    if (!data.next_before) setDone(true);
  }, [page.data]);

  const periodOf = React.useMemo(() => periodByCase(tree), [tree]);
  const accountIds = React.useMemo(() => accountCaseIds(tree), [tree]);

  const grouped = React.useMemo(() => {
    const buckets = new Map<string, GroupTimelineEntry[]>();
    for (const row of rows) {
      const label =
        (row.case_file_id != null ? periodOf.get(row.case_file_id) : undefined) ?? "";
      const bucket = buckets.get(label);
      if (bucket) bucket.push(row);
      else buckets.set(label, [row]);
    }
    // Newest vigencia first; the unlabelled bucket sinks to the bottom.
    return [...buckets.entries()].sort((a, b) => {
      if (!a[0]) return 1;
      if (!b[0]) return -1;
      return b[0].localeCompare(a[0]);
    });
  }, [rows, periodOf]);

  if (page.isLoading && rows.length === 0) {
    return (
      <div className="flex flex-col gap-2.5">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (page.isError && rows.length === 0) {
    return <ErrorBanner error={page.error} />;
  }

  if (rows.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<Layers className="h-6 w-6" />}
          title={t("timeline.empty")}
          hint={t("empty.timeline")}
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {grouped.map(([label, entries], index) => (
        <PeriodTimelineCard
          key={label || "__unlabelled__"}
          label={label}
          entries={entries}
          groupId={groupId}
          accountIds={accountIds}
          defaultOpen={index === 0}
        />
      ))}

      {!done ? (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            disabled={page.isFetching}
            onClick={() => setCursor(page.data?.next_before ?? null)}
          >
            {t("overview.loadMore")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** One vigencia's rows. The latest is open; older ones start collapsed. */
function PeriodTimelineCard({
  label,
  entries,
  groupId,
  accountIds,
  defaultOpen,
}: {
  label: string;
  entries: GroupTimelineEntry[];
  groupId: number;
  accountIds: Set<number>;
  defaultOpen: boolean;
}) {
  const { t } = useTranslation("accounts");
  const timelineCopy = useTimelineCopy();
  const [open, setOpen] = React.useState(defaultOpen);

  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 border-b border-line px-5 py-3.5 text-left transition-colors duration-150 hover:bg-paper-2/60"
      >
        <span className="flex items-center gap-2">
          <CalendarRange className="h-4 w-4 text-brand" />
          <span className="text-h3 tracking-tight text-ink">
            {label || t("overview.otherEntries")}
          </span>
          {label ? (
            <Link
              to={`/groups/${groupId}/periods/${encodeURIComponent(label)}`}
              onClick={(e) => e.stopPropagation()}
              className="text-caption text-ink-3 no-underline transition-colors duration-150 hover:text-brand-deep"
            >
              {t("tree.goToPeriod", { label })}
            </Link>
          ) : null}
        </span>
        <span className="flex items-center gap-2 text-caption tabular-nums text-ink-3">
          {entries.length}
          <ChevronDown
            className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </span>
      </button>

      {open ? (
        <ol className="divide-y divide-line">
          {entries.map((entry, index) => {
            const to =
              entry.policy_id != null
                ? `/groups/${groupId}/policies/${entry.policy_id}`
                : entry.case_file_id != null
                  ? accountIds.has(entry.case_file_id)
                    ? `/groups/${groupId}/accounts/${entry.case_file_id}`
                    : `/cases/${entry.case_file_id}`
                  : null;
            return (
              <li key={`${entry.occurred_at}-${index}`} className="flex gap-3 px-5 py-3">
                <Badge variant={KIND_TONE[entry.kind]} className="mt-0.5 h-fit shrink-0">
                  {timelineCopy(entry).kindLabel}
                </Badge>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    {to ? (
                      <Link
                        to={to}
                        className="text-body font-medium text-ink no-underline transition-colors duration-150 hover:text-brand-deep"
                      >
                        {timelineCopy(entry).label}
                      </Link>
                    ) : (
                      <span className="text-body font-medium text-ink">
                        {timelineCopy(entry).label}
                      </span>
                    )}
                    <span className="text-caption text-ink-3">
                      {formatDateTime(entry.occurred_at)}
                    </span>
                  </div>
                  {timelineCopy(entry).detail ? (
                    <p className="mt-0.5 text-caption text-ink-2">
                      {timelineCopy(entry).detail}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </Card>
  );
}

// =============================================================================
// Vigencias — the tree as cards, with the two filters the payload can honour
// =============================================================================

function PeriodsPane({
  groupId,
  tree,
  isLoading,
}: {
  groupId: number;
  tree: GroupTree | undefined;
  isLoading: boolean;
}) {
  const { t } = useTranslation("accounts");
  const [status, setStatus] = React.useState<string>(ALL);
  const [line, setLine] = React.useState<string>(ALL);

  const lineOptions = React.useMemo(() => {
    const byId = new Map<number, string>();
    for (const period of tree?.periods ?? []) {
      for (const node of period.lines) {
        if (!byId.has(node.insurance_line_id)) byId.set(node.insurance_line_id, node.name);
      }
    }
    return [...byId.entries()].map(([id, name]) => ({ id, name }));
  }, [tree]);

  const matches = (node: TreeLineNode) =>
    (status === ALL || node.account.status === status) &&
    (line === ALL || String(node.insurance_line_id) === line);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2.5">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const periods = tree?.periods ?? [];
  if (periods.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<CalendarRange className="h-6 w-6" />}
          title={t("empty.accounts")}
          hint={t("empty.accountsHint")}
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-wrap items-center gap-2.5 p-3.5">
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-[180px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("overview.filters.allStatuses")}</SelectItem>
            <SelectItem value="open">{t("overview.filters.open")}</SelectItem>
            <SelectItem value="closed">{t("overview.filters.closed")}</SelectItem>
          </SelectContent>
        </Select>

        <Select value={line} onValueChange={setLine}>
          <SelectTrigger className="w-[220px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("overview.filters.allLines")}</SelectItem>
            {lineOptions.map((option) => (
              <SelectItem key={option.id} value={String(option.id)}>
                {option.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <SoonButton reason={t("overview.filters.soon")}>
          {t("overview.filters.insurer")}
        </SoonButton>
        <SoonButton reason={t("overview.filters.soon")}>
          {t("overview.filters.owner")}
        </SoonButton>
      </Card>

      {periods.map((period) => {
        const lines = period.lines.filter(matches);
        return (
          <Card key={period.label} className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3.5">
              <div className="flex items-center gap-2">
                <Link
                  to={`/groups/${groupId}/periods/${encodeURIComponent(period.label)}`}
                  className="text-h3 tracking-tight text-ink no-underline transition-colors duration-150 hover:text-brand-deep"
                >
                  {period.label}
                </Link>
                {period.is_latest ? (
                  <Badge variant="brand">{t("period.current")}</Badge>
                ) : (
                  <Badge variant="muted">{t("tree.historic")}</Badge>
                )}
              </div>
              <span className="text-caption tabular-nums text-ink-3">
                {periodDates(period.start, period.end)}
              </span>
            </div>

            {lines.length === 0 ? (
              <p className="px-5 py-6 text-caption text-ink-3">{t("empty.accounts")}</p>
            ) : (
              <ul className="divide-y divide-line">
                {lines.map((node) => (
                  <li key={node.account.case_file_id}>
                    <Link
                      to={`/groups/${groupId}/accounts/${node.account.case_file_id}`}
                      className="flex flex-wrap items-center gap-3 px-5 py-3 no-underline transition-colors duration-150 hover:bg-paper-2/60"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body font-medium text-ink">
                          {node.name}
                        </span>
                        <span className="mt-0.5 block text-caption tabular-nums text-ink-3">
                          {periodDates(node.account.period_start, node.account.period_end)}
                        </span>
                      </span>
                      <OriginChip origin={node.account.origin} t={t} />
                      <Journey
                        kind={node.account.kind}
                        stage={node.account.stage}
                        variant="compact"
                        className="w-56 shrink-0 max-sm:hidden"
                      />
                      <span className="text-caption tabular-nums text-ink-3">
                        {t("tree.policies")} {node.policies.length}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        );
      })}
    </div>
  );
}

// =============================================================================
// Empresas — the companies inside the group (they hold the RUTs, not the group)
// =============================================================================

function ClientsPane({ groupId }: { groupId: number }) {
  const { t } = useTranslation("accounts");
  const group = useAccountGroup(groupId);
  const detach = useDetachGroupClient(groupId);
  const canEdit = useCan("Groups", "Edit");

  if (group.isLoading) return <Skeleton className="h-40 w-full" />;

  const clients = group.data?.clients ?? [];
  if (clients.length === 0) {
    return (
      <Card>
        <EmptyState icon={<Users className="h-6 w-6" />} title={t("empty.clients")} />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {detach.isError ? <ErrorBanner error={detach.error} /> : null}
      <Card className="overflow-hidden">
        <ul className="divide-y divide-line">
          {clients.map((client) => (
            <li key={client.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
              <Link
                to={`/clients/${client.id}`}
                className="min-w-0 flex-1 truncate text-body text-ink no-underline transition-colors duration-150 hover:text-brand-deep"
              >
                {client.legal_name}
                {client.trade_name ? (
                  <span className="text-ink-3"> · {client.trade_name}</span>
                ) : null}
              </Link>
              <span className="text-caption tabular-nums text-ink-3">{client.rut}</span>
              <ClientStatusBadge status={client.status} />
              <DisabledHint hint={canEdit.allowed ? null : t("group.noEditPermission")}>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!canEdit.allowed || detach.isPending}
                  onClick={() => {
                    if (window.confirm(t("group.detach.confirm"))) detach.mutate(client.id);
                  }}
                >
                  {t("group.actions.removeClient")}
                </Button>
              </DisabledHint>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
