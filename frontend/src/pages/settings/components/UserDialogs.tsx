import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
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
import {
  useAssignableRoles,
  useUpdateUser,
  useUpdateUserRole,
  useUpdateUserStatus,
} from "@/api/users";
import type { AssignableRole, User } from "@/api/types";
import { Field } from "./shared";

/**
 * Per-user write dialogs. Each is mounted only while its user is selected, so
 * the id-bound mutation hooks (`useUpdateUser(id)` …) always have a real id.
 *
 * Endpoints: `PATCH /users/{id}`, `PATCH /users/{id}/role`,
 * `PATCH /users/{id}/status`. The server refuses self-demotion and the last
 * active admin (409); the caller already hides those actions for yourself.
 */

export function EditUserDialog({ user, onDone }: { user: User; onDone: () => void }) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const update = useUpdateUser(user.id);
  const [fullName, setFullName] = React.useState(user.full_name);
  const [jobTitle, setJobTitle] = React.useState(user.job_title ?? "");
  const [phone, setPhone] = React.useState(user.phone ?? "");

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("team.editTitle")}</DialogTitle>
          <DialogDescription>{user.email}</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            update.mutate(
              {
                full_name: fullName.trim(),
                job_title: jobTitle.trim() || null,
                phone: phone.trim() || null,
              },
              {
                onSuccess: () => {
                  toast.success(t("team.toast.updated"));
                  onDone();
                },
                onError: () => toast.error(t("error.generic")),
              },
            );
          }}
        >
          <Field label={t("account.fullName")} htmlFor="edit-user-name">
            <Input
              id="edit-user-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </Field>
          <Field label={t("account.jobTitle")} htmlFor="edit-user-job">
            <Input
              id="edit-user-job"
              value={jobTitle}
              onChange={(e) => setJobTitle(e.target.value)}
            />
          </Field>
          <Field label={t("account.phone")} htmlFor="edit-user-phone">
            <Input
              id="edit-user-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={onDone}
              disabled={update.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? tc("actions.loading") : tc("actions.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ChangeRoleDialog({ user, onDone }: { user: User; onDone: () => void }) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const { data: roles = [] } = useAssignableRoles();
  const update = useUpdateUserRole(user.id);
  const [role, setRole] = React.useState(user.role);

  // The server refuses a role from another actor family (422), so only offer
  // the ones that match this user's family.
  const options = roles.filter((entry: AssignableRole) => entry.user_type === user.user_type);

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("team.roleTitle")}</DialogTitle>
          <DialogDescription>{t("team.roleDescription")}</DialogDescription>
        </DialogHeader>
        <Field label={t("account.role")}>
          <Select value={role} onValueChange={setRole}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {options.map((entry) => (
                <SelectItem key={entry.role} value={entry.role}>
                  {t(`roles.${entry.role}`, entry.role)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <DialogFooter>
          <Button variant="secondary" onClick={onDone} disabled={update.isPending}>
            {tc("actions.cancel")}
          </Button>
          <Button
            disabled={update.isPending || role === user.role}
            onClick={() =>
              update.mutate(
                { role },
                {
                  onSuccess: () => {
                    toast.success(t("team.toast.roleChanged"));
                    onDone();
                  },
                  onError: () => toast.error(t("error.generic")),
                },
              )
            }
          >
            {update.isPending ? tc("actions.loading") : tc("actions.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ToggleStatusDialog({
  user,
  onDone,
}: {
  user: User;
  onDone: () => void;
}) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const update = useUpdateUserStatus(user.id);
  const activating = !user.is_active;

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {activating ? t("team.activateTitle") : t("team.deactivateTitle")}
          </DialogTitle>
          <DialogDescription>
            {activating ? t("team.activateDescription") : t("team.deactivateDescription")}
          </DialogDescription>
        </DialogHeader>
        <p className="text-body text-text-secondary">
          {user.full_name} · {user.email}
        </p>
        <DialogFooter>
          <Button variant="secondary" onClick={onDone} disabled={update.isPending}>
            {tc("actions.cancel")}
          </Button>
          <Button
            variant={activating ? "primary" : "destructive"}
            disabled={update.isPending}
            onClick={() =>
              update.mutate(
                { is_active: activating },
                {
                  onSuccess: () => {
                    toast.success(
                      activating ? t("team.toast.activated") : t("team.toast.deactivated"),
                    );
                    onDone();
                  },
                  onError: () => toast.error(t("error.generic")),
                },
              )
            }
          >
            {update.isPending
              ? tc("actions.loading")
              : activating
                ? t("team.actions.activate")
                : t("team.actions.deactivate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
