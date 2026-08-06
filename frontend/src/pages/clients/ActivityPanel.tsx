import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  Briefcase,
  Building2,
  FileText,
  UserPlus,
  type LucideIcon,
} from "lucide-react";
import { useClientAssets } from "@/api/assets";
import { usePlacements } from "@/api/placements";
import { useDocuments } from "@/api/documents";
import type { Client } from "@/api/types";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeUp, Stagger } from "@/components/common/motion";
import { formatDateTime } from "@/lib/format";
import { SoonNote } from "@/pages/clients/Soon";

/**
 * The client timeline.
 *
 * The backend writes an `activity` audit row on every mutation, but this pass
 * exposes NO `/activities` endpoint — so rather than fake a feed, the timeline
 * is DERIVED from the timestamps of the real records already on screen
 * (client, assets, placements, documents). The note at the top says exactly
 * that, so nobody mistakes it for the full audit trail.
 */
interface TimelineEvent {
  key: string;
  at: string;
  icon: LucideIcon;
  title: string;
  detail?: string;
}

export function ActivityPanel({ client }: { client: Client }) {
  const { t } = useTranslation(["clients", "placements"]);
  const assets = useClientAssets(client.id, { page_size: 100 });
  const placements = usePlacements({ client_id: client.id, page_size: 100 });
  const documents = useDocuments({
    entity_type: "client",
    entity_id: client.id,
    limit: 100,
  });

  const isLoading =
    assets.isLoading || placements.isLoading || documents.isLoading;

  const events = React.useMemo<TimelineEvent[]>(() => {
    const list: TimelineEvent[] = [];

    if (client.created_at) {
      list.push({
        key: `client-${client.id}`,
        at: client.created_at,
        icon: UserPlus,
        title: t("activity.clientCreated"),
        detail: client.insured.legal_name,
      });
    }

    for (const asset of assets.data?.items ?? []) {
      if (!asset.created_at) continue;
      list.push({
        key: `asset-${asset.id}`,
        at: asset.created_at,
        icon: Building2,
        title: t("activity.assetCreated"),
        detail: asset.name,
      });
    }

    for (const placement of placements.data?.items ?? []) {
      if (placement.created_at) {
        list.push({
          key: `placement-${placement.id}`,
          at: placement.created_at,
          icon: Briefcase,
          title: t("activity.placementCreated"),
          detail: [placement.insurance_line?.name, placement.period]
            .filter(Boolean)
            .join(" · "),
        });
      }
      if (
        placement.updated_at &&
        placement.created_at &&
        placement.updated_at !== placement.created_at
      ) {
        list.push({
          key: `placement-upd-${placement.id}`,
          at: placement.updated_at,
          icon: Briefcase,
          title: t("activity.placementUpdated"),
          detail: [
            placement.asset?.name,
            t(`placements:status.${placement.status}`, {
              defaultValue: placement.status,
            }),
          ]
            .filter(Boolean)
            .join(" · "),
        });
      }
    }

    for (const doc of documents.data?.items ?? []) {
      if (!doc.created_at) continue;
      list.push({
        key: `doc-${doc.id}`,
        at: doc.created_at,
        icon: FileText,
        title: t("activity.documentUploaded"),
        detail: doc.original_name,
      });
    }

    return list.sort((a, b) => (a.at < b.at ? 1 : -1));
  }, [client, assets.data, placements.data, documents.data, t]);

  return (
    <div className="flex flex-col gap-4">
      <SoonNote>{t("activity.derivedNote")}</SoonNote>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-12 w-full rounded-card" />
          ))}
        </div>
      ) : events.length === 0 ? (
        <Card className="p-10 text-center text-body text-text-muted">
          {t("activity.empty")}
        </Card>
      ) : (
        <Stagger className="flex flex-col">
          {events.map((event, index) => {
            const Icon = event.icon;
            return (
              <FadeUp key={event.key} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-line bg-bg-surface text-teal-deep">
                    <Icon className="h-4 w-4" strokeWidth={1.75} />
                  </span>
                  {index < events.length - 1 ? (
                    <span className="w-px flex-1 bg-line" />
                  ) : null}
                </div>
                <div className="min-w-0 pb-5">
                  <p className="text-label font-medium text-text-primary">
                    {event.title}
                  </p>
                  {event.detail ? (
                    <p className="truncate text-caption text-text-muted">
                      {event.detail}
                    </p>
                  ) : null}
                  <p className="mt-0.5 font-mono text-mono-sm text-text-muted">
                    {formatDateTime(event.at)}
                  </p>
                </div>
              </FadeUp>
            );
          })}
        </Stagger>
      )}
    </div>
  );
}
