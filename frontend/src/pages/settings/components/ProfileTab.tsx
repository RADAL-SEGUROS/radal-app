import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Building2, ImageIcon, KeyRound, Pencil, Upload, UserRound } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useDocumentDownload, useDocuments, useUploadDocument } from "@/api/documents";
import { useChangePassword, useUpdateUser } from "@/api/users";
import { useAuth } from "@/providers/AuthProvider";
import { useCan } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import type { RadalDocument } from "@/api/types";
import {
  EmptyState,
  Field,
  GuardedButton,
  ReadOnlyField,
  SectionCard,
  type Guard,
} from "./shared";

/**
 * Broker profile + logo + my own account + password.
 *
 * The organization's master data is READ-ONLY here on purpose: there is no
 * `PATCH /brokers/{id}` endpoint in this pass, so the edit control is rendered
 * visibly disabled with a "pronto" tooltip rather than opening a form that
 * cannot save (rule 4/5: never invent a UI affordance with no backend).
 *
 * The logo IS wired: `POST /documents` with `entity_type=broker` +
 * `category=logo` runs the WEBP 512×512 pipeline and registers the row, and the
 * card shows the newest such document.
 */

function LogoPreview({ doc }: { doc: RadalDocument }) {
  const { data, isLoading } = useDocumentDownload(doc.id);
  if (isLoading || !data) {
    return <Skeleton className="h-24 w-24 rounded-[14px]" />;
  }
  return (
    <img
      src={data.url}
      alt={doc.original_name}
      className="h-24 w-24 rounded-[14px] border border-line object-cover"
    />
  );
}

function LogoCard({ brokerId, uploadGuard }: { brokerId: number; uploadGuard: Guard }) {
  const { t } = useTranslation("settings");
  const inputRef = React.useRef<HTMLInputElement>(null);
  const { data, isLoading } = useDocuments({
    entity_type: "broker",
    entity_id: brokerId,
    category: "logo",
    limit: 20,
  });
  const upload = useUploadDocument();

  const logos = data?.items ?? [];
  const current = logos[0];

  const onFile = (file: File | undefined) => {
    if (!file) return;
    upload.mutate(
      { file, entity_type: "broker", entity_id: brokerId, category: "logo" },
      {
        onSuccess: () => toast.success(t("logo.uploaded")),
        onError: () => toast.error(t("error.generic")),
      },
    );
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <SectionCard
      title={t("logo.title")}
      icon={<ImageIcon />}
      description={t("logo.description")}
      actions={
        <>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => onFile(e.target.files?.[0])}
          />
          <GuardedButton
            guard={uploadGuard}
            variant="secondary"
            size="sm"
            disabled={upload.isPending}
            onClick={() => inputRef.current?.click()}
          >
            <Upload />
            {upload.isPending
              ? t("logo.uploading")
              : current
                ? t("logo.replace")
                : t("logo.upload")}
          </GuardedButton>
        </>
      }
    >
      {isLoading ? (
        <Skeleton className="h-24 w-24 rounded-[14px]" />
      ) : !current ? (
        <EmptyState>{t("logo.empty")}</EmptyState>
      ) : (
        <div className="flex items-center gap-4">
          <LogoPreview doc={current} />
          <div className="min-w-0">
            <p className="truncate text-body text-text-primary">{current.original_name}</p>
            <p className="text-caption text-text-muted">
              {formatDateTime(current.created_at)}
            </p>
            {logos.length > 1 ? (
              <p className="mt-1 text-caption text-text-muted">
                {t("logo.history")}: {logos.length}
              </p>
            ) : null}
            <p className="mt-1 text-caption text-text-muted">{t("logo.hint")}</p>
          </div>
        </div>
      )}
    </SectionCard>
  );
}

