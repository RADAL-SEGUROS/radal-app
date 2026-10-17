import * as React from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  CheckCircle2,
  Loader2,
  Mail,
  MailPlus,
  Pause,
  Pencil,
  Play,
  PlugZap,
  Server,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
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
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { DisabledHint, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import {
  MAIL_SECURITIES,
  defaultPort,
  useCreateMailAccount,
  useDeleteMailAccount,
  useMailAccounts,
  useTestMailAccount,
  useUpdateMailAccount,
  type MailAccount,
  type MailAccountStatus,
  type MailCheck,
  type MailOwner,
  type MailProvider,
  type MailProviderOption,
  type MailSecurity,
} from "@/api/mail";
import { useCan } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, Field, GuardedButton, ReadOnlyField, SectionCard, type Guard } from "./shared";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * Configuración → Correo (v12, 04-mail §3).
 *
 * 1. The Radal sender — the virtual `id: null` entry of `GET /mail/accounts`,
 *    read-only, always present.
 * 2. The broker's own SMTP/IMAP mailboxes: create / edit (password write-only,
 *    "Guardada ✓"), Probar conexión → `POST /mail/accounts/{id}/test`, pause,
 *    disconnect. Each control is gated by `Mail.Create` AND the row's own
 *    server-computed `can_manage` (a shared box needs Settings.Manage).
 * 3. The provider picker from the same response: custom is wired; Google and
 *    Microsoft come back `available: false` and render disabled with the
 *    server's "pronto" reason.
 */

// Explicit maps — never `t(\`status.${x}\`)` (dynamic keys render raw).
const STATUS_LABEL: Record<MailAccountStatus, string> = {
  draft: "mail:status.draft",
  connected: "mail:status.connected",
  error: "mail:status.error",
  paused: "mail:status.paused",
};
const STATUS_TONE: Record<MailAccountStatus, NonNullable<BadgeProps["variant"]>> = {
  draft: "neutral",
  connected: "success",
  error: "danger",
  paused: "muted",
};
const OWNER_LABEL: Record<MailOwner, string> = {
  me: "mail:owner.me",
  shared: "mail:owner.shared",
  other: "mail:owner.other",
  radal: "mail:owner.radal",
};
const SECURITY_LABEL: Record<MailSecurity, string> = {
  ssl: "mail:security.ssl",
  starttls: "mail:security.starttls",
  none: "mail:security.none",
};
const PROVIDER_COPY: Partial<Record<MailProvider, { name: string; description: string }>> = {
  custom: { name: "mail:providers.custom.name", description: "mail:providers.custom.description" },
  google: { name: "mail:providers.google.name", description: "mail:providers.google.description" },
  microsoft: {
    name: "mail:providers.microsoft.name",
    description: "mail:providers.microsoft.description",
  },
};

export function MailTab() {
  const { t } = useTranslation(["mail", "common"]);
  const canView = useCan("Mail", "View");
  const canCreate = useCan("Mail", "Create");
  const canAdmin = useCan("Settings", "Manage");
  const accounts = useMailAccounts(canView.allowed);

  const [dialog, setDialog] = React.useState<{ open: boolean; account: MailAccount | null }>({
    open: false,
    account: null,
  });
  const [checks, setChecks] = React.useState<Record<number, MailCheck>>({});

  if (!canView.isLoading && !canView.allowed) {
    return (
      <Card className="p-6">
        <EmptyState>{t("mail:noView")}</EmptyState>
      </Card>
    );
  }

  const items = accounts.data?.items ?? [];
  const radal = items.find((a) => a.kind === "radal_system") ?? null;
  const own = items.filter((a) => a.id !== null);
  const providers = accounts.data?.providers ?? [];

  const createGuard: Guard = {
    allowed: canCreate.allowed,
    reason: t("mail:guard.noCreate"),
  };

  const openCreate = () => setDialog({ open: true, account: null });

  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-3xl text-body text-ink-3">{t("mail:tab.description")}</p>
      <ErrorBanner error={accounts.error} />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <RadalSenderCard account={radal} loading={accounts.isLoading} />
        <ProvidersCard
          providers={providers}
          loading={accounts.isLoading}
          guard={createGuard}
          onConnectCustom={openCreate}
        />
      </div>

      <SectionCard
        title={t("mail:accounts.title")}
        icon={<Server />}
        description={t("mail:accounts.description")}
        actions={
          <GuardedButton guard={createGuard} size="sm" onClick={openCreate}>
            <MailPlus />
            {t("mail:actions.add")}
          </GuardedButton>
        }
      >
        {accounts.isLoading ? (
          <LoadingRows rows={2} />
        ) : own.length === 0 ? (
          <EmptyState>{t("mail:accounts.empty")}</EmptyState>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {own.map((account) => (
              <AccountRow
                key={account.id}
                account={account}
                canCreate={canCreate.allowed}
                check={account.id !== null ? checks[account.id] : undefined}
                onChecked={(check) =>
                  account.id !== null && setChecks((prev) => ({ ...prev, [account.id as number]: check }))
                }
                onEdit={() => setDialog({ open: true, account })}
              />
            ))}
          </ul>
        )}
      </SectionCard>

      <MailAccountDialog
        open={dialog.open}
        account={dialog.account}
        canShare={canAdmin.allowed}
        onOpenChange={(open) => setDialog((prev) => ({ ...prev, open }))}
      />
    </div>
  );
}

