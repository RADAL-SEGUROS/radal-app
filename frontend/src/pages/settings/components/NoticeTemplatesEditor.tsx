import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Eye, FileText, Loader2, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { DisabledHint, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, Field, GuardedButton, SectionCard, type Guard } from "./shared";
import { cadenceLabel, type TFn } from "./cadence";
import {
  useApproveNoticeTemplates,
  useNoticeTemplates,
  useUpdateNoticeTemplates,
  type NoticeKind,
  type NoticeTemplate,
  type NoticeTemplateIn,
  type NoticeTemplateSlot,
} from "../api";

/**
 * The cobranza message editor (team spec C5.1): one template per
 * `(kind, cadence_level, channel)` slot. The effective set comes from
 * `GET /collections/templates` — the broker's own rows, else Radal's Spanish
 * defaults. Edits are local drafts until "Guardar mensajes" (`PUT`, only the
 * changed slots). Approval is PER MESSAGE: a content change withdraws only that
 * message's approval server-side — the others keep sending — and "Aprobar este
 * mensaje" / "Aprobar todos" (`POST /approve {items}`) approve slots. An
 * unapproved slot sends nothing.
 */

const PLACEHOLDER_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
const INQUIRY_MARK = "[RADAL-{{inquiry_token}}]";

// Explicit maps — no dynamic i18n keys.
const KIND_LABEL: Record<NoticeKind, string> = {
  payer_reminder: "settings:collectionsTab.templates.kind.payer_reminder",
  payer_overdue: "settings:collectionsTab.templates.kind.payer_overdue",
  payment_link: "settings:collectionsTab.templates.kind.payment_link",
  insurer_status_inquiry: "settings:collectionsTab.templates.kind.insurer_status_inquiry",
};

const VARIABLE_COPY: Record<string, { label: string; sample: string }> = {
  broker_name: {
    label: "settings:collectionsTab.templates.var.broker_name",
    sample: "settings:collectionsTab.templates.sample.broker_name",
  },
  insured_name: {
    label: "settings:collectionsTab.templates.var.insured_name",
    sample: "settings:collectionsTab.templates.sample.insured_name",
  },
  policy_number: {
    label: "settings:collectionsTab.templates.var.policy_number",
    sample: "settings:collectionsTab.templates.sample.policy_number",
  },
  insurer_name: {
    label: "settings:collectionsTab.templates.var.insurer_name",
    sample: "settings:collectionsTab.templates.sample.insurer_name",
  },
  installment_number: {
    label: "settings:collectionsTab.templates.var.installment_number",
    sample: "settings:collectionsTab.templates.sample.installment_number",
  },
  installment_count: {
    label: "settings:collectionsTab.templates.var.installment_count",
    sample: "settings:collectionsTab.templates.sample.installment_count",
  },
  due_date: {
    label: "settings:collectionsTab.templates.var.due_date",
    sample: "settings:collectionsTab.templates.sample.due_date",
  },
  amount_uf: {
    label: "settings:collectionsTab.templates.var.amount_uf",
    sample: "settings:collectionsTab.templates.sample.amount_uf",
  },
  amount_clp: {
    label: "settings:collectionsTab.templates.var.amount_clp",
    sample: "settings:collectionsTab.templates.sample.amount_clp",
  },
  share_url: {
    label: "settings:collectionsTab.templates.var.share_url",
    sample: "settings:collectionsTab.templates.sample.share_url",
  },
  insurer_instructions: {
    label: "settings:collectionsTab.templates.var.insurer_instructions",
    sample: "settings:collectionsTab.templates.sample.insurer_instructions",
  },
  inquiry_token: {
    label: "settings:collectionsTab.templates.var.inquiry_token",
    sample: "settings:collectionsTab.templates.sample.inquiry_token",
  },
  installments_table: {
    label: "settings:collectionsTab.templates.var.installments_table",
    sample: "settings:collectionsTab.templates.sample.installments_table",
  },
  recipient_name: {
    label: "settings:collectionsTab.templates.var.recipient_name",
    sample: "settings:collectionsTab.templates.sample.recipient_name",
  },
  balance_uf: {
    label: "settings:collectionsTab.templates.var.balance_uf",
    sample: "settings:collectionsTab.templates.sample.balance_uf",
  },
  balance_clp: {
    label: "settings:collectionsTab.templates.var.balance_clp",
    sample: "settings:collectionsTab.templates.sample.balance_clp",
  },
  paid_uf: {
    label: "settings:collectionsTab.templates.var.paid_uf",
    sample: "settings:collectionsTab.templates.sample.paid_uf",
  },
  gross_uf: {
    label: "settings:collectionsTab.templates.var.gross_uf",
    sample: "settings:collectionsTab.templates.sample.gross_uf",
  },
  paid_note: {
    label: "settings:collectionsTab.templates.var.paid_note",
    sample: "settings:collectionsTab.templates.sample.paid_note",
  },
};

