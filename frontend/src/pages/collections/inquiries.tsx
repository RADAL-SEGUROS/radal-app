/**
 * Cobranza — "Consultas a compañías" (`GET /inquiries`).
 *
 * Radal cannot see the insurer's bank account, so it ASKS: a status inquiry is
 * a mail plus an insurer share link. Every reply lands as evidence in
 * "Respuestas por confirmar"; nothing here books a payment.
 *
 * The list filters by the server's single `status` param (the tabs). A row
 * opens a side sheet with the detail (`GET /inquiries/{id}`: mails + evidence)
 * and the two writes — Reenviar and Cancelar. Both are refused by the server
 * on `resolved` / `cancelled`, so the UI disables them there with that reason.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Inbox,
  Loader2,
  Mail,
  MailQuestion,
  RotateCw,
  XCircle,
} from "lucide-react";

import { TeamScopeChip } from "@/components/common/TeamScopeChip";
import { PageHeader } from "@/components/common/PageHeader";
import {
  CopyButton,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  LoadingRows,
} from "@/components/common/kit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate, formatDateTime } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import { cn } from "@/lib/utils";
import {
  useCancelStatusInquiry,
  useResendStatusInquiry,
  useStatusInquiries,
  useStatusInquiry,
  type StatusInquiry,
  type StatusInquiryStatus,
} from "@/api/inquiries";
import {
  EVIDENCE_STATUS_LABEL,
  EVIDENCE_STATUS_TONE,
  INQUIRY_STATUS_LABEL,
  INQUIRY_STATUS_TONE,
  MAIL_STATUS_LABEL,
  REPORTED_STATUS_LABEL,
  labelOf,
  toneOf,
} from "@/components/collections/labels";
import { Field, money } from "@/components/collections/shared";

const PAGE = 30;

const TABS = {
  all: { label: "collections:inquiries.tabs.all", status: undefined },
  sent: { label: "collections:inquiries.tabs.sent", status: "sent" },
  replied: { label: "collections:inquiries.tabs.replied", status: "replied" },
  draft: { label: "collections:inquiries.tabs.draft", status: "draft" },
  failed: { label: "collections:inquiries.tabs.failed", status: "failed" },
  resolved: { label: "collections:inquiries.tabs.resolved", status: "resolved" },
  cancelled: { label: "collections:inquiries.tabs.cancelled", status: "cancelled" },
} as const satisfies Record<string, { label: string; status: StatusInquiryStatus | undefined }>;
type TabKey = keyof typeof TABS;

function isTab(v: string | null): v is TabKey {
  return !!v && Object.prototype.hasOwnProperty.call(TABS, v);
}

/** The server refuses resend/cancel on these (409, Spanish reason). */
const FINAL = new Set<string>(["resolved", "cancelled"]);

function InquiryStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation("collections");
  return (
    <Badge variant={toneOf(INQUIRY_STATUS_TONE, status)} dot>
      {labelOf(t, INQUIRY_STATUS_LABEL, status, t("inquiries.status.unknown"))}
    </Badge>
  );
}

/** A cuota of the inquiry that got paid since: shown as such, and the job never sends it. */
function isPaid(status: string | null | undefined): boolean {
  return status === "paid";
}

function installmentsText(t: TFunction, inquiry: StatusInquiry): string {
  if (inquiry.installments.length === 0) {
    return t("inquiries.installmentsCount", { count: inquiry.installment_ids.length });
  }
  return inquiry.installments
    .map((i) =>
      isPaid(i.status)
        ? t("inquiries.installmentNPaid", { number: i.number })
        : t("inquiries.installmentN", { number: i.number }),
    )
    .join(", ");
}

/** Every cuota of the inquiry is already paid: nothing left to ask. */
function allPaid(inquiry: StatusInquiry): boolean {
  return inquiry.installments.length > 0 && inquiry.installments.every((i) => isPaid(i.status));
}

function AllPaidChip() {
  const { t } = useTranslation("collections");
  return (
    <Badge variant="success" className="w-fit px-1.5 py-0 text-[11px]">
      {t("inquiries.allPaid")}
    </Badge>
  );
}

function EvidenceOpenChip({ count }: { count: number }) {
  const { t } = useTranslation("collections");
  if (count <= 0) return null;
  return (
    <Badge variant="warn" className="px-1.5 py-0 text-[11px]">
      {t("inquiries.evidenceOpen", { count })}
    </Badge>
  );
}

