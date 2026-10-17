/**
 * The cuota side sheet — every action the broker takes on one cuota, in one
 * place, from the board, the plan detail or anywhere a `BoardInstallmentRow`
 * is at hand.
 *
 * The sheet is a small state machine: the **menu** (summary, level, payments,
 * avisos, the action list) and one **sub-view** per action. Only one view is
 * visible at a time, so each carries its own single primary action — the menu's
 * is "Registrar pago".
 *
 * No dead buttons: an action that does not apply is rendered disabled with the
 * reason — the server's own `level_reason` for "Link de pago", the cuota's
 * state for the rest, the permission matrix for every write.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  BellRing,
  CalendarClock,
  ExternalLink,
  FileText,
  Link2,
  MessageSquareText,
  PlayCircle,
  Send,
  Share2,
  ShieldQuestion,
  Wallet,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { DisabledHint, KeyValue, apiError } from "@/components/common/kit";
import { ShareDialog } from "@/components/share/ShareDialog";
import { formatDate, formatDateTime } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import {
  useCollectionNotices,
  usePlanInstallmentRows,
  useStartCollection,
  type BoardInstallmentRow,
} from "@/api/collections";
import { LevelBadge } from "@/components/collections/LevelBadge";
import { RegisterPaymentForm } from "@/components/collections/RegisterPaymentForm";
import { DebitRejectedForm, GestionForm } from "@/components/collections/GestionForms";
import { PaymentsPanel } from "@/components/collections/PaymentsPanel";
import { InquiryForm, PaymentLinkPanel, ReminderPanel } from "@/components/collections/OutreachPanels";
import {
  DEBIT_REJECTED_ACTION_LABEL,
  METHOD_LABEL,
  MISSING_LABEL,
  NOTICE_KIND_LABEL,
  NOTICE_STATUS_LABEL,
  NOTICE_STATUS_TONE,
  labelOf,
  toneOf,
} from "@/components/collections/labels";
import { InstallmentStateBadges, clp, debitKindOf, money } from "@/components/collections/shared";

type View = "menu" | "pay" | "gestion" | "promise" | "debit" | "reminder" | "inquiry" | "link";

const VIEW_TITLE: Record<Exclude<View, "menu">, string> = {
  pay: "collections:actions.pay",
  gestion: "collections:actions.gestion",
  promise: "collections:actions.promise",
  debit: "collections:actions.debitRejected",
  reminder: "collections:actions.reminder",
  inquiry: "collections:actions.inquiry",
  link: "collections:actions.paymentLink",
};

const AUTO_DEBIT = new Set(["direct_debit", "card_debit"]);

/**
 * One action row in the menu: icon, label and — when disabled — the reason as
 * a VISIBLE second line (Radix tooltips never open on touch; the tooltip stays
 * for desktop).
 */
function ActionRow({
  icon,
  label,
  onClick,
  disabledReason,
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  disabledReason?: string | null;
}) {
  const disabled = !!disabledReason;
  return (
    <DisabledHint hint={disabledReason} className="w-full">
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        className={cn(
          "flex min-h-11 w-full items-center gap-3 rounded-sm px-3 py-1.5 text-left text-body text-ink-2 transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96] [&_svg]:h-4 [&_svg]:w-4 [&_svg]:shrink-0",
          disabled ? "cursor-not-allowed" : "hover:bg-paper-2 hover:text-ink",
        )}
      >
        <span className={cn("inline-flex", disabled && "opacity-50")}>{icon}</span>
        <span className="min-w-0 flex-1">
          <span className={cn("block truncate", disabled && "opacity-50")}>{label}</span>
          {disabledReason ? (
            <span className="block text-caption text-ink-3">{disabledReason}</span>
          ) : null}
        </span>
      </button>
    </DisabledHint>
  );
}