/**
 * Is this message approved? The server's per-message `is_approved` when it
 * sends one; else (older server) its own stamp, else the set-wide flag.
 */
function isApproved(item: NoticeTemplate | undefined, setApproved: boolean): boolean {
  if (!item) return false;
  if (typeof item.is_approved === "boolean") return item.is_approved;
  if (item.approved_at) return true;
  return setApproved;
}

function slotKey(t: { kind: string; cadence_level: number | null; channel: string }): string {
  return `${t.kind}|${t.cadence_level ?? "any"}|${t.channel}`;
}

function toIn(t: NoticeTemplate): NoticeTemplateIn {
  return {
    kind: t.kind,
    cadence_level: t.cadence_level,
    channel: t.channel,
    subject: t.subject,
    body: t.body,
    is_active: t.is_active,
  };
}

function sameContent(a: NoticeTemplateIn, b: NoticeTemplateIn): boolean {
  return a.subject === b.subject && a.body === b.body && a.is_active === b.is_active;
}

function placeholders(text: string): string[] {
  return Array.from(text.matchAll(PLACEHOLDER_RE), (m) => m[1]);
}

function slotLabel(t: TFn, kind: NoticeKind, level: number | null): string {
  const kindLabel = t(KIND_LABEL[kind]);
  const levelLabel =
    level === null ? t("settings:collectionsTab.templates.anyLevel") : cadenceLabel(t, level);
  return `${kindLabel} · ${levelLabel}`;
}

