/**
 * Route-level permission gate, driven by `GET /auth/permissions`.
 *
 * Used where a whole route family belongs to one module the broker roles do not
 * hold — the `/platform/*` maintainer console (`Platform.View`). The server
 * still enforces every endpoint behind it; this only keeps a broker who types
 * the URL from landing on a page full of 403s. Never a hardcoded role check:
 * the grant comes from the matrix, like every rail row.
 *
 * Also exports `HomeRedirect`: the front door is `/policies` (v12 — policies
 * are the main view), except for a platform user, who has no broker workspace
 * to list policies from and is sent to the console instead.
 */
import { Navigate, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Lock } from "lucide-react";
import { PageHeader } from "@/components/common/PageHeader";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  can,
  usePermissions,
  type PermissionAction,
  type PermissionModule,
} from "@/lib/permissions";

export function RequirePermission({
  module,
  action = "View",
}: {
  module: PermissionModule;
  action?: PermissionAction;
}) {
  const { t } = useTranslation("common");
  const { data: perms, isLoading } = usePermissions();

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!can(perms, module, action)) {
    return (
      <>
        <PageHeader title={t("state.noAccess")} />
        <Card className="flex flex-col items-center gap-3 py-14 text-center">
          <span className="grid h-10 w-10 place-items-center rounded-sm bg-paper-2 text-ink-3">
            <Lock className="h-5 w-5" strokeWidth={1.5} />
          </span>
          <p className="max-w-md text-pretty text-body text-ink-3">{t("state.noAccessHint")}</p>
        </Card>
      </>
    );
  }
  return <Outlet />;
}

export function HomeRedirect() {
  const { data: perms, isLoading } = usePermissions();
  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const platformHome = perms?.user_type === "platform" && can(perms, "Platform", "View");
  return <Navigate to={platformHome ? "/platform" : "/policies"} replace />;
}
