/**
 * Mantenedor → Cuentas de proveedor (`/platform/provider-accounts`, v12 02 §5).
 *
 * Every Forpay credential set RADAL holds — RADAL's own mall account or an
 * insurer's own one. Per account:
 *
 * - identity + connection fields (create `POST`, edit `PUT`);
 * - secrets are WRITE-ONLY: the server only says which keys are set
 *   (`secret_keys`) — the form shows "Guardado ✓" per key, an empty input keeps
 *   the stored value and "Borrar" sends `""` to clear it;
 * - the webhook URL per kind (one generic webhook, `?account=&sig=&kind=`) with
 *   a copy button;
 * - Verificar (`POST …/verify`, a provider login) and Rotar secreto
 *   (`POST …/rotate-webhook-secret`, behind a confirm: the previous secret
 *   keeps validating for 30 days).
 *
 * Platform users only — nothing here touches a broker endpoint.
 */
import * as React from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  CheckCircle2,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  RotateCcw,
  ShieldCheck,
  Trash2,
  Undo2,
  Webhook,
  XCircle,
} from "lucide-react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import { Textarea } from "@/components/ui/textarea";
import { Stagger, fadeUpItem } from "@/components/common/motion";
import { CopyButton, EmptyState, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import {
  PROVIDER_ACCOUNT_STATUSES,
  PROVIDER_ENVIRONMENTS,
  PROVIDER_OWNERS,
  SECRET_KEYS,
  useCreateProviderAccount,
  useInsurerProfiles,
  useProviderAccounts,
  useRotateWebhookSecret,
  useUpdateProviderAccount,
  useVerifyProviderAccount,
  verifyStatusOf,
  type ProviderAccount,
  type ProviderAccountCreate,
  type ProviderAccountSecrets,
  type ProviderAccountStatus,
  type ProviderEnvironment,
  type ProviderOwner,
  type SecretKey,
  type SignatureFields,
  type ProviderVerifyStatus,
  type VerifyResult,
} from "@/api/platform";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Field, GuardedButton } from "@/pages/settings/components/shared";
import { ConfirmDialog } from "@/pages/settings/components/ConfirmDialog";
import { motion } from "framer-motion";
import {
  ACCOUNT_STATUS_LABEL,
  ACCOUNT_STATUS_TONE,
  ENVIRONMENT_LABEL,
  ENVIRONMENT_TONE,
  OWNER_LABEL,
  PlatformHeader,
  SECRET_LABEL,
  WEBHOOK_KIND_LABEL,
  labelFrom,
  usePlatformGuards,
} from "./components/shared";

type TFn = (key: string, options?: Record<string, unknown>) => string;

const NONE = "__none__";

export default function PlatformProviderAccountsPage() {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const guards = usePlatformGuards();
  const list = useProviderAccounts();
  const [form, setForm] = React.useState<{ open: boolean; account: ProviderAccount | null }>({
    open: false,
    account: null,
  });
  const rows = list.data ?? [];

  const openCreate = () => setForm({ open: true, account: null });

  return (
    <>
      <PlatformHeader
        title={t("providerAccounts.title")}
        subtitle={t("providerAccounts.description")}
        actions={
          <GuardedButton guard={guards.create} className="min-h-10" onClick={openCreate}>
            <Plus />
            {t("providerAccounts.add")}
          </GuardedButton>
        }
      />

      <ErrorBanner error={list.error} className="mb-4" />

      {list.isLoading ? (
        <Card className="p-5">
          <LoadingRows rows={4} />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            icon={<KeyRound />}
            title={t("providerAccounts.empty")}
            hint={t("providerAccounts.emptyHint")}
          />
        </Card>
      ) : (
        <Stagger className="flex flex-col gap-4">
          {rows.map((account) => (
            <motion.div key={account.id} variants={fadeUpItem}>
              <AccountCard account={account} onEdit={() => setForm({ open: true, account })} />
            </motion.div>
          ))}
        </Stagger>
      )}

      <AccountSheet
        open={form.open}
        account={form.account}
        onOpenChange={(open) => setForm((prev) => ({ ...prev, open }))}
      />
    </>
  );
}

// --- One account ------------------------------------------------------------------------

const VERIFY_LABEL: Record<ProviderVerifyStatus, string> = {
  ok: "platform:providerAccounts.verifyState.ok",
  failed: "platform:providerAccounts.verifyState.failed",
  never: "platform:providerAccounts.verifyState.never",
};