function InquiryCard({ inquiry, onOpen }: { inquiry: StatusInquiry; onOpen: () => void }) {
  const { t } = useTranslation("collections");
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full flex-col gap-2.5 rounded-card border border-line bg-bone p-4 text-left shadow-elev transition-[border-color,box-shadow,transform] duration-150 ease-out hover:border-line-strong active:scale-[0.96]"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-body font-medium text-ink">
              {inquiry.insured_name ?? inquiry.policy_number ?? `#${inquiry.policy_id}`}
            </div>
            <div className="truncate text-caption text-ink-3">
              {[inquiry.insurer_name, inquiry.policy_number ? `${t("board.policyShort")} ${inquiry.policy_number}` : null]
                .filter(Boolean)
                .join(" · ")}
            </div>
          </div>
          <InquiryStatusBadge status={inquiry.status} />
        </div>
        <div className="text-caption text-ink-2">{installmentsText(t, inquiry)}</div>
        {allPaid(inquiry) && !FINAL.has(inquiry.status) ? <AllPaidChip /> : null}
        <div className="flex flex-wrap items-center justify-between gap-2 text-caption text-ink-3">
          <span className="min-w-0 truncate">{inquiry.to_address ?? t("inquiries.noRecipient")}</span>
          {/* A draft was never sent: the "Sin enviar" badge says so, no date. */}
          {inquiry.sent_at ? <span className="tabular-nums">{formatDateTime(inquiry.sent_at)}</span> : null}
        </div>
        <EvidenceOpenChip count={inquiry.evidence_open} />
      </button>
    </li>
  );
}

