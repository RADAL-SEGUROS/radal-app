/**
 * Article 528 — coverage termination and rehabilitation.
 *
 * Under art. 528 of the Chilean Commercial Code non-payment terminates the
 * cover by operation of law after the notice period; paying afterwards
 * rehabilitates it, but only from the rehabilitation date and at a cost. The
 * window in between is the dangerous part: a loss that occurs inside it is not
 * covered, and the corpus contains exactly that case.
 *
 * So this component renders the sequence as a timeline with its dates and puts
 * the gap warning next to `days_without_cover` — the number nobody should have
 * to compute by hand.
 */
import { useTranslation } from "react-i18next";
import { AlertTriangle, ShieldOff, ShieldCheck, Timer } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDate, formatDateTime } from "@/lib/format";
import type { CollectionPlan } from "@/api/types";
import { EmptyState, KeyValue, Section, uf } from "@/pages/proposals/shared";
import { asRows, pick, renderValue } from "@/pages/policies/shared";

interface Art528Event {
  date: string | null;
  kind: string | null;
  detail: string;
}

/** `art528_events` is JSON: a list of rows, or a dict keyed by event name. */
function normalize(events: CollectionPlan["art528_events"]): Art528Event[] {
  if (!events) return [];
  if (Array.isArray(events)) {
    return asRows(events).map((row) => ({
      date:
        pick<string>(row, "date", "occurred_at", "fecha", "event_date", "at") ?? null,
      kind: pick<string>(row, "kind", "event", "type", "evento") ?? null,
      detail: renderValue(
        pick(row, "detail", "description", "note", "detalle") ??
          Object.fromEntries(
            Object.entries(row).filter(
              ([key]) =>
                !["date", "occurred_at", "fecha", "event_date", "at", "kind", "event", "type", "evento"].includes(
                  key,
                ),
            ),
          ),
      ),
    }));
  }
  return Object.entries(events).map(([key, value]) => ({
    date: null,
    kind: key,
    detail: renderValue(value),
  }));
}

export function Art528Timeline({ plan }: { plan: CollectionPlan }) {
  const { t } = useTranslation("postsale");
  const events = normalize(plan.art528_events);

  const hasSequence =
    !!plan.terminated_at ||
    !!plan.rehabilitated_at ||
    (plan.days_without_cover ?? 0) > 0 ||
    events.length > 0;

  return (
    <Section title={t("art528.title")} description={t("art528.description")}>
      {!hasSequence ? (
        <EmptyState
          title={t("art528.empty")}
          hint={t("art528.emptyHint")}
          icon={<ShieldCheck className="h-6 w-6 text-pos-text" />}
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KeyValue
              label={t("art528.terminated")}
              value={plan.terminated_at ? formatDateTime(plan.terminated_at) : "—"}
              tone={plan.terminated_at ? "danger" : "default"}
            />
            <KeyValue
              label={t("art528.rehabilitated")}
              value={plan.rehabilitated_at ? formatDateTime(plan.rehabilitated_at) : "—"}
              tone={plan.rehabilitated_at ? "success" : "default"}
            />
            <KeyValue
              label={t("art528.daysWithoutCover")}
              value={
                plan.days_without_cover === null
                  ? "—"
                  : t("shared.days", { count: plan.days_without_cover })
              }
              tone={(plan.days_without_cover ?? 0) > 0 ? "danger" : "default"}
            />
            <KeyValue
              label={t("art528.rehabilitationCost")}
              value={uf(plan.rehabilitation_cost_uf)}
            />
          </div>

          {(plan.days_without_cover ?? 0) > 0 ? (
            <p className="mt-3 flex items-center gap-2 text-caption text-signal-danger">
              <AlertTriangle className="h-4 w-4" />
              {t("art528.gapWarning")}
            </p>
          ) : null}

          {events.length > 0 ? (
            <ol className="mt-5 flex flex-col gap-3 border-l border-line pl-4">
              {events.map((event, index) => (
                <li key={index} className="relative">
                  <span className="absolute -left-[22px] top-1 flex h-4 w-4 items-center justify-center rounded-full bg-bg-recessed text-brand">
                    {event.kind && /rehab/i.test(event.kind) ? (
                      <ShieldCheck className="h-3 w-3" />
                    ) : event.kind && /(term|suspen)/i.test(event.kind) ? (
                      <ShieldOff className="h-3 w-3" />
                    ) : (
                      <Timer className="h-3 w-3" />
                    )}
                  </span>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-caption text-text-muted">
                      {event.date ? formatDate(event.date) : t("art528.eventDate")}
                    </span>
                    {event.kind ? (
                      <Badge variant="neutral">{event.kind}</Badge>
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-body text-text-secondary">{event.detail}</p>
                </li>
              ))}
            </ol>
          ) : null}
        </>
      )}
    </Section>
  );
}
