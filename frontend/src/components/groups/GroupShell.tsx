/**
 * Layout route for the whole `/groups/:groupId` family (spec v4 §2.6).
 *
 * Since the single context-switching sidebar landed, this shell no longer
 * renders any rail: `components/layout/Sidebar.tsx` detects the group route
 * itself and swaps its content to `GroupSidebar` — the tree — in BOTH the
 * desktop rail and the mobile sheet. The no-remount guarantee for the tree
 * moved up with it and is documented in `Sidebar.tsx`; this component's only
 * jobs now are:
 *
 *  1. **The 404 guard.** A group that does not exist — or belongs to another
 *     broker, which the API answers as 404 by design, never 403 — gets a REAL
 *     404 page. The router always matches `groups/:groupId`, so this is the
 *     only place that can say so.
 *  2. **The page frame.** `StandardPage` gives every other route its padding
 *     and reading column, but the group family sits OUTSIDE it, so without
 *     this the pages render flush against both edges. `min-w-0` is what
 *     actually lets the column shrink — a flex/grid child defaults to
 *     `min-width:auto`, so a wide table inside would otherwise push the layout
 *     instead of scrolling in its own container.
 */
import { Link, Outlet, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { FolderOpen } from "lucide-react";
import { useGroupTree } from "@/api/accountGroups";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/pages/proposals/shared";

/** Foreign or absent — the API refuses both with 404 so ids cannot be probed. */
function isNotFound(error: unknown): boolean {
  const status = (error as { response?: { status?: number } } | null)?.response?.status;
  return status === 404;
}

function GroupNotFound() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  return (
    <div className="mx-auto flex w-full max-w-[720px] flex-col px-7 pb-14 pt-[26px]">
      <EmptyState
        title={t("group.notFound")}
        hint={t("group.subtitle")}
        icon={<FolderOpen className="h-6 w-6" />}
        action={
          <Button variant="secondary" size="sm" asChild>
            <Link to="/groups">{tc("actions.back")}</Link>
          </Button>
        }
      />
    </div>
  );
}

export default function GroupShell() {
  const params = useParams<{ groupId: string }>();
  const groupId = Number(params.groupId);
  const valid = Number.isInteger(groupId) && groupId > 0;

  // The sidebar fetches the same query for the tree; this read shares its
  // cache and exists solely to answer "is this a real group of mine?".
  const { error } = useGroupTree(valid ? groupId : undefined);

  if (!valid || isNotFound(error)) return <GroupNotFound />;

  return (
    <div className="mx-auto flex w-full min-w-0 max-w-[1240px] flex-col gap-[22px] px-5 pb-14 pt-6 sm:px-7">
      <Outlet />
    </div>
  );
}

export { GroupShell };
