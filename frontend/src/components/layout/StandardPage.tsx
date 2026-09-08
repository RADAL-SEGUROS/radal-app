import { Outlet, useLocation } from "react-router-dom";

/**
 * The standard page frame — a layout route wrapped around every page that is
 * NOT part of the group family (spec §5.1).
 *
 * It owns the two things `AppShell` used to own:
 *   - `key={location.pathname}`, so the entrance motion replays on every route
 *     change;
 *   - the `mx-auto max-w-[1380px] px-7` reading column.
 *
 * Both had to leave `AppShell`, because the group family renders its own
 * contextual rail edge-to-edge and must NOT remount on navigation — remounting
 * would collapse the tree's expanded state on every click inside it.
 */
export function StandardPage() {
  const location = useLocation();
  return (
    <div
      key={location.pathname}
      className="mx-auto flex w-full max-w-[1380px] flex-col gap-[22px] px-7 pb-14 pt-[26px]"
    >
      <Outlet />
    </div>
  );
}
