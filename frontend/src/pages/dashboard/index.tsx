import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlarmClock,
  Briefcase,
  Building2,
  ChevronRight,
  ClipboardCheck,
  Plus,
  Send,
  UserPlus,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useClients, useClientsSummary } from "@/api/clients";
import { useAssetsSummary } from "@/api/assets";
import { usePlacements, usePlacementsSummary } from "@/api/placements";
import type { ClientListItem, PlacementListItem } from "@/api/types";
import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeUp, Stagger } from "@/components/common/motion";
import { useAuth } from "@/providers/AuthProvider";
import { formatDateTime, greetingPeriod, weekdayLongDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { SoonButton } from "@/pages/clients/Soon";
import { DaysChip, PlacementStatusBadge, STATUS_DOT } from "@/pages/placements/status";

/**
 * The broker's landing surface.
 *
 * There is no `/dashboard` aggregate endpoint in this pass, so everything here
 * is composed from the three real summary endpoints (`/clients/summary`,
 * `/assets/summary`, `/placements/summary`) plus one page of open placements
 * and one page of clients. No numbers are invented, and every card links to a
 * filtered view that shows the same rows.
 */

function SectionCard({
  title,
  icon: Icon,
  action,
  children,
  className,
}: {
  title: string;
  icon: LucideIcon;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("flex flex-col p-5", className)}>
      <div className="mb-3.5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Icon className="h-[17px] w-[17px] text-teal-deep" strokeWidth={1.75} />
          <h2 className="font-display text-h3 text-text-primary">{title}</h2>
        </div>
        {action}
      </div>
      <div className="flex-1">{children}</div>
    </Card>
  );
}

function EmptyLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="py-6 text-center text-body text-text-muted">{children}</p>
  );
}

/** The single most useful ranking we can build from `/placements`: urgency. */
function attentionScore(placement: PlacementListItem): number {
  const days = placement.days_to_period_end;
  if (days !== null && days !== undefined && days <= 60) return days;
  if (placement.status === "inspection") return 500;
  if (placement.status === "negotiating") return 600;
  if (placement.status === "quoting" && placement.proposals_count === 0) return 700;
  return Number.POSITIVE_INFINITY;
}

