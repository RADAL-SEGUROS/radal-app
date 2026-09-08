import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Check, Copy } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAssignableRoles, useCreateUser } from "@/api/users";
import type { UserInviteResponse } from "@/api/types";
import { Field } from "./shared";

/**
 * Invite a user — `POST /users`.
 *
 * The role options come from `GET /users/roles`, i.e. exactly the roles this
 * actor may grant, so the form can never offer something the server rejects.
 * When no password is supplied the API returns a one-time temporary password;
 * there is no outbound email in this pass, so it is shown once for the admin to
 * pass on.
 */
export function InviteUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const { data: roles = [] } = useAssignableRoles(open);
  const create = useCreateUser();

  const [email, setEmail] = React.useState("");
  const [fullName, setFullName] = React.useState("");
  const [role, setRole] = React.useState("");
  const [jobTitle, setJobTitle] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [invited, setInvited] = React.useState<UserInviteResponse | null>(null);
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    if (open) return;
    setEmail("");
    setFullName("");
    setRole("");
    setJobTitle("");
    setPhone("");
    setPassword("");
    setError(null);
    setInvited(null);
    setCopied(false);
  }, [open]);

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError(t("invite.emailInvalid"));
      return;
    }
    if (!fullName.trim()) {
      setError(t("invite.nameRequired"));
      return;
    }
    if (!role) {
      setError(t("invite.roleRequired"));
      return;
    }
    if (password && password.length < 8) {
      setError(t("invite.passwordShort"));
      return;
    }
    create.mutate(
      {
        email: email.trim(),
        full_name: fullName.trim(),
        role,
        job_title: jobTitle.trim() || null,
        phone: phone.trim() || null,
        password: password || null,
      },
      {
        onSuccess: (data) => {
          toast.success(t("invite.created"));
          if (data.temporary_password) {
            setInvited(data);
          } else {
            onOpenChange(false);
          }
        },
        onError: (err: unknown) => {
          const status = (err as { response?: { status?: number } })?.response?.status;
          setError(status === 409 ? t("invite.duplicate") : t("error.generic"));
        },
      },
    );
  };

  const copy = async () => {
    if (!invited?.temporary_password) return;
    try {
      await navigator.clipboard.writeText(invited.temporary_password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        {invited?.temporary_password ? (
          <>
            <DialogHeader>
              <DialogTitle>{t("invite.tempTitle")}</DialogTitle>
              <DialogDescription>
                {t("invite.tempDescription", { name: invited.user.full_name })}
              </DialogDescription>
            </DialogHeader>
            <div className="flex items-center gap-2 rounded-lg border border-line bg-bg-recessed px-3.5 py-3">
              <code className="flex-1 truncate font-mono text-[13px] text-text-primary">
                {invited.temporary_password}
              </code>
              <Button variant="secondary" size="sm" onClick={copy}>
                {copied ? <Check /> : <Copy />}
                {copied ? t("invite.copied") : t("invite.copy")}
              </Button>
            </div>
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>{t("invite.done")}</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>{t("invite.title")}</DialogTitle>
              <DialogDescription>{t("invite.description")}</DialogDescription>
            </DialogHeader>
            <form onSubmit={onSubmit} className="flex flex-col gap-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label={t("invite.fullName")} htmlFor="invite-name">
                  <Input
                    id="invite-name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </Field>
                <Field label={t("invite.email")} htmlFor="invite-email">
                  <Input
                    id="invite-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </Field>
              </div>

              <Field label={t("invite.role")}>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("invite.roleRequired")} />
                  </SelectTrigger>
                  <SelectContent>
                    {roles.map((entry) => (
                      <SelectItem key={entry.role} value={entry.role}>
                        {t(`roles.${entry.role}`, entry.role)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label={t("invite.jobTitle")} htmlFor="invite-job">
                  <Input
                    id="invite-job"
                    value={jobTitle}
                    onChange={(e) => setJobTitle(e.target.value)}
                  />
                </Field>
                <Field label={t("invite.phone")} htmlFor="invite-phone">
                  <Input
                    id="invite-phone"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                  />
                </Field>
              </div>

              <Field
                label={t("invite.password")}
                htmlFor="invite-password"
                hint={t("invite.passwordHint")}
                error={error ?? undefined}
              >
                <Input
                  id="invite-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>

              <DialogFooter>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => onOpenChange(false)}
                  disabled={create.isPending}
                >
                  {tc("actions.cancel")}
                </Button>
                <Button type="submit" disabled={create.isPending}>
                  {create.isPending ? tc("actions.loading") : t("invite.submit")}
                </Button>
              </DialogFooter>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
