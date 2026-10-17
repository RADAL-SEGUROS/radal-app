/**
 * Mantenedor → Eventos de proveedor (`/platform/events`, v12 02 §5).
 *
 * The provider-event inbox: every webhook that reached the one generic
 * endpoint, signed or not (a forged payload is stored `ignored` with an
 * `invalid:` dedup key). Filters by processing status, signature and provider
 * account; a row opens a sheet with the stored payload and "Reprocesar"
 * (`POST /platform/events/{id}/reprocess`, Platform.Manage) — idempotent, it
 * never books a payment twice. The server refuses to reprocess an unsigned
 * event or one with no live account; the button says so before you click.
 *
 * Platform users only — nothing here touches a broker endpoint.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, RefreshCcw, Webhook } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FadeUp } from "@/components/common/motion";
import { EmptyState, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import {
  EVENT_PROCESSING,
  useProviderAccounts,
  useProviderEvent,
  useProviderEvents,
  useReprocessEvent,
  type EventProcessing,
  type ProviderEvent,
  type ProviderEventParams,
} from "@/api/platform";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { GuardedButton, type Guard } from "@/pages/settings/components/shared";
import {
  EVENT_STATUS_LABEL,
  EVENT_STATUS_TONE,
  EVENT_TYPE_LABEL,
  JsonBlock,
  Pager,
  PlatformHeader,
  labelFrom,
  usePlatformGuards,
} from "./components/shared";

type TFn = (key: string, options?: Record<string, unknown>) => string;

const ALL = "__all__";
const LIMIT = 50;
type SigFilter = typeof ALL | "valid" | "invalid";

export default function PlatformEventsPage() {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const [status, setStatus] = React.useState<EventProcessing | typeof ALL>(ALL);
  const [sig, setSig] = React.useState<SigFilter>(ALL);
  const [accountId, setAccountId] = React.useState<string>(ALL);
  const [offset, setOffset] = React.useState(0);
  const [openId, setOpenId] = React.useState<number | null>(null);

  const params: ProviderEventParams = {
    status: status === ALL ? undefined : status,
    signature_valid: sig === ALL ? undefined : sig === "valid",
    provider_account_id: accountId === ALL ? undefined : Number(accountId),
    limit: LIMIT,
    offset,
  };
  const list = useProviderEvents(params);
  const accounts = useProviderAccounts();
  const accountLabel = React.useMemo(() => {
    const map = new Map<number, string>();
    for (const a of accounts.data ?? []) map.set(a.id, a.label);
    return map;
  }, [accounts.data]);
  const rows = list.data?.items ?? [];

  const resetPage = () => setOffset(0);

  return (
    <>
      <PlatformHeader title={t("events.title")} subtitle={t("events.description")} />
      <FadeUp className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
          <Select
            value={status}
            onValueChange={(v) => {
              setStatus(v as EventProcessing | typeof ALL);
              resetPage();
            }}
          >
            <SelectTrigger className="w-full sm:w-52" aria-label={t("events.columns.status")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("events.allStatuses")}</SelectItem>
              {EVENT_PROCESSING.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(EVENT_STATUS_LABEL[s])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={sig}
            onValueChange={(v) => {
              setSig(v as SigFilter);
              resetPage();
            }}
          >
            <SelectTrigger className="w-full sm:w-44" aria-label={t("events.columns.signature")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("events.allSignatures")}</SelectItem>
              <SelectItem value="valid">{t("events.validSig")}</SelectItem>
              <SelectItem value="invalid">{t("events.invalidSig")}</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={accountId}
            onValueChange={(v) => {
              setAccountId(v);
              resetPage();
            }}
            disabled={accounts.isLoading}
          >
            <SelectTrigger className="w-full sm:w-60" aria-label={t("events.columns.account")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("events.allAccounts")}</SelectItem>
              {(accounts.data ?? []).map((a) => (
                <SelectItem key={a.id} value={String(a.id)}>
                  {a.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {list.isFetching && !list.isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-ink-3" aria-hidden />
          ) : null}
        </div>

        <ErrorBanner error={list.error} />

        <Card className="overflow-hidden">
          {list.isLoading ? (
            <div className="p-5">
              <LoadingRows rows={6} />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState icon={<Webhook />} title={t("events.empty")} />
          ) : (
            <>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("events.columns.received")}</TableHead>
                      <TableHead>{t("events.columns.type")}</TableHead>
                      <TableHead>{t("events.columns.account")}</TableHead>
                      <TableHead>{t("events.columns.signature")}</TableHead>
                      <TableHead>{t("events.columns.status")}</TableHead>
                      <TableHead className="text-right">{t("events.columns.attempts")}</TableHead>
                      <TableHead>{t("events.columns.error")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((ev) => (
                      <TableRow
                        key={ev.id}
                        tabIndex={0}
                        role="button"
                        aria-label={t("events.detail.title", { id: ev.id })}
                        onClick={() => setOpenId(ev.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setOpenId(ev.id);
                          }
                        }}
                        className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                      >
                        <TableCell className="whitespace-nowrap text-caption tabular-nums">
                          {formatDateTime(ev.received_at)}
                        </TableCell>
                        <TableCell className="text-caption">{labelFrom(t, EVENT_TYPE_LABEL, ev.event_type, t("events.noType"))}</TableCell>
                        <TableCell className="text-caption">
                          {accountName(ev, accountLabel, t)}
                        </TableCell>
                        <TableCell>
                          <SigBadge valid={ev.signature_valid} t={t} />
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={ev.processing_status} t={t} />
                        </TableCell>
                        <TableCell className="text-right text-caption tabular-nums">{ev.attempts}</TableCell>
                        <TableCell className="max-w-[18rem] truncate text-caption text-neg-text">
                          {ev.error ?? ""}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <ul className="flex flex-col divide-y divide-line md:hidden">
                {rows.map((ev) => (
                  <li key={ev.id}>
                    <button
                      type="button"
                      onClick={() => setOpenId(ev.id)}
                      className="flex w-full flex-col gap-2 p-4 text-left transition-[background-color] duration-150 ease-out hover:bg-paper-2"
                    >
                      <span className="flex items-start justify-between gap-3">
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-ink">
                            {labelFrom(t, EVENT_TYPE_LABEL, ev.event_type, t("events.noType"))}
                          </span>
                          <span className="block text-caption tabular-nums text-ink-3">
                            {formatDateTime(ev.received_at)} · {accountName(ev, accountLabel, t)}
                          </span>
                        </span>
                        <StatusBadge status={ev.processing_status} t={t} />
                      </span>
                      <span className="flex flex-wrap items-center gap-2">
                        <SigBadge valid={ev.signature_valid} t={t} />
                        <span className="text-caption tabular-nums text-ink-3">
                          {t("events.columns.attempts")}: {ev.attempts}
                        </span>
                      </span>
                      {ev.error ? (
                        <span className="line-clamp-2 text-caption text-neg-text">{ev.error}</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>

        {list.data ? (
          <Pager total={list.data.total} limit={list.data.limit} offset={list.data.offset} onOffset={setOffset} />
        ) : null}
      </FadeUp>

      <EventSheet id={openId} accountLabel={accountLabel} onClose={() => setOpenId(null)} />
    </>
  );
}

function accountName(ev: ProviderEvent, labels: Map<number, string>, t: TFn): string {
  if (ev.provider_account_id === null) return t("events.noAccount");
  return labels.get(ev.provider_account_id) ?? `#${ev.provider_account_id}`;
}

function SigBadge({ valid, t }: { valid: boolean; t: TFn }) {
  return (
    <Badge variant={valid ? "success" : "danger"} className="px-1.5 py-0">
      {valid ? t("events.sigValid") : t("events.sigInvalid")}
    </Badge>
  );
}

function StatusBadge({ status, t }: { status: EventProcessing; t: TFn }) {
  return (
    <Badge variant={EVENT_STATUS_TONE[status] ?? "neutral"} dot className="whitespace-nowrap">
      {labelFrom(t, EVENT_STATUS_LABEL, status)}
    </Badge>
  );
}

// --- Detail sheet ----------------------------------------------------------------------------

function EventSheet({
  id,
  accountLabel,
  onClose,
}: {
  id: number | null;
  accountLabel: Map<number, string>;
  onClose: () => void;
}) {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const guards = usePlatformGuards();
  const detail = useProviderEvent(id);
  const reprocess = useReprocessEvent();
  const ev = detail.data && detail.data.id === id ? detail.data : null;

  React.useEffect(() => {
    reprocess.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // The server's own refusals, mirrored so the button explains itself first.
  let guard: Guard = guards.manage;
  if (guard.allowed && ev && !ev.signature_valid) {
    guard = { allowed: false, reason: t("events.detail.invalidSignature") };
  } else if (guard.allowed && ev && ev.provider_account_id === null) {
    guard = { allowed: false, reason: t("events.detail.noAccount") };
  }

  const run = () => {
    if (!ev) return;
    reprocess.mutate(ev.id, {
      onSuccess: () => toast.success(t("events.detail.reprocessed")),
      onError: (error) => toast.error(apiError(error)),
    });
  };

  return (
    <Sheet open={id !== null} onOpenChange={(open) => (!open && !reprocess.isPending ? onClose() : undefined)}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-2xl">
        <SheetHeader className="border-b border-line px-5 py-4 pr-12">
          <SheetTitle>{t("events.detail.title", { id: id ?? "" })}</SheetTitle>
          <SheetDescription>{t("events.detail.reprocessHint")}</SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-5 px-5 py-5">
          <ErrorBanner error={detail.error} />
          {!ev ? (
            <LoadingRows rows={5} />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={ev.processing_status} t={t} />
                <SigBadge valid={ev.signature_valid} t={t} />
              </div>
              <dl className="grid grid-cols-1 gap-x-4 gap-y-3 text-caption sm:grid-cols-2">
                <Item label={t("events.columns.received")} value={formatDateTime(ev.received_at)} />
                <Item label={t("events.columns.type")} value={labelFrom(t, EVENT_TYPE_LABEL, ev.event_type, t("events.noType"))} />
                <Item label={t("events.columns.account")} value={accountName(ev, accountLabel, t)} />
                <Item label={t("events.columns.attempts")} value={String(ev.attempts)} />
                <Item label={t("events.detail.broker")} value={ev.broker_id !== null ? `#${ev.broker_id}` : "—"} />
                <Item label={t("events.detail.provider")} value={ev.provider} />
                <Item
                  label={t("events.detail.dedup")}
                  value={<span className="break-all font-mono text-[12px]">{ev.dedup_key}</span>}
                  wide
                />
              </dl>
              {ev.error ? (
                <p className="rounded-lg bg-neg-soft px-3 py-2 text-caption text-neg-text">
                  <span className="font-medium">{t("events.columns.error")}:</span> {ev.error}
                </p>
              ) : null}
              <section className="flex flex-col gap-2">
                <h3 className="text-body font-medium text-ink">{t("events.detail.payload")}</h3>
                <JsonBlock value={ev.payload} empty={t("events.detail.noPayload")} />
              </section>
            </>
          )}
        </div>

        <SheetFooter className="sticky bottom-0 gap-2 border-t border-line bg-bone px-5 py-4 sm:gap-0">
          <Button type="button" variant="ghost" onClick={onClose} disabled={reprocess.isPending}>
            {t("common.close")}
          </Button>
          <GuardedButton guard={guard} disabled={!ev || reprocess.isPending} onClick={run}>
            {reprocess.isPending ? <Loader2 className="animate-spin" /> : <RefreshCcw />}
            {reprocess.isPending ? t("events.detail.reprocessing") : t("events.detail.reprocess")}
          </GuardedButton>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function Item({ label, value, wide }: { label: string; value: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn("min-w-0", wide && "sm:col-span-2")}>
      <dt className="text-ink-3">{label}</dt>
      <dd className="mt-0.5 break-words text-ink-2">{value}</dd>
    </div>
  );
}