function AccountCard() {
  const { t } = useTranslation("settings");
  const { user, refreshUser } = useAuth();
  const canEdit = useCan("Users", "Edit");
  const update = useUpdateUser(user?.id ?? 0);

  const [fullName, setFullName] = React.useState(user?.full_name ?? "");
  const [jobTitle, setJobTitle] = React.useState(user?.job_title ?? "");
  const [phone, setPhone] = React.useState(user?.phone ?? "");

  React.useEffect(() => {
    setFullName(user?.full_name ?? "");
    setJobTitle(user?.job_title ?? "");
    setPhone(user?.phone ?? "");
  }, [user]);

  const guard: Guard = canEdit.allowed
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("account.noEditPermission") };

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    update.mutate(
      {
        full_name: fullName.trim(),
        job_title: jobTitle.trim() || null,
        phone: phone.trim() || null,
      },
      {
        onSuccess: () => {
          toast.success(t("account.saved"));
          void refreshUser();
        },
        onError: () => toast.error(t("error.generic")),
      },
    );
  };

  return (
    <SectionCard
      title={t("account.title")}
      icon={<UserRound />}
      description={t("account.description")}
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t("account.fullName")} htmlFor="account-name">
            <Input
              id="account-name"
              value={fullName}
              disabled={!canEdit.allowed}
              onChange={(e) => setFullName(e.target.value)}
            />
          </Field>
          <Field
            label={t("account.email")}
            htmlFor="account-email"
            hint={t("account.emailReadOnly")}
          >
            <Input id="account-email" value={user?.email ?? ""} disabled readOnly />
          </Field>
          <Field label={t("account.jobTitle")} htmlFor="account-job">
            <Input
              id="account-job"
              value={jobTitle}
              disabled={!canEdit.allowed}
              onChange={(e) => setJobTitle(e.target.value)}
            />
          </Field>
          <Field label={t("account.phone")} htmlFor="account-phone">
            <Input
              id="account-phone"
              value={phone}
              disabled={!canEdit.allowed}
              onChange={(e) => setPhone(e.target.value)}
            />
          </Field>
        </div>
        <div className="flex items-center justify-between gap-3">
          <Badge variant="brand">
            {t("account.role")}: {user?.role ? t(`roles.${user.role}`, user.role) : "—"}
          </Badge>
          <GuardedButton guard={guard} type="submit" disabled={update.isPending}>
            {t("account.save")}
          </GuardedButton>
        </div>
      </form>
    </SectionCard>
  );
}

function PasswordCard() {
  const { t } = useTranslation("settings");
  const change = useChangePassword();
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError(t("password.tooShort"));
      return;
    }
    if (next !== confirm) {
      setError(t("password.mismatch"));
      return;
    }
    if (next === current) {
      setError(t("password.sameAsCurrent"));
      return;
    }
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          toast.success(t("password.changed"));
          setCurrent("");
          setNext("");
          setConfirm("");
        },
        onError: () => setError(t("password.wrongCurrent")),
      },
    );
  };

  return (
    <SectionCard
      title={t("password.title")}
      icon={<KeyRound />}
      description={t("password.description")}
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <Field label={t("password.current")} htmlFor="password-current">
          <Input
            id="password-current"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t("password.new")} htmlFor="password-new">
            <Input
              id="password-new"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </Field>
          <Field
            label={t("password.confirm")}
            htmlFor="password-confirm"
            error={error ?? undefined}
          >
            <Input
              id="password-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
        </div>
        <div className="flex justify-end">
          <GuardedButton
            guard={{ allowed: true, reason: "" }}
            type="submit"
            disabled={change.isPending || !current || !next || !confirm}
          >
            {t("password.submit")}
          </GuardedButton>
        </div>
      </form>
    </SectionCard>
  );
}

export function ProfileTab() {
  const { t } = useTranslation("settings");
  const { organization } = useAuth();
  const canUpload = useCan("Documents", "Upload");

  const uploadGuard: Guard = canUpload.allowed
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };

  return (
    <div className="flex flex-col gap-[22px]">
      <SectionCard
        title={t("profile.title")}
        icon={<Building2 />}
        description={t("profile.description")}
        actions={
          <GuardedButton
            guard={{ allowed: false, reason: t("profile.editSoon") }}
            variant="secondary"
            size="sm"
          >
            <Pencil /> {t("profile.edit")}
          </GuardedButton>
        }
      >
        {!organization ? (
          <EmptyState>{t("profile.empty")}</EmptyState>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <ReadOnlyField label={t("profile.legalName")} value={organization.legal_name} />
            <ReadOnlyField
              label={t("profile.tradeName")}
              value={organization.trade_name ?? "—"}
            />
            <ReadOnlyField
              label={t("profile.type")}
              value={t(`profile.orgType.${organization.type}`, organization.type)}
            />
            <ReadOnlyField label={t("profile.status")} value={organization.status ?? "—"} />
            <ReadOnlyField label={t("profile.id")} value={`#${organization.id}`} mono />
          </div>
        )}
      </SectionCard>

      {organization ? (
        <LogoCard brokerId={organization.id} uploadGuard={uploadGuard} />
      ) : null}

      <div className="grid grid-cols-1 gap-[22px] xl:grid-cols-2">
        <AccountCard />
        <PasswordCard />
      </div>
    </div>
  );
}