export function NoticeTemplatesEditor({
  guard,
  cadenceDays,
  enabled,
}: {
  guard: Guard;
  cadenceDays: number[];
  enabled: boolean;
}) {
  const { t: rawT } = useTranslation(["settings", "common"]);
  const t = rawT as unknown as TFn;
  const templates = useNoticeTemplates(enabled);
  const save = useUpdateNoticeTemplates();
  const approve = useApproveNoticeTemplates();

  /** Local edits by slot key (also holds slots created here, not yet saved). */
  const [drafts, setDrafts] = React.useState<Record<string, NoticeTemplateIn>>({});
  const [selected, setSelected] = React.useState<string | null>(null);
  const subjectRef = React.useRef<HTMLInputElement>(null);
  const bodyRef = React.useRef<HTMLTextAreaElement>(null);
  const lastField = React.useRef<"subject" | "body">("body");

  const server = React.useMemo(() => templates.data?.items ?? [], [templates.data]);
  const serverByKey = React.useMemo(() => {
    const map = new Map<string, NoticeTemplate>();
    for (const item of server) map.set(slotKey(item), item);
    return map;
  }, [server]);

  /** Server slots, then locally created ones — the list the broker sees. */
  const slots = React.useMemo(() => {
    const keys = server.map(slotKey);
    for (const key of Object.keys(drafts)) if (!serverByKey.has(key)) keys.push(key);
    return keys;
  }, [server, drafts, serverByKey]);

  const current = (key: string): NoticeTemplateIn | null => {
    if (drafts[key]) return drafts[key];
    const item = serverByKey.get(key);
    return item ? toIn(item) : null;
  };

  const dirtyKeys = Object.keys(drafts).filter((key) => {
    const base = serverByKey.get(key);
    return !base || !sameContent(drafts[key], toIn(base));
  });
  const dirty = dirtyKeys.length > 0;

  React.useEffect(() => {
    if ((selected === null || !slots.includes(selected)) && slots.length) setSelected(slots[0]);
  }, [slots, selected]);

  const variables = templates.data?.variables ?? [];
  const known = new Set(variables);

  // Cadence days that notify the team but have no payer message.
  const missingDays = cadenceDays.filter((day) => {
    const kind: NoticeKind = day <= 0 ? "payer_reminder" : "payer_overdue";
    return !slots.includes(slotKey({ kind, cadence_level: day, channel: "email" }));
  });

  const createFor = (day: number) => {
    const kind: NoticeKind = day <= 0 ? "payer_reminder" : "payer_overdue";
    // Clone the nearest message of the same kind so the new one starts sensible.
    const sameKind = slots
      .map((key) => current(key))
      .filter((tpl): tpl is NoticeTemplateIn => !!tpl && tpl.kind === kind && tpl.channel === "email");
    const nearest = sameKind.sort(
      (a, b) =>
        Math.abs((a.cadence_level ?? 0) - day) - Math.abs((b.cadence_level ?? 0) - day),
    )[0];
    const draft: NoticeTemplateIn = {
      kind,
      cadence_level: day,
      channel: "email",
      subject: nearest?.subject ?? t(KIND_LABEL[kind]),
      body: nearest?.body ?? t(KIND_LABEL[kind]),
      is_active: true,
    };
    const key = slotKey(draft);
    setDrafts((prev) => ({ ...prev, [key]: draft }));
    setSelected(key);
  };

  const edit = (key: string, patch: Partial<NoticeTemplateIn>) => {
    const base = current(key);
    if (!base) return;
    setDrafts((prev) => ({ ...prev, [key]: { ...base, ...patch } }));
  };

  const insertVariable = (name: string) => {
    if (!selected) return;
    const token = `{{${name}}}`;
    const field = lastField.current;
    const el = field === "subject" ? subjectRef.current : bodyRef.current;
    const tpl = current(selected);
    if (!tpl) return;
    const value = field === "subject" ? tpl.subject : tpl.body;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    const next = value.slice(0, start) + token + value.slice(end);
    edit(selected, field === "subject" ? { subject: next } : { body: next });
    window.requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const caret = start + token.length;
      el.setSelectionRange(caret, caret);
    });
  };

  const onSave = () => {
    const payload = dirtyKeys.map((key) => drafts[key]);
    save.mutate(payload, {
      onSuccess: (data) => {
        setDrafts({});
        toast.success(
          data.approved
            ? t("settings:collectionsTab.templates.savedToast")
            : t("settings:collectionsTab.templates.savedNeedsApproval"),
        );
      },
      onError: (error) => toast.error(apiError(error, t("settings:error.generic"))),
    });
  };

  const onApprove = (items?: NoticeTemplateSlot[]) =>
    approve.mutate(items, {
      onSuccess: () =>
        toast.success(
          items && items.length === 1
            ? t("settings:collectionsTab.templates.approvedOneToast")
            : t("settings:collectionsTab.templates.approvedToast"),
        ),
      onError: (error) => toast.error(apiError(error, t("settings:error.generic"))),
    });

  const data = templates.data;
  const tpl = selected ? current(selected) : null;
  const readOnly = !guard.allowed;
  const isWhatsapp = tpl?.channel === "whatsapp";

  const unknown = tpl
    ? Array.from(new Set([...placeholders(tpl.subject), ...placeholders(tpl.body)])).filter(
        (name) => !known.has(name),
      )
    : [];
  const missingMark =
    tpl?.kind === "insurer_status_inquiry" && !tpl.subject.replace(/\s+/g, "").includes(INQUIRY_MARK);

  const render = (text: string) =>
    text.replace(PLACEHOLDER_RE, (_m, name: string) => {
      const copy = VARIABLE_COPY[name];
      return copy ? t(copy.sample) : `{{${name}}}`;
    });

  const approveGuard: Guard = !guard.allowed
    ? guard
    : dirty
      ? { allowed: false, reason: t("settings:collectionsTab.templates.approveDirty") }
      : { allowed: true, reason: "" };

  const setApproved = !!data?.approved;
  // WhatsApp slots are an open door ("pronto"): never counted as pending.
  const unapproved = server.filter((item) => item.channel !== "whatsapp" && !isApproved(item, setApproved));
  const selectedBase = selected ? serverByKey.get(selected) : undefined;
  const selectedApproved = isApproved(selectedBase, setApproved);
  const selectedDirty = !!selected && dirtyKeys.includes(selected);
  const approveOneGuard: Guard = !guard.allowed
    ? guard
    : !selectedBase || selectedDirty
      ? { allowed: false, reason: t("settings:collectionsTab.templates.approveOneDirty") }
      : { allowed: true, reason: "" };

  return (
    <SectionCard
      title={t("settings:collectionsTab.templates.title")}
      icon={<FileText />}
      description={t("settings:collectionsTab.templates.description")}
      actions={
        data && unapproved.length > 0 ? (
          <GuardedButton
            guard={approveGuard}
            variant="accent-soft"
            size="sm"
            className="min-h-9"
            disabled={approve.isPending}
            onClick={() => onApprove(unapproved.map((i) => ({ kind: i.kind, cadence_level: i.cadence_level })))}
          >
            {approve.isPending ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
            {approve.isPending
              ? t("settings:collectionsTab.templates.approving")
              : t("settings:collectionsTab.templates.approveAll", { count: unapproved.length })}
          </GuardedButton>
        ) : null
      }
    >
      {templates.isLoading ? (
        <LoadingRows rows={4} />
      ) : templates.error ? (
        <ErrorBanner error={templates.error} />
      ) : !data || slots.length === 0 ? (
        <EmptyState>{t("settings:collectionsTab.templates.empty")}</EmptyState>
      ) : (
        <div className="flex flex-col gap-5">
          {unapproved.length === 0 ? (
            <p className="flex items-center gap-2 rounded-lg bg-pos-soft px-3 py-2 text-caption text-pos-text">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              {data.approved_at
                ? t("settings:collectionsTab.templates.approved", {
                    date: formatDateTime(data.approved_at),
                  })
                : t("settings:collectionsTab.templates.allApproved")}
            </p>
          ) : (
            <p className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-caption text-warn-text">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              {t("settings:collectionsTab.templates.someUnapproved", { count: unapproved.length })}
            </p>
          )}

          {missingDays.length > 0 ? (
            <div className="flex flex-col gap-2 rounded-lg border border-dashed border-line px-3.5 py-3">
              <p className="text-caption font-medium text-ink-2">
                {t("settings:collectionsTab.templates.missingTitle")}
              </p>
              <p className="text-caption text-ink-3">
                {t("settings:collectionsTab.templates.missingHint")}
              </p>
              <div className="flex flex-wrap gap-2">
                {missingDays.map((day) => (
                  <GuardedButton
                    key={day}
                    guard={guard}
                    variant="secondary"
                    size="sm"
                    className="min-h-9"
                    onClick={() => createFor(day)}
                  >
                    <Plus />
                    {t("settings:collectionsTab.templates.createFor", {
                      label: cadenceLabel(t, day),
                    })}
                  </GuardedButton>
                ))}
              </div>
            </div>
          ) : null}

          <div className="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
            <nav aria-label={t("settings:collectionsTab.templates.list")}>
              <ul className="flex flex-col gap-1">
                {slots.map((key) => {
                  const item = current(key);
                  if (!item) return null;
                  const isDirty = dirtyKeys.includes(key);
                  const base = serverByKey.get(key);
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        onClick={() => setSelected(key)}
                        className={cn(
                          "flex min-h-11 w-full flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left transition-[background-color,transform] duration-150 ease-out active:scale-[0.98]",
                          key === selected ? "bg-brand-soft" : "hover:bg-paper-2",
                        )}
                      >
                        <span
                          className={cn(
                            "text-caption font-medium",
                            key === selected ? "text-brand-deep" : "text-ink",
                          )}
                        >
                          {t(KIND_LABEL[item.kind])}
                        </span>
                        <span className="flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
                          {item.cadence_level === null
                            ? t("settings:collectionsTab.templates.anyLevel")
                            : cadenceLabel(t, item.cadence_level)}
                          {isDirty ? (
                            <Badge variant="warn" className="px-1.5 py-0">
                              {t("settings:collectionsTab.templates.edited")}
                            </Badge>
                          ) : item.channel === "whatsapp" ? null : isApproved(base, setApproved) ? (
                            <Badge variant="success" className="px-1.5 py-0">
                              {t("settings:collectionsTab.templates.approvedBadge")}
                            </Badge>
                          ) : (
                            <Badge variant="warn" className="px-1.5 py-0">
                              {t("settings:collectionsTab.templates.unapprovedBadge")}
                            </Badge>
                          )}
                          {!item.is_active ? (
                            <Badge variant="muted" className="px-1.5 py-0">
                              {t("settings:collectionsTab.templates.inactive")}
                            </Badge>
                          ) : null}
                          {item.channel === "whatsapp" ? (
                            <Badge variant="muted" className="px-1.5 py-0">
                              {t("settings:collectionsTab.templates.whatsappSoon")}
                            </Badge>
                          ) : base?.is_default ? (
                            <span>· {t("settings:collectionsTab.templates.default")}</span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </nav>

            {tpl && selected ? (
              <div className="flex min-w-0 flex-col gap-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-body font-medium text-ink">
                    {slotLabel(t, tpl.kind, tpl.cadence_level)}
                  </h3>
                  <DisabledHint hint={readOnly ? guard.reason : null}>
                    <label className="flex min-h-9 items-center gap-2 text-caption text-ink-2">
                      <Switch
                        checked={tpl.is_active}
                        disabled={readOnly || isWhatsapp}
                        onCheckedChange={(v) => edit(selected, { is_active: v })}
                      />
                      {t("settings:collectionsTab.templates.active")}
                    </label>
                  </DisabledHint>
                </div>

                {!isWhatsapp ? (
                  <div
                    className={cn(
                      "flex flex-wrap items-center justify-between gap-2 rounded-lg px-3 py-2 text-caption",
                      selectedApproved && !selectedDirty ? "bg-pos-soft text-pos-text" : "bg-warn-soft text-warn-text",
                    )}
                  >
                    <span className="flex min-w-0 items-start gap-2">
                      {selectedApproved && !selectedDirty ? (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                      ) : (
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                      )}
                      <span>
                        {selectedDirty
                          ? t("settings:collectionsTab.templates.editedWillUnapprove")
                          : selectedApproved
                            ? selectedBase?.approved_at
                              ? selectedBase.approved_by_name
                                ? t("settings:collectionsTab.templates.approvedOnBy", {
                                    date: formatDateTime(selectedBase.approved_at),
                                    name: selectedBase.approved_by_name,
                                  })
                                : t("settings:collectionsTab.templates.approvedOn", {
                                    date: formatDateTime(selectedBase.approved_at),
                                  })
                              : t("settings:collectionsTab.templates.approvedBadge")
                            : t("settings:collectionsTab.templates.unapprovedOne")}
                      </span>
                    </span>
                    {!selectedApproved || selectedDirty ? (
                      <GuardedButton
                        guard={approveOneGuard}
                        variant="secondary"
                        size="sm"
                        className="min-h-9"
                        disabled={approve.isPending}
                        onClick={() =>
                          tpl && onApprove([{ kind: tpl.kind, cadence_level: tpl.cadence_level }])
                        }
                      >
                        <CheckCircle2 />
                        {t("settings:collectionsTab.templates.approveOne")}
                      </GuardedButton>
                    ) : null}
                  </div>
                ) : null}

                <Field label={t("settings:collectionsTab.templates.subject")} htmlFor="tpl-subject">
                  <Input
                    id="tpl-subject"
                    ref={subjectRef}
                    value={tpl.subject}
                    disabled={readOnly || isWhatsapp}
                    onFocus={() => (lastField.current = "subject")}
                    onChange={(e) => edit(selected, { subject: e.target.value })}
                  />
                </Field>
                <Field label={t("settings:collectionsTab.templates.body")} htmlFor="tpl-body">
                  <Textarea
                    id="tpl-body"
                    ref={bodyRef}
                    rows={12}
                    value={tpl.body}
                    disabled={readOnly || isWhatsapp}
                    onFocus={() => (lastField.current = "body")}
                    onChange={(e) => edit(selected, { body: e.target.value })}
                    className="font-sans leading-relaxed"
                  />
                </Field>

                {unknown.length > 0 ? (
                  <p className="rounded-lg bg-neg-soft px-3 py-2 text-caption text-neg-text">
                    {t("settings:collectionsTab.templates.unknownVariables", {
                      list: unknown.map((n) => `{{${n}}}`).join(", "),
                      interpolation: { escapeValue: false },
                    })}
                  </p>
                ) : null}
                {missingMark ? (
                  <p className="rounded-lg bg-neg-soft px-3 py-2 text-caption text-neg-text">
                    {t("settings:collectionsTab.templates.inquiryToken", {
                      inquiry_token: "{{inquiry_token}}",
                      interpolation: { escapeValue: false },
                    })}
                  </p>
                ) : null}

                <div className="flex flex-col gap-2">
                  <p className="text-caption font-medium text-ink-3">
                    {t("settings:collectionsTab.templates.variables")}
                    <span className="ml-2 font-normal">
                      {t("settings:collectionsTab.templates.variablesHint")}
                    </span>
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {variables.map((name) => {
                      const copy = VARIABLE_COPY[name];
                      return (
                        <button
                          key={name}
                          type="button"
                          disabled={readOnly || isWhatsapp}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => insertVariable(name)}
                          title={copy ? t(copy.label) : name}
                          className="inline-flex min-h-8 items-center gap-1 rounded-md border border-line bg-bone px-2 py-1 text-[12px] text-ink-2 transition-[background-color,border-color,transform] duration-150 ease-out hover:border-line-strong hover:bg-paper-2 active:scale-[0.96] disabled:pointer-events-none disabled:opacity-50"
                        >
                          <span className="tabular-nums text-brand-deep">{`{{${name}}}`}</span>
                          {copy ? <span className="hidden text-ink-3 sm:inline">· {t(copy.label)}</span> : null}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="flex flex-col gap-2 rounded-lg border border-line bg-paper-2 p-4">
                  <p className="flex items-center gap-1.5 text-caption font-medium text-ink-3">
                    <Eye className="h-3.5 w-3.5" />
                    {t("settings:collectionsTab.templates.preview")}
                    <span className="font-normal">· {t("settings:collectionsTab.templates.previewHint")}</span>
                  </p>
                  <p className="break-words text-body font-medium text-ink">{render(tpl.subject)}</p>
                  <p className="whitespace-pre-wrap break-words text-body text-ink-2">{render(tpl.body)}</p>
                </div>
              </div>
            ) : null}
          </div>

          <ErrorBanner error={save.error || approve.error} />

          <div className="flex flex-col-reverse gap-2 border-t border-line pt-4 sm:flex-row sm:justify-end">
            {dirty ? (
              <Button
                type="button"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => setDrafts({})}
              >
                {t("settings:collectionsTab.templates.discard")}
              </Button>
            ) : null}
            <GuardedButton
              guard={guard}
              variant="secondary"
              className="min-h-10"
              disabled={!dirty || save.isPending || unknown.length > 0 || missingMark}
              onClick={onSave}
            >
              {save.isPending ? <Loader2 className="animate-spin" /> : null}
              {save.isPending
                ? t("settings:collectionsTab.templates.saving")
                : t("settings:collectionsTab.templates.save")}
            </GuardedButton>
          </div>
        </div>
      )}
    </SectionCard>
  );
}