const VERIFY_TONE: Record<ProviderVerifyStatus, NonNullable<BadgeProps["variant"]>> = {
  ok: "success",
  failed: "danger",
  never: "neutral",
};

function AccountCard({ account, onEdit }: { account: ProviderAccount; onEdit: () => void }) {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const guards = usePlatformGuards();
  const verify = useVerifyProviderAccount();
  const rotate = useRotateWebhookSecret();
  const [result, setResult] = React.useState<VerifyResult | null>(null);
  const [confirmRotate, setConfirmRotate] = React.useState(false);

  const runVerify = () =>
    verify.mutate(account.id, {
      onSuccess: (res) => {
        setResult(res);
        if (res.ok) toast.success(t("providerAccounts.verifiedOk"));
        else toast.error(t("providerAccounts.verifyFailed"));
      },
      onError: (error) => toast.error(apiError(error, t("providerAccounts.verifyFailed"))),
    });

  const runRotate = () =>
    rotate.mutate(account.id, {
      onSuccess: () => {
        toast.success(t("providerAccounts.rotated"));
        setConfirmRotate(false);
      },
      onError: (error) => toast.error(apiError(error)),
    });

  // The card reflects the LAST verification result — a failed Verificar must
  // never leave "Verificada el …" on screen. Prefer the fresh mutation result.
  const verifyStatus = result
    ? result.ok
      ? "ok"
      : "failed"
    : verifyStatusOf(account);
  const lastVerifiedAt = result?.account?.last_verified_at ?? account.last_verified_at;
  const verificationText =
    verifyStatus === "ok"
      ? lastVerifiedAt
        ? t("providerAccounts.lastVerified", { date: formatDateTime(lastVerifiedAt) })
        : t("providerAccounts.verifiedOk")
      : verifyStatus === "failed"
        ? lastVerifiedAt
          ? t("providerAccounts.verifyFailedSince", { date: formatDateTime(lastVerifiedAt) })
          : t("providerAccounts.verifyFailedNever")
        : t("providerAccounts.neverVerified");

  // Webhook URLs are the SERVER's (built on its public API origin) — never
  // composed here from the SPA origin.
  const webhookEntries = Object.entries(account.webhook_urls ?? {});
  if (webhookEntries.length === 0 && account.webhook_url) {
    webhookEntries.push(["payment", account.webhook_url]);
  }

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-h3 tracking-tight text-ink">{account.label}</span>
            <Badge variant={ACCOUNT_STATUS_TONE[account.status] ?? "neutral"} dot>
              {labelFrom(t, ACCOUNT_STATUS_LABEL, account.status)}
            </Badge>
            <Badge variant={ENVIRONMENT_TONE[account.environment] ?? "neutral"}>
              {labelFrom(t, ENVIRONMENT_LABEL, account.environment)}
            </Badge>
            <Badge variant={VERIFY_TONE[verifyStatus]}>{t(VERIFY_LABEL[verifyStatus])}</Badge>
          </p>
          <p className="mt-0.5 text-caption text-ink-3">
            {labelFrom(t, OWNER_LABEL, account.owner)}
            {account.insurer_name ? ` · ${account.insurer_name}` : ""}
            {" · "}
            <span className="break-all">{account.base_url}</span>
          </p>
        </div>
        <div className="flex w-full flex-wrap gap-1.5 sm:w-auto">
          <GuardedButton
            guard={guards.manage}
            variant="secondary"
            size="sm"
            className="min-h-9"
            disabled={verify.isPending}
            onClick={runVerify}
          >
            {verify.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            {verify.isPending ? t("providerAccounts.verifying") : t("providerAccounts.verify")}
          </GuardedButton>
          <GuardedButton
            guard={guards.manage}
            variant="ghost"
            size="sm"
            className="min-h-9"
            onClick={() => setConfirmRotate(true)}
          >
            <RotateCcw />
            {t("providerAccounts.rotate")}
          </GuardedButton>
          <GuardedButton guard={guards.edit} variant="ghost" size="sm" className="min-h-9" onClick={onEdit}>
            <Pencil />
            {t("common.edit")}
          </GuardedButton>
        </div>
      </div>

      <div className="grid gap-5 px-5 py-4 lg:grid-cols-2">
        {/* Connection facts */}
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2.5 text-caption sm:grid-cols-2">
          <Fact label={t("providerAccounts.form.username")} value={account.username} />
          <Fact label={t("providerAccounts.form.commerce")} value={account.commerce} />
          <Fact label={t("providerAccounts.form.channelHeader")} value={account.channel_header} />
          <Fact label={t("providerAccounts.form.processId")} value={account.process_id} />
          <Fact
            label={t("providerAccounts.form.salesExecutiveId")}
            value={account.sales_executive_id}
          />
          <Fact
            label={t("providerAccounts.verification")}
            value={verificationText}
          />
        </dl>

        {/* Secrets: names only, never values */}
        <div className="flex flex-col gap-2">
          <p className="text-caption font-medium text-ink-2">{t("providerAccounts.secrets")}</p>
          {account.secret_keys.length === 0 ? (
            <p className="text-caption text-ink-3">{t("providerAccounts.noSecrets")}</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {account.secret_keys.map((key) => (
                <li key={key}>
                  <Badge variant="success" className="px-1.5 py-0">
                    {labelFrom(t, SECRET_LABEL, key)} · {t("providerAccounts.form.saved")}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Webhook */}
      <div className="flex flex-col gap-2.5 border-t border-line px-5 py-4">
        <p className="flex items-center gap-2 text-caption font-medium text-ink-2">
          <Webhook className="h-4 w-4 text-ink-3" />
          {t("providerAccounts.webhook")}
          <span className="font-normal text-ink-3">
            {t("providerAccounts.webhookKey")}:{" "}
            <span className="break-all tabular-nums">{account.webhook_key}</span>
          </span>
        </p>
        {webhookEntries.length === 0 ? (
          <p className="text-caption text-ink-3">{t("providerAccounts.noWebhook")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {webhookEntries.map(([kind, url]) => (
              <li
                key={kind}
                className="flex flex-col gap-2 rounded-lg bg-paper-2 px-3 py-2.5 sm:flex-row sm:items-center"
              >
                <span className="shrink-0 text-caption font-medium text-ink-2 sm:w-40">
                  {labelFrom(t, WEBHOOK_KIND_LABEL, kind)}
                </span>
                <code className="min-w-0 flex-1 break-all font-mono text-[12px] leading-5 text-ink-2">
                  {url}
                </code>
                <CopyButton
                  value={url}
                  label={t("common.copyUrl")}
                  className="min-h-9 shrink-0"
                  onCopied={() => toast.success(t("providerAccounts.copied"))}
                />
              </li>
            ))}
          </ul>
        )}
        {account.previous_webhook_secret_valid_until ? (
          <p className="text-caption text-warn-text">
            {t("providerAccounts.previousValid", {
              date: formatDateTime(account.previous_webhook_secret_valid_until),
            })}
          </p>
        ) : null}
      </div>

      {/* Errors and the last verification result */}
      {result ? (
        <div
          role="status"
          className={cn(
            "flex items-start gap-2 border-t border-line px-5 py-3 text-caption",
            result.ok ? "bg-pos-soft text-pos-text" : "bg-neg-soft text-neg-text",
          )}
        >
          {result.ok ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          ) : (
            <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          )}
          <span>
            {result.ok ? t("providerAccounts.verifiedOk") : result.error || t("providerAccounts.verifyFailed")}
          </span>
        </div>
      ) : account.last_error ? (
        <div className="flex items-start gap-2 border-t border-line bg-neg-soft px-5 py-3 text-caption text-neg-text">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="break-words">
            {t("providerAccounts.lastError")}: {account.last_error}
          </span>
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title={t("providerAccounts.rotateTitle", { label: account.label })}
        description={t("providerAccounts.rotateDescription")}
        confirmLabel={t("providerAccounts.rotateConfirm")}
        cancelLabel={t("common.cancel")}
        pending={rotate.isPending}
        onConfirm={runRotate}
      />
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-ink-3">{label}</dt>
      <dd className="mt-0.5 break-words text-ink-2">{value || "—"}</dd>
    </div>
  );
}

// --- Create / edit sheet --------------------------------------------------------------------

/** Plain text (not masked): algorithm names and the JSON list are not credentials. */
const PLAIN_SECRET: ReadonlySet<SecretKey> = new Set(["jwe_alg", "jwe_enc"]);
const MULTILINE_SECRET: ReadonlySet<SecretKey> = new Set(["public_key_pem", "signature_fields"]);

function parseSignatureFields(raw: string): SignatureFields | null {
  try {
    const value: unknown = JSON.parse(raw);
    const isList = (v: unknown) => Array.isArray(v) && v.every((x) => typeof x === "string");
    if (isList(value)) return value as string[];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const entries = Object.values(value as Record<string, unknown>);
      if (entries.every(isList)) return value as Record<string, string[]>;
    }
    return null;
  } catch {
    return null;
  }
}

function accountSchema(t: TFn) {
  return z
    .object({
      label: z.string().trim().min(1, t("providerAccounts.form.labelRequired")).max(120),
      owner: z.enum(["radal_mall", "insurer"]),
      insurer_id: z.string(),
      environment: z.enum(["sandbox", "production"]),
      status: z.enum(["draft", "active", "disabled"]),
      base_url: z.string().trim().max(255),
      commerce: z.string().trim().max(64),
      channel_header: z.string().trim().max(64),
      process_id: z.string().trim().max(16),
      username: z.string().trim().max(128),
      sales_executive_id: z.string().trim().max(64),
      secrets: z.record(z.string(), z.string().optional()),
    })
    .superRefine((values, ctx) => {
      if (values.owner === "insurer" && values.insurer_id === NONE) {
        ctx.addIssue({
          code: "custom",
          path: ["insurer_id"],
          message: t("providerAccounts.form.insurerRequired"),
        });
      }
      const url = values.base_url;
      if (url) {
        const ok = values.environment === "production" ? /^https:\/\//i.test(url) : /^https?:\/\//i.test(url);
        if (!ok) {
          ctx.addIssue({ code: "custom", path: ["base_url"], message: t("providerAccounts.form.invalidUrl") });
        }
      }
      const sig = values.secrets.signature_fields?.trim();
      if (sig && parseSignatureFields(sig) === null) {
        ctx.addIssue({
          code: "custom",
          path: ["secrets", "signature_fields"],
          message: t("providerAccounts.form.invalidJson"),
        });
      }
    });
}

interface AccountValues {
  label: string;
  owner: ProviderOwner;
  insurer_id: string;
  environment: ProviderEnvironment;
  status: ProviderAccountStatus;
  base_url: string;
  commerce: string;
  channel_header: string;
  process_id: string;
  username: string;
  sales_executive_id: string;
  secrets: Record<string, string>;
}

function toValues(account: ProviderAccount | null): AccountValues {
  return {
    label: account?.label ?? "",
    owner: account?.owner ?? "radal_mall",
    insurer_id: account?.insurer_id ? String(account.insurer_id) : NONE,
    environment: account?.environment ?? "sandbox",
    status: account?.status ?? "draft",
    base_url: account?.base_url ?? "",
    commerce: account?.commerce ?? "",
    channel_header: account?.channel_header ?? "",
    process_id: account?.process_id ?? "WHITE_LABEL",
    username: account?.username ?? "",
    sales_executive_id: account?.sales_executive_id ?? "",
    secrets: Object.fromEntries(SECRET_KEYS.map((key) => [key, ""])),
  };
}

const blank = (v: string) => (v.trim() ? v.trim() : null);

/**
 * Edit mode: the server ignores a `null` (it keeps the stored value), so a
 * field the admin emptied must travel as `""` to be cleared. Forpay reads every
 * one of these with an `or` fallback, so a stored `""` behaves as blank.
 */
const editText = (v: string, stored: string | null | undefined) =>
  v.trim() ? v.trim() : stored ? "" : null;

function AccountSheet({
  open,
  account,
  onOpenChange,
}: {
  open: boolean;
  account: ProviderAccount | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const creating = account === null;
  const create = useCreateProviderAccount();
  const update = useUpdateProviderAccount();
  const insurers = useInsurerProfiles({});
  const schema = React.useMemo(() => accountSchema(t), [t]);
  const form = useForm<AccountValues>({ resolver: zodResolver(schema), defaultValues: toValues(account) });
  /** Stored secrets the admin asked to clear on save (sent as `""`). */
  const [cleared, setCleared] = React.useState<Set<SecretKey>>(new Set());

  React.useEffect(() => {
    if (open) {
      form.reset(toValues(account));
      setCleared(new Set());
      create.reset();
      update.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, account]);

  const errors = form.formState.errors;
  const owner = form.watch("owner");
  const pending = create.isPending || update.isPending;
  const stored = new Set<SecretKey>(account?.secret_keys ?? []);

  const toggleClear = (key: SecretKey) =>
    setCleared((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else {
        next.add(key);
        form.setValue(`secrets.${key}`, "");
      }
      return next;
    });

  const onSubmit = form.handleSubmit((values) => {
    const secrets: ProviderAccountSecrets = {};
    for (const key of SECRET_KEYS) {
      const raw = values.secrets[key]?.trim() ?? "";
      if (raw) {
        if (key === "signature_fields") secrets.signature_fields = parseSignatureFields(raw);
        else secrets[key] = raw;
      } else if (cleared.has(key)) {
        if (key === "signature_fields") secrets.signature_fields = [];
        else secrets[key] = "";
      }
    }
    const text = (v: string, stored: string | null | undefined) =>
      creating ? blank(v) : editText(v, stored);
    const insurerId =
      values.owner === "insurer" && values.insurer_id !== NONE ? Number(values.insurer_id) : null;
    const body: ProviderAccountCreate = {
      label: values.label.trim(),
      owner: values.owner,
      insurer_id: insurerId,
      environment: values.environment,
      status: values.status,
      commerce: text(values.commerce, account?.commerce),
      channel_header: text(values.channel_header, account?.channel_header),
      process_id: text(values.process_id, account?.process_id),
      username: text(values.username, account?.username),
      sales_executive_id: text(values.sales_executive_id, account?.sales_executive_id),
      ...secrets,
    };
    const baseUrl = blank(values.base_url);
    if (baseUrl) body.base_url = baseUrl;

    if (creating) {
      create.mutate(body, {
        onSuccess: () => {
          toast.success(t("providerAccounts.form.created"));
          onOpenChange(false);
        },
      });
    } else {
      update.mutate(
        { id: account.id, payload: body },
        {
          onSuccess: () => {
            toast.success(t("providerAccounts.form.updated"));
            onOpenChange(false);
          },
        },
      );
    }
  });

  return (
    <Sheet open={open} onOpenChange={(next) => (pending ? undefined : onOpenChange(next))}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b border-line px-5 py-4 pr-12">
          <SheetTitle>
            {creating
              ? t("providerAccounts.form.createTitle")
              : t("providerAccounts.form.editTitle", { label: account?.label ?? "" })}
          </SheetTitle>
          <SheetDescription>{t("providerAccounts.form.description")}</SheetDescription>
        </SheetHeader>

        <form onSubmit={onSubmit} noValidate className="flex flex-1 flex-col">
          <div className="flex flex-1 flex-col gap-6 px-5 py-5">
            {/* Identity */}
            <section className="flex flex-col gap-3">
              <h3 className="text-body font-medium text-ink">{t("providerAccounts.form.identityTitle")}</h3>
              <Field label={t("providerAccounts.form.label")} htmlFor="pa-label" error={errors.label?.message}>
                <Input id="pa-label" autoComplete="off" {...form.register("label")} />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t("providerAccounts.form.owner")}>
                  <Controller
                    control={form.control}
                    name="owner"
                    render={({ field }) => (
                      <Select value={field.value} onValueChange={field.onChange}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PROVIDER_OWNERS.map((o) => (
                            <SelectItem key={o} value={o}>
                              {t(OWNER_LABEL[o])}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                </Field>
                {owner === "insurer" ? (
                  <Field label={t("providerAccounts.form.insurer")} error={errors.insurer_id?.message}>
                    <Controller
                      control={form.control}
                      name="insurer_id"
                      render={({ field }) => (
                        <Select value={field.value} onValueChange={field.onChange} disabled={insurers.isLoading}>
                          <SelectTrigger>
                            <SelectValue placeholder={t("providerAccounts.form.insurerPlaceholder")} />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={NONE}>{t("providerAccounts.form.insurerPlaceholder")}</SelectItem>
                            {(insurers.data ?? []).map((p) => (
                              <SelectItem key={p.insurer_id} value={String(p.insurer_id)}>
                                {p.insurer_name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  </Field>
                ) : null}
                <Field label={t("providerAccounts.form.environment")}>
                  <Controller
                    control={form.control}
                    name="environment"
                    render={({ field }) => (
                      <Select value={field.value} onValueChange={field.onChange}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PROVIDER_ENVIRONMENTS.map((env) => (
                            <SelectItem key={env} value={env}>
                              {t(ENVIRONMENT_LABEL[env])}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                </Field>
                <Field label={t("providerAccounts.form.status")} hint={t("providerAccounts.form.statusHint")}>
                  <Controller
                    control={form.control}
                    name="status"
                    render={({ field }) => (
                      <Select value={field.value} onValueChange={field.onChange}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PROVIDER_ACCOUNT_STATUSES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {t(ACCOUNT_STATUS_LABEL[s])}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                </Field>
              </div>
            </section>

            {/* Connection */}
            <section className="flex flex-col gap-3">
              <h3 className="text-body font-medium text-ink">{t("providerAccounts.form.connectionTitle")}</h3>
              <Field
                label={t("providerAccounts.form.baseUrl")}
                htmlFor="pa-base"
                hint={creating ? t("providerAccounts.form.baseUrlHint") : t("providerAccounts.form.baseUrlHintEdit")}
                error={errors.base_url?.message}
              >
                <Input id="pa-base" autoComplete="off" inputMode="url" {...form.register("base_url")} />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t("providerAccounts.form.username")} htmlFor="pa-user">
                  <Input id="pa-user" autoComplete="off" {...form.register("username")} />
                </Field>
                <Field label={t("providerAccounts.form.commerce")} htmlFor="pa-commerce">
                  <Input id="pa-commerce" autoComplete="off" {...form.register("commerce")} />
                </Field>
                <Field label={t("providerAccounts.form.channelHeader")} htmlFor="pa-channel">
                  <Input id="pa-channel" autoComplete="off" {...form.register("channel_header")} />
                </Field>
                <Field label={t("providerAccounts.form.processId")} htmlFor="pa-process">
                  <Input id="pa-process" autoComplete="off" {...form.register("process_id")} />
                </Field>
                <Field label={t("providerAccounts.form.salesExecutiveId")} htmlFor="pa-sales">
                  <Input id="pa-sales" autoComplete="off" {...form.register("sales_executive_id")} />
                </Field>
              </div>
            </section>

            {/* Secrets — write-only */}
            <section className="flex flex-col gap-3">
              <div>
                <h3 className="text-body font-medium text-ink">{t("providerAccounts.form.secretsTitle")}</h3>
                <p className="text-caption text-ink-3">{t("providerAccounts.form.secretsHint")}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {SECRET_KEYS.map((key) => {
                  const isStored = stored.has(key);
                  const willClear = cleared.has(key);
                  const fieldError = (
                    errors.secrets as Record<string, { message?: string }> | undefined
                  )?.[key]?.message;
                  const id = `pa-secret-${key}`;
                  return (
                    <Field
                      key={key}
                      htmlFor={id}
                      className={MULTILINE_SECRET.has(key) ? "sm:col-span-2" : undefined}
                      error={fieldError}
                      hint={
                        willClear
                          ? t("providerAccounts.form.willClear")
                          : key === "signature_fields"
                            ? t("providerAccounts.form.signatureFieldsHint")
                            : undefined
                      }
                      label={
                        <span className="flex flex-wrap items-center gap-2">
                          {t(SECRET_LABEL[key])}
                          {isStored && !willClear ? (
                            <Badge variant="success" className="px-1.5 py-0">
                              {t("providerAccounts.form.saved")}
                            </Badge>
                          ) : null}
                          {isStored ? (
                            <button
                              type="button"
                              onClick={() => toggleClear(key)}
                              className="inline-flex min-h-7 items-center gap-1 rounded-md px-1.5 text-caption text-ink-3 transition-[color,background-color,transform] duration-150 ease-out hover:bg-paper-2 hover:text-ink active:scale-[0.96]"
                            >
                              {willClear ? <Undo2 className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
                              {willClear ? t("providerAccounts.form.undo") : t("providerAccounts.form.clear")}
                            </button>
                          ) : null}
                        </span>
                      }
                    >
                      {MULTILINE_SECRET.has(key) ? (
                        <Textarea
                          id={id}
                          rows={key === "public_key_pem" ? 5 : 3}
                          className="font-mono text-[12px]"
                          spellCheck={false}
                          readOnly={willClear}
                          placeholder={isStored ? t("providerAccounts.form.keepPlaceholder") : undefined}
                          {...form.register(`secrets.${key}`)}
                        />
                      ) : (
                        <Input
                          id={id}
                          type={PLAIN_SECRET.has(key) ? "text" : "password"}
                          autoComplete="new-password"
                          spellCheck={false}
                          readOnly={willClear}
                          placeholder={isStored ? "••••••••" : undefined}
                          {...form.register(`secrets.${key}`)}
                        />
                      )}
                    </Field>
                  );
                })}
              </div>
            </section>

            <ErrorBanner error={create.error || update.error || insurers.error} />
          </div>

          <SheetFooter className="sticky bottom-0 gap-2 border-t border-line bg-bone px-5 py-4 sm:gap-0">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? <Loader2 className="animate-spin" /> : null}
              {pending ? t("common.saving") : creating ? t("providerAccounts.add") : t("common.save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
