import * as React from "react";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Power, ShieldCheck, UserPlus, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataTable } from "@/components/common/DataTable";
import { useAssignableRoles, useUsers } from "@/api/users";
import { useAuth } from "@/providers/AuthProvider";
import { useModulePermissions } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import type { User } from "@/api/types";
import { GuardedButton, SectionCard, type Guard } from "./shared";
import { InviteUserDialog } from "./InviteUserDialog";
import { ChangeRoleDialog, EditUserDialog, ToggleStatusDialog } from "./UserDialogs";

/**
 * Team & roles — `GET /users` with the invite/edit/role/status writes.
 *
 * Every row action is permission-gated from the RBAC matrix, and the two the
 * server refuses on your own account (role change, deactivation) are disabled
 * with that reason rather than failing with a 409.
 */

const ALL = "__all__";

export function TeamTab() {
  const { t } = useTranslation("settings");
  const { user: me } = useAuth();
  const perms = useModulePermissions("Users");

  const [search, setSearch] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [role, setRole] = React.useState(ALL);
  const [status, setStatus] = React.useState(ALL);

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<User | null>(null);
  const [changingRole, setChangingRole] = React.useState<User | null>(null);
  const [togglingStatus, setTogglingStatus] = React.useState<User | null>(null);

  React.useEffect(() => {
    const id = window.setTimeout(() => setDebounced(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const { data, isLoading } = useUsers(
    {
      search: debounced || undefined,
      role: role === ALL ? undefined : role,
      is_active: status === ALL ? undefined : status === "active",
      limit: 200,
    },
    perms.view,
  );
  const { data: roles = [] } = useAssignableRoles(perms.view);

  const createGuard: Guard = perms.create
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };
  const editGuard: Guard = perms.edit
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };
  const manageGuard: Guard = perms.manage
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };

  const selfGuard = (target: User): Guard =>
    target.id === me?.id ? { allowed: false, reason: t("guard.self") } : manageGuard;

  const columns = React.useMemo<ColumnDef<User>[]>(
    () => [
      {
        accessorKey: "full_name",
        header: t("team.columns.name"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate font-medium text-text-primary">
              {row.original.full_name}
              {row.original.id === me?.id ? (
                <span className="ml-2 text-caption text-text-muted">({t("team.you")})</span>
              ) : null}
            </p>
            <p className="truncate text-caption text-text-muted">{row.original.email}</p>
          </div>
        ),
      },
      {
        accessorKey: "role",
        header: t("team.columns.role"),
        cell: ({ row }) => (
          <Badge variant="brand">{t(`roles.${row.original.role}`, row.original.role)}</Badge>
        ),
      },
      {
        accessorKey: "job_title",
        header: t("team.columns.jobTitle"),
        cell: ({ row }) => row.original.job_title ?? "—",
      },
      {
        accessorKey: "is_active",
        header: t("team.columns.status"),
        cell: ({ row }) => (
          <Badge variant={row.original.is_active ? "success" : "muted"}>
            {row.original.is_active ? t("team.statusActive") : t("team.statusInactive")}
          </Badge>
        ),
      },
      {
        accessorKey: "last_login_at",
        header: t("team.columns.lastLogin"),
        cell: ({ row }) =>
          row.original.last_login_at
            ? formatDateTime(row.original.last_login_at)
            : t("team.never"),
      },
      {
        id: "actions",
        header: "",
        enableSorting: false,
        cell: ({ row }) => {
          const target = row.original;
          return (
            <div className="flex items-center justify-end gap-1">
              <GuardedButton
                guard={editGuard}
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={t("team.actions.editProfile")}
                onClick={() => setEditing(target)}
              >
                <Pencil />
              </GuardedButton>
              <GuardedButton
                guard={selfGuard(target)}
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={t("team.actions.changeRole")}
                onClick={() => setChangingRole(target)}
              >
                <ShieldCheck />
              </GuardedButton>
              <GuardedButton
                guard={target.is_active ? selfGuard(target) : manageGuard}
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={
                  target.is_active
                    ? t("team.actions.deactivate")
                    : t("team.actions.activate")
                }
                onClick={() => setTogglingStatus(target)}
              >
                <Power />
              </GuardedButton>
            </div>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, me?.id, editGuard.allowed, manageGuard.allowed],
  );

  return (
    <SectionCard
      title={t("team.title")}
      icon={<Users />}
      description={t("team.description")}
      actions={
        <GuardedButton guard={createGuard} size="sm" onClick={() => setInviteOpen(true)}>
          <UserPlus /> {t("team.invite")}
        </GuardedButton>
      }
      bodyClassName="p-5 pt-4"
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("team.search")}
          className="h-9 w-full max-w-xs"
        />
        <Select value={role} onValueChange={setRole}>
          <SelectTrigger className="h-9 w-[210px]">
            <SelectValue placeholder={t("team.filterRole")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("team.allRoles")}</SelectItem>
            {roles.map((entry) => (
              <SelectItem key={entry.role} value={entry.role}>
                {t(`roles.${entry.role}`, entry.role)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-9 w-[160px]">
            <SelectValue placeholder={t("team.filterStatus")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("team.allStatuses")}</SelectItem>
            <SelectItem value="active">{t("team.active")}</SelectItem>
            <SelectItem value="inactive">{t("team.inactive")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <DataTable
        columns={columns}
        data={data?.items ?? []}
        isLoading={isLoading}
        emptyMessage={t("team.empty")}
      />

      <InviteUserDialog open={inviteOpen} onOpenChange={setInviteOpen} />
      {editing ? <EditUserDialog user={editing} onDone={() => setEditing(null)} /> : null}
      {changingRole ? (
        <ChangeRoleDialog user={changingRole} onDone={() => setChangingRole(null)} />
      ) : null}
      {togglingStatus ? (
        <ToggleStatusDialog
          user={togglingStatus}
          onDone={() => setTogglingStatus(null)}
        />
      ) : null}
    </SectionCard>
  );
}
