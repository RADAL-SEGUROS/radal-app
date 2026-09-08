import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import { useAuth } from "@/providers/AuthProvider";
import type { UserType } from "@/api/types";

/**
 * The effective permission matrix for the signed-in user, from
 * `GET /api/v1/auth/permissions`.
 *
 * `backend/app/core/roles_config.py` is the SINGLE source of truth. The UI never
 * hardcodes "admins can do X" — it asks the server and then either shows the
 * control, or renders it visibly disabled. Never a dead button.
 *
 * Tenant scoping is NOT in this matrix: a "yes" means "allowed on your own
 * broker's rows", and the server enforces that on every query regardless.
 */

/** `"partial"` = allowed, but the server narrows the scope or editable fields. */
export type Grant = "yes" | "no" | "partial";

/** Modules of the RBAC matrix. Mirrors `roles_config.MODULES`. */
export const MODULES = [
  "Dashboard",
  "Clients",
  "Assets",
  "Placements",
  "Quotes",
  "Proposals",
  "Inspections",
  "Insurers",
  "Offerings",
  "Documents",
  "CaseFiles",
  "Leads",
  "Groups",
  "Policies",
  "Endorsements",
  "Collections",
  "Claims",
  "Reports",
  "Users",
  "Settings",
] as const;
export type PermissionModule = (typeof MODULES)[number];

/** Actions of the RBAC matrix. Mirrors `roles_config.ACTIONS`. */
export const ACTIONS = [
  "View",
  "Create",
  "Edit",
  "Delete",
  "Comment",
  "Upload",
  "Submit",
  "Approve",
  "Manage",
] as const;
export type PermissionAction = (typeof ACTIONS)[number];

export interface PermissionsResponse {
  user_id: number;
  user_type: UserType | null;
  role: string | null;
  modules: string[];
  actions: string[];
  grants: { yes: string; no: string; partial: string };
  /** Raw grants, so the UI can mark a control as partially allowed. */
  matrix: Record<string, Record<string, Grant>>;
  /** Coarse booleans — what `can()` reads. */
  allowed: Record<string, Record<string, boolean>>;
}

/** The permission matrix query. Cached for the session; cleared on logout. */
export function usePermissions() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: qk.auth.permissions,
    enabled: isAuthenticated,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data } = await api.get<PermissionsResponse>("/auth/permissions");
      return data;
    },
  });
}

/** Coarse gate: does the matrix allow `action` on `module`? */
export function can(
  perms: PermissionsResponse | undefined,
  module: PermissionModule,
  action: PermissionAction,
): boolean {
  return !!perms?.allowed?.[module]?.[action];
}

/** True when the grant is `"partial"` — allowed, but with a server-side caveat. */
export function isPartial(
  perms: PermissionsResponse | undefined,
  module: PermissionModule,
  action: PermissionAction,
): boolean {
  return perms?.matrix?.[module]?.[action] === "partial";
}

/**
 * Hook form of {@link can}, for gating a single control.
 *
 * `isLoading` matters: render the control disabled (not hidden, not enabled)
 * while the matrix is in flight, so nothing flashes as clickable and then fails.
 */
export function useCan(module: PermissionModule, action: PermissionAction) {
  const { data, isLoading } = usePermissions();
  return {
    allowed: can(data, module, action),
    partial: isPartial(data, module, action),
    isLoading,
  };
}

/** Every action granted on a module — handy for a detail page's toolbar. */
export function useModulePermissions(module: PermissionModule) {
  const { data, isLoading } = usePermissions();
  const row = data?.allowed?.[module];
  return {
    isLoading,
    view: !!row?.View,
    create: !!row?.Create,
    edit: !!row?.Edit,
    remove: !!row?.Delete,
    comment: !!row?.Comment,
    upload: !!row?.Upload,
    submit: !!row?.Submit,
    approve: !!row?.Approve,
    manage: !!row?.Manage,
  };
}

/**
 * Admin gate for the broker workspace. `broker_admin` is the only broker role
 * holding Settings:Manage / Users:Manage, so those grants define "admin". The
 * `user_type` check keeps insured/insurer admins — who administer only their own
 * organization — from passing this gate.
 */
export function isBrokerAdmin(perms: PermissionsResponse | undefined): boolean {
  if (!perms || perms.user_type !== "broker") return false;
  return can(perms, "Settings", "Manage") || can(perms, "Users", "Manage");
}

export function useIsBrokerAdmin(): { isAdmin: boolean; isLoading: boolean } {
  const { data, isLoading } = usePermissions();
  return { isAdmin: isBrokerAdmin(data), isLoading };
}