export default function DashboardPage() {
  const { t } = useTranslation(["dashboard", "common", "clients", "placements"]);
  const navigate = useNavigate();
  const { user } = useAuth();

  const clientsSummary = useClientsSummary();
  const assetsSummary = useAssetsSummary();
  const placementsSummary = usePlacementsSummary();
  const openPlacements = usePlacements({ open_only: true, page_size: 100 });
  const clients = useClients({ page_size: 50 });

  const canCreateClient = useCan("Clients", "Create");
  const canCreatePlacement = useCan("Placements", "Create");

  const firstName = (user?.full_name ?? "").split(" ")[0];

  const attention = React.useMemo(() => {
    const items = [...(openPlacements.data?.items ?? [])];
    return items
      .map((placement) => ({ placement, score: attentionScore(placement) }))
      .filter((entry) => Number.isFinite(entry.score))
      .sort((a, b) => a.score - b.score)
      .slice(0, 3)
      .map((entry) => entry.placement);
  }, [openPlacements.data]);

  const topClients = React.useMemo(() => {
    const items = [...(clients.data?.items ?? [])];
    return items
      .sort(
        (a, b) =>
          b.active_placements_count - a.active_placements_count ||
          b.assets_count - a.assets_count,
      )
      .filter((client) => client.active_placements_count > 0 || client.assets_count > 0)
      .slice(0, 5);
  }, [clients.data]);

  /** Recently touched records, newest first — derived from `updated_at`. */
  const recent = React.useMemo(() => {
    type Entry = {
      key: string;
      at: string;
      title: string;
      detail: string;
      to: string;
    };
    const entries: Entry[] = [];
    for (const placement of openPlacements.data?.items ?? []) {
      if (!placement.updated_at) continue;
      entries.push({
        key: `p-${placement.id}`,
        at: placement.updated_at,
        title: placement.asset?.name ?? `#${placement.asset_id}`,
        detail: [
          placement.client?.legal_name,
          t(`placements:status.${placement.status}`),
        ]
          .filter(Boolean)
          .join(" · "),
        to: `/placements/${placement.id}`,
      });
    }
    for (const client of clients.data?.items ?? []) {
      if (!client.updated_at) continue;
      entries.push({
        key: `c-${client.id}`,
        at: client.updated_at,
        title: client.insured.trade_name || client.insured.legal_name,
        detail: t(`clients:status.${client.status}`),
        to: `/clients/${client.id}`,
      });
    }
    return entries.sort((a, b) => (a.at < b.at ? 1 : -1)).slice(0, 10);
  }, [openPlacements.data, clients.data, t]);

  const byStatus = placementsSummary.data?.by_status ?? {};
  const statusTotal = Object.values(byStatus).reduce((sum, value) => sum + value, 0);

  const isLoading =
    clientsSummary.isLoading || placementsSummary.isLoading || assetsSummary.isLoading;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        eyebrow={weekdayLongDate()}
        title={
          firstName
            ? t("dashboard:greeting.withName", {
                greeting: t(`common:greeting.${greetingPeriod()}`),
                name: firstName,
              })
            : t(`common:greeting.${greetingPeriod()}`)
        }
        subtitle={t("dashboard:subtitle")}
        actions={
          <>
            {canCreateClient.allowed ? (
              <Button variant="secondary" onClick={() => navigate("/clients?new=1")}>
                <UserPlus />
                {t("dashboard:actions.newClient")}
              </Button>
            ) : (
              <SoonButton
                label={t("dashboard:actions.newClient")}
                reason={t("clients:permissions.noCreate")}
                icon={<UserPlus />}
              />
            )}
            {canCreatePlacement.allowed ? (
              <Button onClick={() => navigate("/placements?new=1")}>
                <Plus />
                {t("dashboard:actions.newPlacement")}
              </Button>
            ) : (
              <SoonButton
                label={t("dashboard:actions.newPlacement")}
                reason={t("placements:permissions.noCreate")}
                icon={<Plus />}
              />
            )}
          </>
        }
      />

      <Stagger className="grid gap-[18px] sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("dashboard:kpi.clients")}
          countTo={clientsSummary.data?.total ?? 0}
          hint={t("dashboard:kpi.clientsHint", {
            count: clientsSummary.data?.with_active_placements ?? 0,
          })}
          icon={<Users />}
          tone="brand"
        />
        <KpiCard
          label={t("dashboard:kpi.openPlacements")}
          countTo={placementsSummary.data?.open ?? 0}
          hint={t("dashboard:kpi.openPlacementsHint", {
            count: placementsSummary.data?.in_market ?? 0,
          })}
          icon={<Briefcase />}
          tone="action"
        />
        <KpiCard
          label={t("dashboard:kpi.expiring")}
          countTo={placementsSummary.data?.expiring_within_60_days ?? 0}
          hint={t("dashboard:kpi.expiringHint")}
          icon={<AlarmClock />}
          tone={placementsSummary.data?.expiring_within_60_days ? "danger" : "default"}
        />
        <KpiCard
          label={t("dashboard:kpi.assets")}
          countTo={assetsSummary.data?.total ?? 0}
          hint={t("dashboard:kpi.assetsHint", {
            count: assetsSummary.data?.without_placements ?? 0,
          })}
          icon={<Building2 />}
          tone="success"
        />
      </Stagger>

      <Stagger className="grid gap-[18px] lg:grid-cols-3">
        {/* Requiere atención */}
        <FadeUp className="lg:col-span-2">
          <SectionCard
            title={t("dashboard:attention.title")}
            icon={AlarmClock}
            action={
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate("/placements?status=quoting")}
              >
                {t("common:actions.viewAll")}
                <ChevronRight />
              </Button>
            }
            className="h-full"
          >
            {openPlacements.isLoading ? (
              <div className="flex flex-col gap-2">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-14 w-full rounded-[12px]" />
                ))}
              </div>
            ) : attention.length === 0 ? (
              <EmptyLine>{t("dashboard:attention.empty")}</EmptyLine>
            ) : (
              <ul className="flex flex-col gap-2">
                {attention.map((placement) => (
                  <li key={placement.id}>
                    <Link
                      to={`/placements/${placement.id}`}
                      className="flex items-center gap-3 rounded-[12px] border border-line bg-bg-surface px-3.5 py-3 transition-all duration-150 hover:-translate-y-px hover:border-[color-mix(in_srgb,var(--teal)_38%,var(--line))] hover:shadow-card"
                    >
                      <span
                        className={cn(
                          "h-2 w-2 shrink-0 rounded-full",
                          STATUS_DOT[placement.status],
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-label font-medium text-text-primary">
                          {placement.asset?.name ?? `#${placement.asset_id}`}
                        </span>
                        <span className="block truncate text-caption text-text-muted">
                          {[placement.client?.legal_name, placement.insurance_line?.name]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </span>
                      <DaysChip days={placement.days_to_period_end} />
                      <PlacementStatusBadge status={placement.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </FadeUp>

        {/* Pipeline */}
        <FadeUp>
          <SectionCard
            title={t("dashboard:pipeline.title")}
            icon={Send}
            className="h-full"
          >
            {isLoading ? (
              <Skeleton className="h-32 w-full rounded-[12px]" />
            ) : statusTotal === 0 ? (
              <EmptyLine>{t("dashboard:pipeline.empty")}</EmptyLine>
            ) : (
              <ul className="flex flex-col gap-2.5">
                {Object.entries(byStatus)
                  .filter(([, count]) => count > 0)
                  .sort((a, b) => b[1] - a[1])
                  .map(([status, count]) => (
                    <li key={status}>
                      <button
                        type="button"
                        onClick={() => navigate(`/placements?status=${status}`)}
                        className="w-full text-left"
                      >
                        <span className="flex items-baseline justify-between gap-2 text-caption">
                          <span className="text-text-secondary">
                            {t(`placements:status.${status}`, {
                              defaultValue: status,
                            })}
                          </span>
                          <span className="font-mono tabular-nums text-text-muted">
                            {count}
                          </span>
                        </span>
                        <span className="mt-1 block h-1.5 w-full overflow-hidden rounded-full bg-bg-recessed">
                          <span
                            className="block h-full rounded-full bg-gradient-to-r from-teal to-blue transition-[width] duration-500"
                            style={{ width: `${(count / statusTotal) * 100}%` }}
                          />
                        </span>
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </SectionCard>
        </FadeUp>
      </Stagger>

      <Stagger className="grid gap-[18px] lg:grid-cols-2">
        {/* Principales clientes */}
        <FadeUp>
          <SectionCard
            title={t("dashboard:topClients.title")}
            icon={Users}
            action={
              <Button variant="ghost" size="sm" onClick={() => navigate("/clients")}>
                {t("common:actions.viewAll")}
                <ChevronRight />
              </Button>
            }
            className="h-full"
          >
            {clients.isLoading ? (
              <div className="flex flex-col gap-2">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-[12px]" />
                ))}
              </div>
            ) : topClients.length === 0 ? (
              <EmptyLine>{t("dashboard:topClients.empty")}</EmptyLine>
            ) : (
              <ul className="flex flex-col">
                {topClients.map((client: ClientListItem) => (
                  <li key={client.id}>
                    <Link
                      to={`/clients/${client.id}`}
                      className="flex items-center gap-3 border-b border-line py-2.5 last:border-0 hover:text-teal-deep"
                    >
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[9px] bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] font-display text-caption text-teal-deep">
                        {(client.insured.legal_name || "?").slice(0, 2).toUpperCase()}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-label font-medium text-text-primary">
                          {client.insured.trade_name || client.insured.legal_name}
                        </span>
                        <span className="block truncate text-caption text-text-muted">
                          {client.sector || t("dashboard:topClients.noSector")}
                        </span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block font-mono text-mono tabular-nums text-text-primary">
                          {client.active_placements_count}
                        </span>
                        <span className="block text-caption text-text-muted">
                          {t("dashboard:topClients.placements")}
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </FadeUp>

        {/* Actividad reciente */}
        <FadeUp>
          <SectionCard
            title={t("dashboard:recent.title")}
            icon={ClipboardCheck}
            className="h-full"
          >
            {openPlacements.isLoading || clients.isLoading ? (
              <div className="flex flex-col gap-2">
                {[0, 1].map((i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-[12px]" />
                ))}
              </div>
            ) : recent.length === 0 ? (
              <EmptyLine>{t("dashboard:recent.empty")}</EmptyLine>
            ) : (
              <>
                <ul className="flex flex-col">
                  {recent.slice(0, 2).map((entry) => (
                    <li key={entry.key}>
                      <Link
                        to={entry.to}
                        className="flex items-center gap-3 border-b border-line py-2.5 hover:text-teal-deep"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-label font-medium text-text-primary">
                            {entry.title}
                          </span>
                          <span className="block truncate text-caption text-text-muted">
                            {entry.detail}
                          </span>
                        </span>
                        <span className="shrink-0 font-mono text-mono-sm text-text-muted">
                          {formatDateTime(entry.at)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
                {recent.length > 2 ? (
                  <Accordion type="single" collapsible>
                    <AccordionItem value="more" className="border-0">
                      <AccordionTrigger className="text-caption text-text-muted hover:text-teal-deep">
                        {t("dashboard:recent.showMore", { count: recent.length - 2 })}
                      </AccordionTrigger>
                      <AccordionContent>
                        <ul className="flex flex-col">
                          {recent.slice(2).map((entry) => (
                            <li key={entry.key}>
                              <Link
                                to={entry.to}
                                className="flex items-center gap-3 border-b border-line py-2.5 last:border-0 hover:text-teal-deep"
                              >
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-label font-medium text-text-primary">
                                    {entry.title}
                                  </span>
                                  <span className="block truncate text-caption text-text-muted">
                                    {entry.detail}
                                  </span>
                                </span>
                                <span className="shrink-0 font-mono text-mono-sm text-text-muted">
                                  {formatDateTime(entry.at)}
                                </span>
                              </Link>
                            </li>
                          ))}
                        </ul>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                ) : null}
                <p className="mt-3 text-caption text-text-muted opacity-80">
                  {t("dashboard:recent.derivedNote")}
                </p>
              </>
            )}
          </SectionCard>
        </FadeUp>
      </Stagger>
    </div>
  );
}
