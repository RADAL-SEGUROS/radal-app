/**
 * The post-sale sub-funnel of a policy.
 *
 * `GET /policies/{id}/case-files` returns the child expedientes already ordered
 * by `kind, sequence_no, version` — the "past -> current with dates" tracker
 * the team asked for. Two axes that are easy to confuse:
 *
 *   - `sequence_no` distinguishes DIFFERENT cases on the same policy (E1, E2);
 *   - `version` is the rework counter of ONE case — v2 supersedes v1 after the
 *     insurer bounces it.
 *
 * So the rail groups by kind, lists each sequence once, and shows the older
 * versions nested underneath with their dates. Stage and status labels come
 * from the `cases` namespace, which owns the journey vocabulary.
 */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, GitBranch, History } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import { usePolicyCaseFiles } from "@/api/caseFiles";
import { CASE_FILE_KINDS, type CaseFileKind, type CaseFileRef } from "@/api/types";
import { EmptyState, ErrorBanner, LoadingRows, Section } from "@/pages/proposals/shared";
import { PostsaleBadge } from "@/pages/policies/shared";

interface Group {
  kind: CaseFileKind;
  /** One entry per `sequence_no`: the live version plus its superseded history. */
  chains: { sequence: number; current: CaseFileRef; history: CaseFileRef[] }[];
}

function group(cases: CaseFileRef[]): Group[] {
  const byKind = new Map<CaseFileKind, Map<number, CaseFileRef[]>>();
  for (const row of cases) {
    const kinds = byKind.get(row.kind) ?? new Map<number, CaseFileRef[]>();
    const chain = kinds.get(row.sequence_no) ?? [];
    chain.push(row);
    kinds.set(row.sequence_no, chain);
    byKind.set(row.kind, kinds);
  }
  const order = CASE_FILE_KINDS;
  return [...byKind.entries()]
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([kind, kinds]) => ({
      kind,
      chains: [...kinds.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([sequence, rows]) => {
          const sorted = [...rows].sort((a, b) => b.version - a.version);
          return { sequence, current: sorted[0], history: sorted.slice(1) };
        }),
    }));
}

function CaseRow({ row, muted }: { row: CaseFileRef; muted?: boolean }) {
  const { t } = useTranslation("postsale");
  const { t: tCases } = useTranslation("cases");

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-card border border-line px-3 py-2.5",
        muted ? "bg-bg-recessed/60 opacity-80" : "bg-bg-surface",
      )}
    >
      <span className="text-caption tabular-nums text-text-tertiary">
        {row.reference ?? `#${row.id}`}
      </span>
      <span className="min-w-0 flex-1 truncate text-body text-text-primary">{row.title}</span>

      <Badge variant="outline">{t("subFunnel.version", { n: row.version })}</Badge>
      <PostsaleBadge value={row.stage} label={tCases(`stages.${row.stage}`)} />
      <PostsaleBadge value={row.status} label={tCases(`statuses.${row.status}`)} />

      <span className="whitespace-nowrap text-caption text-text-muted">
        {formatDate(row.opened_at)}
        {row.closed_at ? ` → ${formatDate(row.closed_at)}` : ""}
      </span>

      <Button asChild variant="ghost" size="sm">
        <Link to={`/cases/${row.id}`}>
          {t("subFunnel.openCase")}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </Button>
    </div>
  );
}

export function SubFunnel({ policyId }: { policyId: number }) {
  const { t } = useTranslation("postsale");
  const cases = usePolicyCaseFiles(policyId);
  const groups = group(cases.data ?? []);

  return (
    <Section title={t("subFunnel.title")} description={t("subFunnel.description")}>
      <ErrorBanner error={cases.error} className="mb-3" />

      {cases.isLoading ? <LoadingRows rows={3} /> : null}

      {!cases.isLoading && groups.length === 0 ? (
        <EmptyState
          title={t("subFunnel.empty")}
          hint={t("subFunnel.emptyHint")}
          icon={<GitBranch className="h-6 w-6" />}
        />
      ) : null}

      <div className="flex flex-col gap-5">
        {groups.map((g) => (
          <div key={g.kind} className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <PostsaleBadge value={g.kind} label={t(`subFunnel.kind.${g.kind}`)} />
              <span className="text-caption text-text-muted">
                {t("subFunnel.columns.case")} · {g.chains.length}
              </span>
            </div>

            {g.chains.map((chain) => (
              <div key={`${g.kind}-${chain.sequence}`} className="flex flex-col gap-1.5">
                <div className="flex items-start gap-2">
                  <span className="mt-2.5 w-9 shrink-0 text-caption font-medium text-brand">
                    {t("subFunnel.sequence", { n: chain.sequence })}
                  </span>
                  <div className="min-w-0 flex-1">
                    <CaseRow row={chain.current} />
                  </div>
                </div>

                {chain.history.length > 0 ? (
                  <div className="ml-11 flex flex-col gap-1.5 border-l border-dashed border-line pl-3">
                    <span className="flex items-center gap-1.5 text-caption text-text-muted">
                      <History className="h-3.5 w-3.5" />
                      {t("subFunnel.supersedes")}
                    </span>
                    {chain.history.map((row) => (
                      <CaseRow key={row.id} row={row} muted />
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ))}
      </div>
    </Section>
  );
}