function NoticesList({ installmentId }: { installmentId: number }) {
  const { t } = useTranslation("collections");
  const notices = useCollectionNotices({ installment_id: installmentId, limit: 20 });
  const items = notices.data?.items ?? [];
  if (notices.isLoading) return null;
  if (items.length === 0) return <p className="text-caption text-ink-3">{t("notice.empty")}</p>;
  return (
    <ul className="flex flex-col gap-2">
      {items.map((n) => (
        <li key={n.id} className="flex flex-wrap items-center gap-2 text-caption">
          <Badge variant={toneOf(NOTICE_STATUS_TONE, n.status)} className="px-1.5 py-0 text-[11px]">
            {labelOf(t, NOTICE_STATUS_LABEL, n.status)}
          </Badge>
          <span className="text-ink-2">{labelOf(t, NOTICE_KIND_LABEL, n.kind)}</span>
          <span className="text-ink-3">· {n.to_address}</span>
          <span className="ml-auto text-ink-3 tabular-nums">
            {formatDateTime(n.sent_at ?? n.created_at)}
          </span>
          {n.error ? <span className="w-full text-neg-text">{n.error}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function InstallmentSheet({
  row,
  ufRate,
  open,
  onOpenChange,
}: {
  row: BoardInstallmentRow | null;
  ufRate: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const [view, setView] = React.useState<View>("menu");
  const canEdit = useCan("Collections", "Edit");
  const contentRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) setView("menu");
  }, [open]);
  React.useEffect(() => {
    setView("menu");
  }, [row?.id]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 overflow-y-auto p-0 focus:outline-none sm:max-w-md"
        // Radix focuses the first tabbable on open — the level badge, whose
        // tooltip then covers the insured's name. Focus the panel itself.
        ref={contentRef}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          contentRef.current?.focus({ preventScroll: true });
        }}
      >
        {row ? (
          <SheetBody
            row={row}
            ufRate={ufRate}
            view={view}
            setView={setView}
            canEdit={canEdit.allowed}
            permissionLoading={canEdit.isLoading}
            onClose={() => onOpenChange(false)}
          />
        ) : (
          <SheetHeader className="p-5">
            <SheetTitle>{t("collections:sheet.title")}</SheetTitle>
          </SheetHeader>
        )}
      </SheetContent>
    </Sheet>
  );
}

function SheetBody({
  row: snapshot,
  ufRate,
  view,
  setView,
  canEdit,
  permissionLoading,
  onClose,
}: {
  row: BoardInstallmentRow;
  ufRate: number | null;
  view: View;
  setView: (v: View) => void;
  canEdit: boolean;
  permissionLoading: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  // Render from the LIVE cuota, resolved through a bucket-independent query
  // (the plan's cuotas — the same cache entry PlanCobranzaPanel uses, under
  // qk.collections.all). A full payment moves a cuota from Vencidas to
  // Saldadas: the caller's bucket list no longer has it, and its click-time
  // snapshot would still offer "Registrar pago" on a paid cuota.
  const planRows = usePlanInstallmentRows(snapshot.plan_id);
  const row = planRows.data?.items.find((r) => r.id === snapshot.id) ?? snapshot;
  const start = useStartCollection(row.plan_id);
  const back = () => setView("menu");

  const paid = row.state === "paid";
  const voided = row.state === "voided";
  const closedReason = paid
    ? t("collections:sheet.alreadyPaid")
    : voided
      ? t("collections:sheet.voided")
      : null;
  const editReason = permissionLoading
    ? t("common:state.loading")
    : canEdit
      ? null
      : t("collections:sheet.noEditPermission");
  const writeReason = (extra?: string | null) => editReason ?? extra ?? null;

  const debitReason =
    voided
      ? t("collections:sheet.voided")
      : row.payment_mode && !AUTO_DEBIT.has(row.payment_mode)
        ? t("collections:sheet.debitOnlyAuto")
        : null;
  // Online payment exists ONLY at the native level (an active AND verified
  // provider account, decided server-side). Below it the sheet offers no
  // "Link de pago" at all — it says how the insured pays instead.
  const native = row.level === "native";
  const linkReason = closedReason;
  const debitKind = debitKindOf(row);
  const debitLabel = t(DEBIT_REJECTED_ACTION_LABEL[debitKind ?? "generic"]);

  const startCollection = async () => {
    try {
      await start.mutateAsync(undefined);
      toast.success(t("collections:sheet.started"));
    } catch (error) {
      toast.error(apiError(error, t("common:state.error")));
    }
  };

  const installmentLabel = t("collections:board.installmentOf", {
    number: row.number,
    count: row.installment_count ?? row.number,
  });

  return (
    <>
      <SheetHeader className="border-b border-line px-5 pb-4 pt-5">
        {view !== "menu" ? (
          <button
            type="button"
            onClick={back}
            className="mb-1 inline-flex min-h-8 items-center gap-1.5 self-start text-caption font-medium text-ink-3 transition-[color] duration-150 hover:text-ink"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("collections:sheet.backToActions")}
          </button>
        ) : null}
        <SheetTitle className="pr-6">
          {view === "menu"
            ? row.insured_name ?? row.policy_number
            : view === "debit"
              ? debitLabel
              : t(VIEW_TITLE[view])}
        </SheetTitle>
        <SheetDescription>
          {[row.insurer_name, `${t("collections:board.policyShort")} ${row.policy_number}`, installmentLabel]
            .filter(Boolean)
            .join(" · ")}
        </SheetDescription>
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <InstallmentStateBadges state={row.state} flags={row.flags} daysLate={row.days_late} debitKind={debitKindOf(row)} />
          <LevelBadge level={row.level} reason={row.level_reason} size="md" />
        </div>
      </SheetHeader>

      <div className="flex flex-1 flex-col gap-5 px-5 py-5">
        {view === "menu" ? (
          <>
            <div className="grid grid-cols-2 gap-4">
              <KeyValue label={t("collections:board.columns.due")} value={formatDate(row.due_date)} />
              <KeyValue
                label={t("collections:board.columns.amount")}
                value={money(row.gross_amount_uf, row.currency)}
              />
              <KeyValue
                label={t("collections:sheet.paid")}
                value={money(row.paid_uf, row.currency)}
                tone={Number(row.paid_uf) > 0 ? "success" : "default"}
              />
              <KeyValue
                label={t("collections:sheet.balance")}
                value={
                  <span>
                    {money(row.balance_uf, row.currency)}
                    {row.balance_clp !== null && Number(row.balance_uf) > 0 ? (
                      <span className="ml-1.5 text-caption font-normal text-ink-3">{clp(row.balance_clp)}</span>
                    ) : null}
                  </span>
                }
                tone={Number(row.balance_uf) > 0 && !voided ? "warn" : "default"}
              />
              <KeyValue
                label={t("collections:board.columns.method")}
                value={labelOf(t, METHOD_LABEL, row.payment_mode, "—")}
              />
              <KeyValue
                label={t("collections:sheet.promiseUntil")}
                value={row.promise_until ? formatDate(row.promise_until) : "—"}
              />
            </div>

            {/* The level's WHY, visible — the header badge's tooltip never opens on touch. */}
            {row.level_reason ? (
              <div className="-mt-2 flex flex-wrap items-center gap-1.5 text-caption text-ink-3">
                <LevelBadge level={row.level} />
                <span className="min-w-0">{row.level_reason}</span>
              </div>
            ) : null}

            {row.level_missing.length > 0 ? (
              <div className="rounded-lg bg-warn-soft px-3 py-2.5">
                <div className="text-caption font-medium text-ink">{t("collections:sheet.missingTitle")}</div>
                <ul className="mt-1 list-disc pl-4 text-caption text-ink-2">
                  {row.level_missing.map((m) => (
                    <li key={m}>{labelOf(t, MISSING_LABEL, m, t("collections:missing.other"))}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {row.last_note ? (
              <div className="rounded-lg bg-paper-2 px-3 py-2.5 text-caption">
                <div className="font-medium text-ink">
                  {t("collections:board.columns.lastNote")}
                  {row.last_note.created_at ? (
                    <span className="ml-1.5 font-normal text-ink-3">{formatDate(row.last_note.created_at)}</span>
                  ) : null}
                </div>
                <p className="mt-0.5 text-ink-2">{row.last_note.body}</p>
              </div>
            ) : null}

            {/* The one primary of the menu view. */}
            <DisabledHint hint={writeReason(closedReason)} className="w-full">
              <Button
                className="h-11 w-full"
                disabled={!!writeReason(closedReason)}
                onClick={() => setView("pay")}
              >
                <Wallet className="h-4 w-4" />
                {t("collections:actions.pay")}
              </Button>
            </DisabledHint>
            {writeReason(closedReason) ? (
              <p className="-mt-3 text-center text-caption text-ink-3">{writeReason(closedReason)}</p>
            ) : null}

            <nav aria-label={t("collections:sheet.actions")} className="-mx-2 flex flex-col">
              <ActionRow
                icon={<MessageSquareText />}
                label={t("collections:actions.gestion")}
                onClick={() => setView("gestion")}
                disabledReason={writeReason()}
              />
              <ActionRow
                icon={<CalendarClock />}
                label={t("collections:actions.promise")}
                onClick={() => setView("promise")}
                disabledReason={writeReason(closedReason)}
              />
              <ActionRow
                icon={<XCircle />}
                label={debitLabel}
                onClick={() => setView("debit")}
                disabledReason={writeReason(debitReason)}
              />
              <ActionRow
                icon={<Send />}
                label={t("collections:actions.reminder")}
                onClick={() => setView("reminder")}
                disabledReason={writeReason(closedReason)}
              />
              <ActionRow
                icon={<ShieldQuestion />}
                label={t("collections:actions.inquiry")}
                onClick={() => setView("inquiry")}
                disabledReason={writeReason(voided ? t("collections:sheet.voided") : null)}
              />
              {native ? (
                <ActionRow
                  icon={<Link2 />}
                  label={t("collections:actions.paymentLink")}
                  onClick={() => setView("link")}
                  disabledReason={writeReason(linkReason)}
                />
              ) : (
                <div className="flex min-h-11 items-start gap-3 px-3 py-1.5 text-caption text-ink-3">
                  <Link2 className="mt-0.5 h-4 w-4 shrink-0 opacity-50" />
                  <span className="min-w-0 flex-1">{t("collections:sheet.noOnlinePayment")}</span>
                </div>
              )}
              <ShareDialog
                entityType="installment"
                entityId={row.id}
                defaultAudience="insured"
                trigger={
                  <button
                    type="button"
                    className="flex min-h-11 w-full items-center gap-3 rounded-sm px-3 text-left text-body text-ink-2 transition-[background-color,color,transform] duration-150 ease-out hover:bg-paper-2 hover:text-ink active:scale-[0.96]"
                  >
                    <Share2 className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate">{t("collections:actions.share")}</span>
                  </button>
                }
              />
              {row.notices_enabled ? (
                <div className="flex min-h-11 items-center gap-3 px-3 text-body text-ink-3">
                  <BellRing className="h-4 w-4 shrink-0 text-pos" />
                  <span className="min-w-0 flex-1">{t("collections:sheet.noticesOn")}</span>
                </div>
              ) : (
                <ActionRow
                  icon={<PlayCircle />}
                  label={start.isPending ? t("common:state.loading") : t("collections:actions.start")}
                  onClick={() => void startCollection()}
                  disabledReason={writeReason(voided || paid ? closedReason : null)}
                />
              )}
            </nav>

            <section className="flex flex-col gap-2">
              <h3 className="text-label font-medium text-ink">{t("collections:payments.title")}</h3>
              <PaymentsPanel installmentId={row.id} />
            </section>

            <section className="flex flex-col gap-2">
              <h3 className="text-label font-medium text-ink">{t("collections:notice.title")}</h3>
              <NoticesList installmentId={row.id} />
            </section>

            <div className="mt-auto flex flex-wrap gap-2 border-t border-line pt-4">
              <Button asChild variant="secondary" size="sm">
                <Link to={`/policies/${row.policy_id}`} onClick={onClose}>
                  <FileText className="h-4 w-4" />
                  {t("collections:sheet.viewPolicy")}
                </Link>
              </Button>
              <Button asChild variant="ghost" size="sm">
                <Link to={`/collections/${row.plan_id}`} onClick={onClose}>
                  <ExternalLink className="h-4 w-4" />
                  {t("collections:sheet.viewPlan")}
                </Link>
              </Button>
            </div>
          </>
        ) : null}

        {view === "pay" ? (
          <RegisterPaymentForm row={row} ufRate={ufRate} onDone={back} onCancel={back} />
        ) : null}
        {view === "gestion" ? <GestionForm row={row} onDone={back} onCancel={back} /> : null}
        {view === "promise" ? <GestionForm row={row} promiseFirst onDone={back} onCancel={back} /> : null}
        {view === "debit" ? <DebitRejectedForm row={row} onDone={back} onCancel={back} /> : null}
        {view === "reminder" ? <ReminderPanel row={row} onDone={back} onCancel={back} /> : null}
        {view === "inquiry" ? <InquiryForm row={row} onDone={back} onCancel={back} /> : null}
        {view === "link" && native ? <PaymentLinkPanel row={row} onCancel={back} /> : null}
      </div>
    </>
  );
}
