/**
 * The three ways a cuota reaches outside the corredora:
 *
 * - **Enviar recordatorio** — preview (`POST /collections/notices/preview`,
 *   writes nothing) THEN send. The broker reads exactly what the payer gets.
 *   WhatsApp is an open door only: visibly disabled with its "pronto" reason.
 * - **Consultar a la compañía** — a status inquiry (`POST /inquiries`): mail +
 *   an insurer share link. The reply becomes evidence a human confirms.
 * - **Link de pago** — a Forpay checkout (`POST /payments/intents`), only for
 *   `native` cuotas; the caller renders it disabled with `level_reason` otherwise.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, ExternalLink, Link2, Loader2, Mail, MessageCircle, Send } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { CopyButton, DisabledHint, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  usePolicyInstallmentRows,
  usePreviewCollectionNotice,
  useSendCollectionNotice,
  type BoardInstallmentRow,
  type CollectionNoticeKind,
} from "@/api/collections";
import { existingInquiryIdOf, useCreateStatusInquiry, type StatusInquiry } from "@/api/inquiries";
import { useCreatePaymentIntent, type PaymentIntentCreated } from "@/api/payments";
import { LevelBadge } from "@/components/collections/LevelBadge";
import {
  INQUIRY_STATUS_LABEL,
  INQUIRY_STATUS_TONE,
  MAIL_STATUS_LABEL,
  MISSING_LABEL,
  NOTICE_KIND_LABEL,
  labelOf,
  toneOf,
} from "@/components/collections/labels";
import { Field, SoonChip, money } from "@/components/collections/shared";
import { safeHttpUrl } from "@/lib/url";

const LATE = new Set(["overdue", "termination_risk", "promised"]);
const CLOSED = new Set(["paid", "voided"]);

// =============================================================================
// Enviar recordatorio
// =============================================================================

export function ReminderPanel({
  row,
  onDone,
  onCancel,
}: {
  row: BoardInstallmentRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const [kind, setKind] = React.useState<CollectionNoticeKind>(
    LATE.has(row.state) ? "payer_overdue" : "payer_reminder",
  );
  const preview = usePreviewCollectionNotice();
  const send = useSendCollectionNotice();
  const { mutate: runPreview } = preview;

  React.useEffect(() => {
    runPreview({ installment_id: row.id, kind, channel: "email" });
  }, [row.id, kind, runPreview]);

  const data = preview.data;
  const canSend = !!data?.to_address && !preview.isPending && !send.isPending;

  const submit = async () => {
    try {
      const notice = await send.mutateAsync({ installment_id: row.id, kind, channel: "email" });
      // `skipped` / `failed`: the notice is on record but no mail left.
      if (notice.status === "sent" || notice.status === "queued") {
        toast.success(t("collections:reminder.sent"));
      } else {
        toast.warning(t("collections:reminder.notSent"));
      }
      onDone();
    } catch {
      // ErrorBanner — 409 duplicate today / settled, 422 no contact or template.
    }
  };

  const kinds: CollectionNoticeKind[] = ["payer_reminder", "payer_overdue"];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <span className="text-caption font-medium text-ink-2">{t("collections:reminder.channel")}</span>
        <div className="grid grid-cols-2 gap-2">
          <div className="flex min-h-10 items-center gap-2 rounded-sm border border-brand bg-brand-soft px-3 text-caption font-medium text-brand-deep">
            <Mail className="h-4 w-4" />
            {t("collections:reminder.email")}
          </div>
          <DisabledHint hint={t("collections:reminder.whatsappSoon")} className="w-full">
            <button
              type="button"
              disabled
              className="flex min-h-10 w-full items-center gap-2 rounded-sm border border-line px-3 text-caption text-ink-3 opacity-60"
            >
              <MessageCircle className="h-4 w-4" />
              {t("collections:reminder.whatsapp")}
              <SoonChip />
            </button>
          </DisabledHint>
        </div>
        {/* Visible on touch, where the tooltip never opens. */}
        <p className="text-caption text-ink-3">{t("collections:reminder.whatsappSoon")}</p>
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-caption font-medium text-ink-2">{t("collections:reminder.kind")}</span>
        <div className="flex flex-wrap gap-1.5">
          {kinds.map((k) => {
            // A mora notice says "venció el …": only for a cuota that is late.
            const blocked = k === "payer_overdue" && !LATE.has(row.state);
            const chip = (
              <button
                key={k}
                type="button"
                disabled={blocked}
                onClick={() => setKind(k)}
                className={cn(
                  "min-h-8 rounded-full px-3 py-1 text-caption transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
                  kind === k
                    ? "bg-brand-soft font-medium text-brand-deep"
                    : "border border-dashed border-line text-ink-2 hover:text-ink",
                  blocked && "cursor-not-allowed opacity-50 hover:text-ink-2 active:scale-100",
                )}
              >
                {labelOf(t, NOTICE_KIND_LABEL, k)}
              </button>
            );
            return blocked ? (
              <DisabledHint key={k} hint={t("collections:reminder.overdueNotDue")}>
                {chip}
              </DisabledHint>
            ) : (
              chip
            );
          })}
        </div>
        {!LATE.has(row.state) ? (
          <p className="text-caption text-ink-3">{t("collections:reminder.overdueNotDue")}</p>
        ) : null}
      </div>

      {preview.isPending ? <LoadingRows rows={3} /> : null}
      <ErrorBanner error={preview.error} />

      {data && !preview.isPending ? (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-caption text-ink-3">
            <span>{t("collections:reminder.to")}</span>
            {data.to_address ? (
              <span className="min-w-0 break-words font-medium text-ink">
                {data.recipient_name ? `${data.recipient_name} · ` : ""}
                {data.to_address}
              </span>
            ) : (
              <Badge variant="danger">{t("collections:reminder.noRecipient")}</Badge>
            )}
            <LevelBadge level={data.level} reason={data.level_reason} />
          </div>
          {data.amount_uf !== undefined && data.amount_uf !== null ? (
            <p className="text-caption tabular-nums text-ink-2">
              {t("collections:reminder.amountAsked", { amount: money(data.amount_uf, row.currency) })}
              {Number(row.paid_uf) > 0 ? (
                <span className="text-ink-3">
                  {" "}
                  · {t("collections:board.paidOf", {
                    paid: money(row.paid_uf, row.currency),
                    total: money(row.gross_amount_uf, row.currency),
                  })}
                </span>
              ) : null}
            </p>
          ) : null}
          {data.warnings.length > 0 || data.missing.length > 0 ? (
            <ul className="flex flex-col gap-1 rounded-lg bg-warn-soft px-3 py-2.5 text-caption text-ink-2">
              {data.missing.map((m) => (
                <li key={`m-${m}`} className="flex gap-2">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
                  {labelOf(t, MISSING_LABEL, m, t("collections:missing.other"))}
                </li>
              ))}
              {data.warnings.map((w, i) => (
                <li key={`w-${i}`} className="flex gap-2">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
                  {w}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="overflow-hidden rounded-lg border border-line">
            <div className="border-b border-line bg-paper-2 px-3 py-2 text-caption font-medium text-ink">
              {data.subject}
            </div>
            <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words px-3 py-2.5 font-sans text-caption text-ink-2">
              {data.text}
            </pre>
          </div>
        </div>
      ) : null}

      <ErrorBanner error={send.error} />

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={send.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <DisabledHint
          hint={data && !data.to_address ? t("collections:reminder.noRecipientHint") : null}
        >
          <Button type="button" onClick={() => void submit()} disabled={!canSend}>
            {send.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {t("collections:reminder.send")}
          </Button>
        </DisabledHint>
      </div>
      {data && !data.to_address ? (
        <p className="-mt-2 text-caption text-ink-3 sm:text-right">{t("collections:reminder.noRecipientHint")}</p>
      ) : null}
    </div>
  );
}

// =============================================================================
// Consultar a la compañía
// =============================================================================

export function InquiryResult({ inquiry }: { inquiry: StatusInquiry }) {
  const { t } = useTranslation("collections");
  return (
    <div className="flex flex-col gap-3 rounded-lg bg-paper-2 px-3 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={toneOf(INQUIRY_STATUS_TONE, inquiry.status)} dot>
          {labelOf(t, INQUIRY_STATUS_LABEL, inquiry.status)}
        </Badge>
        {inquiry.mail_status ? (
          <span className="text-caption text-ink-3">
            {t("inquiries.mail")}: {labelOf(t, MAIL_STATUS_LABEL, inquiry.mail_status)}
          </span>
        ) : null}
      </div>
      {inquiry.status === "draft" ? (
        <p className="text-caption text-ink-2">{t("inquiry.notSentHint")}</p>
      ) : null}
      {inquiry.share_url ? (
        <div className="flex flex-col gap-1.5">
          <span className="text-caption text-ink-3">{t("inquiry.shareUrl")}</span>
          <div className="flex min-w-0 items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-sm border border-line bg-bone px-2 py-1.5 text-[12px] text-ink-2">
              {inquiry.share_url}
            </code>
            <CopyButton value={inquiry.share_url} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function InquiryForm({
  row,
  onDone,
  onCancel,
}: {
  row: BoardInstallmentRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const create = useCreateStatusInquiry();
  const siblings = usePolicyInstallmentRows(row.policy_id);
  const open = (siblings.data?.items ?? []).filter((r) => !CLOSED.has(r.state));
  const [selected, setSelected] = React.useState<number[]>([row.id]);
  const [to, setTo] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [result, setResult] = React.useState<StatusInquiry | null>(null);
  const existingId = existingInquiryIdOf(create.error);

  const toggle = (id: number, on: boolean) =>
    setSelected((prev) => (on ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id)));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selected.length === 0) return;
    try {
      const inquiry = await create.mutateAsync({
        policy_id: row.policy_id,
        installment_ids: selected,
        to_address: to.trim() || null,
        message: message.trim() || null,
      });
      setResult(inquiry);
      // The mail did not leave: a warning, the same tone as the reminder's.
      if (inquiry.status === "sent") toast.success(t("collections:inquiry.sent"));
      else toast.warning(t("collections:inquiry.created"));
    } catch {
      // ErrorBanner.
    }
  };

  if (result) {
    return (
      <div className="flex flex-col gap-4">
        <InquiryResult inquiry={result} />
        <div className="flex justify-end">
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common:actions.close")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4" noValidate>
      <p className="text-caption text-ink-3">{t("collections:inquiry.description")}</p>
      <Field label={t("collections:inquiry.installments")}>
        {siblings.isLoading ? (
          <LoadingRows rows={2} />
        ) : (
          <ul className="flex flex-col gap-1">
            {(open.length > 0 ? open : [row]).map((r) => (
              <li key={r.id}>
                <label className="flex min-h-10 cursor-pointer items-center gap-2.5 rounded-sm px-2 hover:bg-paper-2">
                  <Checkbox
                    checked={selected.includes(r.id)}
                    onCheckedChange={(v) => toggle(r.id, v === true)}
                  />
                  <span className="text-caption text-ink-2">
                    {t("collections:board.installmentOf", {
                      number: r.number,
                      count: r.installment_count ?? r.number,
                    })}{" "}
                    · {formatDate(r.due_date)} ·{" "}
                    <span className="tabular-nums">{money(r.balance_uf, r.currency)}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </Field>
      <Field
        label={t("collections:inquiry.to")}
        htmlFor="inq-to"
        hint={t("collections:inquiry.toHint")}
      >
        <Input id="inq-to" type="email" inputMode="email" value={to} onChange={(e) => setTo(e.target.value)} />
      </Field>
      <Field label={t("collections:inquiry.message")} htmlFor="inq-msg" hint={t("collections:inquiry.messageHint")}>
        <Textarea id="inq-msg" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} />
      </Field>
      <ErrorBanner error={create.error} />
      {existingId ? (
        <Button asChild variant="secondary" size="sm" className="self-start">
          <Link to={`/collections/inquiries?inquiry=${existingId}`}>
            <ExternalLink className="h-4 w-4" />
            {t("collections:inquiry.openExisting")}
          </Link>
        </Button>
      ) : null}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={create.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" disabled={create.isPending || selected.length === 0}>
          {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {t("collections:inquiry.submit")}
        </Button>
      </div>
    </form>
  );
}

// =============================================================================
// Link de pago
// =============================================================================

export function PaymentLinkPanel({
  row,
  onCancel,
}: {
  row: BoardInstallmentRow;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const create = useCreatePaymentIntent();
  const [result, setResult] = React.useState<PaymentIntentCreated | null>(null);

  const generate = async () => {
    try {
      setResult(await create.mutateAsync({ installment_ids: [row.id] }));
    } catch {
      // ErrorBanner — 409 with the server's reason (not native, paid, …).
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-caption text-ink-3">{t("collections:link.description")}</p>
      {result ? (
        <div className="flex flex-col gap-2 rounded-lg bg-paper-2 px-3 py-3">
          <div className="flex flex-wrap items-center gap-2 text-caption text-ink-3">
            <span className="font-medium text-ink tabular-nums">{money(result.intent.total_uf, "UF")}</span>
            {result.reused ? <Badge variant="neutral">{t("collections:link.reused")}</Badge> : null}
            {result.intent.expires_at ? (
              <span>{t("collections:link.expires", { date: formatDateTime(result.intent.expires_at) })}</span>
            ) : null}
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-sm border border-line bg-bone px-2 py-1.5 text-[12px] text-ink-2">
              {result.checkout_url}
            </code>
            <CopyButton value={result.checkout_url} />
          </div>
          {safeHttpUrl(result.checkout_url) ? (
            <Button asChild variant="secondary" size="sm" className="self-start">
              <a href={safeHttpUrl(result.checkout_url) ?? undefined} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-4 w-4" />
                {t("collections:link.open")}
              </a>
            </Button>
          ) : null}
        </div>
      ) : null}
      <ErrorBanner error={create.error} />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={create.isPending}>
          {result ? t("common:actions.close") : t("common:actions.cancel")}
        </Button>
        {!result ? (
          <Button type="button" onClick={() => void generate()} disabled={create.isPending}>
            {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
            {t("collections:link.generate")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
