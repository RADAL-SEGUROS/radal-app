/**
 * The v12 blocks of the plan detail (`/collections/:planId`):
 *
 *  - **Cobranza** — reminders state (not started / active / paused until a
 *    date, C5.4) with Iniciar cobranza · Pausar avisos · Reanudar avisos;
 *  - **Cuotas** — the live board rows of this plan (state, level, balance),
 *    each opening the same side sheet as the Cobranza board;
 *  - **Pagos** — every payment of the plan with its evidence level;
 *  - **Avisos enviados** — the notices of every cuota;
 *  - **Consultas a la compañía** — status inquiries touching these cuotas.
 *
 * The pause is honoured by the server only WITH a date (`notices_paused_until
 * >= today`), so the date is required here. `start` clears any pause, so
 * "Reanudar avisos" is the same call as "Iniciar cobranza".
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { BellOff, BellRing, Loader2, PauseCircle, PlayCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DisabledHint, ErrorBanner, LoadingRows, Section, apiError } from "@/components/common/kit";
import { formatDate, formatDateTime } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { num } from "@/api/types";
import {
  useInstallmentsNotices,
  useInstallmentsPayments,
  usePlanInstallmentRows,
  useStartCollection,
  useUpdateCollectionPlanCobranza,
  type BoardInstallmentRow,
  type CollectionPlanCobranza,
} from "@/api/collections";
import { useStatusInquiries } from "@/api/inquiries";
import { LevelBadge } from "@/components/collections/LevelBadge";
import { InstallmentSheet } from "@/components/collections/InstallmentSheet";
import { EvidenceLevelBadge } from "@/components/collections/PaymentsPanel";
import {
  INQUIRY_STATUS_LABEL,
  INQUIRY_STATUS_TONE,
  LEVEL_LABEL,
  METHOD_LABEL,
  NOTICE_KIND_LABEL,
  NOTICE_STATUS_LABEL,
  NOTICE_STATUS_TONE,
  PAYMENT_SOURCE_LABEL,
  labelOf,
  toneOf,
} from "@/components/collections/labels";
import { Field, InstallmentStateBadges, debitKindOf, money, todaySantiago } from "@/components/collections/shared";

const LEVEL_PREFERENCE_LABEL = {
  auto: "collections:plan.preference.auto",
  assisted: "collections:plan.preference.assisted",
  record: "collections:plan.preference.record",
} as const;

interface PlanLike extends CollectionPlanCobranza {
  id: number;
  policy_id: number;
}

function PauseForm({ planId, onDone }: { planId: number; onDone: () => void }) {
  const { t } = useTranslation(["collections", "common"]);
  const update = useUpdateCollectionPlanCobranza(planId);
  const today = todaySantiago();
  const [until, setUntil] = React.useState("");
  const [reason, setReason] = React.useState("");
  const dateError = until && until < today ? t("collections:plan.pauseDatePast") : null;
  const invalid = !until || !!dateError || !reason.trim();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (invalid) return;
    try {
      await update.mutateAsync({ notices_paused_until: until, notices_pause_reason: reason.trim() });
      toast.success(t("collections:plan.paused"));
      onDone();
    } catch {
      // ErrorBanner.
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="mt-4 flex flex-col gap-3 border-t border-line pt-4" noValidate>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[180px_1fr]">
        <Field label={t("collections:plan.pauseUntil")} htmlFor="pause-until" error={dateError}>
          <Input id="pause-until" type="date" min={today} value={until} onChange={(e) => setUntil(e.target.value)} />
        </Field>
        <Field label={t("collections:plan.pauseReason")} htmlFor="pause-reason">
          <Textarea id="pause-reason" rows={1} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <p className="text-caption text-ink-3">{t("collections:plan.pauseHint")}</p>
      <ErrorBanner error={update.error} />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onDone} disabled={update.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" variant="secondary" size="sm" disabled={invalid || update.isPending}>
          {update.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PauseCircle className="h-4 w-4" />}
          {t("collections:plan.pauseSubmit")}
        </Button>
      </div>
    </form>
  );
}

export function PlanCobranzaPanel({ plan }: { plan: PlanLike }) {
  const { t } = useTranslation(["collections", "common"]);
  const canEdit = useCan("Collections", "Edit");
  const start = useStartCollection(plan.id);
  const rows = usePlanInstallmentRows(plan.id);
  const items = React.useMemo(() => rows.data?.items ?? [], [rows.data]);
  const ids = React.useMemo(() => items.map((r) => r.id), [items]);
  const numberById = React.useMemo(() => new Map(items.map((r) => [r.id, r.number])), [items]);
  const payments = useInstallmentsPayments(ids);
  const notices = useInstallmentsNotices(ids);
  const inquiries = useStatusInquiries({ policy_id: plan.policy_id, limit: 50 });
  const [pausing, setPausing] = React.useState(false);
  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const selectedRow: BoardInstallmentRow | null = items.find((r) => r.id === selectedId) ?? null;
  const ufRate = num(rows.data?.uf_rate ?? null);
  const effectiveLevel =
    items.find((r) => r.state !== "paid" && r.state !== "voided")?.level ?? items[0]?.level ?? null;

  const today = todaySantiago();
  const paused = !!plan.notices_paused_until && plan.notices_paused_until >= today;
  const active = !!plan.notices_enabled && !paused;
  const gate = canEdit.isLoading
    ? t("common:state.loading")
    : canEdit.allowed
      ? null
      : t("collections:sheet.noEditPermission");

  const doStart = async () => {
    try {
      await start.mutateAsync(undefined);
      toast.success(paused ? t("collections:plan.resumed") : t("collections:sheet.started"));
    } catch (error) {
      toast.error(apiError(error, t("common:state.error")));
    }
  };

  const allPayments = payments
    .flatMap((q) => q.data ?? [])
    .sort((a, b) => (a.paid_on < b.paid_on ? 1 : a.paid_on > b.paid_on ? -1 : b.id - a.id));
  const paymentsLoading = payments.some((q) => q.isLoading);
  const allNotices = notices
    .flatMap((q) => q.data?.items ?? [])
    .sort((a, b) => ((a.sent_at ?? a.created_at) < (b.sent_at ?? b.created_at) ? 1 : -1));
  const noticesLoading = notices.some((q) => q.isLoading);
  const idSet = new Set(ids);
  const planInquiries = (inquiries.data?.items ?? []).filter((i) =>
    i.installment_ids.some((id) => idSet.has(id)),
  );

  return (
    <>
      <Section
        title={t("collections:plan.title")}
        description={t("collections:plan.description")}
        actions={
          active ? (
            <DisabledHint hint={gate}>
              <Button variant="secondary" size="sm" disabled={!!gate} onClick={() => setPausing((v) => !v)}>
                <PauseCircle className="h-4 w-4" />
                {t("collections:plan.pause")}
              </Button>
            </DisabledHint>
          ) : (
            <DisabledHint hint={gate}>
              <Button size="sm" disabled={!!gate || start.isPending} onClick={() => void doStart()}>
                {start.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
                {paused ? t("collections:plan.resume") : t("collections:actions.start")}
              </Button>
            </DisabledHint>
          )
        }
      >
        {gate ? <p className="mb-2 text-caption text-ink-3">{gate}</p> : null}
        <div className="flex flex-wrap items-center gap-2">
          {active ? (
            <Badge variant="success" className="gap-1">
              <BellRing className="h-3.5 w-3.5" />
              {t("collections:plan.stateActive")}
            </Badge>
          ) : paused ? (
            <Badge variant="warn" className="gap-1">
              <BellOff className="h-3.5 w-3.5" />
              {t("collections:plan.statePaused", { date: formatDate(plan.notices_paused_until) })}
            </Badge>
          ) : (
            <Badge variant="neutral" className="gap-1">
              <BellOff className="h-3.5 w-3.5" />
              {t("collections:plan.stateOff")}
            </Badge>
          )}
          {plan.level_preference ? (
            <span className="text-caption text-ink-3">
              {t("collections:plan.preferenceLabel")}:{" "}
              {labelOf(t, LEVEL_PREFERENCE_LABEL, plan.level_preference, "—")}
              {/* "Automático" is a preference, not a level: say what it resolves to today. */}
              {plan.level_preference === "auto" && effectiveLevel
                ? ` · ${t("collections:plan.resolvesTo", { level: labelOf(t, LEVEL_LABEL, effectiveLevel, "—") })}`
                : null}
            </span>
          ) : null}
        </div>
        {paused && plan.notices_pause_reason ? (
          <p className="mt-2 text-caption text-ink-2">
            {t("collections:plan.pauseReason")}: {plan.notices_pause_reason}
          </p>
        ) : null}
        {!active && !paused ? <p className="mt-2 text-caption text-ink-3">{t("collections:plan.offHint")}</p> : null}
        {pausing && active ? <PauseForm planId={plan.id} onDone={() => setPausing(false)} /> : null}
      </Section>

      <Section title={t("collections:plan.installments")} bodyClassName="p-0">
        {rows.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={3} />
          </div>
        ) : rows.error ? (
          <div className="p-5">
            <ErrorBanner error={rows.error} />
          </div>
        ) : items.length === 0 ? (
          <p className="p-5 text-caption text-ink-3">{t("collections:plan.noInstallments")}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {items.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(row.id)}
                  className="flex min-h-14 w-full flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 text-left transition-[background-color] duration-150 ease-out hover:bg-paper-2"
                >
                  <span className="w-14 shrink-0 tabular-nums text-body font-medium text-ink">
                    {row.number}/{row.installment_count ?? "—"}
                  </span>
                  <span className="w-24 shrink-0 text-caption text-ink-2">{formatDate(row.due_date)}</span>
                  <span className="min-w-[110px] flex-1">
                    <InstallmentStateBadges state={row.state} flags={row.flags} daysLate={row.days_late} debitKind={debitKindOf(row)} />
                  </span>
                  <LevelBadge level={row.level} reason={row.level_reason} />
                  <span className="ml-auto flex flex-col items-end text-caption tabular-nums">
                    <span className="font-medium text-ink">{money(row.gross_amount_uf, row.currency)}</span>
                    {Number(row.balance_uf) > 0 && row.state !== "voided" ? (
                      <span className="text-ink-3">
                        {t("collections:sheet.balance")} {money(row.balance_uf, row.currency)}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("collections:payments.title")}>
        {paymentsLoading ? (
          <LoadingRows rows={2} />
        ) : allPayments.length === 0 ? (
          <p className="text-caption text-ink-3">{t("collections:payments.empty")}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {allPayments.map((p) => {
              const reversed = p.status === "reversed";
              return (
                <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 first:pt-0 last:pb-0">
                  <span className="w-20 shrink-0 text-caption text-ink-3">
                    {t("collections:plan.installmentN", { number: numberById.get(p.installment_id) ?? "—" })}
                  </span>
                  <span
                    className={
                      reversed
                        ? "text-body font-medium tabular-nums text-ink-3 line-through"
                        : "text-body font-medium tabular-nums text-ink"
                    }
                  >
                    {money(p.amount, p.currency)}
                  </span>
                  <span className="text-caption text-ink-3">
                    {formatDate(p.paid_on)}
                    {p.method ? ` · ${labelOf(t, METHOD_LABEL, p.method, "")}` : ""}
                    {` · ${labelOf(t, PAYMENT_SOURCE_LABEL, p.source, "")}`}
                  </span>
                  <span className="ml-auto">
                    {reversed ? (
                      <Badge variant="muted" className="px-1.5 py-0 text-[11px]">
                        {t("collections:payments.reversedBadge")}
                      </Badge>
                    ) : (
                      <EvidenceLevelBadge level={p.evidence_level} />
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section title={t("collections:notice.title")}>
        {noticesLoading ? (
          <LoadingRows rows={2} />
        ) : allNotices.length === 0 ? (
          <p className="text-caption text-ink-3">{t("collections:notice.empty")}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {allNotices.map((n) => (
              <li key={n.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-caption first:pt-0 last:pb-0">
                <Badge variant={toneOf(NOTICE_STATUS_TONE, n.status)} className="px-1.5 py-0 text-[11px]">
                  {labelOf(t, NOTICE_STATUS_LABEL, n.status)}
                </Badge>
                <span className="text-ink-2">{labelOf(t, NOTICE_KIND_LABEL, n.kind)}</span>
                {n.installment_id ? (
                  <span className="text-ink-3">
                    · {t("collections:plan.installmentN", { number: numberById.get(n.installment_id) ?? "—" })}
                  </span>
                ) : null}
                <span className="min-w-0 truncate text-ink-3">· {n.to_address}</span>
                <span className="ml-auto tabular-nums text-ink-3">{formatDateTime(n.sent_at ?? n.created_at)}</span>
                {n.error ? <span className="w-full text-neg-text">{n.error}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title={t("collections:inquiries.title")}
        actions={
          <Button asChild variant="ghost" size="sm">
            <Link to="/collections/inquiries">{t("collections:plan.allInquiries")}</Link>
          </Button>
        }
      >
        {inquiries.isLoading ? (
          <LoadingRows rows={2} />
        ) : planInquiries.length === 0 ? (
          <p className="text-caption text-ink-3">{t("collections:plan.noInquiries")}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {planInquiries.map((inq) => (
              <li key={inq.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-caption first:pt-0 last:pb-0">
                <Badge variant={toneOf(INQUIRY_STATUS_TONE, inq.status)} dot>
                  {labelOf(t, INQUIRY_STATUS_LABEL, inq.status, t("collections:inquiries.status.unknown"))}
                </Badge>
                <span className="text-ink-2">
                  {inq.installments.length > 0
                    ? inq.installments
                        .map((i) => t("collections:plan.installmentN", { number: i.number }))
                        .join(", ")
                    : t("collections:inquiries.installmentsCount", { count: inq.installment_ids.length })}
                </span>
                <span className="min-w-0 truncate text-ink-3">· {inq.to_address ?? t("collections:inquiries.noRecipient")}</span>
                <span className="ml-auto tabular-nums text-ink-3">{formatDateTime(inq.sent_at ?? inq.created_at)}</span>
                {inq.evidence_open > 0 ? (
                  <Badge variant="warn" className="px-1.5 py-0 text-[11px]">
                    {t("collections:inquiries.evidenceOpen", { count: inq.evidence_open })}
                  </Badge>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <InstallmentSheet
        row={selectedRow}
        ufRate={ufRate}
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
      />
    </>
  );
}
