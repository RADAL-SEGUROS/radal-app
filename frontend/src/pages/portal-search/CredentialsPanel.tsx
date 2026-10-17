/**
 * "Credenciales" (`?tab=credentials`) — the broker's portal logins.
 *
 * WRITE-ONLY: `PUT /portal-searches/credentials/{portal}` takes the password,
 * the read side only ever says `has_secret`. A blank password on an existing
 * credential keeps the stored one (the server contract). Gated on
 * `Settings.Manage` from the server matrix: without it the form renders
 * read-only with the reason, never hidden.
 */
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { CircleCheck, KeyRound, Loader2, ShieldCheck, Trash2 } from "lucide-react";

import {
  portalErrorDetail,
  useDeletePortalCredentials,
  useSavePortalCredentials,
  type Portal,
} from "@/api/rpa";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DisabledHint, EmptyState, KeyValue, SoonButton, apiError } from "@/components/common/kit";
import { formatDateTime } from "@/lib/format";
import {
  CREDENTIAL_STATUS_LABEL_KEY,
  CREDENTIAL_STATUS_TONE,
  own,
} from "@/pages/portal-search/labels";

export function CredentialsPanel({
  portals,
  canManage,
  permsLoading,
}: {
  portals: Portal[];
  canManage: boolean;
  permsLoading: boolean;
}) {
  const { t } = useTranslation("rpa");
  const built = portals.filter((p) => !p.coming_soon);
  const withLogin = built.filter((p) => p.requires_credentials);
  const withoutLogin = built.filter((p) => !p.requires_credentials);
  const upcoming = portals.filter((p) => p.coming_soon);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2.5 rounded-lg bg-brand-soft px-3.5 py-2.5 text-caption text-brand-deep">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
        <p className="[text-wrap:pretty]">{t("credentials.hint")}</p>
      </div>

      {!canManage && !permsLoading ? (
        <p className="text-caption text-ink-3">{t("credentials.noPermission")}</p>
      ) : null}

      {withLogin.length === 0 ? (
        <Card className="p-2">
          <EmptyState icon={<KeyRound />} title={t("credentials.empty")} />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {withLogin.map((portal) => (
            <CredentialCard
              key={portal.key}
              portal={portal}
              canManage={canManage}
              permsLoading={permsLoading}
            />
          ))}
        </div>
      )}

      {withoutLogin.length > 0 ? (
        <div className="flex flex-wrap gap-2 text-caption text-ink-3">
          {withoutLogin.map((portal) => (
            <span key={portal.key}>
              {portal.name}: {t("credentials.notRequired")}
            </span>
          ))}
        </div>
      ) : null}

      {upcoming.length > 0 ? (
        <Card className="flex flex-col gap-3 p-4">
          <div>
            <h3 className="text-label font-medium text-ink">{t("credentials.upcoming")}</h3>
            <p className="mt-0.5 text-caption text-ink-3 [text-wrap:pretty]">{t("credentials.upcomingHint")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {upcoming.map((portal) => (
              <SoonButton
                key={portal.key}
                reason={portal.maintenance_reason ?? undefined}
                className="min-h-10 sm:min-h-8"
              >
                {portal.name}
              </SoonButton>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function CredentialCard({
  portal,
  canManage,
  permsLoading,
}: {
  portal: Portal;
  canManage: boolean;
  permsLoading: boolean;
}) {
  const { t } = useTranslation("rpa");
  const credential = portal.credential;
  const hasSecret = !!credential?.has_secret;
  const save = useSavePortalCredentials(portal.key);
  const remove = useDeletePortalCredentials(portal.key);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const schema = React.useMemo(
    () =>
      z.object({
        username: z.string().trim().min(1, t("credentials.usernameRequired")).max(255),
        // Blank keeps the stored password; required only when nothing is stored.
        password: z
          .string()
          .max(512)
          .refine((value) => hasSecret || value.length > 0, t("credentials.passwordRequired")),
      }),
    [hasSecret, t],
  );
  type Values = z.infer<typeof schema>;

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { username: credential?.username ?? "", password: "" },
  });

  // The server row changed (saved elsewhere, deleted): re-seed the username.
  React.useEffect(() => {
    form.reset({ username: credential?.username ?? "", password: "" });
  }, [credential?.username, credential?.updated_at, form]);

  const disabled = !canManage || permsLoading;
  const reason = !canManage && !permsLoading ? t("credentials.noPermission") : null;

  const onSubmit = form.handleSubmit((values) => {
    save.mutate(
      { username: values.username.trim(), password: values.password ? values.password : null },
      {
        onSuccess: () => {
          toast.success(t("credentials.saved"));
          form.reset({ username: values.username.trim(), password: "" });
        },
        onError: (error) => {
          const detail = portalErrorDetail(error);
          if (detail?.code === "password_required") {
            form.setError("password", { type: "server", message: detail.message ?? t("credentials.passwordRequired") });
          }
          toast.error(detail?.message ?? apiError(error));
        },
      },
    );
  });

  const statusKey = credential ? own(CREDENTIAL_STATUS_LABEL_KEY, credential.status) : null;
  const statusTone = credential ? own(CREDENTIAL_STATUS_TONE, credential.status) : null;

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep">
          <KeyRound className="h-4 w-4" strokeWidth={1.5} aria-hidden />
        </span>
        <h3 className="min-w-0 flex-1 truncate text-label font-medium text-ink">{portal.name}</h3>
        {hasSecret ? (
          <Badge variant="success" dot>
            {t("credentials.hasSecret")}
          </Badge>
        ) : (
          <Badge variant="muted">{t("credentials.noSecret")}</Badge>
        )}
      </div>

      {credential ? (
        <div className="grid grid-cols-2 gap-3">
          <KeyValue
            label={t("credentials.statusLabel")}
            value={
              statusKey ? (
                <Badge variant={statusTone ?? "neutral"}>{t(statusKey)}</Badge>
              ) : (
                credential.status
              )
            }
          />
          <KeyValue label={t("credentials.updated")} value={formatDateTime(credential.updated_at)} />
          {credential.last_verified_at ? (
            <KeyValue label={t("credentials.lastVerified")} value={formatDateTime(credential.last_verified_at)} />
          ) : null}
          {credential.last_error ? (
            <div className="col-span-2 rounded-lg bg-neg-soft px-3 py-2 text-caption text-ink-2">
              <span className="font-medium text-neg-text">{t("credentials.lastError")}:</span>{" "}
              {credential.last_error}
            </div>
          ) : null}
        </div>
      ) : null}

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`cred-user-${portal.key}`} className="text-caption text-ink-3">
              {t("credentials.username")}
            </Label>
            <Input
              id={`cred-user-${portal.key}`}
              autoComplete="off"
              disabled={disabled}
              aria-invalid={!!form.formState.errors.username}
              className="h-10 sm:h-9"
              {...form.register("username")}
            />
            {form.formState.errors.username ? (
              <p className="text-caption text-neg-text">{form.formState.errors.username.message}</p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`cred-pass-${portal.key}`} className="text-caption text-ink-3">
              {t("credentials.password")}
            </Label>
            <Input
              id={`cred-pass-${portal.key}`}
              type="password"
              autoComplete="new-password"
              disabled={disabled}
              placeholder={hasSecret ? "••••••••" : undefined}
              aria-invalid={!!form.formState.errors.password}
              className="h-10 sm:h-9"
              {...form.register("password")}
            />
            {form.formState.errors.password ? (
              <p className="text-caption text-neg-text">{form.formState.errors.password.message}</p>
            ) : hasSecret ? (
              <p className="text-[11px] text-ink-3">{t("credentials.passwordKeep")}</p>
            ) : null}
          </div>
        </div>

        <div className="flex flex-col-reverse gap-2 border-t border-line pt-3 sm:flex-row sm:items-center sm:justify-between">
          {credential ? (
            <DisabledHint hint={reason}>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="min-h-10 text-neg-text sm:min-h-8"
                disabled={disabled || remove.isPending}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                {t("credentials.delete")}
              </Button>
            </DisabledHint>
          ) : (
            <span />
          )}
          <DisabledHint hint={reason} className="w-full sm:w-auto">
            <Button type="submit" disabled={disabled || save.isPending} className="min-h-10 w-full sm:w-auto">
              {save.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : save.isSuccess && !form.formState.isDirty ? (
                <CircleCheck className="h-4 w-4" aria-hidden />
              ) : null}
              {t("credentials.save")}
            </Button>
          </DisabledHint>
        </div>
      </form>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("credentials.deleteTitle", { portal: portal.name })}</DialogTitle>
            <DialogDescription>{t("credentials.deleteBody", { portal: portal.name })}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="ghost" className="min-h-10 sm:min-h-9" onClick={() => setConfirmDelete(false)}>
              {t("credentials.cancel")}
            </Button>
            <Button
              variant="destructive"
              className="min-h-10 sm:min-h-9"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(undefined, {
                  onSuccess: () => {
                    toast.success(t("credentials.deleted"));
                    setConfirmDelete(false);
                  },
                  onError: (error) => toast.error(apiError(error)),
                })
              }
            >
              {remove.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
              {t("credentials.deleteConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