// --- Radal sender -------------------------------------------------------------

function RadalSenderCard({ account, loading }: { account: MailAccount | null; loading: boolean }) {
  const { t } = useTranslation("mail");
  const address = account?.address || "contacto@radalseguros.cl";
  const description =
    account?.transport === "smtp"
      ? t("radal.descriptionSmtp", { address })
      : t("radal.description", { address });
  return (
    <SectionCard
      title={t("radal.title")}
      icon={<ShieldCheck />}
      description={description}
      actions={<Badge variant="muted">{t("radal.readOnly")}</Badge>}
    >
      {loading || !account ? (
        <LoadingRows rows={2} />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <ReadOnlyField label={t("radal.address")} value={account.address} />
            <ReadOnlyField label={t("radal.displayName")} value={account.display_name || "—"} />
            {account.transport_label ? (
              <ReadOnlyField label={t("radal.transport")} value={account.transport_label} />
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={STATUS_TONE[account.status]} dot>
              {t(STATUS_LABEL[account.status])}
            </Badge>
          </div>
          {!account.mail_enabled ? (
            <p className="rounded-lg bg-warn-soft px-3 py-2 text-caption text-warn-text">
              {t("radal.sendingOff")}
            </p>
          ) : null}
        </div>
      )}
    </SectionCard>
  );
}

// --- Provider picker ------------------------------------------------------------

function ProvidersCard({
  providers,
  loading,
  guard,
  onConnectCustom,
}: {
  providers: MailProviderOption[];
  loading: boolean;
  guard: Guard;
  onConnectCustom: () => void;
}) {
  const { t } = useTranslation(["mail", "common"]);
  return (
    <SectionCard
      title={t("mail:providers.title")}
      icon={<PlugZap />}
      description={t("mail:providers.description")}
    >
      {loading ? (
        <LoadingRows rows={3} />
      ) : (
        <ul className="flex flex-col gap-2.5">
          {providers.map((option) => {
            const copy = PROVIDER_COPY[option.provider];
            if (!copy) return null;
            const reason = option.reason || t("mail:providers.unavailable");
            return (
              <li
                key={option.provider}
                className={cn(
                  "flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line px-3.5 py-3",
                  !option.available && "bg-paper-2",
                )}
              >
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-body font-medium text-ink">
                    {t(copy.name)}
                    {!option.available ? (
                      <Badge variant="muted" className="px-1.5 py-0">
                        {t("common:nav.soon")}
                      </Badge>
                    ) : null}
                  </p>
                  <p className="text-caption text-ink-3">
                    {option.available ? t(copy.description) : reason}
                  </p>
                </div>
                {option.available ? (
                  <GuardedButton guard={guard} variant="secondary" size="sm" onClick={onConnectCustom}>
                    {t("mail:providers.connect")}
                  </GuardedButton>
                ) : (
                  <DisabledHint hint={reason}>
                    <Button variant="secondary" size="sm" disabled>
                      {t("mail:providers.connect")}
                    </Button>
                  </DisabledHint>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}

// --- One mailbox --------------------------------------------------------------------

function AccountRow({
  account,
  canCreate,
  check,
  onChecked,
  onEdit,
}: {
  account: MailAccount;
  canCreate: boolean;
  check: MailCheck | undefined;
  onChecked: (check: MailCheck) => void;
  onEdit: () => void;
}) {
  const { t } = useTranslation(["mail", "common"]);
  const test = useTestMailAccount();
  const update = useUpdateMailAccount();
  const remove = useDeleteMailAccount();
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const id = account.id as number;

  const guard: Guard = canCreate
    ? { allowed: account.can_manage, reason: t("mail:guard.noManage") }
    : { allowed: false, reason: t("mail:guard.noCreate") };

  const runTest = () =>
    test.mutate(id, {
      onSuccess: (result) => {
        onChecked(result.check);
        if (result.check.ok) toast.success(t("mail:toast.testOk"));
        else toast.error(t("mail:toast.testFailed"));
      },
      onError: (error) => toast.error(apiError(error, t("mail:toast.testFailed"))),
    });

  const togglePause = () => {
    const pausing = account.status !== "paused";
    update.mutate(
      { id, payload: { paused: pausing } },
      {
        onSuccess: () => toast.success(t(pausing ? "mail:toast.paused" : "mail:toast.resumed")),
        onError: (error) => toast.error(apiError(error)),
      },
    );
  };

  return (
    <li className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2">
            <Mail className="h-4 w-4 shrink-0 text-ink-3" />
            <span className="break-all text-body font-medium text-ink">{account.address}</span>
            <Badge variant={STATUS_TONE[account.status]} dot>
              {t(STATUS_LABEL[account.status])}
            </Badge>
            <Badge variant="outline">{t(OWNER_LABEL[account.owner])}</Badge>
          </p>
          {account.display_name ? (
            <p className="mt-0.5 text-caption text-ink-3">{account.display_name}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <GuardedButton
            guard={guard}
            variant="secondary"
            size="sm"
            className="min-h-9"
            disabled={test.isPending || account.status === "paused"}
            onClick={runTest}
          >
            {test.isPending ? <Loader2 className="animate-spin" /> : <PlugZap />}
            {test.isPending ? t("mail:actions.testing") : t("mail:actions.test")}
          </GuardedButton>
          <GuardedButton guard={guard} variant="ghost" size="sm" onClick={onEdit}>
            <Pencil />
            {t("mail:actions.edit")}
          </GuardedButton>
          <GuardedButton
            guard={guard}
            variant="ghost"
            size="sm"
            disabled={update.isPending}
            onClick={togglePause}
          >
            {account.status === "paused" ? <Play /> : <Pause />}
            {account.status === "paused" ? t("mail:actions.resume") : t("mail:actions.pause")}
          </GuardedButton>
          <GuardedButton
            guard={guard}
            variant="ghost"
            size="sm"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 />
            {t("mail:actions.delete")}
          </GuardedButton>
        </div>
      </div>

      <div className="grid gap-2 text-caption text-ink-3 sm:grid-cols-2">
        <ServerLine
          label={t("mail:fields.smtpTitle")}
          on={account.send_enabled}
          host={account.smtp_host}
          port={account.smtp_port}
          security={account.smtp_security}
        />
        <ServerLine
          label={t("mail:fields.imapTitle")}
          on={account.receive_enabled}
          host={account.imap_host}
          port={account.imap_port}
          security={account.imap_security}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-3">
        <span>
          {account.has_secret ? (
            <span className="text-pos-text">{t("mail:fields.passwordSaved")}</span>
          ) : (
            t("mail:fields.passwordNone")
          )}
        </span>
        {account.last_sync_at ? (
          <span>{t("mail:accounts.lastSync", { date: formatDateTime(account.last_sync_at) })}</span>
        ) : null}
      </div>

      {account.last_error && account.status === "error" ? (
        <p className="rounded-lg bg-neg-soft px-3 py-2 text-caption text-neg-text">
          {account.last_error}
        </p>
      ) : null}

      {check ? <CheckResult check={check} /> : null}

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("mail:delete.title", { address: account.address })}
        description={t("mail:delete.description")}
        confirmLabel={t("mail:delete.confirm")}
        cancelLabel={t("mail:actions.cancel")}
        destructive
        pending={remove.isPending}
        onConfirm={() =>
          remove.mutate(id, {
            onSuccess: () => {
              toast.success(t("mail:toast.deleted"));
              setConfirmDelete(false);
            },
            onError: (error) => toast.error(apiError(error)),
          })
        }
      />
    </li>
  );
}

function ServerLine({
  label,
  on,
  host,
  port,
  security,
}: {
  label: string;
  on: boolean;
  host: string | null;
  port: number | null;
  security: MailSecurity | null;
}) {
  const { t } = useTranslation("mail");
  return (
    <div className="min-w-0 rounded-lg bg-paper-2 px-3 py-2">
      <span className="font-medium text-ink-2">{label}</span>{" "}
      {on && host ? (
        <span className="break-all tabular-nums">
          {host}:{port ?? "—"}
          {security ? ` · ${t(SECURITY_LABEL[security])}` : ""}
        </span>
      ) : (
        <span>{t("accounts.off")}</span>
      )}
    </div>
  );
}

function CheckResult({ check }: { check: MailCheck }) {
  const { t } = useTranslation("mail");
  const rows: { label: string; ok: boolean | null; error: string | null }[] = [
    { label: t("fields.smtpTitle"), ok: check.smtp_ok, error: check.smtp_error },
    { label: t("fields.imapTitle"), ok: check.imap_ok, error: check.imap_error },
  ];
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col gap-1.5 rounded-lg px-3 py-2.5 text-caption",
        check.ok ? "bg-pos-soft" : "bg-neg-soft",
      )}
    >
      <p className={cn("font-medium", check.ok ? "text-pos-text" : "text-neg-text")}>
        {check.ok ? t("test.ok") : t("test.failed")}
        {check.checked_at ? (
          <span className="ml-2 font-normal text-ink-3">
            {t("test.checkedAt", { date: formatDateTime(check.checked_at) })}
          </span>
        ) : null}
      </p>
      {rows.map((row) => (
        <p key={row.label} className="flex items-start gap-1.5 text-ink-2">
          {row.ok === null ? (
            <span className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border border-line-strong" />
          ) : row.ok ? (
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pos" />
          ) : (
            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neg" />
          )}
          <span>
            <span className="font-medium">{row.label}:</span>{" "}
            {row.ok === null ? t("test.notChecked") : row.ok ? t("test.ok") : row.error || t("test.failed")}
          </span>
        </p>
      ))}
    </div>
  );
}

// --- Create / edit dialog -------------------------------------------------------------

const PORT_RE = /^\d{1,5}$/;

function buildSchema(t: (key: string) => string, creating: boolean, hasSecret = false) {
  return z
    .object({
      address: z.string().trim(),
      display_name: z.string().optional(),
      username: z.string().optional(),
      password: z.string().optional(),
      smtp_host: z.string().optional(),
      smtp_port: z.string().optional(),
      smtp_security: z.enum(["ssl", "starttls", "none"]),
      imap_host: z.string().optional(),
      imap_port: z.string().optional(),
      imap_security: z.enum(["ssl", "starttls", "none"]),
      send_enabled: z.boolean(),
      receive_enabled: z.boolean(),
      owner: z.enum(["me", "shared"]),
    })
    .superRefine((values, ctx) => {
      if (creating && !z.string().email().safeParse(values.address).success) {
        ctx.addIssue({ code: "custom", path: ["address"], message: t("mail:validation.email") });
      }
      // A mailbox with no stored secret cannot log in: the password is required
      // until the server holds one (edit keeps it when left blank).
      if (!hasSecret && !values.password?.trim()) {
        ctx.addIssue({ code: "custom", path: ["password"], message: t("mail:validation.password") });
      }
      if (!values.send_enabled && !values.receive_enabled) {
        ctx.addIssue({
          code: "custom",
          path: ["receive_enabled"],
          message: t("mail:validation.oneDirection"),
        });
      }
      const checkServer = (prefix: "smtp" | "imap") => {
        const host = values[`${prefix}_host`]?.trim();
        const port = values[`${prefix}_port`]?.trim() ?? "";
        if (!host) {
          ctx.addIssue({ code: "custom", path: [`${prefix}_host`], message: t("mail:validation.host") });
        }
        const n = Number(port);
        if (!PORT_RE.test(port) || n < 1 || n > 65535) {
          ctx.addIssue({ code: "custom", path: [`${prefix}_port`], message: t("mail:validation.port") });
        }
      };
      if (values.send_enabled) checkServer("smtp");
      if (values.receive_enabled) checkServer("imap");
    });
}

type MailFormValues = z.infer<ReturnType<typeof buildSchema>>;

function blankToNull(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function portOrNull(value: string | undefined): number | null {
  const trimmed = value?.trim();
  return trimmed && PORT_RE.test(trimmed) ? Number(trimmed) : null;
}

function initialValues(account: MailAccount | null): MailFormValues {
  return {
    address: account?.address ?? "",
    display_name: account?.display_name ?? "",
    username: account?.username ?? "",
    password: "",
    smtp_host: account?.smtp_host ?? "",
    smtp_port: String(account?.smtp_port ?? defaultPort("smtp", account?.smtp_security ?? "ssl")),
    smtp_security: account?.smtp_security ?? "ssl",
    imap_host: account?.imap_host ?? "",
    imap_port: String(account?.imap_port ?? defaultPort("imap", account?.imap_security ?? "ssl")),
    imap_security: account?.imap_security ?? "ssl",
    send_enabled: account?.send_enabled ?? true,
    receive_enabled: account?.receive_enabled ?? true,
    owner: account?.owner === "shared" ? "shared" : "me",
  };
}

function MailAccountDialog({
  open,
  account,
  canShare,
  onOpenChange,
}: {
  open: boolean;
  account: MailAccount | null;
  canShare: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(["mail", "common"]);
  const creating = account === null;
  const create = useCreateMailAccount();
  const update = useUpdateMailAccount();
  const hasSecret = !!account?.has_secret;
  const schema = React.useMemo(() => buildSchema(t, creating, hasSecret), [t, creating, hasSecret]);
  const form = useForm<MailFormValues>({
    resolver: zodResolver(schema),
    defaultValues: initialValues(account),
  });

  React.useEffect(() => {
    if (open) {
      form.reset(initialValues(account));
      create.reset();
      update.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, account]);

  const errors = form.formState.errors;
  const pending = create.isPending || update.isPending;
  const sendOn = form.watch("send_enabled");
  const receiveOn = form.watch("receive_enabled");

  /** Re-suggest the conventional port when the security mode changes. */
  const onSecurity = (kind: "smtp" | "imap", next: MailSecurity) => {
    const prev = form.getValues(`${kind}_security`);
    const port = form.getValues(`${kind}_port`)?.trim();
    form.setValue(`${kind}_security`, next, { shouldDirty: true });
    if (!port || port === String(defaultPort(kind, prev))) {
      form.setValue(`${kind}_port`, String(defaultPort(kind, next)), { shouldDirty: true });
    }
  };

  const onSubmit = form.handleSubmit((values) => {
    const common = {
      display_name: blankToNull(values.display_name),
      username: blankToNull(values.username) ?? (creating ? values.address.trim() : null),
      smtp_host: blankToNull(values.smtp_host),
      smtp_port: portOrNull(values.smtp_port),
      smtp_security: values.smtp_security,
      imap_host: blankToNull(values.imap_host),
      imap_port: portOrNull(values.imap_port),
      imap_security: values.imap_security,
      send_enabled: values.send_enabled,
      receive_enabled: values.receive_enabled,
      ...(values.password ? { password: values.password } : {}),
    };
    if (creating) {
      create.mutate(
        { ...common, address: values.address.trim(), owner: values.owner },
        {
          onSuccess: () => {
            toast.success(t("mail:toast.created"));
            onOpenChange(false);
          },
        },
      );
    } else {
      update.mutate(
        { id: account.id as number, payload: common },
        {
          onSuccess: () => {
            toast.success(t("mail:toast.updated"));
            onOpenChange(false);
          },
        },
      );
    }
  });

  const securityOptions = MAIL_SECURITIES.map((value) => (
    <SelectItem key={value} value={value}>
      {t(SECURITY_LABEL[value])}
    </SelectItem>
  ));

  return (
    <Dialog open={open} onOpenChange={(next) => (pending ? undefined : onOpenChange(next))}>
      <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{creating ? t("mail:dialog.createTitle") : t("mail:dialog.editTitle")}</DialogTitle>
          <DialogDescription>
            {creating ? t("mail:dialog.createDescription") : t("mail:dialog.editDescription")}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("mail:fields.address")} htmlFor="mail-address" error={errors.address?.message}>
              <Input
                id="mail-address"
                type="email"
                autoComplete="off"
                disabled={!creating}
                {...form.register("address")}
              />
            </Field>
            <Field
              label={t("mail:fields.displayName")}
              htmlFor="mail-display"
              hint={t("mail:fields.displayNameHint")}
            >
              <Input id="mail-display" {...form.register("display_name")} />
            </Field>
            <Field
              label={t("mail:fields.username")}
              htmlFor="mail-user"
              hint={t("mail:fields.usernameHint")}
            >
              <Input id="mail-user" autoComplete="off" {...form.register("username")} />
            </Field>
            <Field
              label={
                <span className="flex items-center gap-2">
                  {t("mail:fields.password")}
                  {account?.has_secret ? (
                    <Badge variant="success" className="px-1.5 py-0">
                      {t("mail:fields.passwordSaved")}
                    </Badge>
                  ) : null}
                </span>
              }
              htmlFor="mail-password"
              hint={account?.has_secret ? t("mail:fields.passwordKeep") : undefined}
              error={errors.password?.message}
            >
              <Input
                id="mail-password"
                type="password"
                autoComplete="new-password"
                placeholder={account?.has_secret ? "••••••••" : undefined}
                {...form.register("password")}
              />
            </Field>
          </div>

          {creating ? (
            <Field label={t("mail:fields.owner")}>
              <Controller
                control={form.control}
                name="owner"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger className="sm:max-w-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="me">{t("mail:fields.ownerMe")}</SelectItem>
                      <SelectItem value="shared" disabled={!canShare}>
                        {t("mail:fields.ownerShared")}
                        {!canShare ? ` · ${t("mail:guard.sharedNeedsAdmin")}` : ""}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </Field>
          ) : null}

          {(["smtp", "imap"] as const).map((kind) => {
            const enabledField = kind === "smtp" ? "send_enabled" : "receive_enabled";
            const on = kind === "smtp" ? sendOn : receiveOn;
            return (
              <fieldset key={kind} className="flex flex-col gap-3 rounded-lg border border-line p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <legend className="text-body font-medium text-ink">
                    {kind === "smtp" ? t("mail:fields.smtpTitle") : t("mail:fields.imapTitle")}
                  </legend>
                  <label className="flex min-h-9 items-center gap-2 text-caption text-ink-2">
                    <Controller
                      control={form.control}
                      name={enabledField}
                      render={({ field }) => (
                        <Switch checked={field.value} onCheckedChange={field.onChange} />
                      )}
                    />
                    {kind === "smtp" ? t("mail:fields.sendEnabled") : t("mail:fields.receiveEnabled")}
                  </label>
                </div>
                {on ? (
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_10rem]">
                    <Field
                      label={t("mail:fields.host")}
                      htmlFor={`mail-${kind}-host`}
                      error={errors[`${kind}_host`]?.message}
                    >
                      <Input
                        id={`mail-${kind}-host`}
                        autoComplete="off"
                        placeholder={kind === "smtp" ? "smtpout.secureserver.net" : "imap.secureserver.net"}
                        {...form.register(`${kind}_host`)}
                      />
                    </Field>
                    <Field
                      label={t("mail:fields.port")}
                      htmlFor={`mail-${kind}-port`}
                      error={errors[`${kind}_port`]?.message}
                    >
                      <Input
                        id={`mail-${kind}-port`}
                        inputMode="numeric"
                        className="tabular-nums"
                        {...form.register(`${kind}_port`)}
                      />
                    </Field>
                    <Field label={t("mail:fields.security")}>
                      <Controller
                        control={form.control}
                        name={`${kind}_security`}
                        render={({ field }) => (
                          <Select
                            value={field.value}
                            onValueChange={(v) => onSecurity(kind, v as MailSecurity)}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>{securityOptions}</SelectContent>
                          </Select>
                        )}
                      />
                    </Field>
                  </div>
                ) : null}
              </fieldset>
            );
          })}
          {errors.receive_enabled?.message ? (
            <p className="text-caption text-neg-text">{errors.receive_enabled.message}</p>
          ) : null}

          <ErrorBanner error={create.error || update.error} />

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
              {t("mail:actions.cancel")}
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? <Loader2 className="animate-spin" /> : null}
              {pending
                ? t("mail:actions.saving")
                : creating
                  ? t("mail:actions.add")
                  : t("mail:actions.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