function CancelDialog({
  inquiry,
  open,
  onOpenChange,
}: {
  inquiry: StatusInquiry;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const cancel = useCancelStatusInquiry();
  const submit = async () => {
    try {
      await cancel.mutateAsync(inquiry.id);
      toast.success(t("collections:inquiries.cancelled"));
      onOpenChange(false);
    } catch {
      // ErrorBanner.
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("collections:inquiries.cancelTitle")}</DialogTitle>
          <DialogDescription>{t("collections:inquiries.cancelBody")}</DialogDescription>
        </DialogHeader>
        <ErrorBanner error={cancel.error} />
        <DialogFooter className="flex-col-reverse gap-2 sm:flex-row">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={cancel.isPending}>
            {t("common:actions.back")}
          </Button>
          <Button variant="destructive" onClick={() => void submit()} disabled={cancel.isPending}>
            {cancel.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
            {t("collections:inquiries.cancelSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function InquirySheet({
  inquiryId,
  fallback,
  open,
  onOpenChange,
}: {
  inquiryId: number | null;
  fallback: StatusInquiry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const detail = useStatusInquiry(inquiryId ?? undefined);
  const resend = useResendStatusInquiry();
  const canEdit = useCan("Collections", "Edit");
  const [message, setMessage] = React.useState("");
  const [confirmCancel, setConfirmCancel] = React.useState(false);

  const { reset: resetResend } = resend;
  React.useEffect(() => {
    setMessage("");
    resetResend();
  }, [inquiryId, resetResend]);

  const inquiry = detail.data ?? fallback;
  const final = inquiry ? FINAL.has(inquiry.status) : true;
  const gate = canEdit.isLoading
    ? t("common:state.loading")
    : canEdit.allowed
      ? null
      : t("collections:sheet.noEditPermission");
  const finalReason = inquiry && final
    ? t("collections:inquiries.finalReason", {
        status: labelOf(t, INQUIRY_STATUS_LABEL, inquiry.status, inquiry.status),
      })
    : null;
  const writeReason = gate ?? finalReason;

  const doResend = async () => {
    if (!inquiry) return;
    try {
      const updated = await resend.mutateAsync({ inquiryId: inquiry.id, message: message.trim() || null });
      // Mail not sent → the warning tone every "no salió" toast uses.
      if (updated.status === "sent") toast.success(t("collections:inquiries.resent"));
      else toast.warning(t("collections:inquiries.resentDraft"));
      setMessage("");
    } catch {
      // ErrorBanner.
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-md">
        {!inquiry ? (
          <SheetHeader className="p-5">
            <SheetTitle>{t("collections:inquiries.title")}</SheetTitle>
            <LoadingRows rows={3} />
          </SheetHeader>
        ) : (
          <>
            <SheetHeader className="border-b border-line px-5 pb-4 pt-5">
              <SheetTitle className="pr-6">
                {inquiry.insured_name ?? inquiry.policy_number ?? `#${inquiry.policy_id}`}
              </SheetTitle>
              <SheetDescription>
                {[inquiry.insurer_name, inquiry.policy_number ? `${t("collections:board.policyShort")} ${inquiry.policy_number}` : null]
                  .filter(Boolean)
                  .join(" · ")}
              </SheetDescription>
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                <InquiryStatusBadge status={inquiry.status} />
                {inquiry.mail_status ? (
                  <Badge variant="outline" className="px-1.5 py-0 text-[11px]">
                    {t("collections:inquiries.mail")}: {labelOf(t, MAIL_STATUS_LABEL, inquiry.mail_status)}
                  </Badge>
                ) : null}
                {inquiry.created_by_job ? (
                  <Badge variant="muted" className="px-1.5 py-0 text-[11px]">
                    {t("collections:inquiries.automatic")}
                  </Badge>
                ) : null}
              </div>
            </SheetHeader>

            <div className="flex flex-1 flex-col gap-5 px-5 py-5">
              <div className="grid grid-cols-2 gap-4">
                <KeyValue
                  label={t("collections:inquiries.columns.to")}
                  value={inquiry.to_address ?? "—"}
                  className="col-span-2"
                />
                <KeyValue label={t("collections:inquiries.columns.sent")} value={formatDateTime(inquiry.sent_at)} />
                <KeyValue label={t("collections:inquiries.columns.replied")} value={formatDateTime(inquiry.replied_at)} />
              </div>

              <section className="flex flex-col gap-2">
                <h3 className="text-label font-medium text-ink">{t("collections:inquiries.installments")}</h3>
                {inquiry.installments.length === 0 ? (
                  <p className="text-caption text-ink-3">{installmentsText(t, inquiry)}</p>
                ) : (
                  <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
                    {inquiry.installments.map((i) => (
                      <li key={i.id} className="flex items-center justify-between gap-2 px-3 py-2 text-caption">
                        <span className="flex flex-wrap items-center gap-1.5 text-ink-2">
                          {t("collections:inquiries.installmentN", { number: i.number })} · {formatDate(i.due_date)}
                          {isPaid(i.status) ? (
                            <Badge variant="success" className="px-1.5 py-0 text-[11px]">
                              {t("collections:state.paid")}
                            </Badge>
                          ) : null}
                        </span>
                        <span className={cn("tabular-nums", isPaid(i.status) ? "text-ink-3" : "text-ink")}>
                          {money(i.gross_amount_uf, i.currency)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {allPaid(inquiry) && !final ? (
                  <p className="text-caption text-ink-3">{t("collections:inquiries.allPaidHint")}</p>
                ) : null}
              </section>

              {inquiry.share_url && !final ? (
                <section className="flex flex-col gap-1.5">
                  <h3 className="text-label font-medium text-ink">{t("collections:inquiry.shareUrl")}</h3>
                  <div className="flex min-w-0 items-center gap-2">
                    <code className="min-w-0 flex-1 truncate rounded-sm border border-line bg-bone px-2 py-1.5 text-[12px] text-ink-2">
                      {inquiry.share_url}
                    </code>
                    <CopyButton value={inquiry.share_url} />
                  </div>
                </section>
              ) : null}

              <section className="flex flex-col gap-2">
                <h3 className="text-label font-medium text-ink">{t("collections:inquiries.messages")}</h3>
                {detail.isLoading ? (
                  <LoadingRows rows={2} />
                ) : (detail.data?.messages ?? []).length === 0 ? (
                  <p className="text-caption text-ink-3">{t("collections:inquiries.noMessages")}</p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {(detail.data?.messages ?? []).map((m) => (
                      <li key={m.id} className="rounded-lg bg-paper-2 px-3 py-2 text-caption">
                        <div className="flex flex-wrap items-center gap-2">
                          <Mail className="h-3.5 w-3.5 shrink-0 text-ink-3" />
                          <span className="font-medium text-ink">{labelOf(t, MAIL_STATUS_LABEL, m.status)}</span>
                          <span className="ml-auto tabular-nums text-ink-3">{formatDateTime(m.occurred_at)}</span>
                        </div>
                        {m.subject ? <div className="mt-1 truncate text-ink-2">{m.subject}</div> : null}
                        {m.from_address ? <div className="truncate text-ink-3">{m.from_address}</div> : null}
                        {m.error ? <div className="mt-1 text-neg-text">{m.error}</div> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-label font-medium text-ink">{t("collections:inquiries.evidence")}</h3>
                  {inquiry.evidence_open > 0 ? (
                    <Button asChild variant="ghost" size="sm">
                      <Link to="/collections/evidence">
                        <Inbox className="h-4 w-4" />
                        {t("collections:inquiries.reviewEvidence")}
                      </Link>
                    </Button>
                  ) : null}
                </div>
                {detail.isLoading ? null : (detail.data?.evidence ?? []).length === 0 ? (
                  <p className="text-caption text-ink-3">{t("collections:inquiries.noEvidence")}</p>
                ) : (
                  <ul className="flex flex-col gap-1.5">
                    {(detail.data?.evidence ?? []).map((ev) => (
                      <li key={ev.id} className="flex flex-wrap items-center gap-2 text-caption">
                        <Badge variant={toneOf(EVIDENCE_STATUS_TONE, ev.status)} className="px-1.5 py-0 text-[11px]">
                          {labelOf(t, EVIDENCE_STATUS_LABEL, ev.status)}
                        </Badge>
                        <span className="text-ink-2">
                          {labelOf(t, REPORTED_STATUS_LABEL, ev.reported_status, t("collections:evidence.reported.unknown"))}
                          {ev.installment_number ? ` · ${t("collections:inquiries.installmentN", { number: ev.installment_number })}` : ""}
                        </span>
                        <span className="ml-auto tabular-nums text-ink-3">{formatDateTime(ev.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="flex flex-col gap-3 border-t border-line pt-4">
                <Field
                  label={t("collections:inquiry.message")}
                  htmlFor="inq-resend-msg"
                  hint={t("collections:inquiries.resendHint")}
                >
                  <Textarea
                    id="inq-resend-msg"
                    rows={2}
                    value={message}
                    disabled={!!writeReason}
                    onChange={(e) => setMessage(e.target.value)}
                  />
                </Field>
                <ErrorBanner error={resend.error} />
                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                  <DisabledHint hint={writeReason}>
                    <Button
                      variant="ghost"
                      disabled={!!writeReason || resend.isPending}
                      onClick={() => setConfirmCancel(true)}
                    >
                      <XCircle className="h-4 w-4" />
                      {t("collections:inquiries.cancel")}
                    </Button>
                  </DisabledHint>
                  <DisabledHint hint={writeReason}>
                    <Button disabled={!!writeReason || resend.isPending} onClick={() => void doResend()}>
                      {resend.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
                      {t("collections:inquiries.resend")}
                    </Button>
                  </DisabledHint>
                </div>
                {writeReason ? (
                  <p className="text-caption text-ink-3 sm:text-right">{writeReason}</p>
                ) : null}
              </section>

              <div className="mt-auto flex flex-wrap gap-2 border-t border-line pt-4">
                <Button asChild variant="secondary" size="sm">
                  <Link to={`/policies/${inquiry.policy_id}`} onClick={() => onOpenChange(false)}>
                    {t("collections:sheet.viewPolicy")}
                  </Link>
                </Button>
              </div>
            </div>
            <CancelDialog inquiry={inquiry} open={confirmCancel} onOpenChange={setConfirmCancel} />
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default function CollectionsInquiriesPage() {
  const { t } = useTranslation(["collections", "common"]);
  const setParams = useSetUrlParams();
  const [tabRaw] = useUrlParam("tab");
  const [pageRaw] = useUrlParam("page");
  // `?inquiry=ID` opens that inquiry's sheet — the 409 "ya hay una consulta
  // abierta" links here.
  const [inquiryRaw] = useUrlParam("inquiry");
  const deepId = inquiryRaw && Number(inquiryRaw) > 0 ? Number(inquiryRaw) : null;
  const tab: TabKey = isTab(tabRaw) ? tabRaw : "all";
  const pageIndex = pageRaw && Number(pageRaw) > 1 ? Number(pageRaw) - 1 : 0;

  const list = useStatusInquiries({
    status: TABS[tab].status,
    limit: PAGE,
    offset: pageIndex * PAGE,
  });
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));

  const [selected, setSelected] = React.useState<StatusInquiry | null>(null);
  const liveSelected = (selected && items.find((i) => i.id === selected.id)) || selected;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/collections" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("collections:title")}
          </Link>
        }
        title={t("collections:inquiries.title")}
        subtitle={t("collections:inquiries.description")}
        actions={
          <Button asChild variant="secondary">
            <Link to="/collections/evidence">
              <Inbox className="h-4 w-4" />
              {t("collections:evidence.title")}
              <TeamScopeChip />
            </Link>
          </Button>
        }
      />

      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v === "all" ? null : v, page: null })}>
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <TabsList variant="underline" className="min-w-max">
            {(Object.keys(TABS) as TabKey[]).map((key) => (
              <TabsTrigger key={key} value={key}>
                {t(TABS[key].label)}
                {key === tab && list.data ? (
                  <span className="ml-1.5 rounded-full bg-paper-2 px-1.5 text-[11px] tabular-nums text-ink-3">
                    {total}
                  </span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>

      <ErrorBanner error={list.error} />

      {list.isLoading ? (
        <Card className="p-5">
          <LoadingRows rows={5} />
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<MailQuestion />}
            title={tab === "all" ? t("collections:inquiries.empty") : t("collections:inquiries.emptyFiltered")}
            hint={tab === "all" ? t("collections:inquiries.emptyHint") : undefined}
            action={
              tab === "all" ? (
                <Button asChild variant="secondary" size="sm">
                  <Link to="/collections?tab=overdue">{t("collections:inquiries.emptyCta")}</Link>
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <>
          <ul className="flex flex-col gap-2.5 md:hidden">
            {items.map((inq) => (
              <InquiryCard key={inq.id} inquiry={inq} onOpen={() => setSelected(inq)} />
            ))}
          </ul>

          <Card className={cn("hidden overflow-hidden md:block", list.isFetching && "opacity-80")}>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("collections:board.columns.insured")}</TableHead>
                    <TableHead>{t("collections:board.columns.policy")}</TableHead>
                    <TableHead>{t("collections:board.columns.insurer")}</TableHead>
                    <TableHead>{t("collections:inquiries.columns.installments")}</TableHead>
                    <TableHead>{t("collections:inquiries.columns.to")}</TableHead>
                    <TableHead>{t("collections:inquiries.columns.sent")}</TableHead>
                    <TableHead>{t("collections:inquiries.columns.status")}</TableHead>
                    <TableHead className="text-right">
                      <span className="sr-only">{t("collections:board.columns.actions")}</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((inq) => (
                    <TableRow key={inq.id} className="cursor-pointer" onClick={() => setSelected(inq)}>
                      <TableCell className="max-w-[200px] truncate font-medium text-ink">
                        {inq.insured_name ?? "—"}
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums text-ink-2">
                        {inq.policy_number ?? `#${inq.policy_id}`}
                      </TableCell>
                      <TableCell className="max-w-[160px] truncate text-ink-2">{inq.insurer_name ?? "—"}</TableCell>
                      <TableCell className="max-w-[160px] truncate text-ink-2">{installmentsText(t, inq)}</TableCell>
                      <TableCell className="max-w-[200px] truncate text-ink-2">
                        {inq.to_address ?? <span className="text-ink-3">{t("collections:inquiries.noRecipient")}</span>}
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums text-ink-2">
                        {formatDateTime(inq.sent_at)}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1">
                          <InquiryStatusBadge status={inq.status} />
                          {allPaid(inq) && !FINAL.has(inq.status) ? <AllPaidChip /> : null}
                          <EvidenceOpenChip count={inq.evidence_open} />
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelected(inq);
                          }}
                        >
                          {t("common:actions.view")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Card>
        </>
      )}

      {pageCount > 1 ? (
        <div className="flex items-center justify-end gap-2">
          <span className="text-caption tabular-nums text-ink-3">
            {t("collections:board.page", { page: pageIndex + 1, pages: pageCount })}
          </span>
          <Button
            variant="secondary"
            size="icon"
            aria-label={t("common:actions.previous")}
            disabled={pageIndex === 0}
            onClick={() => setParams({ page: pageIndex <= 1 ? null : String(pageIndex) })}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="secondary"
            size="icon"
            aria-label={t("common:actions.next")}
            disabled={pageIndex + 1 >= pageCount}
            onClick={() => setParams({ page: String(pageIndex + 2) })}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      ) : null}

      <InquirySheet
        inquiryId={selected?.id ?? deepId}
        fallback={liveSelected}
        open={selected !== null || deepId !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelected(null);
            if (deepId !== null) setParams({ inquiry: null });
          }
        }}
      />
    </>
  );
}
